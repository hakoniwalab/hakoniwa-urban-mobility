import itertools
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "drone"))
sys.path.insert(0, str(ROOT / "tools"))

import drone_schedule as schedule_module  # noqa: E402

SCHEDULE = textwrap.dedent("""
    schema_version: 2
    name: route with a Drone
    vehicles: [{name: Car-1}]
    route: {closed: true, points: [{east_m: 0, north_m: 0}, {east_m: 10, north_m: 0}]}
    drones:
      - name: Drone-1
        start_delay_sec: 4
        speed_m_s: 3
        loop_count: 2
        takeoff: {rise_m: 15, hold_sec: 2}
        waypoints:
          - {name: over-bridge, east_m: 100, north_m: -5, up_m: 260, yaw_deg: 180, speed_m_s: 5, hold_sec: 10}
          - {name: north, east_m: 100, north_m: 45, rise_m: 20}
        land: true
""")


class DroneScheduleTest(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())

    def write(self, text: str) -> Path:
        path = self.directory / "scenario.yaml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_urban_enu_maps_to_drone_cores_ros_frame(self):
        self.assertEqual(schedule_module.to_ros(3.0, 4.0, 5.0), (4.0, -3.0, 5.0))  # x north, y west, z up
        self.assertAlmostEqual(schedule_module.ros_yaw(90.0), 0.0)     # Urban north is ROS 0
        self.assertAlmostEqual(schedule_module.ros_yaw(0.0), -90.0)    # Urban east
        self.assertAlmostEqual(schedule_module.ros_yaw(180.0), 90.0)   # Urban west
        self.assertAlmostEqual(schedule_module.ros_yaw(270.0), -180.0)

    def test_the_flight_runs_its_rounds_then_lands_over_the_takeoff_point(self):
        schedule = schedule_module.load_schedule(self.write(SCHEDULE), "Drone-1")
        flight = list(schedule_module.steps(schedule, (82.0, -5.0, 247.0)))
        ops = [step["op"] for step in flight]
        self.assertEqual(ops, ["wait", "set_ready", "takeoff", "wait",
                               "goto", "wait", "goto", "goto", "wait", "goto", "goto", "land"])
        self.assertEqual(flight[2]["up_m"], 262.0)                      # spawn + rise
        self.assertEqual((flight[4]["up_m"], flight[4]["yaw_deg"], flight[4]["speed_m_s"]), (260.0, 180.0, 5.0))
        self.assertEqual(flight[6]["up_m"], 267.0)                      # rise_m above the takeoff point
        self.assertAlmostEqual(flight[6]["yaw_deg"], 90.0)              # no yaw: faces where it flies (north)
        self.assertEqual(flight[6]["speed_m_s"], 3.0)                   # the schedule's speed
        self.assertEqual([step["round"] for step in flight if step["op"] == "goto"], [1, 1, 2, 2, 2])
        self.assertEqual((flight[-2]["east_m"], flight[-2]["north_m"], flight[-2]["up_m"]), (82.0, -5.0, 262.0))

    def test_it_can_land_somewhere_other_than_the_takeoff_point(self):
        text = SCHEDULE.replace("loop_count: 2", "loop_count: 1").replace(
            "land: true", "land: {east_m: 89.0, north_m: -4.0, up_m: 249.0, rise_m: 12}")
        schedule = schedule_module.load_schedule(self.write(text), "Drone-1")
        flight = list(schedule_module.steps(schedule, (50.0, -3.0, 11.0)))  # takes off on a deck
        over, land = flight[-2], flight[-1]
        self.assertEqual((over["name"], over["east_m"], over["north_m"], over["up_m"]),
                         ("over-landing-point", 89.0, -4.0, 261.0))
        self.assertEqual(land, {"op": "land", "up_m": 249.0})
        # rise_m defaults to the takeoff's.
        schedule = schedule_module.load_schedule(self.write(text.replace(", rise_m: 12", "")), "Drone-1")
        self.assertEqual(list(schedule_module.steps(schedule, (50.0, -3.0, 11.0)))[-2]["up_m"], 264.0)
        with self.assertRaisesRegex(schedule_module.ScheduleError, "takes east_m, north_m, up_m"):
            schedule_module.load_schedule(self.write(text.replace("up_m: 249.0, ", "")), "Drone-1")

    def test_the_flight_path_for_a_viewer_draws_one_round_and_the_landing(self):
        text = SCHEDULE.replace("loop_count: 2", "loop_count: 2\n    hop_m: 3").replace(
            "land: true", "land: {east_m: 89.0, north_m: -4.0, up_m: 249.0, rise_m: 12}")
        schedule = schedule_module.load_schedule(self.write(text), "Drone-1")
        line = schedule_module.flight_path(schedule, (50.0, -3.0, 11.0))
        self.assertEqual([(item["kind"], item["label"]) for item in line], [
            ("takeoff", "T"), ("takeoff", "T"), ("waypoint", "1"), ("waypoint", "2"),
            ("waypoint", "1"), ("land", "L"), ("land", "L")])
        self.assertTrue(line[0]["stand"] and line[-1]["stand"])
        self.assertTrue(line[4]["again"])  # back to the first waypoint for round 2; hops left out
        self.assertEqual((line[1]["up_m"], line[3]["up_m"], line[-2]["up_m"], line[-1]["up_m"]), (26.0, 31.0, 261.0, 249.0))

    def test_a_flight_that_never_lands_ends_its_path_back_at_the_first_waypoint(self):
        text = SCHEDULE.replace("loop_count: 2", "loop_count: forever").replace("land: true", "land: false")
        line = schedule_module.flight_path(schedule_module.load_schedule(self.write(text), "Drone-1"), (0.0, 0.0, 0.0))
        self.assertEqual([item["label"] for item in line], ["T", "T", "1", "2", "1"])
        landing_home = schedule_module.flight_path(
            schedule_module.load_schedule(self.write(SCHEDULE.replace("loop_count: 2", "loop_count: 1")), "Drone-1"),
            (82.0, -5.0, 247.0))
        self.assertEqual([item["label"] for item in landing_home][-2:], ["T", "T"])  # lands where it took off
        self.assertEqual((landing_home[-1]["east_m"], landing_home[-1]["up_m"]), (82.0, 247.0))

    def test_legs_are_flown_in_short_hops(self):
        schedule = schedule_module.load_schedule(self.write(SCHEDULE.replace("loop_count: 2", "loop_count: 2\n    hop_m: 3")), "Drone-1")
        flight = list(schedule_module.steps(schedule, (82.0, -5.0, 247.0)))
        gotos = [step for step in flight if step["op"] == "goto"]
        points = [(82.0, -5.0, 262.0)] + [(step["east_m"], step["north_m"], step["up_m"]) for step in gotos]
        import math
        self.assertTrue(all(math.dist(a, b) <= 3.0 + 1e-9 for a, b in zip(points, points[1:])))
        over_bridge = [step for step in gotos if step["name"] == "over-bridge" and step["round"] == 1]
        self.assertEqual(over_bridge[-1]["hop"], f"{len(over_bridge)}/{len(over_bridge)}")
        self.assertEqual((over_bridge[-1]["east_m"], over_bridge[-1]["up_m"]), (100.0, 260.0))

    def test_forever_never_ends_and_never_lands(self):
        text = SCHEDULE.replace("loop_count: 2", "loop_count: forever").replace("land: true", "land: false")
        schedule = schedule_module.load_schedule(self.write(text), "Drone-1")
        first = list(itertools.islice(schedule_module.steps(schedule, (0.0, 0.0, 0.0)), 200))
        self.assertEqual(len(first), 200)
        self.assertNotIn("land", [step["op"] for step in first])

    def test_bad_schedules_say_what_is_wrong(self):
        cases = {
            "loop_count: 2": ("loop_count: forever", "never lands"),
            "up_m: 260, ": ("", "needs one height"),
            "speed_m_s: 5, ": ("speed_m_s: 0, ", "must be positive"),
            "hold_sec: 10}": ("hold_sec: 10, altitude: 3}", "unknown keys"),
        }
        for old, (new, message) in cases.items():
            with self.subTest(message=message):
                with self.assertRaisesRegex(schedule_module.ScheduleError, message):
                    schedule_module.load_schedule(self.write(SCHEDULE.replace(old, new)), "Drone-1")
        with self.assertRaisesRegex(schedule_module.ScheduleError, "no single drones: entry"):
            schedule_module.load_schedule(self.write(SCHEDULE), "Drone-2")

    def test_check_prints_the_flight_without_a_simulation(self):
        path = self.write(SCHEDULE)
        result = subprocess.run([sys.executable, str(ROOT / "apps/drone/drone_schedule.py"), "--check",
                                 "--schedule", str(path), "--spawn", "82,-5,247", "--check-steps", "1000"],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"op": "land"', result.stdout)

    def test_the_viewer_configs_get_the_flight_path_of_a_schedule_drone(self):
        import json
        from types import SimpleNamespace
        from unittest import mock

        import urban_simulation

        schedule_path = self.write(SCHEDULE)
        marker = self.directory / "mujoco-city-fleet.json"
        marker.write_text(json.dumps({"flight_plan": {"runtime_spawn": {
            "frame": "ENU", "east_m": 82.0, "north_m": -5.0, "up_m": 247.0, "yaw_deg": 0.0}}}), encoding="utf-8")
        viewer = self.directory / "viewer-config.json"
        viewer.write_text(json.dumps({"ui": {}}), encoding="utf-8")
        drone = SimpleNamespace(name="Drone-1", control="schedule", params={"schedule": str(schedule_path)})
        car = SimpleNamespace(name="Car-1", control="api", params={})
        composition = self.directory / "composition.yaml"
        with mock.patch.object(urban_simulation, "load_composition", return_value=SimpleNamespace(vehicles=[car, drone])):
            urban_simulation.write_viewer_planned_paths([viewer], composition, marker)
        written = json.loads(viewer.read_text(encoding="utf-8"))
        self.assertEqual(written["ui"], {})
        self.assertEqual([path["drone"] for path in written["flightPaths"]], ["Drone-1"])
        self.assertEqual(written["flightPaths"][0]["points"][0]["east_m"], 82.0)
        # Without a schedule Drone the path is cleared.
        with mock.patch.object(urban_simulation, "load_composition", return_value=SimpleNamespace(vehicles=[car])):
            urban_simulation.write_viewer_planned_paths([viewer], composition, marker)
        self.assertNotIn("flightPaths", json.loads(viewer.read_text(encoding="utf-8")))

    def test_a_car_route_for_the_viewer_stays_on_the_road_under_a_bridge(self):
        from types import SimpleNamespace
        from unittest import mock

        import urban_simulation

        route = self.directory / "loop.yaml"
        route.write_text(textwrap.dedent("""
            schema_version: 2
            name: Loop
            rate_hz: 50
            loop_count: forever
            vehicles: [{name: Car-1, route_offset_m: 0}]
            control: {speed_m_s: 3, lookahead_m: 4, position_gain: 0.8, wheelbase_m: 1.55, max_steering_deg: 32}
            route: {closed: true, points: [{east_m: 0, north_m: 0}, {east_m: 20, north_m: 0}, {east_m: 20, north_m: 4}]}
        """), encoding="utf-8")

        class Ground:  # a road at 4.5 m with a bridge deck at 11 m over east 8..12
            def __call__(self, east, north):
                return 11.0 if 8 <= east <= 12 else 4.5

            def ground_below(self, east, north, from_up):
                return 11.0 if 8 <= east <= 12 and from_up > 11.0 else 4.5

        car = SimpleNamespace(name="Car-1", control="api", params={"scenario": str(route)},
                              asset=SimpleNamespace(category="car"))
        composition = SimpleNamespace(vehicles=[car], path=self.directory / "composition.yaml")
        with mock.patch.object(urban_simulation, "load_composition", return_value=composition):
            [path] = urban_simulation.route_paths(composition.path, Ground())
        self.assertEqual((path["route"], path["vehicles"], path["closed"]), ("Loop", ["Car-1"], True))
        under = [point for point in path["points"] if 8 <= point["east_m"] <= 12 and point["north_m"] == 0]
        self.assertTrue(under)
        self.assertEqual({point["up_m"] for point in under}, {4.8})  # the road, not the bridge
        self.assertTrue(all("road_friction" not in point for point in path["points"]))  # none set

        # A point's road friction is its leg's: here the one from (20, 0) to (20, 4).
        route.write_text(route.read_text(encoding="utf-8").replace(
            "{east_m: 20, north_m: 0}", "{east_m: 20, north_m: 0, road_friction: 0.1}"), encoding="utf-8")
        with mock.patch.object(urban_simulation, "load_composition", return_value=composition):
            [path] = urban_simulation.route_paths(composition.path, Ground())
        frictions = {(point["east_m"], point["north_m"]): point.get("road_friction") for point in path["points"]}
        self.assertEqual((frictions[(0.0, 0.0)], frictions[(10.0, 0.0)], frictions[(20.0, 2.0)]), (None, None, 0.1))
        import route_line

        # Corners at samples 0, 3, 6 of 8; points 2 and 3 set 0.4 and 0.9.
        self.assertEqual(route_line.section_values([0, 3, 6], [None, 0.4, 0.9], 8),
                         [None, None, None, 0.4, 0.4, 0.4, 0.9, 0.9])
        self.assertEqual(route_line.section_values([0, 3], [None, None], 4), [None] * 4)

    def test_the_hexa_has_a_schedule_control_on_the_rpc_service(self):
        import urban_assets
        import urban_composition

        asset = urban_assets.load_manifest(ROOT / "assets/eams-hexa.asset.yaml")
        control = asset.controls()["schedule"]
        self.assertIn("car-route-scenario", control["params"]["schedule"]["kinds"])
        self.assertEqual(urban_composition.DRONE_CONTROL_MODES["schedule"], "fleet-rpc")


if __name__ == "__main__":
    unittest.main()
