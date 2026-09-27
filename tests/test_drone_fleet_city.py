from __future__ import annotations

import importlib.util
import json
import math
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "drone_fleet_city.py"
SPEC = importlib.util.spec_from_file_location("drone_fleet_city", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
recipe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recipe)


DRONE_CORE = Path(__file__).resolve().parents[2] / "hakoniwa-drone-core"


def _drone_core_mujoco() -> Path | None:
    try:
        return recipe.find_mujoco_library(DRONE_CORE)
    except Exception:  # noqa: BLE001 - the runtime is optional for this test
        return None


class MujocoRaySceneTest(unittest.TestCase):
    @unittest.skipIf(_drone_core_mujoco() is None, "Drone Core MuJoCo runtime is not installed")
    def test_ray_query_runs_on_the_drone_core_mujoco(self) -> None:
        # MuJoCo 3.13 mj_ray takes a trailing normal[3]; calling it without
        # that argument killed the process on Windows. Run the query in a
        # child process so such a crash fails the test instead of the run.
        with tempfile.TemporaryDirectory() as directory:
            world = Path(directory) / "world.xml"
            world.write_text(
                '<mujoco><worldbody><geom type="plane" size="10 10 0.1"/>'
                '<geom type="box" pos="2 0 1.5" size="1 1 1.5"/></worldbody></mujoco>',
                encoding="utf-8",
            )
            script = (
                "import importlib.util, sys\n"
                "from pathlib import Path\n"
                f"spec = importlib.util.spec_from_file_location('m', r'{SCRIPT}')\n"
                "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
                f"with m._MujocoRayScene(Path(r'{world}'), Path(r'{_drone_core_mujoco()}')) as s:\n"
                "    print(round(s.height(2.0, 0.0), 6), round(s.height(-5.0, 0.0), 6))\n"
            )
            import subprocess
            import sys

            result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), ["3.0", "0.0"])


class FleetMujocoCityTest(unittest.TestCase):
    def test_city_position_converts_to_fleet_ned(self) -> None:
        self.assertEqual(
            recipe._city_position_to_fleet_ned(5.0, -45.0, 7.0),
            [5.0, 45.0, -7.0],
        )

    def _fake_drone_root(self, root: Path) -> Path:
        drone_root = root / "hakoniwa-drone-core"
        types = drone_root / "config" / "drone" / "fleets" / "types"
        tools = drone_root / "tools"
        types.mkdir(parents=True)
        tools.mkdir(parents=True)
        (tools / "gen_mujoco_multidrone_xml.py").write_text(
            """
def generate_xml(scene, drone, count):
    bodies = []
    for index in range(1, count + 1):
        bodies.append(drone.replace('__ID__', str(index)))
    return scene.replace('__DRONE_BODIES__', '\\n'.join(bodies))
""".strip()
            + "\n",
            encoding="utf-8",
        )
        (types / "mujoco-scene.xml.template").write_text(
            """<mujoco>
  <size nstack="1" nconmax="1"/>
  <default><default class="drone"><geom density="1"/></default></default>
  <asset/>
  <worldbody>
    <geom name="ground" type="plane" size="1 1 .1"/>
    <body name="landmark_box_1"><geom type="box" size="1 1 1"/></body>
    __DRONE_BODIES__
  </worldbody>
</mujoco>
""",
            encoding="utf-8",
        )
        (types / "mujoco-drone.xml.template").write_text(
            '<body name="d__ID___b_drone_base" childclass="drone">'
            '<freejoint/><geom type="box" size=".1 .1 .1"/></body>\n',
            encoding="utf-8",
        )
        return drone_root

    def test_drone_show_generated_type_disables_csv_logging(self) -> None:
        type_config = {
            "simulation": {
                "logging": {"mode": "csv", "existing_option": True},
            }
        }

        recipe._disable_runtime_csv_logging(type_config)

        self.assertEqual(type_config["simulation"]["logging"]["mode"], "none")
        self.assertTrue(
            type_config["simulation"]["logging"]["existing_option"]
        )

    def test_base_fleet_has_deterministic_names_and_collision_mask(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            drone_root = self._fake_drone_root(root)
            output = root / "fleet.xml"
            recipe._generate_base_fleet_xml(drone_root, 2, output)
            record = recipe._prepare_base_fleet_xml(output, 2)
            tree = ET.parse(output)
            model = tree.getroot()
            self.assertEqual(
                record["drone_body_names"],
                ["d1_b_drone_base", "d2_b_drone_base"],
            )
            self.assertEqual(record["removed_demo_landmarks"], ["landmark_box_1"])
            self.assertEqual(model.find("size").attrib, recipe.MODEL_SIZE)
            default = model.find("./default/default[@class='drone']/geom")
            self.assertEqual(default.get("contype"), "2")
            self.assertEqual(default.get("conaffinity"), "1")
            self.assertIsNone(model.find("./worldbody/body[@name='landmark_box_1']"))

    def test_invalid_drone_count_is_rejected_before_city_resolution(self) -> None:
        with self.assertRaisesRegex(recipe.FleetMujocoError, r"\[1, 200\]"):
            recipe.build_shared_model(
                drone_root=Path("missing"),
                city_world_path=Path("missing"),
                drone_count=0,
                output_dir=Path("missing"),
            )

    def test_flat_model_sets_ground_height_and_removes_landmarks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            drone_root = self._fake_drone_root(root)

            def compile_model(source: Path, output: Path, _library: Path) -> dict:
                output.write_bytes(b"flat-mjb")
                return {"output_mjb": str(output)}

            with (
                mock.patch.object(recipe, "find_mujoco_library", return_value=root / "libmujoco"),
                mock.patch.object(recipe, "compile_mujoco_xml", side_effect=compile_model),
            ):
                receipt = recipe.build_flat_shared_model(
                    drone_root=drone_root,
                    drone_count=2,
                    output_dir=root / "flat",
                    ground_height_m=5.5,
                    origin={
                        "latitude": 35.0,
                        "longitude": 138.0,
                        "altitude_offset_m": 2.0,
                    },
                )
            model = ET.parse(root / "flat" / "flat-fleet.xml").getroot()
            ground = model.find("./worldbody/geom[@name='ground']")
            self.assertEqual(ground.get("pos"), "0 0 5.5")
            self.assertEqual(ground.get("size"), "1000 1000 .01")
            self.assertIsNone(model.find("./worldbody/body[@name='landmark_box_1']"))
            self.assertEqual(receipt["environment"]["mode"], "flat")

    def test_safe_spawn_selection_rejects_building_and_keeps_separation(self) -> None:
        def terrain_height(_x: float, _y: float) -> float:
            return 5.0

        def city_height(x: float, y: float) -> float:
            # Simulate a building occupying the center launch footprint.
            return 15.0 if abs(x) < 1.0 and abs(y) < 1.0 else 5.0

        points = recipe._select_safe_spawn_points(
            drone_count=2,
            half_extent_m={"north_south": 20.0, "east_west": 20.0},
            terrain_height=terrain_height,
            city_height=city_height,
        )
        self.assertNotEqual((points[0]["x_m"], points[0]["y_m"]), (0.0, 0.0))
        separation = (
            (points[0]["x_m"] - points[1]["x_m"]) ** 2
            + (points[0]["y_m"] - points[1]["y_m"]) ** 2
        ) ** 0.5
        self.assertGreaterEqual(separation, recipe.SPAWN_DEFAULT_SEPARATION_M)

    def test_safe_spawn_height_uses_highest_surface_under_drone_footprint(self) -> None:
        def sloped_height(x: float, _y: float) -> float:
            return 5.004 if x > 0.5 else 5.0

        points = recipe._select_safe_spawn_points(
            drone_count=1,
            half_extent_m={"north_south": 20.0, "east_west": 20.0},
            terrain_height=sloped_height,
            city_height=sloped_height,
        )

        self.assertEqual((points[0]["x_m"], points[0]["y_m"]), (0.0, 0.0))
        self.assertAlmostEqual(points[0]["terrain_height_m"], 5.0)
        self.assertAlmostEqual(points[0]["surface_height_m"], 5.004)

    def test_safe_spawn_selection_searches_around_requested_center(self) -> None:
        points = recipe._select_safe_spawn_points(
            drone_count=4,
            half_extent_m={"north_south": 50.0, "east_west": 50.0},
            terrain_height=lambda _x, _y: 5.0,
            city_height=lambda _x, _y: 5.0,
            center_m=(12.0, -8.0),
        )

        self.assertAlmostEqual(
            sum(point["x_m"] for point in points) / len(points), 12.0
        )
        self.assertAlmostEqual(
            sum(point["y_m"] for point in points) / len(points), -8.0
        )
        self.assertTrue(
            all(
                math.hypot(point["x_m"] - 12.0, point["y_m"] + 8.0)
                <= 40.0
                for point in points
            )
        )

    def test_safe_spawn_selection_rejects_steep_takeoff_footprint(self) -> None:
        def steep_center(x: float, y: float) -> float:
            if abs(x) <= 0.75 and abs(y) <= 0.75 and x > 0.5:
                return 5.15
            return 5.0

        points = recipe._select_safe_spawn_points(
            drone_count=1,
            half_extent_m={"north_south": 20.0, "east_west": 20.0},
            terrain_height=steep_center,
            city_height=steep_center,
        )

        self.assertNotEqual((points[0]["x_m"], points[0]["y_m"]), (0.0, 0.0))

    def test_manual_spawn_trusts_requested_grid_without_flatness_validation(self) -> None:
        points = recipe._manual_spawn_points(
            drone_count=4,
            half_extent_m={"north_south": 100.0, "east_west": 100.0},
            terrain_height=lambda x, _y: 5.0 + x * 0.1,
            city_height=lambda x, _y: 5.0 + x * 0.1,
            spawn_spacing_m=1.0,
            center_m=(-50.0, 0.0),
        )

        self.assertEqual(len(points), 4)
        self.assertAlmostEqual(
            sum(point["x_m"] for point in points) / len(points), -50.0
        )

    def test_compact_spawn_formation_fits_64_drones_with_one_meter_spacing(self) -> None:
        points = recipe._select_safe_spawn_points(
            drone_count=64,
            half_extent_m={"north_south": 20.0, "east_west": 20.0},
            terrain_height=lambda _x, _y: 0.0,
            city_height=lambda _x, _y: 0.0,
            spawn_spacing_m=1.0,
        )
        self.assertEqual(len(points), 64)
        self.assertLessEqual(
            max(math.hypot(point["x_m"], point["y_m"]) for point in points),
            6.1,
        )
        for index, point in enumerate(points):
            for other in points[index + 1 :]:
                self.assertGreaterEqual(
                    math.hypot(
                        point["x_m"] - other["x_m"],
                        point["y_m"] - other["y_m"],
                    ),
                    1.0,
                )

    def test_formation_clearance_probes_only_formation_neighborhoods(self) -> None:
        targets = [(-1.0, 0.0), (1.0, 0.0), (0.0, 8.0)]

        points = recipe._formation_clearance_points(targets)

        self.assertIn((-1.0, 0.0), points)
        self.assertIn((1.0, 0.0), points)
        self.assertIn((0.0, 8.0), points)
        self.assertEqual(len(points), 27)

    def test_spawn_spacing_accepts_compact_positive_values(self) -> None:
        points = recipe._select_safe_spawn_points(
            drone_count=4,
            half_extent_m={"north_south": 20.0, "east_west": 20.0},
            terrain_height=lambda _x, _y: 0.0,
            city_height=lambda _x, _y: 0.0,
            spawn_spacing_m=0.5,
        )
        self.assertEqual(len(points), 4)

    def test_spawn_spacing_rejects_zero(self) -> None:
        with self.assertRaisesRegex(recipe.FleetMujocoError, "finite value"):
            recipe._select_safe_spawn_points(
                drone_count=1,
                half_extent_m={"north_south": 20.0, "east_west": 20.0},
                terrain_height=lambda _x, _y: 0.0,
                city_height=lambda _x, _y: 0.0,
                spawn_spacing_m=0.0,
            )

    def test_formation_targets_are_resolved_in_city_local_frame(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            formation = root / "formation.json"
            formation.write_text(
                json.dumps({"id": "demo", "points": [[1.0, 2.0, 0.0]]}),
                encoding="utf-8",
            )
            show_path = root / "show.json"
            show = {
                "options": {"center": [10.0, 20.0, 0.0], "scale": 2.0},
                "formation_files": [{"id": "demo", "path": "formation.json"}],
            }
            show_path.write_text(json.dumps(show), encoding="utf-8")
            self.assertEqual(
                recipe._formation_targets(show, show_path=show_path),
                [(12.0, 24.0)],
            )

    def test_formation_rotation_makes_ros_x_axis_read_eastward_on_map(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            formation = root / "formation.json"
            formation.write_text(
                json.dumps({"id": "demo", "points": [[10.0, 0.0, 3.0]]}),
                encoding="utf-8",
            )
            show_path = root / "show.json"
            show = {
                "options": {"center": [0.0, 0.0, 0.0], "scale": 1.0},
                "formation_files": [{"id": "demo", "path": "formation.json"}],
            }
            recipe._rotate_formation_files(
                show, show_path=show_path, rotation_deg=90.0
            )
            rotated = json.loads(formation.read_text(encoding="utf-8"))
            self.assertAlmostEqual(rotated["points"][0][0], 0.0, places=6)
            self.assertAlmostEqual(rotated["points"][0][1], -10.0, places=6)
            self.assertEqual(rotated["points"][0][2], 3.0)
            recipe._rotate_formation_files(
                show, show_path=show_path, rotation_deg=90.0
            )
            rerun = json.loads(formation.read_text(encoding="utf-8"))
            self.assertAlmostEqual(rerun["points"][0][0], 0.0, places=6)
            self.assertAlmostEqual(rerun["points"][0][1], -10.0, places=6)

    def test_city_show_materializes_three_picture_phases(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            formations = root / "formations"
            formations.mkdir()
            (formations / "formation-HAKONIWA.json").write_text(
                json.dumps(
                    {
                        "id": "HAKONIWA",
                        "points": [
                            [-20.0 + index * 5.0, -5.0 + index, 0.0]
                            for index in range(128)
                        ],
                    }
                ),
                encoding="utf-8",
            )
            show_path = root / "show.json"
            show = {
                "meta": {"drone_count": 128},
                "formation_files": [
                    {"id": "HAKONIWA", "path": "formations/formation-HAKONIWA.json"}
                ],
                "timeline": [
                    {
                        "formation": "HAKONIWA",
                        "duration_sec": 6.0,
                        "hold_sec": 10.0,
                    }
                ],
            }
            recipe._materialize_three_phase_city_show(
                show,
                show_path=show_path,
                drone_count=128,
            )
            written = json.loads(show_path.read_text(encoding="utf-8"))
            self.assertEqual(
                [entry["id"] for entry in written["formation_files"]],
                ["CHIIKAWA", "HACHIWARE", "USAGI"],
            )
            self.assertEqual(
                [step["formation"] for step in written["timeline"]],
                ["CHIIKAWA", "HACHIWARE", "USAGI"],
            )
            self.assertEqual(
                [step["duration_sec"] for step in written["timeline"]],
                [6.0, 6.0, 6.0],
            )
            self.assertEqual(
                [step["hold_sec"] for step in written["timeline"]],
                [10.0, 10.0, 10.0],
            )
            for name in (
                "formation-CHIIKAWA.json",
                "formation-HACHIWARE.json",
                "formation-USAGI.json",
            ):
                payload = json.loads((formations / name).read_text(encoding="utf-8"))
                self.assertEqual(len(payload["points"]), 128)
                self.assertEqual(payload["resampling"], "equal-arc-length-per-component")

    def test_audience_tilt_raises_one_side_without_lowering_the_other(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            formation = root / "formation.json"
            formation.write_text(
                json.dumps({"id": "demo", "points": [[-2.0, 0.0, 0.0], [2.0, 0.0, 0.0]]}),
                encoding="utf-8",
            )
            show_path = root / "show.json"
            show = {
                "formation_files": [{"id": "demo", "path": "formation.json"}],
            }
            recipe._rotate_formation_files(
                show,
                show_path=show_path,
                rotation_deg=0.0,
                tilt_deg=15.0,
            )
            tilted = json.loads(formation.read_text(encoding="utf-8"))
            self.assertAlmostEqual(tilted["points"][0][2], 0.0, places=6)
            self.assertGreater(tilted["points"][1][2], 1.0)
            self.assertEqual(tilted["audience_tilt_deg"], 15.0)

    def test_formation_audience_tilt_accepts_show_facing_angle_range(self) -> None:
        with self.assertRaisesRegex(
            recipe.FleetMujocoError, r"formation_tilt_deg must be in \[-85, 85\]"
        ):
            recipe.materialize_fleet_config(
                drone_root=Path("."),
                recipe_config=Path("."),
                model_receipt={},
                formation_tilt_deg=86.0,
            )

        with self.assertRaisesRegex(
            recipe.FleetMujocoError, r"formation_tilt_deg must be in \[-85, 85\]"
        ):
            recipe.materialize_fleet_config(
                drone_root=Path("."),
                recipe_config=Path("."),
                model_receipt={},
                formation_tilt_deg=-86.0,
            )

    def test_partition_ids_assign_remainder_to_final_processes(self) -> None:
        self.assertEqual(
            recipe._partition_drone_ids(10, 3),
            [[1, 2, 3], [4, 5, 6], [7, 8, 9, 10]],
        )

    def test_single_drone_city_show_uses_compatibility_point(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            scenario = Path(temporary)
            formations = scenario / "formations"
            formations.mkdir()
            word = formations / "formation-HAKONIWA.json"
            word.write_text(json.dumps({"points": [[0.0, 0.0, 0.0]]}))
            show_path = scenario / "show.json"
            show = {
                "formation_files": [
                    {"id": "HAKONIWA", "path": "formations/formation-HAKONIWA.json"}
                ],
                "timeline": [
                    {"formation": "HAKONIWA", "duration_sec": 1.0, "hold_sec": 0.0}
                ],
            }
            recipe._materialize_three_phase_city_show(
                show, show_path=show_path, drone_count=1
            )
            self.assertEqual(len(show["formation_files"]), 3)
            for entry in show["formation_files"]:
                payload = json.loads(
                    (scenario / entry["path"]).read_text(encoding="utf-8")
                )
                self.assertEqual(payload["points"], [[0.0, 0.0, 0.0]])
                self.assertEqual(payload["derived_from"], "single-drone-city-checkpoint")

    def test_base_fleet_can_keep_only_global_ids_for_one_process(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            drone_root = self._fake_drone_root(root)
            output = root / "fleet.xml"
            recipe._generate_base_fleet_xml(drone_root, 4, output)
            record = recipe._prepare_base_fleet_xml(
                output, 4, drone_ids=[3, 4]
            )
            self.assertEqual(
                record["drone_body_names"],
                ["d3_b_drone_base", "d4_b_drone_base"],
            )

    def test_city_visual_max_height_uses_glb_y_up_bounds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            components = root / "components" / "buildings"
            components.mkdir(parents=True)
            buildings_xml = components / "buildings.xml"
            buildings_xml.write_text("<mujoco/>", encoding="utf-8")
            glb_receipt = components / "buildings-glb-receipt.json"
            glb_receipt.write_text(
                json.dumps({"bounds": {"max": [50.0, 105.75, 80.0]}}),
                encoding="utf-8",
            )
            city_receipt = root / "city-world-receipt.json"
            city_receipt.write_text(
                json.dumps(
                    {"components": {"buildings_xml": str(buildings_xml)}}
                ),
                encoding="utf-8",
            )
            height, source = recipe._city_visual_max_height(city_receipt)
            self.assertEqual(height, 105.75)
            self.assertEqual(source, glb_receipt.resolve())


if __name__ == "__main__":
    unittest.main()


class CompileCacheTest(unittest.TestCase):
    def test_the_same_model_compiles_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            xml, library = root / "city-fleet.xml", root / "mujoco.dll"
            xml.write_text("<mujoco/>", encoding="utf-8")
            library.write_bytes(b"lib")
            calls = []

            def compile_xml(source, output, _library):
                calls.append(source)
                Path(output).write_bytes(b"MJB")
                return {"source_xml": str(source), "output_mjb": str(output)}

            with mock.patch.object(recipe, "MJB_CACHE_DIR", root / "cache"), \
                    mock.patch.object(recipe, "compile_mujoco_xml", side_effect=compile_xml):
                first = recipe._compile_cached(xml, root / "a.mjb", library)
                second = recipe._compile_cached(xml, root / "b.mjb", library)
                xml.write_text("<mujoco><worldbody/></mujoco>", encoding="utf-8")
                third = recipe._compile_cached(xml, root / "c.mjb", library)
            self.assertEqual(len(calls), 2)
            self.assertEqual((first["cache"]["hit"], second["cache"]["hit"], third["cache"]["hit"]), (False, True, False))
            self.assertEqual((root / "b.mjb").read_bytes(), b"MJB")
            self.assertEqual(second["output_mjb"], str(root / "b.mjb"))
