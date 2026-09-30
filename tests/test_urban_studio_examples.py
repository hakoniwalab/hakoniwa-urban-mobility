"""Urban Studio lists what the workspace can run: example Compositions appear
once every Asset they name is in the catalog (also examples other workspace
repositories provide); a saved Composition says what it lacks; a City whose
receipt says kind plain is not put on a map; a registered City can carry a title."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import test_city_world_job  # noqa: E402
import urban_assets  # noqa: E402
import urban_manifest  # noqa: E402
import urban_studio  # noqa: E402

SHIZUOKA = "shizuoka-22203-lat35.099-lon138.859"


class ExamplesFollowTheWorkspaceTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.work = Path(directory.name)
        self.user_assets = self.work / "assets"
        for patch in (mock.patch.object(urban_assets, "USER_ASSETS", self.user_assets),
                      mock.patch.object(urban_studio, "USER_COMPOSITIONS", self.work / "compositions")):
            patch.start()
            self.addCleanup(patch.stop)

    def listed(self) -> dict:
        return {item["id"]: item for item in urban_studio.list_compositions()}

    def register(self, city_id: str, **options) -> Path:
        receipt = test_city_world_job.make_job(self.work / "jobs", city_id)
        return urban_assets.register_city(receipt, directory=self.user_assets, **options)

    def test_an_example_appears_once_its_world_is_registered(self):
        before = self.listed()
        self.assertIn("plain-hexa-rc", before)  # everything it names is in this repository
        if SHIZUOKA in urban_assets.catalog():
            self.skipTest("this workspace already has the Shizuoka City registered")
        self.assertNotIn("city-golf-cart-rc", before)
        self.register(SHIZUOKA)
        after = self.listed()
        self.assertIn("city-golf-cart-rc", after)
        self.assertEqual((after["city-golf-cart-rc"]["available"], after["city-golf-cart-rc"]["missing"]), (True, []))

    def test_examples_of_other_workspace_repositories_are_found(self):
        workspace = self.work / "workspace"
        (workspace / "some-repo/assets").mkdir(parents=True)
        (workspace / "some-repo/assets/ground-drone.composition.yaml").write_text(
            "schema: hakoniwa.composition/v1\nid: ground-drone\nworld: plain-ground\nvehicles:\n"
            "  - {name: Drone-1, asset: eams-hexa, control: rc, spawn: {east_m: 0, north_m: 0, yaw_deg: 0}}\n",
            encoding="utf-8")
        real_path = urban_manifest.path
        with mock.patch.object(urban_studio, "WORKSPACE", workspace), \
                mock.patch.object(urban_manifest, "path", side_effect=lambda name: workspace / "*/assets"
                                  if name == "compositions.workspace" else real_path(name)):
            examples = urban_studio.example_compositions()
            self.assertEqual(examples["ground-drone"], workspace / "some-repo/assets/ground-drone.composition.yaml")
            self.assertIn("plain-hexa-rc", examples)
            self.assertFalse(self.listed()["ground-drone"]["editable"])
            self.assertEqual(urban_studio.composition_path("ground-drone"), examples["ground-drone"])

    def test_a_saved_composition_says_what_the_workspace_lacks(self):
        (self.work / "compositions").mkdir()
        (self.work / "compositions/my-run.yaml").write_text(
            "schema: hakoniwa.composition/v1\nid: my-run\nworld: no-such-city\nvehicles:\n"
            "  - {name: Car-1, asset: no-such-car, control: rc, spawn: {east_m: 0, north_m: 0, yaw_deg: 0}}\n",
            encoding="utf-8")
        mine = self.listed()["my-run"]
        self.assertEqual((mine["editable"], mine["available"]), (True, False))
        self.assertEqual(mine["missing"], ["World no-such-city", "Asset no-such-car"])

    def test_a_city_without_a_map_is_not_put_on_one(self):
        self.register("mapped-city")
        plain = self.register("studio-course", title="車のテストコース")
        data = json.loads((self.work / "jobs/studio-course/build/world/city-world-receipt.json").read_text())
        data["kind"] = "plain"
        (self.work / "jobs/studio-course/build/world/city-world-receipt.json").write_text(json.dumps(data))
        self.assertEqual(urban_studio.world_info("mapped-city")["map"], True)
        info = urban_studio.world_info("studio-course")
        self.assertEqual(info["map"], False)
        self.assertNotIn("origin", info)
        self.assertEqual(info["title"], "車のテストコース")
        self.assertIn("title: 車のテストコース", plain.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
