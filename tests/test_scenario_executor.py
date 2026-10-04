from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/car"))
MODULE_PATH = ROOT / "apps/car/scenario_executor.py"
SPEC = importlib.util.spec_from_file_location("scenario_executor", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
scenario_executor = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = scenario_executor
SPEC.loader.exec_module(scenario_executor)


class ScenarioExecutorTest(unittest.TestCase):
    def test_checked_in_convoy_is_staggered_and_valid(self):
        scenario = scenario_executor.load_scenario(
            ROOT / "recipes/scenarios/two-car-convoy.yaml"
        )
        self.assertEqual(tuple(scenario.schedules), ("Car-1", "Car-2"))
        self.assertEqual(scenario.schedules["Car-1"][0].at_sec, 0.0)
        self.assertEqual(scenario.schedules["Car-2"][0].at_sec, 0.8)
        self.assertAlmostEqual(scenario.duration_sec, 12.8)

    def test_active_command_returns_stop_during_gaps(self):
        scenario = scenario_executor.load_scenario(
            ROOT / "recipes/scenarios/two-car-convoy.yaml"
        )
        commands = scenario.schedules["Car-2"]
        self.assertIsNone(scenario_executor.active_command(commands, 0.5))
        self.assertEqual(
            scenario_executor.active_command(commands, 1.0).label,
            "follow-straight",
        )

    def test_overlapping_commands_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.yaml"
            path.write_text(
                """schema_version: 1
name: invalid
vehicles:
  - name: Car-1
    commands:
      - {at_sec: 0, duration_sec: 2, speed_m_s: 1}
      - {at_sec: 1, duration_sec: 2, speed_m_s: 1}
""",
                encoding="utf-8",
            )
            with self.assertRaises(scenario_executor.ScenarioError):
                scenario_executor.load_scenario(path)

    def test_checked_in_hotel_route_generates_ten_vehicle_convoy(self):
        scenario = scenario_executor.load_scenario(
            ROOT / "recipes/scenarios/hotel-convoy-loop.yaml"
        )
        self.assertIsInstance(scenario, scenario_executor.RouteScenario)
        self.assertIsNone(scenario.loop_count)
        self.assertEqual(
            tuple(vehicle.name for vehicle in scenario.vehicles),
            tuple(f"Car-{index}" for index in range(1, 11)),
        )
        self.assertEqual(scenario.vehicles[1].offset_m, -4.5)
        self.assertEqual(scenario.vehicles[-1].offset_m, -40.5)
        self.assertEqual(
            next(point for point in scenario.points if point.name == "hotel-stop").dwell_sec,
            5.0,
        )
        geometry = scenario_executor.RouteGeometry(scenario.points)
        self.assertGreater(geometry.length, 80.0)

    def test_route_cursor_holds_at_hotel_each_lap(self):
        scenario = scenario_executor.load_scenario(
            ROOT / "recipes/scenarios/hotel-convoy-loop.yaml"
        )
        geometry = scenario_executor.RouteGeometry(scenario.points)
        cursor = scenario_executor.RouteCursor(
            geometry, scenario.control.speed_m_s, loop_count=None
        )
        stop = cursor.advance(60.0)
        self.assertEqual(stop, "hotel-stop")
        self.assertAlmostEqual(cursor.hold_remaining_sec, 5.0)
        stopped_distance = cursor.distance_m
        cursor.advance(2.0)
        self.assertEqual(cursor.distance_m, stopped_distance)
        self.assertAlmostEqual(cursor.hold_remaining_sec, 3.0)

    def square_route(self):
        points = tuple(
            scenario_executor.RoutePoint(name, east, north)
            for name, east, north in (("a", 0.0, 0.0), ("b", 50.0, 0.0), ("c", 50.0, 50.0), ("d", 0.0, 50.0))
        )
        return scenario_executor.RouteGeometry(points)  # 200 m loop

    def route_control(self):
        return scenario_executor.RouteControl(
            speed_m_s=2.0, lookahead_m=2.5, position_gain=0.8, wheelbase_m=1.55,
            max_steering_rad=math.radians(32.0),
        )

    def test_an_unbounded_lag_wraps_and_stops_the_vehicle(self):
        # The failure the hold prevents: 110 m behind on a 200 m loop reads as ahead.
        geometry = self.square_route()
        control = self.route_control()
        vehicle = scenario_executor.RouteVehicle(name="Car-2", offset_m=0.0)
        cursor = scenario_executor.RouteCursor(geometry, control.speed_m_s, loop_count=None)
        cursor.advance(60.0)  # target at 120 m
        pose = scenario_executor.VehiclePose(10.0, 0.0, 0.5, 0.0)  # at 10 m
        self.assertLess(scenario_executor.route_lag_m(geometry, cursor, vehicle, pose), 0.0)
        speed, _ = scenario_executor.route_command(geometry, cursor, vehicle, pose, control)
        self.assertEqual(speed, 0.0)

    def test_a_vehicle_falling_behind_makes_the_target_wait(self):
        geometry = self.square_route()
        control = self.route_control()
        vehicle = scenario_executor.RouteVehicle(name="Car-2", offset_m=0.0)
        cursor = scenario_executor.RouteCursor(geometry, control.speed_m_s, loop_count=None)
        cursor.advance(5.0)  # target at 10 m
        pose = scenario_executor.VehiclePose(1.0, 0.0, 0.5, 0.0)  # at 1 m: 9 m behind
        max_lead = scenario_executor.max_route_lead_m(control)
        lag = scenario_executor.route_lag_m(geometry, cursor, vehicle, pose)
        self.assertAlmostEqual(lag, 9.0)
        self.assertGreater(lag, max_lead)
        self.assertEqual(scenario_executor.hold_for_slowest({"Car-2": lag, "Car-1": 0.5}, max_lead), "Car-2")
        # While held, the vehicle is still driven towards its target.
        speed, _ = scenario_executor.route_command(geometry, cursor, vehicle, pose, control)
        self.assertGreater(speed, 0.0)

    def test_a_vehicle_behind_its_target_does_not_cut_the_corner(self):
        # Held 7 m behind (it yielded), short of the corner at (50, 0): it keeps
        # straight instead of steering for a point past the corner.
        geometry = self.square_route()
        control = self.route_control()
        vehicle = scenario_executor.RouteVehicle(name="Car-2", offset_m=0.0)
        cursor = scenario_executor.RouteCursor(geometry, control.speed_m_s, loop_count=None)
        cursor.advance(26.0)  # target at 52 m, past the corner
        pose = scenario_executor.VehiclePose(45.0, 0.0, 0.5, 0.0)  # at 45 m, heading east
        speed, steering = scenario_executor.route_command(geometry, cursor, vehicle, pose, control)
        self.assertGreater(speed, 0.0)
        self.assertAlmostEqual(steering, 0.0, places=6)

    def test_a_vehicle_ahead_in_range_makes_it_yield(self):
        east = 0.0  # heading east
        poses = {
            "Car-1": scenario_executor.VehiclePose(0.0, 0.0, 0.5, east),
            "Car-2": scenario_executor.VehiclePose(3.0, 0.5, 0.5, math.pi / 2),  # 3 m ahead, crossing
        }
        self.assertEqual(scenario_executor.yield_to("Car-1", poses)[0], "Car-2")
        # Car-2 heads north; Car-1 is behind its left side, outside its cone.
        self.assertIsNone(scenario_executor.yield_to("Car-2", poses))

    def test_vehicles_out_of_range_or_behind_do_not_block(self):
        poses = {
            "Car-1": scenario_executor.VehiclePose(0.0, 0.0, 0.5, 0.0),
            "Car-2": scenario_executor.VehiclePose(-3.0, 0.0, 0.5, 0.0),   # behind
            "Car-3": scenario_executor.VehiclePose(8.0, 0.0, 0.5, 0.0),    # too far
            "Car-4": scenario_executor.VehiclePose(2.0, 3.0, 0.5, 0.0),    # outside the cone
        }
        self.assertIsNone(scenario_executor.yield_to("Car-1", poses))

    def test_head_on_vehicles_do_not_both_wait(self):
        poses = {
            "Car-1": scenario_executor.VehiclePose(0.0, 0.0, 0.5, 0.0),
            "Car-2": scenario_executor.VehiclePose(4.0, 0.0, 0.5, math.pi),
        }
        self.assertIsNone(scenario_executor.yield_to("Car-1", poses))
        self.assertEqual(scenario_executor.yield_to("Car-2", poses)[0], "Car-1")

    def test_no_hold_while_every_vehicle_keeps_up(self):
        self.assertIsNone(scenario_executor.hold_for_slowest({"Car-1": 1.0, "Car-2": -2.0}, 7.5))
        self.assertIsNone(scenario_executor.hold_for_slowest({}, 7.5))

    def test_the_road_friction_holds_in_its_legs_band(self):
        # A 50 m square; b sets 0.4 on b->c (east side, 10 m wide), d sets 0 on d->a (west side, the route's 6 m).
        points = (
            scenario_executor.RoutePoint("a", 0.0, 0.0),
            scenario_executor.RoutePoint("b", 50.0, 0.0, road_friction=0.4, road_width_m=10.0),
            scenario_executor.RoutePoint("c", 50.0, 50.0),
            scenario_executor.RoutePoint("d", 0.0, 50.0, road_friction=0.0),
        )
        tires = {"Car-2": scenario_executor.Tire(grip=1.5, model_friction=1.6)}
        friction = scenario_executor.TireFriction(scenario_executor.RouteGeometry(points), 6.0, tires)
        value = friction.value_at
        self.assertEqual(value(50.0, 25.0), 0.4)   # on the east leg
        self.assertEqual(value(54.5, 25.0), 0.4)   # 4.5 m off it: inside its 10 m band
        self.assertIsNone(value(56.0, 25.0))       # outside it: the model's
        self.assertIsNone(value(25.0, 0.0))        # the south leg sets none
        self.assertEqual(value(2.5, 25.0), 0.0)    # the west leg's 6 m band
        self.assertIsNone(value(3.5, 25.0))
        # At corner c both bands overlap: the nearer leg's.
        self.assertEqual(value(52.0, 49.0), 0.4)   # 2 m from the east leg, 2.2 m from the north one
        self.assertIsNone(value(49.0, 52.0))       # 2 m from the north leg (none), 2.2 m from the east one
        self.assertEqual(value(50.0, 54.0), 0.4)   # 4 m past c: in the east band's round end, outside the north one
        self.assertIsNone(value(30.0, 49.0))       # on the north leg, nearest to it
        sent = []

        class Fleet:
            def send_float64(self, robot, pdu, value):
                sent.append((robot, pdu, value))
                return True

        pose = lambda east, north: scenario_executor.VehiclePose(east, north, 0.5, 0.0)
        for east, north in ((10, 0), (20, 0), (50, 10), (50, 20), (60, 30), (30, 50), (0, 30)):
            friction.update(Fleet(), 0.0, "Car-1", pose(east, north))
        # Sent once per change, as the PDU the runtime's geom_friction takes;
        # out of a band the model's tire friction comes back (nothing at the
        # start: the tires have it already). Sliding off the band counts too.
        self.assertEqual([value for _, _, value in sent], [0.4, 1.6, 0.0])
        self.assertEqual({(robot, pdu) for robot, pdu, _ in sent}, {("Car-1", "tire_friction")})
        # The tires get the road's friction times the vehicle's grip.
        friction.update(Fleet(), 0.0, "Car-2", pose(50, 10))
        self.assertEqual(sent[-1], ("Car-2", "tire_friction", 0.4 * 1.5))
        quiet = scenario_executor.TireFriction(self.square_route())
        quiet.update(Fleet(), 0.0, "Car-2", pose(10, 0))
        self.assertEqual(len(sent), 4)  # no road_friction on the route: nothing is sent

    def test_the_road_width_is_the_routes_unless_a_point_sets_its_own(self):
        scenario = scenario_executor.load_scenario(ROOT / "recipes/scenarios/golf-cart-demo-loop.yaml")
        self.assertEqual(scenario.road_width_m, scenario_executor.DEFAULT_ROAD_WIDTH_M)
        with tempfile.TemporaryDirectory() as directory:
            import yaml

            data = yaml.safe_load((ROOT / "recipes/scenarios/golf-cart-demo-loop.yaml").read_text(encoding="utf-8"))
            data["route"]["road_width_m"] = 8.0
            data["route"]["points"][1]["road_width_m"] = 3.0
            path = Path(directory) / "route.yaml"
            path.write_text(yaml.safe_dump(data), encoding="utf-8")
            scenario = scenario_executor.load_scenario(path)
            self.assertEqual((scenario.road_width_m, scenario.points[1].road_width_m, scenario.points[0].road_width_m),
                             (8.0, 3.0, None))
            data["route"]["road_width_m"] = 0
            path.write_text(yaml.safe_dump(data), encoding="utf-8")
            with self.assertRaises(scenario_executor.ScenarioError):
                scenario_executor.load_scenario(path)

    def test_tires_come_from_the_builders_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tires.json"
            path.write_text(json.dumps({"Car-1": {"tire_grip": 1.3, "model_friction": 1.6}, "Cart-1": {}}),
                            encoding="utf-8")
            self.assertEqual(scenario_executor.load_tires(path), {
                "Car-1": scenario_executor.Tire(1.3, 1.6), "Cart-1": scenario_executor.Tire(1.0, 1.6)})
            path.write_text(json.dumps({"Car-1": {"tire_grip": 0}}), encoding="utf-8")
            with self.assertRaises(scenario_executor.ScenarioError):
                scenario_executor.load_tires(path)
        self.assertEqual(scenario_executor.load_tires(None), {})

    def test_route_controller_drives_west_from_initial_spawn(self):
        scenario = scenario_executor.load_scenario(
            ROOT / "recipes/scenarios/hotel-convoy-loop.yaml"
        )
        geometry = scenario_executor.RouteGeometry(scenario.points)
        cursor = scenario_executor.RouteCursor(
            geometry, scenario.control.speed_m_s, loop_count=None
        )
        pose = scenario_executor.VehiclePose(35.0, -7.0, 4.6, math.pi)
        speed, steering = scenario_executor.route_command(
            geometry, cursor, scenario.vehicles[0], pose, scenario.control
        )
        self.assertGreater(speed, 0.0)
        self.assertAlmostEqual(steering, 0.0, places=6)

    def test_shizuoka_schema_three_loads_lateral_formation(self):
        scenario = scenario_executor.load_scenario(
            ROOT / "recipes/scenarios/shizuoka-five-car-formation-loop.yaml"
        )
        self.assertIsInstance(scenario, scenario_executor.RouteScenario)
        self.assertEqual(len(scenario.vehicles), 5)
        self.assertEqual(scenario.vehicles[0].lateral_offset_m, 0.0)
        self.assertEqual(scenario.vehicles[1].lateral_offset_m, 1.8)
        self.assertEqual(scenario.vehicles[2].lateral_offset_m, -1.8)

    def test_lateral_formation_target_changes_steering(self):
        scenario = scenario_executor.load_scenario(
            ROOT / "recipes/scenarios/shizuoka-five-car-formation-loop.yaml"
        )
        geometry = scenario_executor.RouteGeometry(scenario.points)
        cursor = scenario_executor.RouteCursor(
            geometry, scenario.control.speed_m_s, loop_count=None
        )
        (east_m, north_m), yaw_rad = geometry.formation_sample(
            0.0, scenario.vehicles[1].offset_m, 0.0
        )
        pose = scenario_executor.VehiclePose(east_m, north_m, 0.0, yaw_rad)
        _speed, steering = scenario_executor.route_command(
            geometry, cursor, scenario.vehicles[1], pose, scenario.control
        )
        self.assertGreater(steering, 0.0)


if __name__ == "__main__":
    unittest.main()
