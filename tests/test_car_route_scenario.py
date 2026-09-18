from __future__ import annotations

import math
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/car"))

from route_scenario import CarRouteController  # noqa: E402
from scenario_executor import RouteScenario, load_scenario  # noqa: E402
from urban_car import VehiclePose  # noqa: E402


class FakeCarFleet:
    def __init__(self) -> None:
        self.time_sec = 0.0
        self.commands: list[tuple[str, float, float]] = []
        self.stop_calls = 0
        self.pose = VehiclePose(
            45.0,
            7.5,
            4.6,
            math.atan2(-9.5, -5.0),
        )

    def stop(self, repeat: int = 3) -> None:
        self.stop_calls += 1

    def simulation_time_sec(self) -> float:
        return self.time_sec

    def vehicle_poses(self):
        return {"Car-1": self.pose}

    def send(self, robot: str, speed: float, steering: float) -> None:
        self.commands.append((robot, speed, steering))


class CarRouteScenarioTest(unittest.TestCase):
    def setUp(self) -> None:
        scenario = load_scenario(
            ROOT / "recipes/scenarios/golf-cart-demo-loop.yaml"
        )
        self.assertIsInstance(scenario, RouteScenario)
        self.scenario = scenario

    def test_checked_in_demo_is_one_car_and_one_lap(self):
        self.assertEqual(self.scenario.loop_count, 1)
        self.assertEqual(
            tuple(vehicle.name for vehicle in self.scenario.vehicles), ("Car-1",)
        )
        self.assertGreater(CarRouteController(self.scenario).geometry.length, 80.0)

    def test_step_sends_closed_loop_command_from_simulation_time(self):
        fleet = FakeCarFleet()
        controller = CarRouteController(self.scenario)
        controller.start(fleet)
        fleet.time_sec = 1.0

        snapshot = controller.step(fleet)

        self.assertAlmostEqual(snapshot.route_progress_m, 1.0)
        self.assertEqual(fleet.commands[-1][0], "Car-1")
        self.assertGreater(fleet.commands[-1][1], 0.0)

    def test_controller_rejects_unstarted_step(self):
        with self.assertRaisesRegex(Exception, "not been started"):
            CarRouteController(self.scenario).step(FakeCarFleet())


if __name__ == "__main__":
    unittest.main()
