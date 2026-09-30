"""Making Cities with Environment Studio (tools/urban_city_authoring.py):
where the Studio is, the configure steps (this Recipe, then the Studio's
own), and the start with Urban's export folder."""

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import urban_city_authoring  # noqa: E402
import urban_manifest  # noqa: E402


class CityAuthoringTest(unittest.TestCase):
    def test_the_recipe_declares_environment_studio_and_the_manifest_names_it(self):
        recipe = yaml.safe_load(urban_city_authoring.RECIPE.read_text(encoding="utf-8"))
        self.assertEqual(recipe["id"], urban_manifest.value("recipes.city.recipe_id"))
        studio = recipe["recipe_local_requirements"]["hakoniwa-environment-studio"]
        self.assertEqual(studio["root"]["override_env"], "HAKONIWA_ENVIRONMENT_STUDIO_ROOT")
        self.assertEqual(recipe["foundation_contract"]["mode"], "not_required")
        self.assertEqual(urban_city_authoring.EXPORT_DIR, urban_manifest.path("assets.studio_city_jobs"))

    def test_the_studio_is_found_as_recipe_py_finds_it(self):
        self.assertEqual(urban_city_authoring.studio_root({}), (ROOT.parent / "hakoniwa-environment-studio").resolve())
        with tempfile.TemporaryDirectory() as elsewhere:
            self.assertEqual(urban_city_authoring.studio_root({"HAKONIWA_ENVIRONMENT_STUDIO_ROOT": elsewhere}),
                             Path(elsewhere).resolve())

    def test_start_configures_both_recipes_then_starts_the_studio_with_urbans_folder(self):
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            studio = Path(directory) / "hakoniwa-environment-studio"
            (studio / "tools").mkdir(parents=True)
            (studio / "tools/env_studio.py").write_text("", encoding="utf-8")
            exports = Path(directory) / "studio-cities"
            with mock.patch.object(urban_city_authoring, "_run", side_effect=lambda command: calls.append(command) or 0), \
                    mock.patch.object(urban_city_authoring, "EXPORT_DIR", exports), \
                    mock.patch.dict(os.environ, {"HAKONIWA_ENVIRONMENT_STUDIO_ROOT": str(studio)}):
                self.assertEqual(urban_city_authoring.start(open_browser=True), 0)
            self.assertTrue(exports.is_dir())
        recipe_tool = str(urban_manifest.business_pack() / "tools/recipe.py")
        self.assertEqual(calls[0][1:], [recipe_tool, "configure", "--recipe", str(urban_city_authoring.RECIPE)])
        self.assertEqual(calls[1][1:], [recipe_tool, "configure", "--recipe",
                                        str(studio.resolve() / "recipes/business-pack/environment-studio.yaml")])
        self.assertEqual(calls[2][1:], [str(studio.resolve() / "tools/env_studio.py"), "start", "--port",
                                        str(urban_city_authoring.PORT), "--export-dir", str(exports), "--open-browser"])

    def test_a_failed_configure_stops_before_the_studio_starts(self):
        calls = []
        with mock.patch.object(urban_city_authoring, "_run", side_effect=lambda command: calls.append(command) or 3):
            self.assertEqual(urban_city_authoring.start(), 3)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
