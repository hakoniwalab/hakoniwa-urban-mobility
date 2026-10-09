#!/usr/bin/env python3
"""Run the EAMS hexa in PX4 SITL with the Hakoniwa Drone aircraft service.

  build    build PX4 SITL out of tree (tools/px4_sitl_build.bash)
  prepare  make an isolated runtime under build/px4-sitl/runtime:
           PX4 startup data with the EAMS airframe, an empty PX4 rootfs,
           the vehicle config (config/drone/hexa-px4) and the aircraft service
  start    launch PX4 and the aircraft service, then start the simulation
           (foreground; Ctrl-C stops everything)

The PX4 SITL connects to the aircraft service over TCP 4560 and exposes
MAVLink on UDP 14540 (API, used by apps/drone/mavlink) and 14550 (QGroundControl).
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

import urban_manifest

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
HEXA_PX4_ROOT = ROOT / "config" / "drone" / "hexa-px4"
DEFAULT_OUT = ROOT / "build" / "px4-sitl"
DEFAULT_PX4_ROOT = Path(os.environ.get("PX4_AUTOPILOT_ROOT", WORKSPACE / "PX4-Autopilot"))
DEFAULT_DRONE_ROOT = Path(os.environ.get("HAKONIWA_DRONE_CORE_ROOT", WORKSPACE / "hakoniwa-drone-core"))
PX4_REVISION = "a1726d316a941af9524f6279eb293a713d8fdcac"
AIRFRAME = "900002_hakoniwa_eams"
SIM_MODEL = "hakoniwa_eams"
SITL_PERIOD_MSEC = 3
# PX4 home: matches the magnetic field in config/drone/hexa-px4/drone_config_0.json.
PX4_HOME = {"PX4_HOME_LAT": "47.641468", "PX4_HOME_LON": "-122.140165", "PX4_HOME_ALT": "121.321"}
PDUDEF = "config/pdudef/drone-pdudef-1.json"
MARKER = "px4-sitl-runtime.json"


def aircraft_service_source(drone_root: Path) -> Path:
    system = platform.system()
    if system == "Darwin":
        return drone_root / "mac" / "mac-main_hako_aircraft_service_px4"
    if system == "Linux":
        return drone_root / "lnx" / "linux-main_hako_aircraft_service_px4"
    raise SystemExit(f"unsupported platform: {system}")


def cmd_build(args: argparse.Namespace) -> int:
    command = ["bash", str(ROOT / "tools" / "px4_sitl_build.bash"), "--px4-dir", str(args.px4_dir), "--out", str(args.out)]
    return subprocess.call(command)


def cmd_prepare(args: argparse.Namespace) -> int:
    px4_build = args.out / "build" / "px4_sitl_default"
    px4_binary = px4_build / "bin" / "px4"
    if not px4_binary.is_file():
        raise SystemExit(f"PX4 SITL is not built: {px4_binary}\nRun: python tools/px4_sitl.py build")
    service_source = aircraft_service_source(args.drone_root)
    if not service_source.is_file():
        raise SystemExit(f"aircraft service was not found: {service_source}")
    head = subprocess.run(["git", "-C", str(args.px4_dir), "rev-parse", "HEAD"], capture_output=True, text=True)
    if head.returncode == 0 and head.stdout.strip() != PX4_REVISION:
        print(f"WARNING: PX4-Autopilot is at {head.stdout.strip()[:10]}, verified with {PX4_REVISION[:10]}", file=sys.stderr)

    runtime = args.out / "runtime"
    if runtime.exists():
        if not (runtime / MARKER).is_file():
            raise SystemExit(f"{runtime} exists but is not a PX4 SITL runtime; remove it first")
        shutil.rmtree(runtime)
    runtime.mkdir(parents=True)

    # PX4 startup data with the EAMS airframe; PX4's own build stays unchanged.
    px4_etc = runtime / "px4-etc"
    shutil.copytree(px4_build / "etc", px4_etc, symlinks=True)
    airframe = px4_etc / "init.d-posix" / "airframes" / AIRFRAME
    shutil.copy2(HEXA_PX4_ROOT / "px4" / AIRFRAME, airframe)
    airframe.chmod(0o755)
    (runtime / "px4-rootfs").mkdir()

    # Vehicle config with absolute paths (the aircraft service runs in the drone-core root).
    drone_config_dir = runtime / "drone_config"
    drone_config_dir.mkdir()
    for name in ("drone.xml", "controller-params.txt"):
        shutil.copy2(HEXA_PX4_ROOT / name, drone_config_dir / name)
    config = json.loads((HEXA_PX4_ROOT / "drone_config_0.json").read_text(encoding="utf-8"))
    config["components"]["droneDynamics"]["mujoco"]["modelPath"] = str(drone_config_dir / "drone.xml")
    config["controller"]["paramFilePath"] = str(drone_config_dir / "controller-params.txt")
    (drone_config_dir / "drone_config_0.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

    # The release binary in the drone-core checkout is not executable; use a copy.
    bin_dir = runtime / "bin"
    bin_dir.mkdir()
    service = bin_dir / service_source.name
    shutil.copy2(service_source, service)
    service.chmod(0o755)

    marker = {
        "status": "ready",
        "px4_autopilot_dir": str(args.px4_dir),
        "px4_binary": str(px4_binary),
        "px4_data_dir": str(px4_etc),
        "px4_work_dir": str(runtime / "px4-rootfs"),
        "px4_model_name": SIM_MODEL,
        "airframe": AIRFRAME,
        "drone_root": str(args.drone_root),
        "drone_config_dir": str(drone_config_dir),
        "aircraft_service": str(service),
    }
    (runtime / MARKER).write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")
    print(f"OK: prepared {runtime}")
    print("Start with: python tools/px4_sitl.py start")
    return 0


def launcher_config(marker: dict, viewer: bool) -> dict:
    service_args = ["127.0.0.1", "4560", marker["drone_config_dir"], PDUDEF]
    if viewer:
        service_args.append("--mujoco-viewer")
    service_args += ["--real-sleep-msec", str(SITL_PERIOD_MSEC)]
    return {
        "version": "0.1",
        "defaults": {
            "cwd": ".",
            "stdout": "/dev/stdout",
            "stderr": "/dev/stderr",
            "env": {
                "prepend": {
                    "lib_path": ["${HAKO_RUNTIME_PREFIX}/lib"],
                    "PATH": ["${HAKO_RUNTIME_PREFIX}/bin"],
                }
            },
            "start_grace_sec": 2,
            "delay_sec": 2,
        },
        "assets": [
            {
                "name": "px4-sitl",
                "activation_timing": "before_start",
                "command": marker["px4_binary"],
                "args": ["-d", "-w", marker["px4_work_dir"], marker["px4_data_dir"]],
                "cwd": marker["px4_work_dir"],
                "env": {"set": dict(PX4_HOME, PX4_SIM_MODEL=marker["px4_model_name"])},
                "delay_sec": 3,
            },
            {
                "name": "aircraft-service",
                "activation_timing": "before_start",
                "command": marker["aircraft_service"],
                "args": service_args,
                "cwd": marker["drone_root"],
                "depends_on": ["px4-sitl"],
                "readiness": {
                    "type": "hako_asset",
                    "asset_name": "drone",
                    "timeout_sec": 30,
                    "poll_interval_sec": 0.2,
                    "command_timeout_sec": 1,
                },
            },
        ],
    }


def cmd_start(args: argparse.Namespace) -> int:
    runtime = args.out / "runtime"
    marker_path = runtime / MARKER
    if not marker_path.is_file():
        raise SystemExit("the runtime is not prepared. Run: python tools/px4_sitl.py prepare")
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    launcher_path = runtime / "launcher.json"
    launcher_path.write_text(json.dumps(launcher_config(marker, args.viewer), indent=2) + "\n", encoding="utf-8")
    if args.foundation is None:
        args.foundation = urban_manifest.work_dir() / "foundation" / "install"
    python = args.foundation / "python" / "bin" / "python"
    if not python.is_file():
        raise SystemExit(f"Foundation Python was not found: {python}")
    env = dict(os.environ, HAKO_RUNTIME_PREFIX=str(args.foundation))
    env.pop("PYTHONPATH", None)
    print(f"[px4_sitl] runtime={runtime}")
    os.execve(
        str(python),
        [str(python), "-m", "hakoniwa_pdu.apps.launcher.hako_launcher", "--mode", "immediate", str(launcher_path)],
        env,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--px4-dir", type=Path, default=DEFAULT_PX4_ROOT, help="PX4-Autopilot checkout ($PX4_AUTOPILOT_ROOT)")
    parser.add_argument("--drone-root", type=Path, default=DEFAULT_DRONE_ROOT, help="hakoniwa-drone-core ($HAKONIWA_DRONE_CORE_ROOT)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--foundation", type=Path, help="Hakoniwa Foundation install (default: $HAKONIWA_WORK_DIR/foundation/install)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build", help="build PX4 SITL out of tree")
    sub.add_parser("prepare", help="prepare the runtime")
    start = sub.add_parser("start", help="launch PX4 SITL and the aircraft service")
    start.add_argument("--no-viewer", dest="viewer", action="store_false", help="run without the MuJoCo viewer")
    args = parser.parse_args(argv)
    args.px4_dir = args.px4_dir.resolve()
    args.drone_root = args.drone_root.resolve()
    args.out = args.out.resolve()
    return {"build": cmd_build, "prepare": cmd_prepare, "start": cmd_start}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
