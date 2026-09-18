from __future__ import annotations

from pathlib import Path
import sys
import threading
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/drone"))

from surveillance_scenario import (  # noqa: E402
    build_orbit_waypoints,
    execute_surveillance_flight,
    load_surveillance_config,
)


class FakeDroneFleet:
    def __init__(self) -> None:
        self.goto_calls = []

    def get_state(self, drone: str):
        position = types.SimpleNamespace(x=5.0, y=-45.0, z=4.4)
        pose = types.SimpleNamespace(position=position)
        return types.SimpleNamespace(ok=True, current_pose=pose, message="ready")

    def goto(self, *args, **kwargs):
        self.goto_calls.append((args, kwargs))
        return types.SimpleNamespace(ok=True, message="completed")


class DroneSurveillanceScenarioTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_surveillance_config(
            ROOT / "recipes/scenarios/drone-surveillance-golf-cart.json"
        )

    def test_config_uses_ten_meter_watch_altitude(self):
        self.assertEqual(self.config.surveillance_altitude_m, 10.0)
        self.assertLess(
            self.config.takeoff_altitude_m,
            self.config.surveillance_altitude_m,
        )

    def test_orbit_is_closed_and_returns_to_center(self):
        points = build_orbit_waypoints(5.0, -45.0, self.config)
        self.assertEqual(len(points), self.config.orbit_waypoint_count + 2)
        self.assertEqual(points[0], points[-2])
        self.assertEqual((points[-1].x_m, points[-1].y_m), (5.0, -45.0))

    def test_executor_climbs_orbits_and_returns(self):
        fleet = FakeDroneFleet()
        config = self.config.__class__(
            **{
                **self.config.__dict__,
                "hover_before_orbit_sec": 0.001,
            }
        )
        completed = execute_surveillance_flight(
            fleet, config, threading.Event()
        )
        self.assertEqual(completed, config.orbit_waypoint_count + 2)
        self.assertEqual(len(fleet.goto_calls), completed + 1)
        climb_args, climb_kwargs = fleet.goto_calls[0]
        self.assertEqual(climb_args[:4], ("Drone-1", 5.0, -45.0, 10.0))
        self.assertEqual(climb_kwargs["speed_m_s"], config.orbit_speed_m_s)

    def test_executor_honors_stop_during_initial_hover(self):
        stop = threading.Event()
        stop.set()
        fleet = FakeDroneFleet()
        self.assertEqual(execute_surveillance_flight(fleet, self.config, stop), 0)
        self.assertEqual(len(fleet.goto_calls), 1)


if __name__ == "__main__":
    unittest.main()
