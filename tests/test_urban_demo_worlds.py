"""The demo Worlds of demos 3-1 to 3-5 made again on a Workspace
(demos/, tools/urban_demo_worlds.py): the ids the Compositions use, the
Environment Studio id scheme, the Sapporo Recipe, and the demo files."""

import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import urban_demo_worlds as demo  # noqa: E402
import urban_portable  # noqa: E402

SPEC = demo.load_spec()
DEMO_URBAN = ROOT / "demos/urban"
ABSOLUTE = re.compile(r"(/Users/|/home/|[A-Za-z]:\\\\|[A-Za-z]:/)")


class SpecTest(unittest.TestCase):
    def test_every_demo_composition_names_one_of_the_three_worlds(self):
        ids = {SPEC[name]["asset_id"] for name in ("tocho", "sapporo", "hull")}
        self.assertEqual(ids, {"tokyo-13104-multi-lat35_689-lon139_691", "sapporo-rotary-snow", "tocho-bridge-a-hull"})
        compositions = sorted((DEMO_URBAN / "compositions").glob("*.yaml"))
        self.assertTrue(compositions)
        worlds = {yaml.safe_load(path.read_text(encoding="utf-8"))["world"] for path in compositions}
        self.assertEqual(worlds, ids)
        for demo_number in range(31, 36):
            self.assertTrue(list((DEMO_URBAN / "compositions").glob(f"demo{demo_number}-*.yaml")), demo_number)
        self.assertEqual(SPEC["hull"]["source_asset_id"], SPEC["tocho"]["asset_id"])

    def test_the_demo_files_carry_no_absolute_path_and_their_references_are_there(self):
        for path in sorted(DEMO_URBAN.rglob("*.yaml")):
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(ABSOLUTE.search(text), path)
            for relative in re.findall(r"\$\{repo:hakoniwa-business-pack\}/work/urban/([^\s\"']+)", text):
                self.assertTrue((DEMO_URBAN / relative).is_file(), f"{path.name} -> {relative}")
            for relative in re.findall(r"\$\{repo:hakoniwa-urban-mobility\}/([^\s\"']+)", text):
                self.assertTrue((ROOT / relative).is_file(), f"{path.name} -> {relative}")

    def test_the_portable_bundle_takes_what_build_showcase_installs(self):
        spec = json.loads(urban_portable.DEMO_SPEC.read_text(encoding="utf-8"))
        showcase = demo.spec_file(SPEC, SPEC["showcase"]["files"])
        for pattern in spec["include"]:
            if pattern.startswith(("urban/compositions/", "urban/scenarios/", "urban/scenes/")):
                self.assertTrue(list(showcase.glob(pattern.removeprefix("urban/"))), pattern)
        for name in ("tocho", "sapporo"):
            self.assertIn(f"urban/assets/cities/{SPEC[name]['asset_id']}.asset.yaml", spec["include"])
        self.assertIn(f"recipes/environment-studio/urban/{SPEC['sapporo']['asset_id']}", spec["include"])

    def test_the_showcase_uses_only_the_tocho_and_sapporo_worlds(self):
        showcase = demo.spec_file(SPEC, SPEC["showcase"]["files"])
        worlds = {(yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("world")
                  for path in (showcase / "compositions").glob("*.yaml")}
        self.assertEqual(worlds, {SPEC["tocho"]["asset_id"], SPEC["sapporo"]["asset_id"]})
        # Every work/urban file the showcase names is in the showcase itself.
        for path in (showcase / "compositions").glob("*.yaml"):
            for ref in re.findall(r"work/urban/([\w./-]+\.yaml)", path.read_text(encoding="utf-8")):
                self.assertTrue((showcase / ref).is_file(), f"{path.name}: {ref}")

    def test_the_tocho_request_is_the_one_the_demo_was_made_with(self):
        request = demo.read_json(demo.spec_file(SPEC, SPEC["tocho"]["request"]))
        self.assertEqual(request["id"], SPEC["tocho"]["asset_id"])
        self.assertEqual(request["selection"], {"center": {"latitude": 35.689245, "longitude": 139.690808},
                                                "half_extent_m": {"north_south": 217, "east_west": 218.3}})
        self.assertEqual(request["options"], {"building_physics_level": 3,
                                              "building_collider_reduction": "convex-decompose",
                                              "terrain_uncovered_policy": "error",
                                              "terrain_bridge_carve": True, "terrain_bridge_blend": True})
        self.assertEqual(request["source"], "plateau")
        self.assertFalse(request["offline"])


class StudioIdTest(unittest.TestCase):
    def test_the_page_id_of_the_demo_areas(self):
        # Tocho: PLATEAU files of 新宿区, 渋谷区 and 中野区 (13104-2025, 13113-2025, 13114-2025).
        self.assertEqual(demo.studio_city_world_id((35.689245, 139.690808), ["13113", "13104", "13114", "13104"]),
                         SPEC["tocho"]["asset_id"])
        sapporo = demo.read_json(demo.spec_file(SPEC, SPEC["sapporo"]["city_world"]))
        center = sapporo["selection"]["center"]
        self.assertEqual(demo.studio_city_world_id((center["latitude"], center["longitude"]), ["01100"]), sapporo["id"])
        self.assertEqual(demo.studio_city_world_id((1.0, 2.0), []), "pref00-00000-lat1_000-lon2_000")

    def test_rounding_is_javascript_tofixed(self):
        self.assertEqual(demo._fixed(0.0625, 3), "0.063")      # Python's format would give 0.062
        self.assertEqual(demo._fixed(139.690808, 3), "139.691")
        self.assertEqual(demo._fixed(-0.0625, 3), "-0.063")

    def test_the_page_title(self):
        inspection = {"building_municipalities": ["新宿区", "渋谷区"], "municipalities": []}
        request = demo.read_json(demo.spec_file(SPEC, SPEC["tocho"]["request"]))
        self.assertEqual(demo.studio_title(inspection, request["selection"]), request["name"])
        square = {"half_extent_m": {"north_south": 100, "east_west": 100}}
        self.assertEqual(demo.studio_title({"municipalities": [{"city": "沼津市"}]}, square), "沼津市 付近（200 m 四方）")

    def test_a_city_with_the_same_selection_is_found_for_adopt(self):
        request = demo.read_json(demo.spec_file(SPEC, SPEC["tocho"]["request"]))
        frame = {"origin": {"latitude": 35.689245, "longitude": 139.690808},
                 "half_extent_m": {"north_south": 217.0, "east_west": 218.3}}
        self.assertTrue(demo.same_selection(frame, request))
        self.assertFalse(demo.same_selection({**frame, "half_extent_m": {"north_south": 217, "east_west": 218}},
                                             request))
        self.assertFalse(demo.same_selection(None, request))
        with tempfile.TemporaryDirectory() as directory:
            exports = Path(directory)
            for name, frame_of in (("tokyo-13104-lat35_689-lon139_691", frame),
                                   ("elsewhere", {**frame, "origin": {"latitude": 1, "longitude": 2}})):
                receipt = exports / name / "build/world/city-world-receipt.json"
                receipt.parent.mkdir(parents=True)
                receipt.write_text(json.dumps({"coordinate_frame": frame_of}), encoding="utf-8")
            with mock.patch("urban_assets.catalog", return_value={}):
                found = demo.tocho_candidates(request, exports)
            self.assertEqual([path.parts[-4] for path in found], ["tokyo-13104-lat35_689-lon139_691"])


class SapporoRecipeTest(unittest.TestCase):
    RECIPE = demo.spec_file(SPEC, SPEC["sapporo"]["recipe"])

    def test_the_recipe_is_the_demo_one_without_absolute_paths(self):
        text = self.RECIPE.read_text(encoding="utf-8")
        self.assertIsNone(ABSOLUTE.search(text))
        recipe = yaml.safe_load(text)
        self.assertEqual(recipe["schema"], "hakoniwa.environment-recipe/v1")
        self.assertEqual(recipe["geo"]["bbox_deg"], {"south": 43.066188183, "west": 141.347904942,
                                                     "north": 43.068571749, "east": 141.353513166})
        self.assertEqual(recipe["geo"]["origin"], {"lat_deg": 43.06738, "lon_deg": 141.350709})
        query = json.loads(recipe["geo"]["query"])   # provenance only: the CityGML files it was made from
        self.assertTrue(all(source["path"].startswith("01100-2020/") for source in query["sources"]))
        items = [item["item"] for item in recipe["objects"]]
        self.assertEqual(len(items), 101)
        self.assertEqual(items.count("building-footprint"), 52)
        self.assertEqual(items.count("stop-line"), 32)
        # Every file it names is in the base Recipe's assets (made again from PLATEAU).
        base = SPEC["sapporo"]["base_recipe"]["id"]
        files = demo.recipe_files(recipe)
        self.assertTrue(files)
        self.assertTrue(all(name.startswith(f"{base}.assets/") for name in files), files)
        self.assertIn(f"{base}.assets/terrain/terrain-receipt.json", files)

    def test_the_sapporo_city_world_is_the_one_sapporo_351_exact_came_from(self):
        request = demo.read_json(demo.spec_file(SPEC, SPEC["sapporo"]["city_world"]))
        self.assertEqual(request["selection"], {"center": {"latitude": 43.06738, "longitude": 141.350709},
                                                "half_extent_m": {"north_south": 91.3, "east_west": 172.7}})
        self.assertEqual(request["options"]["building_physics_level"], 3)
        self.assertEqual(request["options"]["building_collider_reduction"], "convex-decompose")
        self.assertEqual(SPEC["sapporo"]["base_recipe"]["terrain"], "city-dem")

    def test_install_points_the_catalog_at_this_workspace_and_checks_the_files(self):
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(demo, "environment_studio", return_value=Path(directory) / "repos/environment studio"):
            recipes = Path(directory) / "work/recipes/environment-studio/recipes"
            target = demo.install_recipe(self.RECIPE, recipes / "sapporo-rotary-snow.yaml")
            recipe = yaml.safe_load(target.read_text(encoding="utf-8"))
            self.assertEqual((recipes / recipe["catalog"]).resolve(),
                             (Path(directory) / "repos/environment studio/catalogs/starter/catalog.yaml").resolve())
            self.assertEqual(recipe["objects"], yaml.safe_load(self.RECIPE.read_text(encoding="utf-8"))["objects"])
            missing = demo.check_recipe_files(target)
            self.assertEqual(len(missing), len(demo.recipe_files(recipe)))
            for name in demo.recipe_files(recipe):
                (recipes / name).parent.mkdir(parents=True, exist_ok=True)
                (recipes / name).write_bytes(b"x")
            self.assertEqual(demo.check_recipe_files(target), [])
            demo.install_recipe(self.RECIPE, target)   # the same text again is fine
            target.write_text(target.read_text(encoding="utf-8") + "# edited\n", encoding="utf-8")
            with self.assertRaisesRegex(demo.DemoWorldError, "--force"):
                demo.install_recipe(self.RECIPE, target)
            demo.install_recipe(self.RECIPE, target, force=True)

    def test_compare_base_counts_changed_and_added_objects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = {"objects": [{"id": "a", "item": "x"}, {"id": "b", "item": "x"}]}
            mine = {"objects": [{"id": "a", "item": "x"}, {"id": "b", "item": "y"}, {"id": "c", "item": "z"}]}
            (root / "base.yaml").write_text(yaml.safe_dump(base), encoding="utf-8")
            (root / "mine.yaml").write_text(yaml.safe_dump(mine), encoding="utf-8")
            self.assertEqual(demo.compare_base(root / "mine.yaml", root / "base.yaml"), (1, 1))


class InstallDemosTest(unittest.TestCase):
    def test_copies_keeps_edits_and_replaces_with_force(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch("sys.stdout"):
            work = Path(directory) / "work"
            total = len([path for path in DEMO_URBAN.rglob("*") if path.is_file()])
            self.assertEqual(demo.install_demos(SPEC, work), {"copied": total, "same": 0, "kept": 0})
            edited = work / "urban/compositions/demo33-a-hull.yaml"
            edited.write_text("edited\n", encoding="utf-8")
            self.assertEqual(demo.install_demos(SPEC, work), {"copied": 0, "same": total - 1, "kept": 1})
            self.assertEqual(edited.read_text(encoding="utf-8"), "edited\n")
            self.assertEqual(demo.install_demos(SPEC, work, force=True), {"copied": 1, "same": total - 1, "kept": 0})
            self.assertEqual(edited.read_bytes(), (DEMO_URBAN / "compositions/demo33-a-hull.yaml").read_bytes())

    def test_build_needs_a_step(self):
        with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
            demo.main(["build"])
        args = demo.parser().parse_args(["build", "--all", "--sapporo-build", "b"])
        self.assertTrue(args.all)
        self.assertEqual(args.sapporo_build, Path("b"))


if __name__ == "__main__":
    unittest.main()


class GenerationCheckTest(unittest.TestCase):
    """check notices a Tocho City made with other options: its id comes from the centre only."""

    def write_city(self, root: Path, reduction: str, level: int = 3) -> Path:
        buildings = root / "components/buildings"
        buildings.mkdir(parents=True)
        (buildings / "building-physics-application.json").write_text(json.dumps(
            {"building_collider_reduction": reduction, "max_physics_level": level}), encoding="utf-8")
        receipt = root / "world/city-world-receipt.json"
        receipt.parent.mkdir(parents=True)
        receipt.write_text(json.dumps({"components": {"buildings_xml": str(buildings / "buildings.xml")}}),
                           encoding="utf-8")
        return receipt

    def test_the_request_options_match(self):
        request = demo.read_json(demo.spec_file(SPEC, SPEC["tocho"]["request"]))
        with tempfile.TemporaryDirectory() as directory:
            receipt = self.write_city(Path(directory), request["options"]["building_collider_reduction"])
            self.assertEqual(demo.generation_mismatch(receipt, request), [])

    def test_a_city_with_the_wrong_merge_option_is_named(self):
        request = demo.read_json(demo.spec_file(SPEC, SPEC["tocho"]["request"]))
        with tempfile.TemporaryDirectory() as directory:
            receipt = self.write_city(Path(directory), "coplanar-union", level=2)
            wrong = demo.generation_mismatch(receipt, request)
        self.assertEqual(len(wrong), 2)
        self.assertIn("coplanar-union", wrong[0])
        self.assertIn("convex-decompose", wrong[0])

    def test_an_unreadable_receipt_is_not_a_mismatch(self):
        request = demo.read_json(demo.spec_file(SPEC, SPEC["tocho"]["request"]))
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(demo.generation_mismatch(Path(directory) / "missing.json", request), [])
