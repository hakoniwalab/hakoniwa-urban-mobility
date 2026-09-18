#!/usr/bin/env python3
"""Run one Golf Cart loop with an independent surveillance Drone."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import threading
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps/car"))
sys.path.insert(0, str(ROOT / "apps/drone"))

from route_scenario import CarRouteController  # noqa: E402
from scenario_executor import RouteScenario, load_scenario  # noqa: E402
from urban_car import AckermannClientError, AckermannFleetClient  # noqa: E402
from city_fleet_mission import wait_for_dem_settle  # noqa: E402
from surveillance_scenario import (  # noqa: E402
    DroneSurveillanceError,
    execute_surveillance_flight,
    land_after_surveillance,
    load_surveillance_config,
)


class ComposedScenarioError(RuntimeError):
    pass


def _require_ok(operation: str, response: Any) -> None:
    if not bool(getattr(response, "ok", False)):
        raise ComposedScenarioError(
            f"{operation} failed: {getattr(response, 'message', '')}"
        )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--drone-root", type=Path, required=True)
    result.add_argument("--service-config", type=Path, required=True)
    result.add_argument("--pdu-def", type=Path, required=True)
    result.add_argument("--car-scenario", type=Path, required=True)
    result.add_argument("--drone-scenario", type=Path, required=True)
    result.add_argument("--summary-json", type=Path, required=True)
    return result


def main() -> int:
    args = parser().parse_args()
    car_scenario = load_scenario(args.car_scenario.resolve())
    if not isinstance(car_scenario, RouteScenario):
        raise ComposedScenarioError("Car demo requires a route scenario")
    drone_config = load_surveillance_config(args.drone_scenario.resolve())
    if car_scenario.vehicles[0].name != drone_config.car:
        raise ComposedScenarioError("Car and Drone scenario names do not match")

    external_rpc = args.drone_root.resolve() / "drone_api/external_rpc"
    if not external_rpc.is_dir():
        raise ComposedScenarioError(f"Drone external_rpc not found: {external_rpc}")
    sys.path.insert(0, str(external_rpc))
    from fleet_rpc import FleetRpcController
    from hakoniwa_pdu.pdu_msgs.geometry_msgs.pdu_conv_Twist import pdu_to_py_Twist

    summary: dict[str, Any] = {
        "status": "running",
        "car": drone_config.car,
        "drone": drone_config.drone,
        "phases": [],
    }
    stop_event = threading.Event()
    started = time.time()
    drone_airborne = False
    try:
        with FleetRpcController(
            [drone_config.drone],
            service_config_path=args.service_config.resolve(),
            use_async_shared=True,
        ) as drone_fleet:
            drone_fleet.prepare_basic_services()
            summary["dem_settle"] = wait_for_dem_settle(
                drone_fleet,
                drone_config.drone,
                decode_twist=pdu_to_py_Twist,
                timeout_sec=drone_config.settle_timeout_sec,
                stable_sec=drone_config.settle_stable_sec,
                maximum_speed_m_s=drone_config.settle_maximum_speed_m_s,
                maximum_height_span_m=drone_config.settle_maximum_height_span_m,
            )
            summary["phases"].append("settle_on_dem")
            print("DEMO: setting Drone ready", flush=True)
            _require_ok("set_ready", drone_fleet.set_ready(drone_config.drone))
            summary["phases"].append("set_ready")
            print(
                f"DEMO: taking off to {drone_config.takeoff_altitude_m:.1f}m",
                flush=True,
            )
            _require_ok(
                "takeoff",
                drone_fleet.takeoff(
                    drone_config.drone, drone_config.takeoff_altitude_m
                ),
            )
            drone_airborne = True
            summary["phases"].append("takeoff")

            # Initialize the polling-based Car client only after the Drone RPC
            # runtime has completed setup and takeoff. Both clients live in one
            # human-facing process, but their native initializers must not race.
            with AckermannFleetClient(
                args.pdu_def.resolve(),
                [drone_config.car],
                rate_hz=car_scenario.rate_hz,
            ) as car_fleet:
                car = CarRouteController(car_scenario)
                car.start(car_fleet)
                period = 1.0 / car_scenario.rate_hz
                next_tick = time.monotonic()
                print(
                    "DEMO: starting Golf Cart loop and Drone surveillance",
                    flush=True,
                )
                with ThreadPoolExecutor(
                    max_workers=1, thread_name_prefix="drone-surveillance"
                ) as pool:
                    surveillance = pool.submit(
                        execute_surveillance_flight,
                        drone_fleet,
                        drone_config,
                        stop_event,
                    )
                    while not car.finished or not surveillance.done():
                        snapshot = car.step(car_fleet)
                        if snapshot.stop_name is not None:
                            print(
                                f"CAR: stop {snapshot.stop_name} "
                                f"at route={snapshot.route_progress_m:.1f}m",
                                flush=True,
                            )
                        next_tick += period
                        delay = next_tick - time.monotonic()
                        if delay > 0.0:
                            time.sleep(delay)
                        else:
                            next_tick = time.monotonic()
                    summary["surveillance_waypoints_completed"] = (
                        surveillance.result()
                    )
                car.stop(car_fleet)
            summary["phases"].append("car_loop")
            summary["land_completion"] = land_after_surveillance(
                drone_fleet, drone_config
            )
            drone_airborne = False
            summary["phases"].append("land")
        summary["status"] = "success"
        print(
            "DEMO: Golf Cart loop and Drone surveillance completed",
            flush=True,
        )
        return 0
    except KeyboardInterrupt:
        stop_event.set()
        summary["status"] = "interrupted"
        print("DEMO: interrupted; stopping Car and landing Drone", flush=True)
        return 130
    except Exception as error:
        stop_event.set()
        summary["status"] = "failed"
        summary["error"] = str(error)
        raise
    finally:
        summary["drone_was_airborne_at_cleanup"] = drone_airborne
        summary["elapsed_sec"] = time.time() - started
        args.summary_json.parent.mkdir(parents=True, exist_ok=True)
        args.summary_json.write_text(
            json.dumps(summary, indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        AckermannClientError,
        ComposedScenarioError,
        DroneSurveillanceError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
