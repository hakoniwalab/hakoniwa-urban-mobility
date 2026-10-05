#!/usr/bin/env python3
"""Put the real-time pacer (apps/realtime/realtime_pacer.py) into a Launcher.

Every Urban route runs one pacer asset beside the Conductor owner, so the
simulators run without their own wall-clock sleeps and world time follows
the wall clock on every OS (the Car plant's sleep-based sync and the Drone
service's per-step sleep are coarse on Windows; the Urban Drone routes had
no pacing at all once the Drone Show runner was replaced by the controls).
"""

from __future__ import annotations

from pathlib import Path
import sys
import re


ROOT = Path(__file__).resolve().parents[1]
PACER = ROOT / "apps/realtime/realtime_pacer.py"
PACER_CONFIG = ROOT / "config/realtime/pacer-asset.json"
PACER_ASSET = "urban-realtime-pacer"
# apps/realtime/realtime_pacer.py reports "[pacer] wall=<s>s sim=<s>s rtf=<r> idle=<%>"
# (idle, the headroom, is absent in older logs).
PACER_REPORT = re.compile(r"\[pacer\] wall=([0-9.]+)s sim=([0-9.]+)s rtf=([0-9.]+)(?: idle=([0-9.]+)%)?")


def latest_rtf(log: Path) -> dict | None:
    """The last real-time report in a pacer log, or None before the first one."""
    try:
        with Path(log).open("rb") as handle:
            handle.seek(0, 2)
            handle.seek(max(0, handle.tell() - 16384))
            tail = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    reports = PACER_REPORT.findall(tail)
    if not reports:
        return None
    wall, sim, rtf, idle = reports[-1]
    return {
        "wall_sec": float(wall), "sim_sec": float(sim), "rtf": float(rtf),
        "idle_percent": float(idle) if idle else None,
        "updated": Path(log).stat().st_mtime,
    }
# Pacers other tools write; the Urban pacer replaces them.
LEGACY_PACERS = {"fpv-realtime-pacer"}
DELTA_MSEC = 10
# The max_delay of each Conductor owner (asset name -> ms).
CONDUCTOR_MAX_DELAY_MSEC = {
    # The Car plant's own Conductor (--conductor-max-delay-msec, tools/multi_car.py):
    # world time stays within 20 ms of the slowest asset. It must be at least
    # every asset's own step: the pacer's 10 ms and the WebBridge's 20 ms.
    "urban-car-fleet-plant": 20,
    "drone-service-1": 20,          # Drone Core built-in Conductor
    "fpv-drone-service": 20,
}
# Windows (#85): the Car route runs on an even 40 ms cadence there: the
# Conductor's max_delay, the pacer's step and the WebBridge's step are all
# 40 ms, so the WebBridge sends at 25 Hz with less jitter than 10/20 ms steps
# give under Windows sleep granularity. Other OSes and the Drone routes keep
# the values above.
WINDOWS_CAR_STEP_MSEC = 40
CAR_PLANT_ASSET = "urban-car-fleet-plant"


def _windows_car(conductor: str) -> bool:
    return sys.platform == "win32" and conductor == CAR_PLANT_ASSET


def conductor_max_delay_msec(conductor: str) -> int:
    if _windows_car(conductor):
        return WINDOWS_CAR_STEP_MSEC
    return CONDUCTOR_MAX_DELAY_MSEC[conductor]


def web_bridge_step_usec(conductor: str) -> int:
    """The WebBridge step (--delta-time-step-usec) under this Conductor owner."""
    return (WINDOWS_CAR_STEP_MSEC if _windows_car(conductor) else 20) * 1000


# Drone services sleep this long per step unless told otherwise.
DRONE_SLEEP_ARG = "--real-sleep-msec"


class RealtimeError(RuntimeError):
    pass


def pacer_asset(python: str, conductor: str) -> dict:
    """Return the pacer Launcher asset for a Conductor owner."""
    if conductor not in CONDUCTOR_MAX_DELAY_MSEC:
        raise RealtimeError(f"unknown Conductor owner for the pacer: {conductor}")
    max_delay = conductor_max_delay_msec(conductor)
    # The pacer's step must not exceed the Conductor's max_delay (deadlock-freedom).
    delta = WINDOWS_CAR_STEP_MSEC if _windows_car(conductor) else min(DELTA_MSEC, max_delay)
    return {
        "name": PACER_ASSET,
        # The pacer must register before hako-cmd start so the Conductor
        # includes it in every world-time advance decision.
        "activation_timing": "before_start",
        "command": python,
        "args": [
            "-u", str(PACER), str(PACER_CONFIG),
            "--delta-msec", str(delta), "--max-delay-msec", str(max_delay),
            # Windows: wake on the delta boundaries so world time (and the
            # WebBridge cadence) advances evenly (#85). Other OSes unchanged.
            *(["--sleep-to-deadline"] if sys.platform == "win32" else []),
        ],
        "cwd": str(ROOT),
        "depends_on": [conductor],
        "delay_sec": 1,
    }


def _set_arg(args: list, flag: str, value: str) -> list:
    if flag in args:
        index = args.index(flag)
        return [*args[:index + 1], value, *args[index + 2:]]
    return [*args, flag, value]


WEB_BRIDGE_EXECUTABLE = "hakoniwa-pdu-web-bridge"
WEB_BRIDGE_NO_SLEEP_ARG = "--disable-real-sleep"


def apply_pacer(launcher: dict, pacer: dict, *, drone_services: tuple[str, ...] = ()) -> dict:
    """Insert the pacer right after its Conductor owner, in place.

    Any other pacer is removed, and the named Drone services stop sleeping
    per step (--real-sleep-msec 0), since the pacer now paces them. WebBridge
    assets stop sleeping too: by default a WebBridge sleeps its 20 ms step in
    wall time on top of its work, so its asset time falls behind wall time and
    the Conductor holds the whole simulation to about 0.83x real time.
    """
    conductor = pacer["depends_on"][0]
    assets = [
        asset for asset in launcher.get("assets", [])
        if asset.get("name") not in LEGACY_PACERS | {PACER_ASSET}
    ]
    names = [asset.get("name") for asset in assets]
    if conductor not in names:
        raise RealtimeError(f"the Launcher has no Conductor owner {conductor} for the pacer")
    for asset in assets:
        if asset.get("name") in drone_services:
            asset["args"] = _set_arg(list(asset.get("args", [])), DRONE_SLEEP_ARG, "0")
        is_web_bridge = Path(str(asset.get("command", ""))).stem == WEB_BRIDGE_EXECUTABLE
        if is_web_bridge and WEB_BRIDGE_NO_SLEEP_ARG not in asset.get("args", []):
            asset["args"] = [*asset.get("args", []), WEB_BRIDGE_NO_SLEEP_ARG]
    position = names.index(conductor) + 1
    launcher["assets"] = assets[:position] + [pacer] + assets[position:]
    return launcher
