from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
