from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import urban_assets  # noqa: E402


class CityRegistrationLifecycleTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.work = Path(directory.name)
        self.user = self.work / "user-assets"
        self.jobs = self.work / "city-world-web-ui/runtime/jobs"

    def make_receipt(self, root: Path, city_id: str) -> Path:
        receipt = root / city_id / "build/world/city-world-receipt.json"
        receipt.parent.mkdir(parents=True)
        receipt.write_text("{}", encoding="utf-8")
        return receipt

    def test_unregister_removes_only_the_manifest(self):
        receipt = self.make_receipt(self.jobs, "hokkaido-01100-lat43.067-lon141.350")
        manifest = urban_assets.register_city(receipt, directory=self.user)

        removed = urban_assets.unregister_city("hokkaido-01100-lat43.067-lon141.350", directory=self.user)

        self.assertEqual(removed, manifest.resolve())
        self.assertFalse(manifest.exists())
        self.assertTrue(receipt.is_file())
        with self.assertRaisesRegex(urban_assets.AssetError, "not found"):
            urban_assets.unregister_city("hokkaido-01100-lat43.067-lon141.350", directory=self.user)

    def test_prune_unregisters_cities_whose_web_ui_job_was_deleted(self):
        deleted = self.make_receipt(self.jobs, "hokkaido-01100-lat43.067-lon141.350")
        kept = self.make_receipt(self.jobs, "hokkaido-01100-lat43.067-lon141.351")
        urban_assets.register_city(deleted, directory=self.user)
        urban_assets.register_city(kept, directory=self.user)
        deleted.unlink()

        removed = urban_assets.prune_missing_cities(self.jobs, directory=self.user)

        self.assertEqual(removed, ["hokkaido-01100-lat43.067-lon141.350"])
        remaining = [path.name for path in (self.user / "cities").iterdir()]
        self.assertEqual(remaining, ["hokkaido-01100-lat43.067-lon141.351.asset.yaml"])
        self.assertEqual(urban_assets.prune_missing_cities(self.jobs, directory=self.user), [])

    def test_prune_keeps_cities_registered_from_elsewhere(self):
        external = self.make_receipt(self.work / "external", "osaka")
        manifest = urban_assets.register_city(external, directory=self.user)
        external.unlink()

        self.assertEqual(urban_assets.prune_missing_cities(self.jobs, directory=self.user), [])
        self.assertTrue(manifest.exists())
        self.assertFalse(urban_assets.city_receipt_available(urban_assets.load_manifest(manifest)))

    def test_receipt_availability_follows_the_file(self):
        receipt = self.make_receipt(self.jobs, "tokyo")
        asset = urban_assets.load_manifest(urban_assets.register_city(receipt, directory=self.user))
        self.assertTrue(urban_assets.city_receipt_available(asset))
        receipt.unlink()
        self.assertFalse(urban_assets.city_receipt_available(asset))

    def test_prune_without_registered_cities_is_a_no_op(self):
        self.assertEqual(urban_assets.prune_missing_cities(self.jobs, directory=self.user), [])


if __name__ == "__main__":
    unittest.main()
