from argparse import Namespace
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest import mock

from tools import urban_composer, urban_mobility


def integrated_context() -> urban_mobility.RecipeContext:
    path = urban_mobility.ROOT / "recipes/experiments/urban-mobility-rc.yaml"
    return urban_mobility.RecipeContext(
        path=path,
        data={
            "id": "urban-mobility-rc",
            "urban_mobility": {
                "use_case": "drone-car-distributed",
                "composition": {
                    "path": "recipes/experiments/urban-mobility-shizuoka.yaml"
                },
            },
        },
        recipe_id="urban-mobility-rc",
        use_case="drone-car-distributed",
    )


def car_rc_context() -> urban_mobility.RecipeContext:
    path = urban_mobility.ROOT / "recipes/usecases/urban-car-rc.yaml"
    return urban_mobility.RecipeContext(
        path=path,
        data={
            "id": "urban-car-rc",
            "urban_mobility": {
                "use_case": "car-rc",
                "template": {
                    "path": "recipes/experiments/urban-car-one.yaml",
                },
                "parameters": {
                    "city_receipt": {
                        "cli": "--city-receipt",
                        "target": "inputs.business_pack_city_receipt.path",
                        "type": "path",
                        "required": True,
                    },
                    "web_bridge_port": {
                        "cli": "--web-bridge-port",
                        "target": "inputs.browser_visualization.web_bridge_port",
                        "type": "int",
                        "required": False,
                    },
                },
            },
        },
        recipe_id="urban-car-rc",
        use_case="car-rc",
    )


class UrbanMobilityToolTest(unittest.TestCase):
    def test_shizuoka_composition_has_rooftop_drone_and_five_car_route(self):
        config, resolved, drone = urban_composer.load_composition(
            urban_composer.DEFAULT_CONFIG
        )

        self.assertEqual(config["id"], "urban-mobility-rc")
        self.assertEqual(len(resolved["vehicles"]), 5)
        self.assertEqual(drone["recipe"].control_mode, "ps4-rc")
        self.assertEqual(drone["recipe"].source_path, drone["path"])
        self.assertEqual(drone["rooftop"]["source_geometry"], "plateau-0017")
        self.assertAlmostEqual(drone["spawn"]["up_m"], 35.79)
        self.assertAlmostEqual(
            drone["spawn"]["up_m"],
            float(drone["rooftop"]["surface_height_m"])
            + float(drone["rooftop"]["base_clearance_m"]),
        )

    def test_recipe_command_delegates_selected_recipe_with_current_python(self):
        context = car_rc_context()
        completed = mock.Mock(returncode=0)
        with mock.patch.object(
            urban_mobility.subprocess, "run", return_value=completed
        ) as runner:
            self.assertEqual(urban_mobility.recipe_command("doctor", context), 0)
        command = runner.call_args.args[0]
        self.assertEqual(command[0], sys.executable)
        self.assertEqual(command[2], "doctor")
        self.assertEqual(command[3], "--recipe")
        self.assertEqual(Path(command[4]), context.path)

    def test_foundation_python_uses_workspace_platform_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            install = Path(directory) / "install"
            expected = install / "python" / "Scripts" / "python.exe"
            expected.parent.mkdir(parents=True)
            expected.touch()
            with (
                mock.patch.object(
                    urban_mobility, "foundation_install", return_value=install
                ),
                mock.patch.object(
                    urban_mobility,
                    "foundation_python_layout",
                    return_value=(expected, expected.parent),
                ) as layout,
            ):
                self.assertEqual(urban_mobility.foundation_python(), expected)
            layout.assert_called_once_with(install / "python")

    def test_integrated_recipe_keeps_git_materialization_contract(self):
        recipe = (
            urban_mobility.ROOT / "recipes/experiments/urban-mobility-rc.yaml"
        ).read_text(encoding="utf-8")
        self.assertNotIn(
            "build/bin/urban-car-hakoniwa-asset${NATIVE_EXECUTABLE_SUFFIX}",
            recipe,
        )
        self.assertIn("apps/car/urban-car-hakoniwa-asset.cpp", recipe)
        self.assertIn("use_case: drone-car-distributed", recipe)
        self.assertIn(
            "url: https://github.com/hakoniwalab/hakoniwa-drone-show.git",
            recipe,
        )
        self.assertIn(
            "url: https://github.com/toppers/hakoniwa-drone-core.git",
            recipe,
        )
        self.assertIn(
            "requirements: recipes/requirements/urban-mobility-rc.txt",
            recipe,
        )

    def test_car_rc_recipe_declares_template_city_input_and_runtime_packages(self):
        recipe = (
            urban_mobility.ROOT / "recipes/usecases/urban-car-rc.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn("use_case: car-rc", recipe)
        self.assertIn("path: recipes/experiments/urban-car-one.yaml", recipe)
        self.assertIn("cli: --city-receipt", recipe)
        self.assertIn("target: inputs.business_pack_city_receipt.path", recipe)
        self.assertNotIn("hakoniwa-drone-core:", recipe)
        self.assertNotIn("hakoniwa-drone-show:", recipe)
        self.assertIn("hakoniwa-pdu-registry:", recipe)
        self.assertIn(
            "url: https://github.com/hakoniwalab/hakoniwa-pdu-registry.git",
            recipe,
        )
        self.assertIn(
            "pdu/types/ackermann_msgs/pdu_cpptype_AckermannDrive.hpp",
            recipe,
        )

        requirements = (
            urban_mobility.ROOT / "recipes/requirements/urban-car-rc.txt"
        ).read_text(encoding="utf-8")
        self.assertIn("PyYAML>=6.0,<7", requirements)
        self.assertIn("pygame==2.6.1", requirements)
        self.assertIn("mujoco==3.13.0", requirements)

    def test_car_asset_build_matches_release_foundation_on_multiconfig_hosts(self):
        multi_car = urban_composer.multi_car
        with mock.patch.object(multi_car.subprocess, "run") as runner:
            multi_car.build_car_asset()

        configure = runner.call_args_list[0].args[0]
        build = runner.call_args_list[1].args[0]
        self.assertIn("-DCMAKE_BUILD_TYPE=Release", configure)
        self.assertEqual(
            build[0:3],
            ["cmake", "--build", str(multi_car.ROOT / "build")],
        )
        self.assertIn("--config", build)
        self.assertEqual(build[build.index("--config") + 1], "Release")
        self.assertIn("--target", build)
        self.assertEqual(
            build[build.index("--target") + 1],
            "urban-car-hakoniwa-asset",
        )

    def test_external_browser_asset_is_copied_into_recipe_workspace(self):
        multi_car = urban_composer.multi_car
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "external" / "city-world.glb"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"glb")
            target = root / "workspace" / "config" / "threejs" / "assets" / "city-world.glb"

            materialized = multi_car.materialize_browser_asset(source, target)

            self.assertEqual(materialized, target.resolve())
            self.assertEqual(target.read_bytes(), b"glb")

    def test_multi_car_native_executable_uses_windows_suffix(self):
        multi_car = urban_composer.multi_car
        with mock.patch.object(multi_car.sys, "platform", "win32"):
            self.assertEqual(
                multi_car.native_executable(
                    Path("build/bin/urban-car-hakoniwa-asset")
                ),
                Path("build/bin/urban-car-hakoniwa-asset.exe"),
            )

    def test_composition_path_comes_from_selected_integrated_recipe(self):
        path = urban_mobility.composition_path(integrated_context())
        self.assertEqual(
            path,
            (
                urban_mobility.ROOT
                / "recipes/experiments/urban-mobility-shizuoka.yaml"
            ).resolve(),
        )

    def test_car_rc_template_fills_city_receipt_from_cli(self):
        context = car_rc_context()
        template = {
            "id": "urban-car-one",
            "inputs": {
                "business_pack_city_receipt": {"path": "tracked-default.json"},
            },
            "composition": {
                "output": {"recipe_id": "urban-car-one"},
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            selected = Path(directory) / "work"
            city_receipt = Path(directory) / "city-world-receipt.json"
            city_receipt.write_text("{}", encoding="utf-8")
            args = Namespace(city_receipt=city_receipt, web_bridge_port=None)
            with (
                mock.patch.dict(
                    "os.environ", {"HAKONIWA_WORK_DIR": str(selected)}
                ),
                mock.patch.object(
                    urban_composer.multi_car,
                    "load_yaml",
                    return_value=template,
                ),
            ):
                output = urban_mobility.materialize_template(context, args)
            generated = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(generated["id"], "urban-car-rc")
        self.assertEqual(
            generated["composition"]["output"]["recipe_id"],
            "urban-car-rc",
        )
        self.assertEqual(
            generated["inputs"]["business_pack_city_receipt"]["path"],
            str(city_receipt.resolve()),
        )

    def test_car_rc_optional_web_bridge_port_override(self):
        context = car_rc_context()
        template = {
            "id": "urban-car-one",
            "inputs": {
                "business_pack_city_receipt": {"path": "tracked-default.json"},
                "browser_visualization": {"web_bridge_port": 18765},
            },
            "composition": {"output": {"recipe_id": "urban-car-one"}},
        }
        with tempfile.TemporaryDirectory() as directory:
            selected = Path(directory) / "work"
            city_receipt = Path(directory) / "city-world-receipt.json"
            city_receipt.write_text("{}", encoding="utf-8")
            args = Namespace(city_receipt=city_receipt, web_bridge_port=19001)
            with (
                mock.patch.dict(
                    "os.environ", {"HAKONIWA_WORK_DIR": str(selected)}
                ),
                mock.patch.object(
                    urban_composer.multi_car,
                    "load_yaml",
                    return_value=template,
                ),
            ):
                output = urban_mobility.materialize_template(context, args)
            generated = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(
            generated["inputs"]["browser_visualization"]["web_bridge_port"],
            19001,
        )

    def test_spec_is_selected_recipe_local(self):
        context = car_rc_context()
        with tempfile.TemporaryDirectory() as directory:
            selected = Path(directory) / "work"
            with mock.patch.dict(
                "os.environ", {"HAKONIWA_WORK_DIR": str(selected)}
            ):
                lifecycle = urban_mobility.spec(context, require_viewer=False)
        expected = selected.resolve() / "recipes/urban-car-rc"
        self.assertEqual(lifecycle.recipe_root, expected)
        self.assertEqual(lifecycle.launcher, expected / "config/launcher.json")
        self.assertEqual(
            lifecycle.session, expected / "runtime/launcher-session.json"
        )

    def test_start_checks_selected_recipe_before_launcher(self):
        context = car_rc_context()
        with mock.patch.object(
            urban_mobility, "recipe_command", return_value=1
        ) as doctor:
            self.assertEqual(
                urban_mobility.launcher_command("start", context), 1
            )
        doctor.assert_called_once_with("doctor", context)

    def test_open_viewer_does_not_open_when_recipe_is_not_ready(self):
        context = car_rc_context()
        fake = mock.Mock(viewer_url="http://127.0.0.1:8000/viewer")
        with (
            mock.patch.object(urban_mobility, "spec", return_value=fake),
            mock.patch.object(
                urban_mobility.urban_lifecycle,
                "require_viewer_ready",
                side_effect=urban_mobility.urban_lifecycle.LifecycleError(
                    "not ready"
                ),
            ),
            mock.patch.object(urban_mobility.webbrowser, "open") as opener,
            self.assertRaises(
                urban_mobility.urban_lifecycle.LifecycleError
            ),
        ):
            urban_mobility.open_viewer(context)
        opener.assert_not_called()

    def test_status_rejects_foreign_session_before_launcher_ctl(self):
        context = car_rc_context()
        fake = mock.Mock()
        fake.session = mock.Mock()
        fake.session.is_file.return_value = True
        with (
            mock.patch.object(urban_mobility, "spec", return_value=fake),
            mock.patch.object(
                urban_mobility.urban_lifecycle,
                "read_session",
                side_effect=urban_mobility.urban_lifecycle.LifecycleError(
                    "foreign session"
                ),
            ),
            mock.patch.object(urban_mobility.subprocess, "run") as runner,
            self.assertRaisesRegex(
                urban_mobility.urban_lifecycle.LifecycleError,
                "foreign session",
            ),
        ):
            urban_mobility.launcher_command("status", context)
        runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
