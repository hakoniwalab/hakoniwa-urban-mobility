#!/usr/bin/env python3
"""Configure and run the Golf Cart + PS4-controlled Drone Urban demo."""

from __future__ import annotations

import argparse
import copy
from dataclasses import replace
import json
from pathlib import Path
import platform
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
DEFAULT_DRONE_ROOT = WORKSPACE / "hakoniwa-drone-core"
CONFIG = ROOT / "recipes/experiments/drone-car-rc.yaml"
RECIPE_ID = "urban-drone-car-rc"

sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "apps/car"))
sys.path.insert(0, str(ROOT / "apps/drone"))

import drone_one  # noqa: E402
import multi_car  # noqa: E402


class RcDemoError(RuntimeError):
    pass


def foundation_python() -> Path:
    return multi_car.foundation_python()


def work() -> Path:
    return multi_car.resolve_config(CONFIG)["work"]


def drone_paths():
    return drone_one._paths(RECIPE_ID)


def car_scenario_path() -> Path:
    root = multi_car.load_yaml(CONFIG)
    try:
        return multi_car.resolve_path(root["scenarios"]["car"], "Car scenario")
    except (KeyError, TypeError) as error:
        raise RcDemoError("experiment has no valid Car scenario reference") from error


def configured_drone_start() -> tuple[float, float, float]:
    """Read the City launch point selected by the proven Drone recipe."""
    marker_path = drone_paths().recipe_config / "mujoco-city-fleet.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    fleet_path = Path(marker["fleet_config"])
    fleet = json.loads(fleet_path.read_text(encoding="utf-8"))
    drones = fleet.get("drones")
    if not isinstance(drones, list) or len(drones) != 1:
        raise RcDemoError("RC demo requires exactly one Drone")
    position = drones[0].get("position_meter")
    if not isinstance(position, list) or len(position) != 3:
        raise RcDemoError("Drone-1 has no valid initial position")
    return float(position[0]), float(position[1]), -float(position[2])


def use_generated_drone_body_for_mirrors(resolved: dict) -> Path:
    """Use the generated Urban Hexa model for the Car-side Mirror."""
    body = (
        drone_paths().recipe_config
        / "drone/mujoco-city-fleet/process-01/hexa-body.xml"
    )
    if not body.is_file():
        raise RcDemoError(f"generated Drone Core model is missing: {body}")
    mirrors = resolved.get("mirrors")
    if not isinstance(mirrors, list) or len(mirrors) != 1:
        raise RcDemoError("RC demo requires exactly one Drone Mirror")
    mirrors[0]["mjcf_model"] = body
    return body


def merge_launchers(
    car_launcher_path: Path, output: Path, drone_root: Path
) -> Path:
    drone_launcher = json.loads(
        (drone_paths().recipe_config / "launcher.json").read_text(encoding="utf-8")
    )
    car_launcher = json.loads(car_launcher_path.read_text(encoding="utf-8"))
    drone_service = next(
        (
            asset for asset in drone_launcher.get("assets", [])
            if asset.get("name") == "drone-service-1"
        ),
        None,
    )
    car_plant = next(
        (
            asset for asset in car_launcher.get("assets", [])
            if asset.get("name") == "urban-car-fleet-plant"
        ),
        None,
    )
    if drone_service is None or car_plant is None:
        raise RcDemoError("source Launcher is missing a required asset")

    recipe_root = output.parent.parent
    logs = recipe_root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    defaults = copy.deepcopy(drone_launcher["defaults"])
    defaults["cwd"] = str(ROOT)
    defaults["stdout"] = str(logs / "${asset}.out")
    defaults["stderr"] = str(logs / "${asset}.err")
    defaults.setdefault("env", {}).setdefault("set", {}).update({
        "HAKONIWA_CORE_ROOT": str(multi_car.foundation_install()),
        "HAKONIWA_PDU_ENDPOINT_ROOT": str(multi_car.foundation_install()),
        "PYTHONUNBUFFERED": "1",
    })

    drone_service = copy.deepcopy(drone_service)
    drone_service["args"] = [
        value for value in drone_service.get("args", [])
        if value != "--mujoco-viewer"
    ]
    unified_pdu_definition = recipe_root / "config/car/urban-car-pdudef.json"
    if len(drone_service["args"]) < 2:
        raise RcDemoError("Drone service has no PDU definition argument")
    drone_service["args"][1] = str(unified_pdu_definition)
    drone_service["readiness"] = {
        "type": "hako_asset",
        "asset_name": "drone",
        "timeout_sec": 120,
        "poll_interval_sec": 0.2,
        "command_timeout_sec": 2,
    }
    drone_service["delay_sec"] = 8

    car_plant = copy.deepcopy(car_plant)
    if "--external-conductor" not in car_plant["args"]:
        car_plant["args"].append("--external-conductor")

    rc_controller = {
        "name": "urban-drone-ps4-controller",
        "command": str(foundation_python()),
        "args": [
            str(drone_root.resolve() / "drone_api/rc/rc-custom.py"),
            str(unified_pdu_definition),
            str(
                drone_root.resolve()
                / "drone_api/rc/rc_config/ps4-control.json"
            ),
            "--name", "Drone-1",
            "--index", "0",
        ],
        "cwd": str(drone_root.resolve() / "drone_api"),
        "depends_on": ["drone-service-1"],
        "activation_timing": "after_start",
        "delay_sec": 1,
    }

    launcher = {
        "version": "0.1",
        "defaults": defaults,
        "assets": [drone_service, car_plant, rc_controller],
        "runtime": {"cleanup_mmap_on_start": True},
    }
    multi_car.write_json(output, launcher)
    return output


def build_car_asset() -> None:
    subprocess.run([
        "cmake", "-S", str(ROOT), "-B", str(ROOT / "build"),
        "-DHAKO_URBAN_ENABLE_MIRROR=ON",
        "-DHAKO_URBAN_ENABLE_VIEWER=ON",
        "-DBUILD_TESTING=ON",
    ], cwd=ROOT, check=True)
    subprocess.run(
        ["cmake", "--build", str(ROOT / "build"), "-j4"],
        cwd=ROOT,
        check=True,
    )


def configure(drone_root: Path) -> int:
    build_car_asset()
    resolved = multi_car.resolve_config(CONFIG)
    drone_recipe = replace(
        drone_one.load_urban_recipe(drone_one.DEFAULT_RECIPE),
        control_mode="ps4-rc",
        city_receipt=resolved["city_receipt"],
    )
    if drone_one.configure(
        drone_root,
        recipe=drone_recipe,
        workspace=drone_paths(),
        runtime_config_dir=resolved["work"] / "config/drone/rc",
    ) != 0:
        return 1
    use_generated_drone_body_for_mirrors(resolved)
    # Keep the safe launch point selected by drone_one.configure(). Moving the
    # physical Drone to the Car route caused the vehicle to settle on a
    # different surface and prevented the previously verified takeoff.
    start = configured_drone_start()
    if drone_one.base.doctor(
        drone_recipe.fleet_experiment,
        drone_root,
        drone_one.DEFAULT_VIEWER_ROOT,
        workspace=drone_paths(),
        launcher_writer=drone_one.urban_launcher_writer(drone_recipe),
    ) != 0:
        return 1
    if multi_car.configure(resolved) != 0:
        return 1
    launcher = merge_launchers(
        resolved["work"] / "config/launcher.json",
        resolved["work"] / "config/launcher-two-assets.json",
        drone_root,
    )
    print("Golf Cart + PS4-controlled Drone demo configured")
    print(f"Launcher : {launcher}")
    print(f"Drone    : start=({start[0]:.1f},{start[1]:.1f},{start[2]:.1f})")
    print("Viewer   : Car world only (Golf Cart + Mirror Drone)")
    print("PS4      : press Cross (button 0) once to enable RadioControl")
    print("Start    : python3 tools/drone_car_rc.py start")
    print("Car start: python3 tools/drone_car_rc.py car-start")
    print("Status   : python3 tools/drone_car_rc.py status")
    print("Stop     : python3 tools/drone_car_rc.py stop")
    return 0


def doctor(drone_root: Path) -> int:
    resolved = multi_car.resolve_config(CONFIG)
    checks = [
        (
            "Drone Core service",
            drone_one.base.resolve_drone_binary(drone_root, platform.system()),
        ),
        ("Car Mirror asset", ROOT / "build/bin/urban-car-hakoniwa-asset"),
        ("Car scenario", car_scenario_path()),
        ("PS4 RC program", drone_root / "drone_api/rc/rc-custom.py"),
        ("PS4 mapping", drone_root / "drone_api/rc/rc_config/ps4-control.json"),
        ("Combined Launcher", resolved["work"] / "config/launcher-two-assets.json"),
    ]
    failed = False
    for label, path in checks:
        ok = path.is_file()
        print(f"[{'OK' if ok else 'NG'}] {label}: {path}")
        failed = failed or not ok
    return 1 if failed else 0


def control(operation: str) -> int:
    experiment_work = work()
    launcher = experiment_work / "config/launcher-two-assets.json"
    session = experiment_work / "runtime/launcher-session.json"
    session.parent.mkdir(parents=True, exist_ok=True)
    if operation != "start" and not session.is_file():
        print(f"Simulation is stopped (no Launcher session: {session})")
        return 0
    if operation == "start":
        command = [
            str(foundation_python()), "-m",
            "hakoniwa_pdu.apps.launcher.hako_launcher",
            str(launcher), "--background", str(session),
        ]
    else:
        command = [
            str(foundation_python()), "-m",
            "hakoniwa_pdu.apps.launcher.hako_launcher_ctl",
            "status" if operation == "status" else "terminate", str(session),
        ]
    subprocess.run(command, cwd=ROOT, check=True)
    return 0


def start_car() -> int:
    """Start the one-lap Golf Cart route on explicit operator command."""
    experiment_work = work()
    session = experiment_work / "runtime/launcher-session.json"
    if not session.is_file():
        raise RcDemoError(
            "simulation is stopped; run 'python3 tools/drone_car_rc.py start' first"
        )
    try:
        session_state = json.loads(session.read_text(encoding="utf-8")).get("state")
    except (OSError, json.JSONDecodeError) as error:
        raise RcDemoError(f"cannot read Launcher session: {session}") from error
    if session_state != "RUNNING":
        raise RcDemoError(
            f"simulation is not running (state={session_state}); "
            "run 'python3 tools/drone_car_rc.py start' first"
        )
    command = [
        str(foundation_python()),
        str(ROOT / "apps/car/scenario_executor.py"),
        str(car_scenario_path()),
        "--pdu-def",
        str(experiment_work / "config/car/urban-car-pdudef.json"),
    ]
    subprocess.run(command, cwd=ROOT, check=True)
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "command",
        choices=("configure", "doctor", "start", "car-start", "status", "stop"),
    )
    result.add_argument("--drone-root", type=Path, default=DEFAULT_DRONE_ROOT)
    return result


def main() -> int:
    args = parser().parse_args()
    drone_root = args.drone_root.expanduser().resolve()
    if args.command == "configure":
        return configure(drone_root)
    if args.command == "doctor":
        return doctor(drone_root)
    if args.command == "car-start":
        return start_car()
    return control(args.command)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        OSError,
        subprocess.CalledProcessError,
        RcDemoError,
        multi_car.RecipeError,
        drone_one.base.RecipeError,
        drone_one.city.FleetMujocoError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
