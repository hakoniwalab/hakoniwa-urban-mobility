from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from tools import urban_composer, urban_mobility


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

    def test_recipe_command_delegates_with_current_python(self):
        completed = mock.Mock(returncode=0)
        with mock.patch.object(urban_mobility.subprocess, "run", return_value=completed) as runner:
            self.assertEqual(urban_mobility.recipe_command("doctor"), 0)
        command = runner.call_args.args[0]
        self.assertEqual(command[0], sys.executable)
        self.assertEqual(command[2], "doctor")
        self.assertEqual(command[3], "--recipe")
        self.assertEqual(Path(command[4]), urban_mobility.MANAGED_RECIPE)

    def test_foundation_python_uses_workspace_platform_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            install = Path(directory) / "install"
            expected = install / "python" / "Scripts" / "python.exe"
            expected.parent.mkdir(parents=True)
            expected.touch()
            with (
                mock.patch.object(urban_mobility, "foundation_install", return_value=install),
                mock.patch.object(
                    urban_mobility,
                    "foundation_python_layout",
                    return_value=(expected, expected.parent),
                ) as layout,
            ):
                self.assertEqual(urban_mobility.foundation_python(), expected)
            layout.assert_called_once_with(install / "python")

    def test_managed_recipe_uses_native_executable_suffix(self):
        recipe = urban_mobility.MANAGED_RECIPE.read_text(encoding="utf-8")
        self.assertIn(
            "build/bin/urban-car-hakoniwa-asset${NATIVE_EXECUTABLE_SUFFIX}",
            recipe,
        )

    def test_multi_car_native_executable_uses_windows_suffix(self):
        multi_car = urban_composer.multi_car
        with mock.patch.object(multi_car.sys, "platform", "win32"):
            self.assertEqual(
                multi_car.native_executable(Path("build/bin/urban-car-hakoniwa-asset")),
                Path("build/bin/urban-car-hakoniwa-asset.exe"),
            )

    def test_multi_car_yaml_loader_reuses_business_pack_exporter(self):
        multi_car = urban_composer.multi_car
        completed = mock.Mock(returncode=0, stdout='{"id":"urban"}', stderr="")
        with (
            mock.patch.object(multi_car, "required", side_effect=lambda path, label: path),
            mock.patch.object(multi_car.subprocess, "run", return_value=completed) as runner,
        ):
            self.assertEqual(multi_car.load_yaml(Path("config.yaml")), {"id": "urban"})
        command = runner.call_args.args[0]
        self.assertEqual(command[0], "ruby")
        self.assertTrue(str(command[1]).endswith("recipes/tools/export_recipe_json.rb"))

    def test_spec_is_recipe_local(self):
        with tempfile.TemporaryDirectory() as directory:
            selected = Path(directory) / "work"
            with mock.patch.dict("os.environ", {"HAKONIWA_WORK_DIR": str(selected)}):
                lifecycle = urban_mobility.spec(require_viewer=False)
        expected = selected.resolve() / "recipes/urban-mobility-rc"
        self.assertEqual(lifecycle.recipe_root, expected)
        self.assertEqual(lifecycle.launcher, expected / "config/launcher.json")
        self.assertEqual(lifecycle.session, expected / "runtime/launcher-session.json")

    def test_start_checks_foundation_before_launcher(self):
        with mock.patch.object(urban_mobility, "recipe_command", return_value=1) as doctor:
            self.assertEqual(urban_mobility.launcher_command("start"), 1)
        doctor.assert_called_once_with("doctor")

    def test_open_viewer_does_not_open_when_recipe_is_not_ready(self):
        fake = mock.Mock(viewer_url="http://127.0.0.1:8000/viewer")
        with (
            mock.patch.object(urban_mobility, "spec", return_value=fake),
            mock.patch.object(
                urban_mobility.urban_lifecycle,
                "require_viewer_ready",
                side_effect=urban_mobility.urban_lifecycle.LifecycleError("not ready"),
            ),
            mock.patch.object(urban_mobility.webbrowser, "open") as opener,
            self.assertRaises(urban_mobility.urban_lifecycle.LifecycleError),
        ):
            urban_mobility.open_viewer()
        opener.assert_not_called()

    def test_status_rejects_foreign_session_before_launcher_ctl(self):
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
            urban_mobility.launcher_command("status")
        runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
