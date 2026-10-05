"""The Windows portable package of Urban Studio (issue #82): the repository
profile Business Pack reads, the paths the demo data carries, the portable
mode that skips Git / pip / CMake, the packaged plant, the ports, and the
child processes without console windows. None of it needs Windows."""

from argparse import Namespace
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
sys.path.insert(0, str(ROOT / "tools"))

import multi_car  # noqa: E402
import urban_city_authoring  # noqa: E402
import urban_manifest  # noqa: E402
import urban_mobility  # noqa: E402
import urban_portable  # noqa: E402
import urban_simulation  # noqa: E402
import urban_studio  # noqa: E402

PROFILE = ROOT / "portable/windows-profile.json"
PORTABLE_ON = {"HAKONIWA_PORTABLE_WORKSPACE": "1"}


def portable_off():
    environ = {key: value for key, value in os.environ.items() if key != "HAKONIWA_PORTABLE_WORKSPACE"}
    return mock.patch.dict(os.environ, environ, clear=True)


class ProfileTest(unittest.TestCase):
    def test_business_pack_accepts_the_profile(self):
        tools = WORKSPACE / "hakoniwa-business-pack/tools"
        if not (tools / "portable_package_profiles.py").is_file():
            self.skipTest("hakoniwa-business-pack is not checked out next to this repository")
        sys.path.insert(0, str(tools))
        try:
            import portable_package_profiles
        finally:
            sys.path.remove(str(tools))
        profile = portable_package_profiles.load_repository_profile(PROFILE, WORKSPACE)
        self.assertEqual(profile.kind, "repository")
        self.assertEqual(profile.id, "urban-studio")
        self.assertEqual(profile.requirements, ())
        tool = profile.repository_tool
        self.assertEqual(tool.repository, ROOT.name)
        self.assertEqual(tool.entrypoint_name, "urban-studio")
        self.assertTrue((ROOT / tool.tool).is_file())
        self.assertTrue((ROOT / tool.readme).is_file())
        # prepare unpacks the demo data there in staging; the ZIP must not carry it.
        self.assertEqual(set(tool.staging_cleanup),
                         {"hakoniwa-business-pack/work/urban", "hakoniwa-business-pack/work/recipes"})

    def test_the_owner_carries_what_collect_gathers_and_what_the_studio_serves(self):
        profile = json.loads(PROFILE.read_text(encoding="utf-8"))
        owner = next(item for item in profile["repositories"] if item["name"] == ROOT.name)
        # The packager drops every build/ folder that is not listed.
        self.assertIn("build/portable-runtime", owner["include_paths"])
        self.assertEqual(urban_portable.RUNTIME, ROOT / "build/portable-runtime")
        for relative in owner["include_paths"]:
            if relative != "build/portable-runtime":
                self.assertTrue((ROOT / relative).exists(), relative)
        for relative in ("tools", "apps", "web", "assets", "worlds", "urban.manifest.yaml", "portable"):
            self.assertIn(relative, owner["include_paths"])
        names = {item["name"] for item in profile["repositories"]}
        for needed in ("hakoniwa-environment-studio", "hakoniwa-envsim", "hakoniwa-drone-core",
                       "hakoniwa-threejs-drone", "hakoniwa-map-viewer", "hakoniwa-mbody-registry",
                       "hakoniwa-pdu-python", "hakoniwa-pdu-registry", "hakoniwa-mujoco-robots"):
            self.assertIn(needed, names)
        drone_core = next(item for item in profile["repositories"] if item["name"] == "hakoniwa-drone-core")
        self.assertIn("vendor/mujoco", drone_core["include_paths"])
        self.assertIn("win", drone_core["include_paths"])

    def test_every_body_the_assets_use_is_carried(self):
        profile = json.loads(PROFILE.read_text(encoding="utf-8"))
        mbody = next(item for item in profile["repositories"] if item["name"] == "hakoniwa-mbody-registry")
        carried = {path for path in mbody["include_paths"] if path.startswith("bodies/")}
        used = set()
        for manifest in (ROOT / "assets").glob("*.asset.yaml"):
            used.update("bodies/" + name for name in re.findall(r"bodies/([A-Za-z0-9_]+)",
                                                                 manifest.read_text(encoding="utf-8")))
        self.assertTrue(used, "no Asset names an mbody body")
        self.assertLessEqual(used, carried)


class PythonPathsTest(unittest.TestCase):
    """The embeddable Python's ._pth replaces sys.path: no script folder, no
    PYTHONPATH. Every module the Studio, the routes, and Environment Studio
    import by flat name must come from python_paths."""

    MODULES = (
        "urban_manifest", "urban_portable", "urban_studio", "urban_simulation", "urban_mobility",
        "multi_car", "urban_composer", "drone_one", "drone_fleet", "urban_city_authoring", "urban_assets",
        "workspace_http_server", "scenario_executor", "ps5_ackermann_sender", "drone_schedule",
        "people_plant", "ride_plan", "festival_director", "realtime_pacer", "env_studio", "env_cityworld",
        "hakoniwa_pdu.apps.launcher.hako_launcher", "hakoniwa_pdu.apps.launcher.hako_launcher_ctl",
        "rc_utils.rc_utils", "fpv_drone_generator", "portable_workspace_runtime", "workspace",
    )
    SCRIPT = """
import importlib, json, sys
workspace, paths, modules = sys.argv[1], json.loads(sys.argv[2]), json.loads(sys.argv[3])
# Only the interpreter's own folders stay (stdlib, site-packages), as with ._pth.
sys.path[:] = [p for p in sys.path if not p.startswith(workspace) or "site-packages" in p]
sys.path[:0] = paths
failures = {}
for name in modules:
    try:
        importlib.import_module(name)
    except ModuleNotFoundError as exc:
        failures[name] = ["missing", exc.name]
    except BaseException as exc:
        failures[name] = ["error", f"{type(exc).__name__}: {exc}"]
print(json.dumps(failures))
"""

    @staticmethod
    def _defined_in_workspace(name: str) -> list[Path]:
        top = name.split(".")[0]
        found = []
        for repository in WORKSPACE.glob("hakoniwa-*"):
            for depth in ("*", "*/*", "*/*/*"):
                for candidate in repository.glob(f"{depth}/{top}.py"):
                    found.append(candidate)
                for candidate in repository.glob(f"{depth}/{top}/__init__.py"):
                    found.append(candidate)
        return [path for path in found if "tests" not in path.parts and "work" not in path.parts]

    def _failures(self, paths: list[str], modules) -> dict:
        result = subprocess.run(
            [sys.executable, "-I", "-c", self.SCRIPT, str(WORKSPACE), json.dumps(paths), json.dumps(list(modules))],
            capture_output=True, text=True, check=False, cwd=tempfile.gettempdir(), timeout=300,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_without_the_tools_folder_the_studio_does_not_import(self):
        # What happens in the embeddable Python when python_paths lacks a folder.
        profile = json.loads(PROFILE.read_text(encoding="utf-8"))
        paths = [str(WORKSPACE / item) for item in profile["python_paths"] if item != f"{ROOT.name}/tools"]
        self.assertEqual(self._failures(paths, ["urban_studio"]), {"urban_studio": ["missing", "urban_studio"]})

    def test_modules_resolve_from_python_paths_alone(self):
        profile = json.loads(PROFILE.read_text(encoding="utf-8"))
        failures = self._failures([str(WORKSPACE / item) for item in profile["python_paths"]], self.MODULES)
        problems = []
        for module, (kind, detail) in failures.items():
            if kind == "missing" and not self._defined_in_workspace(detail or ""):
                continue  # a package this machine has not installed, not a path the profile lacks
            if kind == "missing" and not self._defined_in_workspace(module):
                continue  # that repository is not checked out here
            problems.append(f"{module}: {kind} {detail}")
        self.assertEqual(problems, [])

    def test_the_paths_the_issue_names_are_there(self):
        paths = json.loads(PROFILE.read_text(encoding="utf-8"))["python_paths"]
        for needed in ("hakoniwa-urban-mobility/tools", "hakoniwa-urban-mobility/apps/car",
                       "hakoniwa-environment-studio/tools", "hakoniwa-envsim/tools",
                       "hakoniwa-envsim/src/city_pipeline", "hakoniwa-fpv-drone/src",
                       "hakoniwa-business-pack/tools", "hakoniwa-pdu-python/src",
                       "hakoniwa-business-pack/work/foundation/install/share/hakoniwa/python"):
            self.assertIn(needed, paths)
        for apps in sorted((ROOT / "apps").iterdir()):
            if apps.is_dir() and any(apps.glob("*.py")):
                self.assertIn(f"{ROOT.name}/apps/{apps.name}", paths)


class TokenTest(unittest.TestCase):
    def test_a_posix_source_round_trips_into_a_folder_with_spaces_japanese_and_ampersand(self):
        source = "/Users/dev/project/fpv-drone"
        work = source + "/hakoniwa-business-pack/work"
        roots = [(work, urban_portable.WORK_TOKEN), (source, urban_portable.ROOT_TOKEN)]
        replacements = {urban_portable.WORK_TOKEN: "C:/Users/Taro Yamada/箱庭 R&D/pkg/hakoniwa-business-pack/work",
                        urban_portable.ROOT_TOKEN: "C:/Users/Taro Yamada/箱庭 R&D/pkg"}
        receipt = {"mjcf": {"path": work + "/urban/x/build/world/city-world.xml"},
                   "viewer": source + "/hakoniwa-threejs-drone/index.html"}
        text = urban_portable.tokenize_text(json.dumps(receipt), roots)
        self.assertNotIn("/Users/dev", text)
        restored = json.loads(urban_portable.detokenize_text(text, ".json", replacements))
        self.assertEqual(restored["mjcf"]["path"],
                         "C:/Users/Taro Yamada/箱庭 R&D/pkg/hakoniwa-business-pack/work/urban/x/build/world/city-world.xml")
        self.assertEqual(restored["viewer"], "C:/Users/Taro Yamada/箱庭 R&D/pkg/hakoniwa-threejs-drone/index.html")
        xml = f'<mujoco><asset><hfield file="{work}/t/terrain.hf"/></asset></mujoco>'
        xml = urban_portable.detokenize_text(urban_portable.tokenize_text(xml, roots), ".xml", replacements)
        import xml.etree.ElementTree as ET

        self.assertEqual(ET.fromstring(xml).find("asset/hfield").get("file"),
                         "C:/Users/Taro Yamada/箱庭 R&D/pkg/hakoniwa-business-pack/work/t/terrain.hf")

    def test_every_windows_spelling_is_replaced_and_only_at_a_path_boundary(self):
        source = r"C:\project\fpv-drone"
        roots = [(source, urban_portable.ROOT_TOKEN)]
        token = urban_portable.ROOT_TOKEN
        self.assertEqual(urban_portable.tokenize_text(r"C:\project\fpv-drone\a.xml", roots), token + r"\a.xml")
        self.assertEqual(urban_portable.tokenize_text("c:/Project/fpv-drone/a.xml", roots), token + "/a.xml")
        escaped = json.dumps({"p": r"C:\project\fpv-drone\a.xml"})
        self.assertEqual(json.loads(urban_portable.tokenize_text(escaped, roots))["p"], token + r"\a.xml")
        self.assertEqual(urban_portable.tokenize_text("C:/project/fpv-drone2/a", roots), "C:/project/fpv-drone2/a")

    def test_a_yaml_path_folded_at_its_spaces_is_still_found(self):
        source = "/tmp/My Folder With Spaces/pkg"
        receipt = source + "/hakoniwa-business-pack/work/urban/studio-cities/a-very-long-city-id/build/world/receipt.json"
        folded = yaml.safe_dump({"receipt": receipt, "version": 1})  # PyYAML folds at width 80
        self.assertIn("\n", folded.strip())
        tokenized = urban_portable.tokenize_file_text(folded, ".yaml", [(source, urban_portable.ROOT_TOKEN)])
        self.assertEqual(yaml.safe_load(tokenized)["receipt"],
                         urban_portable.ROOT_TOKEN + receipt[len(source):])
        restored = urban_portable.detokenize_text(tokenized, ".yaml", {urban_portable.ROOT_TOKEN: "D:/x y"})
        self.assertEqual(yaml.safe_load(restored)["receipt"], "D:/x y" + receipt[len(source):])
        self.assertEqual(len(restored.strip().splitlines()), 2)  # not folded again

    def test_references_are_found_absolute_and_through_the_repo_placeholder(self):
        work = Path("/w/hakoniwa-business-pack/work")
        text = json.dumps({"a": f"{work}/urban/a.yaml", "b": "${repo:hakoniwa-business-pack}/work/urban/b.yaml",
                           "c": f"{work}/../escape.yaml"})
        self.assertEqual(urban_portable.referenced_work_files(text, work), {"urban/a.yaml", "urban/b.yaml"})


def _demo_workspace(root: Path) -> tuple[Path, dict]:
    """A source workspace with one City (receipt -> MJCF -> terrain) and a Composition."""
    work = root / "hakoniwa-business-pack/work"
    city = work / "urban/studio-cities/c1"
    world = work / "recipes/environment-studio/city-worlds/c1/build"
    (city / "build/world").mkdir(parents=True)
    (city / "viewer").mkdir()
    (world / "world").mkdir(parents=True)
    (world / "components/terrain").mkdir(parents=True)
    (world / "source").mkdir()
    (world / "source/huge.gml").write_text("x", encoding="utf-8")
    (world / "components/terrain/terrain.hf").write_bytes(b"\x00\x01")
    (world / "world/city-world.xml").write_text(
        f'<mujoco><asset><hfield file="{world}/components/terrain/terrain.hf"/></asset></mujoco>', encoding="utf-8")
    (world / "world/city-world.glb").write_bytes(b"glb")
    receipt = city / "build/world/city-world-receipt.json"
    receipt.write_text(json.dumps({"mjcf": {"path": str(world / "world/city-world.xml")},
                                   "glb": {"path": str(world / "world/city-world.glb")},
                                   "source": str(world / "source")}), encoding="utf-8")
    (city / "viewer/city-world.glb").write_bytes(b"glb")
    (city / "job.json").write_text(json.dumps({"title": "c1", "source": {"path": str(world)}}), encoding="utf-8")
    (work / "urban/assets/cities").mkdir(parents=True)
    (work / "urban/assets/cities/c1.asset.yaml").write_text(
        yaml.safe_dump({"schema": "hakoniwa.asset/v1", "id": "c1", "kind": "city", "version": 1,
                        "receipt": str(receipt)}), encoding="utf-8")
    (work / "urban/compositions").mkdir(parents=True)
    (work / "urban/scenarios").mkdir(parents=True)
    (work / "urban/scenarios/route.yaml").write_text("schema: x\n", encoding="utf-8")
    (work / "urban/compositions/demo31-a.yaml").write_text(
        "world: c1\nvehicles:\n- params:\n    scenario: ${repo:hakoniwa-business-pack}/work/urban/scenarios/route.yaml\n",
        encoding="utf-8")
    (work / "urban/cache").mkdir()
    (work / "urban/cache/height.bin").write_bytes(b"cache")
    spec = {"include": ["urban/assets/cities/c1.asset.yaml", "urban/studio-cities/c1",
                        "urban/compositions/demo31-*.yaml", "urban/cache"],
            "exclude": ["urban/cache", "*/build/source"]}
    return work, spec


class BundleTest(unittest.TestCase):
    def test_collect_follows_references_and_leaves_out_sources_and_caches(self):
        with tempfile.TemporaryDirectory() as directory:
            work, spec = _demo_workspace(Path(directory))
            files, missing = urban_portable.bundle_files(spec, work)
        self.assertIn("recipes/environment-studio/city-worlds/c1/build/world/city-world.xml", files)
        self.assertIn("recipes/environment-studio/city-worlds/c1/build/components/terrain/terrain.hf", files)
        self.assertIn("urban/scenarios/route.yaml", files)
        self.assertNotIn("urban/cache/height.bin", files)
        self.assertFalse(any("/build/source" in item for item in files))
        self.assertEqual(missing, [])

    def test_unpack_fills_the_paths_and_a_moved_folder_is_rewritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            work, spec = _demo_workspace(source)
            bundle = root / "bundle.zip"
            manifest = urban_portable.write_bundle(spec, work, source, bundle)
            with zipfile.ZipFile(bundle) as archive:
                for name in manifest["tokenized"]:
                    self.assertNotIn(str(source), archive.read(name).decode("utf-8"))
            package = root / "展開 先 & co"
            target = package / "hakoniwa-business-pack/work"
            target.mkdir(parents=True)
            self.assertEqual(urban_portable.install_demo_data(bundle, target, package), "unpacked")
            self.assertEqual(urban_portable.install_demo_data(bundle, target, package), "current")

            def check(work_dir: Path) -> None:
                asset = yaml.safe_load((work_dir / "urban/assets/cities/c1.asset.yaml").read_text(encoding="utf-8"))
                receipt = Path(asset["receipt"])
                self.assertTrue(receipt.is_file())
                # Urban Studio would register the City again if the version were stale.
                self.assertEqual(asset["version"], receipt.stat().st_mtime_ns)
                mjcf = Path(json.loads(receipt.read_text(encoding="utf-8"))["mjcf"]["path"])
                import xml.etree.ElementTree as ET

                terrain = ET.parse(mjcf).getroot().find("asset/hfield").get("file")
                self.assertTrue(Path(terrain).is_file(), terrain)

            check(target)
            moved = root / "moved"
            package.rename(moved)
            self.assertEqual(urban_portable.install_demo_data(bundle, moved / "hakoniwa-business-pack/work", moved),
                             "relocated")
            check(moved / "hakoniwa-business-pack/work")
            stamp = json.loads((moved / "hakoniwa-business-pack/work" / urban_portable.STAMP).read_text())
            self.assertEqual(stamp["package_root"], str(moved.resolve()))

    def test_collect_bundles_the_demo_worlds_of_this_workspace_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work, spec = _demo_workspace(root / "win")
            spec_path = root / "demo-data.json"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")
            output = root / "win/runtime/urban-demo-data.zip"
            with mock.patch.object(urban_portable, "PACKAGE_ROOT", root / "win"):
                self.assertEqual(urban_portable.collect_demo_data(output=output, spec_path=spec_path, work=work), output)
            self.assertEqual(urban_portable.read_bundle_manifest(output)["files"], 9)
            # A Workspace without the demo Cities: no bundle from elsewhere, and a stale one is not packaged.
            empty = root / "other/hakoniwa-business-pack/work"
            empty.mkdir(parents=True)
            with self.assertRaisesRegex(urban_portable.PortableError, "urban_demo_worlds.py build --all"):
                urban_portable.collect_demo_data(output=output, spec_path=spec_path, work=empty)
            self.assertFalse(output.exists())
            self.assertEqual(urban_portable.missing_demo_data(spec, empty), spec["include"])

    def test_there_is_no_way_to_bring_a_bundle_made_elsewhere(self):
        with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
            urban_portable.parser().parse_args(["bundle"])
        with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
            urban_portable.parser().parse_args(["collect", "--demo-data", "x.zip"])

    def test_a_source_path_left_in_a_file_stops_collect(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work, spec = _demo_workspace(root / "source")
            with mock.patch.object(urban_portable, "tokenize_file_text", side_effect=lambda text, *_: text):
                with self.assertRaisesRegex(urban_portable.PortableError, "絶対パス"):
                    urban_portable.write_bundle(spec, work, root / "source", root / "bundle.zip")
            self.assertFalse((root / "bundle.zip").exists())

    def test_a_long_extraction_folder_is_refused_before_unpacking(self):
        urban_portable.check_path_budget(Path("C:/hako/hakoniwa-business-pack/work"), "x" * 150)
        with self.assertRaisesRegex(urban_portable.PortableError, "短い場所"):
            urban_portable.check_path_budget(Path("C:/" + "d" * 100 + "/hakoniwa-business-pack/work"), "x" * 150)

    def test_the_demo_data_spec_names_the_demo_worlds_and_leaves_out_caches(self):
        spec = json.loads(urban_portable.DEMO_SPEC.read_text(encoding="utf-8"))
        for world in ("tokyo-13104-multi-lat35_689-lon139_691", "tocho-bridge-a-hull", "sapporo-rotary-snow"):
            self.assertIn(f"urban/assets/cities/{world}.asset.yaml", spec["include"])
        for demo in range(31, 36):
            self.assertIn(f"urban/compositions/demo{demo}-*.yaml", spec["include"])
        self.assertIn("urban/cache", spec["exclude"])
        self.assertIn("*/build/source", spec["exclude"])

    def test_prepare_never_runs_in_a_source_workspace(self):
        with mock.patch.dict(os.environ, PORTABLE_ON), \
                mock.patch.object(urban_portable, "install_demo_data") as install:
            # A source checkout has no .hakoniwa-repository-root (the packager writes it).
            with self.assertRaisesRegex(urban_portable.PortableError, "パッケージの中"):
                urban_portable.prepare()
        install.assert_not_called()


class PortableModeTest(unittest.TestCase):
    def test_the_drone_fleet_and_fpv_routes_skip_recipe_configure(self):
        selected = mock.Mock(managed_recipe=ROOT / "recipes/usecases/urban-drone-rc.yaml", route="drone")
        with mock.patch.dict(os.environ, PORTABLE_ON), mock.patch.object(urban_simulation.subprocess, "run") as run:
            self.assertEqual(urban_simulation.prepare_route(selected, "configure"), 0)
        run.assert_not_called()
        with portable_off(), mock.patch.object(urban_simulation.subprocess, "run") as run:
            run.return_value.returncode = 0
            urban_simulation.prepare_route(selected, "configure")
        run.assert_called_once()

    def test_car_and_integrated_configure_skip_recipe_configure(self):
        context = urban_mobility.RecipeContext(path=Path("r.yaml"), data={}, recipe_id="urban-car-rc", use_case="car-rc")
        with mock.patch.dict(os.environ, PORTABLE_ON), \
                mock.patch.object(urban_mobility, "recipe_command") as recipe, \
                mock.patch.object(urban_mobility, "configure_car_rc", return_value=0):
            self.assertEqual(urban_mobility.configure(context, Namespace()), 0)
        recipe.assert_not_called()

    def test_the_car_route_reuses_the_packaged_plant_instead_of_cmake(self):
        context = urban_mobility.RecipeContext(path=Path("r.yaml"), data={}, recipe_id="urban-car-rc", use_case="car-rc")
        with mock.patch.dict(os.environ, PORTABLE_ON), \
                mock.patch.object(urban_mobility, "materialize_template", return_value=Path("c.json")), \
                mock.patch.object(multi_car, "resolve_config", return_value={"vehicles": [{"tire_friction": True}]}), \
                mock.patch.object(multi_car, "build_car_asset") as build, \
                mock.patch.object(multi_car, "require_built_car_asset") as require, \
                mock.patch.object(multi_car, "configure", return_value=1):
            self.assertEqual(urban_mobility.configure_car_rc(context, Namespace()), 1)
        build.assert_not_called()
        require.assert_called_once_with(enable_mirror=True)
        with mock.patch.dict(os.environ, PORTABLE_ON), \
                mock.patch.object(urban_mobility, "materialize_template", return_value=Path("c.json")), \
                mock.patch.object(multi_car, "resolve_config", return_value={"vehicles": []}), \
                mock.patch.object(multi_car, "require_built_car_asset", side_effect=multi_car.RecipeError("no plant")):
            with self.assertRaisesRegex(urban_mobility.UrbanMobilityError, "no plant"):
                urban_mobility.configure_car_rc(context, Namespace())

    def test_the_integrated_route_builds_only_outside_a_package(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(multi_car, "ROOT", root), \
                    mock.patch.object(multi_car, "build_car_asset") as build:
                with mock.patch.dict(os.environ, PORTABLE_ON):
                    with self.assertRaisesRegex(multi_car.RecipeError, "not there"):
                        multi_car.ensure_car_asset(enable_mirror=True)
                    plant = multi_car.native_executable(root / "build/portable-runtime/bin" / multi_car.PLANT_NAME)
                    plant.parent.mkdir(parents=True)
                    plant.write_bytes(b"exe")
                    (plant.parent / multi_car.PLANT_FEATURES).write_text('{"plant_directive": false}')
                    with self.assertRaisesRegex(multi_car.RecipeError, "MIRROR=ON"):
                        multi_car.ensure_car_asset(enable_mirror=True)
                    (plant.parent / multi_car.PLANT_FEATURES).write_text('{"plant_directive": true}')
                    self.assertEqual(multi_car.ensure_car_asset(enable_mirror=True), plant)
                build.assert_not_called()
                with portable_off():
                    multi_car.ensure_car_asset(enable_mirror=True)
                build.assert_called_once_with(enable_mirror=True)

    def test_environment_studio_starts_without_configure_in_a_package(self):
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            studio = Path(directory) / "hakoniwa-environment-studio"
            (studio / "tools").mkdir(parents=True)
            (studio / "tools/env_studio.py").write_text("", encoding="utf-8")
            with mock.patch.dict(os.environ, {**PORTABLE_ON, "HAKONIWA_ENVIRONMENT_STUDIO_ROOT": str(studio)}), \
                    mock.patch.object(urban_city_authoring, "_run", side_effect=lambda command: calls.append(command) or 0), \
                    mock.patch.object(urban_city_authoring, "EXPORT_DIR", Path(directory) / "exports"):
                self.assertEqual(urban_city_authoring.start(), 0)
                self.assertEqual(urban_city_authoring.configure(), 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][2], "start")


class PlantTest(unittest.TestCase):
    def test_the_features_file_says_whether_the_plant_has_the_plant_directive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(multi_car, "ROOT", root):
                packaged = multi_car.native_executable(root / "build/portable-runtime/bin" / multi_car.PLANT_NAME)
                self.assertEqual(multi_car.plant_executable(),
                                 multi_car.native_executable(root / "build/bin" / multi_car.PLANT_NAME))
                packaged.parent.mkdir(parents=True)
                packaged.write_bytes(b"exe")
                self.assertEqual(multi_car.plant_executable(), packaged)
                self.assertIsNone(multi_car.built_with_plant_directive())
                (packaged.parent / multi_car.PLANT_FEATURES).write_text(
                    json.dumps({"schema_version": 1, "plant_directive": True}), encoding="utf-8")
                self.assertTrue(multi_car.built_with_plant_directive())
                # A developer build next to the CMake cache wins over the packaged copy.
                built = multi_car.native_executable(root / "build/bin" / multi_car.PLANT_NAME)
                built.parent.mkdir(parents=True)
                built.write_bytes(b"exe")
                (root / "build/CMakeCache.txt").write_text("HAKO_URBAN_ENABLE_MIRROR:BOOL=OFF\n", encoding="utf-8")
                self.assertEqual(multi_car.plant_executable(), built)
                self.assertFalse(multi_car.built_with_plant_directive())
                self.assertEqual(multi_car.paths()["plant"], built)

    def test_cmake_writes_the_features_file_next_to_the_plant(self):
        text = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn('"${CMAKE_BINARY_DIR}/bin/urban-car-hakoniwa-asset.features.json"', text)
        self.assertIn('\\"plant_directive\\": @_urban_plant_directive@', text)
        self.assertEqual(multi_car.PLANT_FEATURES, urban_portable.PLANT_FEATURES)

    def test_collect_copies_the_plant_its_dlls_and_its_features(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "build/bin"
            source.mkdir(parents=True)
            (source / "urban-car-hakoniwa-asset.exe").write_bytes(b"exe")
            (source / "mujoco.dll").write_bytes(b"dll")
            (source / "hako-mirror-mujoco-plant-test.exe").write_bytes(b"test")
            cache = root / "build/CMakeCache.txt"
            cache.write_text("HAKO_URBAN_ENABLE_MIRROR:BOOL=OFF\n", encoding="utf-8")
            destination = root / "out"
            with self.assertRaisesRegex(urban_portable.PortableError, "MIRROR=OFF"):
                urban_portable.collect_plant(source, destination, cache, windows=True)
            cache.write_text("HAKO_URBAN_ENABLE_MIRROR:BOOL=ON\n", encoding="utf-8")
            urban_portable.collect_plant(source, destination, cache, windows=True)
            self.assertEqual(sorted(path.name for path in destination.iterdir()),
                             ["mujoco.dll", "urban-car-hakoniwa-asset.exe", urban_portable.PLANT_FEATURES])
            features = json.loads((destination / urban_portable.PLANT_FEATURES).read_text(encoding="utf-8"))
            self.assertTrue(features["plant_directive"])
            (source / "urban-car-hakoniwa-asset.exe").unlink()
            with self.assertRaisesRegex(urban_portable.PortableError, "Plant がありません"):
                urban_portable.collect_plant(source, destination, cache, windows=True)

    def test_glfw_comes_from_drone_core_or_the_vcpkg_of_the_foundation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vcpkg = root / "vcpkg/bin"
            vcpkg.mkdir(parents=True)
            (vcpkg / "glfw3.dll").write_bytes(b"dll")
            drone = root / "win"
            drone.mkdir()
            collected = urban_portable.collect_drone_dlls(vcpkg, root / "out", drone)
            self.assertEqual(collected, [root / "out/glfw3.dll"])
            (drone / "glfw3.dll").write_bytes(b"dll")
            self.assertEqual(urban_portable.collect_drone_dlls(root / "nowhere", root / "out2", drone), [])


class PortTest(unittest.TestCase):
    def test_a_listening_port_is_busy(self):
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen()
            port = server.getsockname()[1]
            self.assertTrue(urban_portable.port_busy(port))
        self.assertFalse(urban_portable.port_busy(port))

    def test_conflicts_name_the_port_the_program_and_the_fix(self):
        rows = [
            {"id": "urban-studio", "port": 28090, "fixed": False, "purpose": "the Urban Studio browser backend"},
            {"id": "environment-studio", "port": 28097, "fixed": True, "purpose": "Environment Studio"},
            {"id": "viewer-http", "port": 28100, "fixed": False, "purpose": "viewer"},
        ]
        conflicts = urban_portable.port_conflicts(
            rows, ours={"environment-studio"}, busy=lambda port: True,
            owner=lambda port: "wslrelay.exe (pid 42)" if port == 28100 else None)
        self.assertEqual([item["id"] for item in conflicts], ["urban-studio", "viewer-http"])
        self.assertIn("ポート 28100", conflicts[1]["message"])
        self.assertIn("wslrelay.exe (pid 42)", conflicts[1]["message"])
        self.assertIn("ports.yaml", conflicts[1]["message"])
        fixed = urban_portable.port_conflicts(rows[1:2], busy=lambda port: True, owner=lambda port: None)
        self.assertIn("変えられません", fixed[0]["message"])

    def test_the_manifest_ports_are_checked(self):
        rows = urban_portable.port_table()
        self.assertEqual({row["id"] for row in rows}, set(urban_manifest.load()["ports"]))
        self.assertTrue(next(row for row in rows if row["id"] == "environment-studio")["fixed"])

    def test_netstat_names_the_listening_pid(self):
        text = """
  Proto  Local Address          Foreign Address        State           PID
  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       1060
  TCP    127.0.0.1:28100        0.0.0.0:0              LISTENING       4242
  TCP    127.0.0.1:28100        127.0.0.1:50000        ESTABLISHED     4242
  TCP    [::]:28090             [::]:0                 LISTENING       77
"""
        self.assertEqual(urban_portable.parse_netstat_pid(text, 28100), 4242)
        self.assertEqual(urban_portable.parse_netstat_pid(text, 28090), 77)
        self.assertIsNone(urban_portable.parse_netstat_pid(text, 28097))

    def test_start_stops_when_the_studio_port_is_taken(self):
        conflict = [{"id": "urban-studio", "port": 28090, "message": "ポート 28090 ..."}]
        with mock.patch.object(urban_portable, "prepare", return_value=0), \
                mock.patch.object(urban_portable, "_health", return_value=None), \
                mock.patch.object(urban_portable, "port_table", return_value=[]), \
                mock.patch.object(urban_portable, "ours_running", return_value=set()), \
                mock.patch.object(urban_portable, "port_conflicts", return_value=conflict), \
                mock.patch.object(urban_portable, "_studio") as studio:
            self.assertEqual(urban_portable.start(), 1)
        studio.assert_not_called()


class ChildWindowTest(unittest.TestCase):
    def test_children_get_no_console_window_on_windows_only(self):
        self.assertEqual(urban_studio.child_process_options("nt"), {"creationflags": 0x08000000})
        self.assertEqual(urban_studio.child_process_options("posix"), {})

    def test_the_job_runner_starts_commands_with_those_options(self):
        runner = urban_studio.JobRunner()
        job = urban_studio.Job(id="1", composition="c", command="plan", steps=[["python", "x.py"]])
        process = mock.Mock()
        process.stdout = iter(["line\n"])
        process.wait.return_value = 0
        process.stdout = mock.MagicMock()
        process.stdout.__iter__.return_value = iter(["line\n"])
        with mock.patch.object(urban_studio, "child_process_options", return_value={"creationflags": 7}), \
                mock.patch.object(urban_studio.subprocess, "Popen", return_value=process) as popen:
            runner._run(job)
        self.assertEqual(popen.call_args.kwargs["creationflags"], 7)
        self.assertEqual(job.state, "succeeded")
        self.assertEqual(job.lines, ["line"])

    def test_session_scan_finds_only_running_simulations(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            for name, state in (("urban-car-rc", "RUNNING"), ("urban-drone-one", "TERMINATED")):
                session = work / "recipes" / name / "runtime/launcher-session.json"
                session.parent.mkdir(parents=True)
                session.write_text(json.dumps({"state": state, "pid": os.getpid()}), encoding="utf-8")
            sessions = urban_portable.running_sessions(work)
        self.assertEqual([urban_portable._session_name(path) for path in sessions], ["urban-car-rc"])


if __name__ == "__main__":
    unittest.main()


class FoundationPythonTest(unittest.TestCase):
    """doctor finds the Foundation Python in the package (embeddable, at the root) and in a
    developer Workspace on Windows (a venv, in Scripts)."""

    def test_the_package_python_at_the_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "python.exe").write_bytes(b"")
            self.assertEqual(urban_portable.foundation_python(root), root / "python.exe")

    def test_the_workspace_venv_in_scripts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Scripts").mkdir()
            (root / "Scripts/python.exe").write_bytes(b"")
            self.assertEqual(urban_portable.foundation_python(root), root / "Scripts/python.exe")
            self.assertTrue(urban_portable.foundation_python(root).is_file())


class PackagePathTest(unittest.TestCase):
    """prepare refuses a package folder outside letters, digits and _ . -: MuJoCo on
    Windows cannot open a path outside ASCII, and spaces and shell characters break
    the bat entrypoints."""

    def test_letters_digits_and_a_few_marks_are_fine(self):
        urban_portable.check_package_path(Path("C:/hako/hako-urban-studio-win64"))
        urban_portable.check_package_path(Path("D:/work_2026/v1.0/hako-urban-studio-win64"))

    def test_a_japanese_folder_is_refused_with_the_characters_and_a_way_out(self):
        with self.assertRaises(urban_portable.PortableError) as raised:
            urban_portable.check_package_path(Path("C:/Users/山田/Downloads/hako-urban-studio-win64"))
        message = str(raised.exception)
        self.assertIn("山", message)
        self.assertIn("C:\\hako", message)

    def test_spaces_and_shell_characters_are_refused(self):
        for folder in ("C:/hako demo/pkg", "C:/hako&co/pkg", "C:/100%/pkg", "C:/hako!/pkg"):
            with self.subTest(folder=folder), self.assertRaises(urban_portable.PortableError):
                urban_portable.check_package_path(Path(folder))
        with self.assertRaises(urban_portable.PortableError) as raised:
            urban_portable.check_package_path(Path("C:/hako demo/pkg"))
        self.assertIn("空白", str(raised.exception))

    def test_prepare_checks_the_package_folder_before_anything_else(self):
        with mock.patch.object(urban_portable, "portable_package", return_value=True), \
                mock.patch.object(urban_portable, "PACKAGE_ROOT", Path("C:/箱庭 デモ/pkg")), \
                mock.patch.object(urban_portable, "relocate_foundation_receipts") as relocate:
            with self.assertRaises(urban_portable.PortableError):
                urban_portable.prepare()
        relocate.assert_not_called()


class ChildOutputEncodingTest(unittest.TestCase):
    """A child's error in Japanese (a path) reaches the message instead of a decode error."""

    def test_world_height_reads_the_compile_error_as_utf8(self):
        import world_height

        failed = subprocess.CompletedProcess([], 1, stdout="", stderr="Error opening file 'C:/箱庭/terrain.hf'\n")
        with mock.patch.object(world_height.subprocess, "run", return_value=failed) as run:
            with self.assertRaises(world_height.WorldHeightError) as raised:
                world_height._compile_in_process(0, Path("a.xml"), Path("a.mjb"))
        self.assertIn("箱庭", str(raised.exception))
        options = run.call_args.kwargs
        self.assertEqual(options["encoding"], "utf-8")
        self.assertEqual(options["env"]["PYTHONIOENCODING"], "utf-8")

    def test_a_missing_stderr_does_not_hide_the_failure(self):
        import world_height

        failed = subprocess.CompletedProcess([], 1, stdout=None, stderr=None)
        with mock.patch.object(world_height.subprocess, "run", return_value=failed):
            with self.assertRaises(world_height.WorldHeightError):
                world_height._compile_in_process(0, Path("a.xml"), Path("a.mjb"))
