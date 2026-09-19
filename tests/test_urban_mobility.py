from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tools import urban_mobility


class UrbanMobilityToolTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
