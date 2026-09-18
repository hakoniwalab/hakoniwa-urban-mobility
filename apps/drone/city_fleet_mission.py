#!/usr/bin/env python3
"""Fly one Drone Core Fleet vehicle in a configured PLATEAU City World."""

from __future__ import annotations

import argparse
from collections import deque
import json
import math
from pathlib import Path
import sys
import time
from typing import Any


class MissionError(RuntimeError):
    pass


def resolve_mission(marker: dict[str, Any], mission: dict[str, Any]) -> dict[str, Any]:
    flight_plan = marker.get("flight_plan")
    if not isinstance(flight_plan, dict):
        raise MissionError("MuJoCo City marker has no flight_plan")
    spawn_points = flight_plan.get("spawn_points")
    if not isinstance(spawn_points, list) or len(spawn_points) != 1:
        raise MissionError("the first checkpoint requires exactly one spawn point")
    spawn = spawn_points[0]
    offset = mission.get("move_offset_m")
    if not isinstance(offset, list) or len(offset) != 2:
        raise MissionError("move_offset_m must contain [east, north]")
    try:
        east_m, north_m = (float(value) for value in offset)
        target_x = float(spawn["x_m"]) + east_m
        target_y = float(spawn["y_m"]) + north_m
        target_z = float(flight_plan["resolved_flight_altitude_m"])
    except (KeyError, TypeError, ValueError) as exc:
        raise MissionError(f"invalid City flight plan: {exc}") from exc
    if east_m * east_m + north_m * north_m > 9.0:
        raise MissionError(
            "move_offset_m must stay within the launch area's verified 3 m safety margin"
        )
    return {
        "drone": str(mission.get("drone", "Drone-1")),
        "takeoff_altitude_m": target_z,
        "move_offset_m": [east_m, north_m],
        "target": [target_x, target_y, target_z],
        "yaw_deg": float(mission.get("yaw_deg", 0.0)),
        "speed_m_s": float(mission.get("speed_m_s", 1.0)),
        "tolerance_m": float(mission.get("tolerance_m", 0.25)),
        "timeout_sec": float(mission.get("timeout_sec", 120.0)),
    }


def _response(operation: str, value: Any) -> None:
    ok = bool(getattr(value, "ok", False))
    message = str(getattr(value, "message", ""))
    print(f"MISSION: {operation} ok={ok} message={message}", flush=True)
    if not ok:
        raise MissionError(f"{operation} failed: {message}")


def hold_for_observation(label: str, seconds: float) -> None:
    if seconds <= 0.0:
        return
    print(f"MISSION: hold {label} for {seconds:.1f}s", flush=True)
    time.sleep(seconds)


def landed_on_city_surface(state: Any, marker: dict[str, Any]) -> bool:
    flight_plan = marker["flight_plan"]
    actual_z = float(state.current_pose.position.z)
    mode_text = f"{getattr(state, 'mode', '')} {getattr(state, 'message', '')}".lower()
    return "landed" in mode_text and actual_z < float(
        flight_plan["resolved_flight_altitude_m"]
    ) - 0.5


def settled_window(
    samples: list[tuple[float, float, float]],
    *,
    stable_sec: float,
    maximum_speed_m_s: float,
    maximum_height_span_m: float,
) -> bool:
    """Return true when recent (time, vertical position, speed) is stationary."""
    if len(samples) < 2 or samples[-1][0] - samples[0][0] < stable_sec:
        return False
    heights = [sample[1] for sample in samples]
    speeds = [sample[2] for sample in samples]
    return (
        max(speeds) <= maximum_speed_m_s
        and max(heights) - min(heights) <= maximum_height_span_m
    )


def wait_for_dem_settle(
    fleet: Any,
    drone: str,
    *,
    decode_twist: Any,
    timeout_sec: float,
    stable_sec: float,
    maximum_speed_m_s: float,
    maximum_height_span_m: float,
    sample_interval_sec: float = 0.1,
) -> dict[str, float]:
    """Wait until the unpowered Drone has fallen onto and settled on the DEM."""
    deadline = time.monotonic() + timeout_sec
    samples: deque[tuple[float, float, float]] = deque()
    while time.monotonic() < deadline:
        now = time.monotonic()
        position = decode_twist(fleet.get_raw_pdu(drone, "pos")).linear
        velocity = decode_twist(fleet.get_raw_pdu(drone, "velocity")).linear
        speed_m_s = math.sqrt(
            float(velocity.x) ** 2
            + float(velocity.y) ** 2
            + float(velocity.z) ** 2
        )
        vertical_position_m = float(position.z)
        samples.append((now, vertical_position_m, speed_m_s))
        while samples and now - samples[0][0] > stable_sec + sample_interval_sec:
            samples.popleft()
        if settled_window(
            list(samples),
            stable_sec=stable_sec,
            maximum_speed_m_s=maximum_speed_m_s,
            maximum_height_span_m=maximum_height_span_m,
        ):
            result = {
                "raw_ned_z_m": vertical_position_m,
                "speed_m_s": speed_m_s,
                "observed_stable_sec": now - samples[0][0],
            }
            print(
                "MISSION: settled on PLATEAU DEM "
                f"raw_ned_z={vertical_position_m:.3f} "
                f"speed={speed_m_s:.4f}m/s",
                flush=True,
            )
            return result
        time.sleep(sample_interval_sec)
    raise MissionError(
        "Drone did not settle on the PLATEAU DEM before the startup timeout"
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--drone-root", type=Path, required=True)
    result.add_argument("--service-config", type=Path, required=True)
    result.add_argument("--city-marker", type=Path, required=True)
    result.add_argument("--mission", type=Path, required=True)
    result.add_argument("--summary-json", type=Path, required=True)
    result.add_argument(
        "--keep-alive-after-mission",
        action="store_true",
        help="keep the Launcher asset alive until the experiment is stopped",
    )
    return result


def main() -> int:
    args = parser().parse_args()
    marker = json.loads(args.city_marker.read_text(encoding="utf-8"))
    mission_config = json.loads(args.mission.read_text(encoding="utf-8"))
    resolved = resolve_mission(marker, mission_config)

    external_rpc = args.drone_root.resolve() / "drone_api" / "external_rpc"
    if not external_rpc.is_dir():
        raise MissionError(f"Drone Core external_rpc directory not found: {external_rpc}")
    sys.path.insert(0, str(external_rpc))
    from fleet_rpc import FleetRpcController
    from hakoniwa_pdu.pdu_msgs.geometry_msgs.pdu_conv_Twist import pdu_to_py_Twist

    drone = resolved["drone"]
    started = time.time()
    summary: dict[str, Any] = {
        "status": "running",
        "drone": drone,
        "target_enu_m": resolved["target"],
        "phases": [],
    }
    try:
        with FleetRpcController(
            [drone],
            service_config_path=args.service_config.resolve(),
            use_async_shared=True,
        ) as fleet:
            # Register the complete command set up front on one shared RPC
            # runtime. This is the Drone Core recommended Fleet path and avoids
            # adding service clients between mission phases.
            fleet.prepare_basic_services()
            summary["dem_settle"] = wait_for_dem_settle(
                fleet,
                drone,
                decode_twist=pdu_to_py_Twist,
                timeout_sec=float(mission_config.get("settle_timeout_sec", 15.0)),
                stable_sec=float(mission_config.get("settle_stable_sec", 0.75)),
                maximum_speed_m_s=float(
                    mission_config.get("settle_maximum_speed_m_s", 0.08)
                ),
                maximum_height_span_m=float(
                    mission_config.get("settle_maximum_height_span_m", 0.03)
                ),
            )
            summary["phases"].append("settle_on_dem")
            hold_for_observation(
                "after DEM settle",
                float(mission_config.get("settle_hold_sec", 2.0)),
            )
            _response("set_ready", fleet.set_ready(drone))
            summary["phases"].append("set_ready")
            _response(
                "takeoff",
                fleet.takeoff(drone, resolved["takeoff_altitude_m"]),
            )
            summary["phases"].append("takeoff")
            hold_for_observation(
                "after takeoff",
                float(mission_config.get("takeoff_hold_sec", 2.0)),
            )
            state = fleet.get_state(drone)
            _response("get_state", state)
            position = state.current_pose.position
            east_m, north_m = resolved["move_offset_m"]
            target_x = float(position.x) + east_m
            target_y = float(position.y) + north_m
            target_z = float(position.z)
            summary["takeoff_pose"] = [
                float(position.x),
                float(position.y),
                float(position.z),
            ]
            summary["target_enu_m"] = [target_x, target_y, target_z]
            print(
                "MISSION: actual-pose target "
                f"from=({position.x:.3f}, {position.y:.3f}, {position.z:.3f}) "
                f"to=({target_x:.3f}, {target_y:.3f}, {target_z:.3f})",
                flush=True,
            )
            _response(
                "goto",
                fleet.goto(
                    drone,
                    target_x,
                    target_y,
                    target_z,
                    resolved["yaw_deg"],
                    speed_m_s=resolved["speed_m_s"],
                    tolerance_m=resolved["tolerance_m"],
                    timeout_sec=resolved["timeout_sec"],
                ),
            )
            summary["phases"].append("goto")
            hold_for_observation(
                "after goto",
                float(mission_config.get("goto_hold_sec", 2.0)),
            )
            try:
                _response(
                    "land",
                    fleet.land(
                        drone,
                        timeout_sec=float(mission_config.get("land_timeout_sec", 20.0)),
                    ),
                )
                summary["land_completion"] = "rpc-response"
            except RuntimeError as exc:
                if "request timeout" not in str(exc):
                    raise
                landed_state = fleet.get_state(drone)
                _response("get_state_after_land", landed_state)
                if not landed_on_city_surface(landed_state, marker):
                    raise
                landed_position = landed_state.current_pose.position
                summary["land_completion"] = "city-surface-contact-after-rpc-timeout"
                summary["land_pose"] = [
                    float(landed_position.x),
                    float(landed_position.y),
                    float(landed_position.z),
                ]
                print(
                    "MISSION: land accepted by City surface contact "
                    "(Drone Core v4 RPC completion assumes local Z=0)",
                    flush=True,
                )
            summary["phases"].append("land")
            hold_for_observation(
                "after land",
                float(mission_config.get("land_hold_sec", 3.0)),
            )
        summary["status"] = "success"
        if args.keep_alive_after_mission:
            summary["elapsed_sec"] = time.time() - started
            args.summary_json.parent.mkdir(parents=True, exist_ok=True)
            args.summary_json.write_text(
                json.dumps(summary, indent=2) + "\n", encoding="utf-8"
            )
            print(
                "MISSION: completed; keeping experiment alive until explicit stop",
                flush=True,
            )
            while True:
                time.sleep(60.0)
        return 0
    except Exception as exc:
        summary["status"] = "failed"
        summary["error"] = str(exc)
        raise
    finally:
        summary["elapsed_sec"] = time.time() - started
        args.summary_json.parent.mkdir(parents=True, exist_ok=True)
        args.summary_json.write_text(
            json.dumps(summary, indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    raise SystemExit(main())
