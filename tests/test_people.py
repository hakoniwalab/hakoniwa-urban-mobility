from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path
import tempfile
import unittest

import yaml
import xml.etree.ElementTree as ET
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses look their module up
    spec.loader.exec_module(module)
    return module


plant = load("people_plant", ROOT / "apps/people/people_plant.py")
sys.path.insert(0, str(ROOT / "apps/people"))
people_sim = load("people_sim", ROOT / "tools/people_sim.py")


class AnimationTest(unittest.TestCase):
    def test_walking_swings_arms_against_legs(self):
        gait = plant.Gait()
        angles = [plant.animate(gait, "auto", 1.2, 0.01) for _ in range(30)]
        swung = max(angles, key=lambda a: abs(a["hip_left_joint"]))
        self.assertGreater(abs(swung["hip_left_joint"]), math.radians(5))
        self.assertAlmostEqual(swung["hip_left_joint"], -swung["hip_right_joint"])
        self.assertAlmostEqual(swung["shoulder_left_joint"], -swung["hip_left_joint"])

    def test_standing_still_is_idle_and_straight(self):
        gait = plant.Gait()
        for _ in range(200):
            angles = plant.animate(gait, "auto", 0.0, 0.01)
        self.assertLess(abs(angles["hip_left_joint"]), 1e-9)
        self.assertLess(abs(angles["shoulder_left_joint"]), math.radians(3))

    def test_wave_raises_the_right_arm_and_sit_bends_the_hips(self):
        gait = plant.Gait()
        for _ in range(200):
            waving = plant.animate(gait, "wave", 0.0, 0.01)
        self.assertLess(waving["shoulder_right_joint"], math.radians(-120))
        gait = plant.Gait()
        for _ in range(200):
            sitting = plant.animate(gait, "sit", 0.0, 0.01)
        self.assertAlmostEqual(sitting["hip_left_joint"], math.radians(-90))
        self.assertAlmostEqual(sitting["knee_left_joint"], math.radians(90))

    def test_the_trailing_leg_bends_its_knee(self):
        gait = plant.Gait()
        for _ in range(40):
            angles = plant.animate(gait, "walk", 1.2, 0.01)
            back = "left" if angles["hip_left_joint"] > 0 else "right"
            front = "right" if back == "left" else "left"
            self.assertGreaterEqual(angles[f"knee_{back}_joint"], 0.0)
            self.assertEqual(angles[f"knee_{front}_joint"], 0.0)

    def test_unknown_animation_falls_back_to_auto(self):
        self.assertEqual(plant.animate(plant.Gait(), "dance", 0.0, 0.01),
                         plant.animate(plant.Gait(), "auto", 0.0, 0.01))


class DriveTest(unittest.TestCase):
    def test_enu_velocity_maps_to_mujoco_north_west(self):
        vx, vy, _turn = plant.drive(east=1.0, north=2.0, yaw_rate=0.0, yaw_mjcf=0.0)
        self.assertEqual((vx, vy), (2.0, -1.0))

    def test_a_walker_turns_towards_where_it_walks(self):
        # Facing north (MuJoCo yaw 0), walking east (MuJoCo -Y) turns right.
        _vx, _vy, turn = plant.drive(east=1.0, north=0.0, yaw_rate=0.5, yaw_mjcf=0.0)
        self.assertLess(turn, 0)
        _vx, _vy, turn = plant.drive(east=0.0, north=1.0, yaw_rate=0.5, yaw_mjcf=0.0)
        self.assertAlmostEqual(turn, 0.0)

    def test_standing_turns_with_the_commanded_rate(self):
        self.assertEqual(plant.drive(0.0, 0.0, 0.7, 0.0)[2], 0.7)
        self.assertEqual(plant.drive(0.0, 0.0, 9.0, 0.0)[2], plant.MAX_TURN_RAD_S)


class RecipeTest(unittest.TestCase):
    def write(self, text: str) -> Path:
        directory = Path(tempfile.mkdtemp())
        path = directory / "people.yaml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_people_need_unique_names_and_known_looks(self):
        cases = {
            "duplicate": "schema: hakoniwa.people/v1\npeople: [{name: A}, {name: A}]\n",
            "slash": "schema: hakoniwa.people/v1\npeople: [{name: A/B}]\n",
            "look": "schema: hakoniwa.people/v1\npeople: [{name: A, look: robot}]\n",
            "schema": "schema: other\npeople: [{name: A}]\n",
        }
        for case, text in cases.items():
            with self.subTest(case=case), patch.object(people_sim.urban_manifest, "work_dir", return_value=Path("/w")):
                with self.assertRaises(people_sim.PeopleError):
                    people_sim.load(self.write(text))

    def test_world_prefixes_each_person_and_places_it(self):
        if not people_sim.PERSON_BODY.is_dir():
            self.skipTest("hakoniwa-mbody-registry is not beside this repository")
        with patch.object(people_sim.urban_manifest, "work_dir", return_value=Path("/w")):
            resolved = people_sim.load(ROOT / "recipes/people/people-three.yaml")
        self.assertEqual([p["scale"] for p in resolved["people"]], [1.0, 1.0, 0.7])
        root = ET.fromstring(people_sim.world_xml(resolved))
        bodies = {body.get("name"): body.get("pos") for body in root.findall("worldbody/body")}
        self.assertEqual(bodies["Person-1/person"], "0 2 0")  # east -2 -> MuJoCo +Y (west)
        self.assertEqual(bodies["Person-2/person"], "2 -0 0")
        joints = {joint.get("name") for joint in root.iter("joint")}
        self.assertIn("Person-3/hip_left_joint", joints)
        actuated = {actuator.get("joint") for actuator in root.find("actuator")}
        self.assertIn("Person-2/slide_x_joint", actuated)
        try:
            import mujoco
        except ImportError:
            return
        model = mujoco.MjModel.from_xml_string(ET.tostring(root, encoding="unicode"))
        self.assertEqual(model.nu, 12)  # move_x, move_y, lift, turn per person

    def test_people_join_a_city_world_and_its_files_still_resolve(self):
        if not people_sim.PERSON_BODY.is_dir():
            self.skipTest("hakoniwa-mbody-registry is not beside this repository")
        build = Path(tempfile.mkdtemp()) / "job/build"
        (build / "world").mkdir(parents=True)
        (build / "components").mkdir()
        (build / "components/ground.hf").write_bytes(b"")
        mjcf = build / "world/city-world.xml"
        mjcf.write_text(
            '<mujoco model="town"><asset><hfield name="ground" file="../components/ground.hf" nrow="2" ncol="2" '
            'size="5 5 0.01 0.1"/></asset><worldbody><geom name="wall" type="box" size="0.1 2 1" pos="3 0 1"/>'
            "</worldbody></mujoco>", encoding="utf-8")
        receipt = build / "world/city-world-receipt.json"
        receipt.write_text(
            '{"mjcf": {"path": "%s"}, "glb": {"path": "%s"}, "coordinate_frame": {"half_extent_m": '
            '{"north_south": 5, "east_west": 5}, "coordinate_systems": {"mjcf": "X=North,Y=-East,Z=Up"}}}'
            % (mjcf, build / "world/city-world.glb"), encoding="utf-8")
        city = people_sim.city_world(receipt)
        self.assertEqual(city["size_m"], 10.0)
        with patch.object(people_sim.urban_manifest, "work_dir", return_value=Path("/w")):
            resolved = people_sim.load(ROOT / "recipes/people/people-one.yaml")
        root = ET.fromstring(people_sim.world_xml(resolved, city))
        self.assertEqual(root.find("asset/hfield").get("file"), str((build / "components/ground.hf").resolve()))
        self.assertEqual({geom.get("name") for geom in root.findall("worldbody/geom")}, {"wall"})
        self.assertIsNotNone(root.find("worldbody/body[@name='Person-1/person']"))
        self.assertEqual(root.find("compiler").get("angle"), "degree")


class FakeTransport:
    def __init__(self, *_args):
        self.sent = []

    def connect(self):
        pass

    def close(self):
        pass

    def send(self, robot, pdu, payload):
        self.sent.append((robot, pdu, bytes(payload)))
        return True

    def read(self, robot, pdu):
        for sent_robot, sent_pdu, payload in reversed(self.sent):
            if (sent_robot, sent_pdu) == (robot, pdu):
                return bytearray(payload)
        return None


class ClientTest(unittest.TestCase):
    def setUp(self):
        try:
            import hakoniwa_pdu  # noqa: F401
        except ImportError:
            self.skipTest("hakoniwa_pdu is not installed")
        self.client_module = load("hakoniwa_people", ROOT / "apps/people/hakoniwa_people.py")
        directory = Path(tempfile.mkdtemp())
        (directory / "people-pdudef.json").write_text("{}", encoding="utf-8")
        (directory / "people-plant.json").write_text(
            '{"people": [{"name": "A", "look": "staff"}, {"name": "B", "look": "child"}]}', encoding="utf-8")
        with patch.object(self.client_module, "HakoniwaPollingTransport", FakeTransport):
            self.people = self.client_module.PeopleClient(directory / "people-pdudef.json")
        self.transport = self.people._transport

    def test_stop_returns_to_the_auto_animation(self):
        self.people.set_animation("A", "walk")
        self.people.stop("A")
        self.assertEqual(self.people.animation("A"), "auto")
        self.people.set_animation("A", "walk")
        self.people.stop("A", reset_animation=False)
        self.assertEqual(self.people.animation("A"), "walk")

    def test_batch_velocities_and_stop_all_reach_everyone(self):
        self.people.set_velocities({"A": (1.0, 0.0), "B": (0.0, 0.5, 0.2)})
        self.assertEqual([robot for robot, pdu, _ in self.transport.sent if pdu == "cmd_vel"], ["A", "B"])
        self.people.stop_all()
        self.assertEqual(self.people.names(), ["A", "B"])
        self.assertEqual({self.people.animation(name) for name in ("A", "B")}, {"auto"})

    def test_people_tells_who_is_here(self):
        with patch.object(self.people, "poses", return_value={"A": "pose-a"}):
            found = self.people.people()
        self.assertEqual(found["A"], {"look": "staff", "pose": "pose-a", "animation": "auto", "ride": ""})
        self.assertEqual(found["B"]["look"], "child")


if __name__ == "__main__":
    unittest.main()


class CompositionTest(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(ROOT / "tools"))
        import urban_simulation
        import urban_people

        self.simulation, self.people = urban_simulation, urban_people

    def test_people_ride_the_car_route(self):
        plan = self.simulation.plan(ROOT / "recipes/compositions/plain-golf-cart-people.yaml")
        self.assertEqual(plan.route, "car")
        self.assertIn("hakoniwa-people", plan.to_json()["simulators"])

    def test_four_people_ride_the_hakoniwa_cart(self):
        import urban_assets

        cart = urban_assets.catalog()["hakoniwa-cart"]
        seats = cart.data["seats"]
        self.assertEqual(sorted(seats), ["driver", "passenger", "rear_left", "rear_right"])
        self.assertGreater(seats["driver"][1], 0)  # the driver sits on the left, behind the steering wheel
        self.assertGreater(seats["driver"][0], seats["rear_left"][0])
        plan = self.simulation.plan(ROOT / "recipes/compositions/plain-hakoniwa-cart-people.yaml")
        self.assertEqual(plan.route, "car")
        self.assertIn("hakoniwa-people", plan.to_json()["simulators"])

    def test_people_alone_are_not_a_route(self):
        directory = Path(tempfile.mkdtemp())
        path = directory / "people-only.yaml"
        path.write_text("schema: hakoniwa.composition/v1\nid: people-only\nworld: plain-ground\nvehicles:\n"
                        "  - {name: P, asset: hakoniwa-person-visitor, control: external, spawn: {east_m: 0, north_m: 0, yaw_deg: 0}}\n",
                        encoding="utf-8")
        with self.assertRaisesRegex(self.simulation.SimulationError, "people_sim"):
            self.simulation.plan(path)

    def test_external_people_start_no_control_process(self):
        import urban_controls

        composition = self.simulation.load_composition(ROOT / "recipes/compositions/plain-golf-cart-people.yaml")
        processes = urban_controls.control_processes(composition, {"ackermann-mujoco": self.simulation.car_runtime(Path("/w"))})
        self.assertEqual([process["name"] for process in processes], ["control-car-1-rc"])

    def test_a_composition_without_people_removes_the_plant(self):
        work = Path(tempfile.mkdtemp())
        path = work / "cart.yaml"
        path.write_text("schema: hakoniwa.composition/v1\nid: cart\nworld: plain-ground\nvehicles:\n"
                        "  - {name: Car-1, asset: golf-cart, control: rc, spawn: {east_m: 0, north_m: 0, yaw_deg: 0}}\n",
                        encoding="utf-8")
        composition = self.simulation.load_composition(path)
        (work / "config/people").mkdir(parents=True)
        launcher = {"assets": [{"name": "urban-car-fleet-plant"}, {"name": "people-plant"}]}
        self.people.apply(work, composition, launcher, "urban-car-fleet-plant")
        self.assertEqual([asset["name"] for asset in launcher["assets"]], ["urban-car-fleet-plant"])
        self.assertFalse((work / "config/people").exists())



class RidePlanTest(unittest.TestCase):
    """The people side of a car route scenario (apps/people/ride_plan.py)."""

    def setUp(self):
        sys.path.insert(0, str(ROOT / "apps/people"))
        sys.path.insert(0, str(ROOT / "apps/car"))
        import ride_plan

        self.ride = ride_plan
        self.scenario = yaml.safe_load((ROOT / "recipes/scenarios/hakoniwa-cart-station-pickup.yaml").read_text(encoding="utf-8"))

    def test_the_scenario_drives_the_cart_and_lists_its_riders(self):
        sys.path.insert(0, str(ROOT / "apps/car"))
        import scenario_executor

        route = scenario_executor.load_scenario(ROOT / "recipes/scenarios/hakoniwa-cart-station-pickup.yaml")
        self.assertEqual([vehicle.name for vehicle in route.vehicles], ["Cart-1"])  # the executor ignores people:
        plan = self.ride.load_plan(self.scenario)
        self.assertEqual(plan.vehicle, "Cart-1")
        self.assertEqual(sorted(plan.stops), ["destination", "station"])
        self.assertEqual([rider.seat for rider in plan.riders], ["passenger", "rear_right", "rear_left"])

    def test_a_plan_must_name_stops_of_the_route_and_free_seats(self):
        broken = {**self.scenario, "people": {**self.scenario["people"], "riders": [
            {"name": "A", "board": {"stop": "nowhere", "seat": "passenger"}}]}}
        with self.assertRaisesRegex(self.ride.PlanError, "board.stop"):
            self.ride.load_plan(broken)
        twice = {**self.scenario, "people": {**self.scenario["people"], "riders": [
            {"name": "A", "board": {"stop": "station", "seat": "passenger"}},
            {"name": "B", "board": {"stop": "station", "seat": "passenger"}}]}}
        with self.assertRaisesRegex(self.ride.PlanError, "seat"):
            self.ride.load_plan(twice)

    def test_the_far_side_door_is_reached_round_the_back(self):
        near = self.ride.boarding_path((0.0, -2.0), (0.12, -0.26))
        self.assertEqual(len(near), 1)
        far = self.ride.boarding_path((0.0, -2.0), (-1.08, 0.26))
        self.assertEqual(len(far), 3)
        self.assertTrue(all(point[0] < -2.0 for point in far[:2]))  # behind the car
        self.assertGreater(far[-1][1], 0.26)  # the door, outside the left seat

    def test_the_riders_share_one_control_process(self):
        sys.path.insert(0, str(ROOT / "tools"))
        import urban_controls
        import urban_simulation

        composition = urban_simulation.load_composition(ROOT / "recipes/compositions/plain-hakoniwa-cart-ride.yaml")
        processes = urban_controls.control_processes(composition, {
            "ackermann-mujoco": urban_simulation.car_runtime(Path("/w")),
            "hakoniwa-people": urban_simulation.people_runtime(Path("/w"))})
        names = [process["name"] for process in processes]
        self.assertEqual(len(names), 2)  # the cart's scenario executor and one ride plan for three looks
        self.assertTrue(any("ride_plan.py" in " ".join(process["args"]) for process in processes))

class RideTest(unittest.TestCase):
    def test_a_rider_goes_with_the_car_and_gets_off_beside_it(self):
        if not people_sim.PERSON_BODY.is_dir():
            self.skipTest("hakoniwa-mbody-registry is not beside this repository")
        try:
            import mujoco  # noqa: F401
        except ImportError:
            self.skipTest("mujoco is not installed")
        with patch.object(people_sim.urban_manifest, "work_dir", return_value=Path("/w")):
            resolved = people_sim.load(ROOT / "recipes/people/people-one.yaml")
        world = Path(tempfile.mkdtemp()) / "world.xml"
        world.write_text(people_sim.world_xml(resolved), encoding="utf-8")
        people = plant.PeoplePlant({"world_xml": str(world), "delta_usec": 10000, "people": resolved["people"],
                                    "vehicles": {"Car-1": {"seats": {"driver": [-0.28, 0.22, 0.58]}}}})
        person = people.people[0]
        people.vehicle_poses = {"Car-1": (5.0, 2.0, 0.45, math.pi / 2)}  # MuJoCo x north, facing west
        person["ride"] = "Car-1/driver"
        people.step()
        x, y, z = people.data.xpos[person["body"]]
        # The seat (-0.28 forward, 0.22 left) of a car facing +y lands at (5 - 0.22, 2 - 0.28).
        self.assertAlmostEqual(x, 4.78, places=3)
        self.assertAlmostEqual(y, 1.72, places=3)
        self.assertAlmostEqual(z, 0.45 + 0.58 - plant.HIP_M, places=3)
        self.assertEqual(people.model.geom_contype[person["collision"]], 0)
        self.assertEqual(person["angles"]["hip_left_joint"] < 0, True)  # sitting
        person["ride"] = ""
        people.step()
        x, y, z = people.data.xpos[person["body"]]
        self.assertAlmostEqual(y, 2.0 - 0.28, places=3)
        self.assertAlmostEqual(x, 5.0 - (0.22 + plant.GET_OFF_M), places=3)  # its left: -x when facing +y
        self.assertLess(abs(z), 0.01)
        self.assertEqual(people.model.geom_contype[person["collision"]], 1)


class ContactTest(unittest.TestCase):
    def test_a_car_hitting_a_person_is_a_vehicle_contact(self):
        if not people_sim.PERSON_BODY.is_dir():
            self.skipTest("hakoniwa-mbody-registry is not beside this repository")
        try:
            import mujoco  # noqa: F401
        except ImportError:
            self.skipTest("mujoco is not installed")
        with patch.object(people_sim.urban_manifest, "work_dir", return_value=Path("/w")):
            resolved = people_sim.load(ROOT / "recipes/people/people-one.yaml")
        vehicles = {"Car-1": {"seats": {}, "box": people_sim.vehicle_box(
            {"dimensions": {"width_m": 1.2, "length_m": 2.0}, "spawn": {"ground_clearance_m": 0.45}})}}
        directory = Path(tempfile.mkdtemp())
        world = directory / "world.xml"
        world.write_text(people_sim.world_xml(resolved, None, vehicles), encoding="utf-8")
        log = directory / "contacts.jsonl"
        people = plant.PeoplePlant({"world_xml": str(world), "delta_usec": 10000, "people": resolved["people"],
                                    "vehicles": vehicles, "contact_log": str(log)})
        # Person-1 stands at the origin; the car (MuJoCo x north) drives north into it.
        for step in range(150):
            x = -3.0 + 0.02 * step  # 2 m/s
            people.previous_vehicle_poses = {"Car-1": (x - 0.02, 0.0, 0.45, 0.0)}
            people.vehicle_poses = {"Car-1": (x, 0.0, 0.45, 0.0)}
            people.step()
        events = [event for event in people.events if event["kind"] == "vehicle"]
        self.assertTrue(events and events[0]["started"])
        self.assertEqual((events[0]["self"], events[0]["other"]), ("Person-1", "Car-1"))
        self.assertGreater(events[0]["relative_speed"], 1.5)
        self.assertIn('"other": "Car-1"', log.read_text(encoding="utf-8"))
        # The person was pushed ahead of the car, not run through.
        self.assertGreater(people.data.xpos[people.people[0]["body"]][0], 0.5)


class TerrainTest(unittest.TestCase):
    def test_people_stand_on_a_terrain_above_zero(self):
        if not people_sim.PERSON_BODY.is_dir():
            self.skipTest("hakoniwa-mbody-registry is not beside this repository")
        try:
            import mujoco  # noqa: F401
        except ImportError:
            self.skipTest("mujoco is not installed")
        directory = Path(tempfile.mkdtemp())
        city = directory / "city.xml"
        # A flat hfield whose surface is 1.2 m up (a City's DEM is rarely at z = 0).
        city.write_text('<mujoco><asset><hfield name="t" nrow="2" ncol="2" size="10 10 0.5 0.5" '
                        'elevation="0 0 0 0"/></asset><worldbody>'
                        '<geom name="terrain" type="hfield" hfield="t" pos="0 0 1.2"/></worldbody></mujoco>',
                        encoding="utf-8")
        with patch.object(people_sim.urban_manifest, "work_dir", return_value=Path("/w")):
            resolved = people_sim.load(ROOT / "recipes/people/people-one.yaml")
        world = directory / "world.xml"
        world.write_text(people_sim.world_xml(resolved, {"mjcf": city}), encoding="utf-8")
        people = plant.PeoplePlant({"world_xml": str(world), "delta_usec": 10000, "people": resolved["people"]})
        for _ in range(20):
            people.step()
        self.assertAlmostEqual(float(people.data.xpos[people.people[0]["body"]][2]), 1.2, places=2)


class DirectorTest(unittest.TestCase):
    def setUp(self):
        try:
            import yaml  # noqa: F401
            import hakoniwa_pdu  # noqa: F401
        except ImportError:
            self.skipTest("yaml / hakoniwa_pdu are not installed")
        self.director = load("festival_director", ROOT / "apps/people/festival_director.py")
        import yaml

        self.scene = yaml.safe_load((ROOT / "recipes/people/scenes/sapporo-festival.yaml").read_text(encoding="utf-8"))

    def test_seats_ring_each_table_of_each_square(self):
        seats = self.director.Director.make_seats(self.scene)
        self.assertEqual(len(seats), 2 * 2 * 4)
        for seat in seats:
            gap = math.hypot(seat["pos"][0] - seat["centre"][0], seat["pos"][1] - seat["centre"][1])
            self.assertAlmostEqual(gap, 0.72, places=6)
            out = math.hypot(seat["approach"][0] - seat["centre"][0], seat["approach"][1] - seat["centre"][1])
            self.assertAlmostEqual(out, 0.72 + 0.45, places=6)
        # The lively square (yaw 0) at (-102, -44.6): table 1 at u -0.9, y 0.35 -> x +0.9.
        centres = {seat["centre"] for seat in seats}
        self.assertIn((-101.1, -44.25), {(round(x, 2), round(y, 2)) for x, y in centres})

    def test_queue_places_match_their_patterns(self):
        director = self.director.Director.__new__(self.director.Director)
        director.scene = self.scene
        self.assertEqual(director.queue_spec("stall-n3")["along"], [1.0, 0.0])
        self.assertIsNone(director.queue_spec("aisle-west"))
