#!/usr/bin/env python3
"""Independent surveillance flight used by the Golf Cart demo."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import threading
from typing import Any


class DroneSurveillanceError(RuntimeError):
    pass


@dataclass(frozen=True)
class DroneSurveillanceConfig:
    drone: str
    car: str
    takeoff_altitude_m: float
    surveillance_altitude_m: float
    hover_before_orbit_sec: float
    orbit_radius_m: float
    orbit_waypoint_count: int
    orbit_speed_m_s: float
    tolerance_m: float
    goto_timeout_sec: float
    land_timeout_sec: float
    settle_timeout_sec: float
    settle_stable_sec: float
    settle_maximum_speed_m_s: float
    settle_maximum_height_span_m: float


@dataclass(frozen=True)
class DroneWaypoint:
    x_m: float
    y_m: float


def _finite_positive(root: dict[str, Any], key: str) -> float:
    try:
        value = float(root[key])
    except (KeyError, TypeError, ValueError) as error:
        raise DroneSurveillanceError(f"{key} must be a number") from error
    if not math.isfinite(value) or value <= 0.0:
        raise DroneSurveillanceError(f"{key} must be finite and positive")
    return value


def load_surveillance_config(path: Path) -> DroneSurveillanceConfig:
    try:
        root = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DroneSurveillanceError(
            f"failed to load Drone surveillance config: {path}"
        ) from error
    if root.get("schema_version") != 1:
        raise DroneSurveillanceError("Drone surveillance schema_version must be 1")
    drone = str(root.get("drone", "")).strip()
    car = str(root.get("car", "")).strip()
    if not drone or not car:
        raise DroneSurveillanceError("drone and car must not be empty")
    waypoint_count = root.get("orbit_waypoint_count")
    if not isinstance(waypoint_count, int) or isinstance(waypoint_count, bool):
        raise DroneSurveillanceError("orbit_waypoint_count must be an integer")
    if waypoint_count < 4:
        raise DroneSurveillanceError("orbit_waypoint_count must be at least 4")
    return DroneSurveillanceConfig(
        drone=drone,
        car=car,
        takeoff_altitude_m=_finite_positive(root, "takeoff_altitude_m"),
        surveillance_altitude_m=_finite_positive(
            root, "surveillance_altitude_m"
        ),
        hover_before_orbit_sec=_finite_positive(root, "hover_before_orbit_sec"),
        orbit_radius_m=_finite_positive(root, "orbit_radius_m"),
        orbit_waypoint_count=waypoint_count,
        orbit_speed_m_s=_finite_positive(root, "orbit_speed_m_s"),
        tolerance_m=_finite_positive(root, "tolerance_m"),
        goto_timeout_sec=_finite_positive(root, "goto_timeout_sec"),
        land_timeout_sec=_finite_positive(root, "land_timeout_sec"),
        settle_timeout_sec=_finite_positive(root, "settle_timeout_sec"),
        settle_stable_sec=_finite_positive(root, "settle_stable_sec"),
        settle_maximum_speed_m_s=_finite_positive(
            root, "settle_maximum_speed_m_s"
        ),
        settle_maximum_height_span_m=_finite_positive(
            root, "settle_maximum_height_span_m"
        ),
    )


def build_orbit_waypoints(
    center_x_m: float,
    center_y_m: float,
    config: DroneSurveillanceConfig,
) -> tuple[DroneWaypoint, ...]:
    points = []
    for index in range(config.orbit_waypoint_count):
        angle = 2.0 * math.pi * index / config.orbit_waypoint_count
        points.append(
            DroneWaypoint(
                center_x_m + config.orbit_radius_m * math.cos(angle),
                center_y_m + config.orbit_radius_m * math.sin(angle),
            )
        )
    points.append(points[0])
    points.append(DroneWaypoint(center_x_m, center_y_m))
    return tuple(points)


def _require_ok(operation: str, response: Any) -> None:
    if not bool(getattr(response, "ok", False)):
        raise DroneSurveillanceError(
            f"{operation} failed: {getattr(response, 'message', '')}"
        )


def execute_surveillance_flight(
    fleet: Any,
    config: DroneSurveillanceConfig,
    stop_event: threading.Event,
) -> int:
    """Climb, hover, fly one low-speed orbit, and return to the watch point."""
    state = fleet.get_state(config.drone)
    _require_ok("get surveillance center", state)
    position = state.current_pose.position
    center_x_m = float(position.x)
    center_y_m = float(position.y)

    print(
        f"SURVEILLANCE: climbing to {config.surveillance_altitude_m:.1f}m",
        flush=True,
    )
    _require_ok(
        "climb to surveillance altitude",
        fleet.goto(
            config.drone,
            center_x_m,
            center_y_m,
            config.surveillance_altitude_m,
            0.0,
            speed_m_s=config.orbit_speed_m_s,
            tolerance_m=config.tolerance_m,
            timeout_sec=config.goto_timeout_sec,
        ),
    )
    if stop_event.wait(config.hover_before_orbit_sec):
        return 0

    completed = 0
    waypoints = build_orbit_waypoints(center_x_m, center_y_m, config)
    for index, waypoint in enumerate(waypoints, start=1):
        if stop_event.is_set():
            break
        print(
            f"SURVEILLANCE: orbit {index}/{len(waypoints)} "
            f"target=({waypoint.x_m:.2f},{waypoint.y_m:.2f},"
            f"{config.surveillance_altitude_m:.2f})",
            flush=True,
        )
        _require_ok(
            f"surveillance waypoint {index}",
            fleet.goto(
                config.drone,
                waypoint.x_m,
                waypoint.y_m,
                config.surveillance_altitude_m,
                0.0,
                speed_m_s=config.orbit_speed_m_s,
                tolerance_m=config.tolerance_m,
                timeout_sec=config.goto_timeout_sec,
            ),
        )
        completed += 1
    print("SURVEILLANCE: holding at the watch point", flush=True)
    return completed


def land_after_surveillance(
    fleet: Any, config: DroneSurveillanceConfig
) -> str:
    try:
        response = fleet.land(config.drone, timeout_sec=config.land_timeout_sec)
        _require_ok("land", response)
        return "rpc-response"
    except RuntimeError as error:
        if "request timeout" not in str(error):
            raise
        state = fleet.get_state(config.drone)
        mode = f"{getattr(state, 'mode', '')} {getattr(state, 'message', '')}".lower()
        if not bool(getattr(state, "ok", False)) or "landed" not in mode:
            raise
        return "surface-contact-after-rpc-timeout"
