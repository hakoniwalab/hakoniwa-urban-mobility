"""PX4 SITL for the one-Drone City route (tools/drone_one.py, profile eams-nominal-9kg-px4).

The City model, the Viewer and the controls stay those of the Drone Core route;
three things change:

- the vehicle: config/drone/hexa-px4 (3 ms MuJoCo step, HIL sensor period)
  with the magnetic field PX4's World Magnetic Model expects at the City origin
  (tools/px4_magnetic.py), so the PX4 EKF gets a consistent heading
- the contact: no landing box. The box on the City mesh made the IMU chatter
  (about 4 m/s^2 at rest) and PX4 refused to arm ("High Accelerometer Bias");
  the two skids alone rest still
- the Launcher: PX4 SITL starts first, and the Drone Core aircraft service
  replaces the drone service under the same asset name (drone-service-1), so
  readiness, the pacer, the Viewer and the controls keep working

configure builds PX4 SITL when it is not built yet (ensure_built); the
managed Recipe (recipes/usecases/urban-drone-px4.yaml) materializes
PX4-Autopilot and pymavlink before.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess

import px4_magnetic

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
PROFILE = "eams-nominal-9kg-px4"
HEXA_PX4_ROOT = ROOT / "config" / "drone" / "hexa-px4"
PX4_OUT = ROOT / "build" / "px4-sitl"
AIRFRAME = "900002_hakoniwa_eams"
SIM_MODEL = "hakoniwa_eams"
SERVICE_ASSET = "drone-service-1"
PX4_ASSET = "px4-sitl"
# Seconds PX4 needs to open TCP 4560 before the aircraft service connects.
PX4_START_DELAY_SEC = 3
# MAVLink API port of the PX4 SITL instance 0 (apps/drone/mavlink).
MAVLINK_URL = "udpin:127.0.0.1:14540"


class Px4Error(RuntimeError):
    pass


def is_px4(profile: str | None) -> bool:
    return profile == PROFILE


def px4_root() -> Path:
    return Path(os.environ.get("PX4_AUTOPILOT_ROOT", WORKSPACE / "PX4-Autopilot")).resolve()


def hexa_sources() -> tuple[Path, Path]:
    """The vehicle model and config the City model is made from."""
    return HEXA_PX4_ROOT / "drone.xml", HEXA_PX4_ROOT / "drone_config_0.json"


def apply_type_config(type_config: dict, hexa_config: dict) -> None:
    """SITL settings on the City type config (location stays the City origin)."""
    simulation = type_config["simulation"]
    simulation["timeStep"] = hexa_config["simulation"]["timeStep"]
    simulation["mavlink_tx_period_msec"] = copy.deepcopy(hexa_config["simulation"]["mavlink_tx_period_msec"])
    location = simulation["location"]
    location["magneticField"] = px4_magnetic.magnetic_field(
        px4_root(), float(location["latitude"]), float(location["longitude"])
    )


def ensure_built() -> Path:
    """Build PX4 SITL out of tree (tools/px4_sitl_build.bash) unless it is built."""
    binary = PX4_OUT / "build" / "px4_sitl_default" / "bin" / "px4"
    if binary.is_file():
        return binary
    print(f"PX4 SITL: building {px4_root()} into {PX4_OUT} (the first build takes a few minutes)", flush=True)
    result = subprocess.run(
        ["bash", str(ROOT / "tools" / "px4_sitl_build.bash"), "--px4-dir", str(px4_root()), "--out", str(PX4_OUT)],
        cwd=ROOT, check=False,
    )
    if result.returncode != 0 or not binary.is_file():
        raise Px4Error(f"the PX4 SITL build failed (exit code {result.returncode}); see the output above")
    return binary


def _aircraft_service_source(drone_root: Path) -> Path:
    system = platform.system()
    if system == "Darwin":
        return drone_root / "mac" / "mac-main_hako_aircraft_service_px4"
    if system == "Linux":
        return drone_root / "lnx" / "linux-main_hako_aircraft_service_px4"
    raise Px4Error(f"unsupported platform for PX4 SITL: {system}")


def prepare_runtime(recipe_root: Path, drone_root: Path) -> dict:
    """PX4 startup data with the EAMS airframe, a PX4 rootfs and the aircraft service.

    The rootfs keeps PX4's parameters and logs between starts; the startup data
    and the aircraft service are refreshed every time.
    """
    build = PX4_OUT / "build" / "px4_sitl_default"
    px4_binary = build / "bin" / "px4"
    if not px4_binary.is_file():
        raise Px4Error(f"PX4 SITL is not built: {px4_binary}\nRun: python tools/px4_sitl.py build")
    service_source = _aircraft_service_source(drone_root)
    if not service_source.is_file():
        raise Px4Error(f"the Drone Core aircraft service was not found: {service_source}")
    runtime = recipe_root / "runtime" / "px4"
    data_dir = runtime / "px4-etc"
    if data_dir.exists():
        shutil.rmtree(data_dir)
    shutil.copytree(build / "etc", data_dir, symlinks=True)
    airframe = data_dir / "init.d-posix" / "airframes" / AIRFRAME
    shutil.copy2(HEXA_PX4_ROOT / "px4" / AIRFRAME, airframe)
    airframe.chmod(0o755)
    work_dir = runtime / "px4-rootfs"
    work_dir.mkdir(parents=True, exist_ok=True)
    bin_dir = runtime / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    service = bin_dir / service_source.name
    shutil.copy2(service_source, service)
    service.chmod(0o755)
    return {"px4_binary": px4_binary, "data_dir": data_dir, "work_dir": work_dir, "service": service}


def patch_launcher(path: Path, *, recipe_root: Path, drone_root: Path, marker: dict) -> Path:
    """Put PX4 SITL in front and swap the drone service for the aircraft service."""
    runtime = prepare_runtime(recipe_root, drone_root)
    launcher = json.loads(path.read_text(encoding="utf-8"))
    assets = launcher.get("assets", [])
    names = [asset.get("name") for asset in assets]
    if SERVICE_ASSET not in names:
        raise Px4Error(f"the Launcher has no {SERVICE_ASSET} asset")
    service = assets[names.index(SERVICE_ASSET)]
    args = list(service.get("args", []))
    if len(args) < 2:
        raise Px4Error(f"{SERVICE_ASSET} has no fleet config and PDU definition arguments")
    # Drone service args: <fleet config> <pdudef> [options]. The aircraft service
    # takes the same config and PDU definition after the PX4 simulator address.
    service["command"] = str(runtime["service"])
    service["args"] = ["127.0.0.1", "4560", *args]
    service["depends_on"] = [PX4_ASSET]
    origin = marker["city_world"]["origin"]
    location = json.loads(Path(marker["type_config"]).read_text(encoding="utf-8"))["simulation"]["location"]
    px4 = {
        "name": PX4_ASSET,
        "activation_timing": "before_start",
        "command": str(runtime["px4_binary"]),
        "args": ["-d", "-w", str(runtime["work_dir"]), str(runtime["data_dir"])],
        "cwd": str(runtime["work_dir"]),
        "stdout": str(recipe_root / "logs" / f"{PX4_ASSET}.out"),
        "stderr": str(recipe_root / "logs" / f"{PX4_ASSET}.err"),
        "env": {
            "set": {
                "PX4_SIM_MODEL": SIM_MODEL,
                # PX4's home is the City origin, where the magnetic field was looked up.
                "PX4_HOME_LAT": str(origin["latitude"]),
                "PX4_HOME_LON": str(origin["longitude"]),
                "PX4_HOME_ALT": str(location.get("altitude", 0.0)),
            }
        },
        "delay_sec": PX4_START_DELAY_SEC,
    }
    index = names.index(SERVICE_ASSET)
    launcher["assets"] = [*assets[:index], px4, *assets[index:]]
    path.write_text(json.dumps(launcher, indent=2) + "\n", encoding="utf-8")
    return path
