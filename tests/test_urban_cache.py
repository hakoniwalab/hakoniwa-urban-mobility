import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import urban_assets  # noqa: E402
import urban_cache  # noqa: E402
import world_height  # noqa: E402


class UrbanCachePruneTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.work = Path(directory.name)
        self.cache = self.work / "urban/cache"
        self.height = self.cache / "world-height"
        self.plain = self.cache / "plain-world"
        self.height.mkdir(parents=True)
        self.plain.mkdir(parents=True)
        self.jobs = self.work / "city-world-web-ui/runtime/jobs"
        self.user = self.work / "user-assets"

    def city(self, city_id: str, *, register: bool = True) -> Path:
        world = self.jobs / city_id / "build/world"
        world.mkdir(parents=True)
        (world / "terrain.bin").write_bytes(b"terrain")
        mjcf = world / "city-world.xml"
        mjcf.write_text(f'<mujoco model="{city_id}"><asset><hfield file="terrain.bin"/></asset></mujoco>', encoding="utf-8")
        receipt = world / "city-world-receipt.json"
        receipt.write_text("{}", encoding="utf-8")
        if register:
            urban_assets.register_city(receipt, directory=self.user)
        return mjcf

    def entry(self, mjcf: Path, version: str, fingerprint: str | None = None) -> Path:
        key = f"{fingerprint or world_height.fingerprint(mjcf)}-mujoco-{version}"
        path = self.height / key
        path.mkdir()
        (path / "chunk-00.mjb").write_bytes(b"m" * 100)
        (path / "manifest.json").write_text(
            json.dumps({"layout": 2, "mjcf": str(mjcf), "chunks": ["chunk-00.mjb"]}), encoding="utf-8"
        )
        return path

    def prune(self, **options):
        options.setdefault("mujoco_version", "3.13.0")
        return urban_cache.prune(
            cache_root=self.cache, jobs_root=self.jobs,
            assets=urban_assets.catalog([self.user]), **options,
        )

    def decisions(self, report) -> dict:
        return {
            Path(entry["path"]).name: (entry["remove"], entry["reason"])
            for entry in report["world_height"] + report["plain_world"]
        }

    def test_registered_city_of_the_current_version_is_kept(self):
        current = self.entry(self.city("tokyo"), "3.13.0")

        report = self.prune(apply=True)

        self.assertEqual(self.decisions(report)[current.name], (False, "registered City"))
        self.assertTrue(current.is_dir())
        self.assertEqual(report["reclaimable_bytes"], 0)

    def test_deleted_and_unregistered_cities_are_removed(self):
        deleted = self.entry(self.city("deleted"), "3.13.0")
        unregistered = self.entry(self.city("unregistered", register=False), "3.13.0")
        (self.user / "cities/deleted.asset.yaml").unlink()
        import shutil
        shutil.rmtree(self.jobs / "deleted")

        report = self.prune(apply=True)

        decisions = self.decisions(report)
        self.assertEqual(decisions[deleted.name], (True, "City World job deleted"))
        self.assertEqual(decisions[unregistered.name], (True, "City not registered"))
        self.assertFalse(deleted.exists())
        self.assertFalse(unregistered.exists())
        self.assertTrue((self.jobs / "unregistered/build/world/city-world.xml").is_file())

    def test_regenerated_world_drops_the_old_fingerprint(self):
        mjcf = self.city("tokyo")
        stale = self.entry(mjcf, "3.13.0", fingerprint="0" * 64)
        current = self.entry(mjcf, "3.13.0")

        decisions = self.decisions(self.prune())

        self.assertEqual(decisions[stale.name], (True, "World regenerated since"))
        self.assertFalse(decisions[current.name][0])

    def test_other_mujoco_version_is_removed_only_once_superseded(self):
        tokyo = self.city("tokyo")
        osaka = self.city("osaka")
        superseded = self.entry(tokyo, "3.10.0")
        self.entry(tokyo, "3.13.0")
        only = self.entry(osaka, "3.10.0")

        decisions = self.decisions(self.prune(apply=True))

        self.assertTrue(decisions[superseded.name][0])
        self.assertIn("superseded", decisions[superseded.name][1])
        self.assertFalse(decisions[only.name][0])
        self.assertTrue(only.is_dir())
        # An interpreter with another MuJoCo (outside the Workspace) keeps the Workspace's caches.
        decisions = self.decisions(self.prune(mujoco_version="3.7.0"))
        self.assertFalse(any(remove for remove, _ in decisions.values()))

    def test_other_mujoco_versions_flag_removes_them_all(self):
        only = self.entry(self.city("osaka"), "3.10.0")

        self.prune(apply=True, other_versions=True)

        self.assertFalse(only.exists())

    def test_dry_run_is_the_default(self):
        stale = self.entry(self.city("tokyo"), "3.13.0", fingerprint="0" * 64)

        report = self.prune()

        self.assertFalse(report["applied"])
        self.assertEqual(report["removed"], [str(stale)])
        self.assertTrue(stale.is_dir())

    def test_partial_compiles_are_removed_only_when_abandoned(self):
        fresh = self.height / f"{'1' * 64}-mujoco-3.13.0.partial"
        old = self.height / f"{'2' * 64}-mujoco-3.13.0.partial"
        fresh.mkdir()
        old.mkdir()
        hours_ago = time.time() - 2 * urban_cache.PARTIAL_GRACE_SEC
        os.utime(old, (hours_ago, hours_ago))

        self.prune(apply=True)

        self.assertTrue(fresh.is_dir())
        self.assertFalse(old.exists())

    def test_plain_world_cache_is_removed_only_on_request(self):
        generated = self.plain / f"{'a' * 64}.xml"
        generated.write_text("<mujoco/>", encoding="utf-8")

        self.prune(apply=True)
        self.assertTrue(generated.is_file())
        self.prune(apply=True, plain_world=True)
        self.assertFalse(generated.exists())

    def test_worlds_outside_the_jobs_folder_follow_their_mjcf(self):
        world = self.work / "urban/worlds/plain-ground/world.xml"
        world.parent.mkdir(parents=True)
        world.write_text("<mujoco/>", encoding="utf-8")
        kept = self.entry(world, "3.13.0")
        gone = self.entry(self.work / "missing.xml", "3.13.0", fingerprint="3" * 64)

        decisions = self.decisions(self.prune())

        self.assertEqual(decisions[kept.name], (False, "current World"))
        self.assertEqual(decisions[gone.name], (True, "World MJCF deleted"))

    def test_unknown_names_are_left_untouched(self):
        (self.height / "notes.txt").write_text("keep", encoding="utf-8")

        report = self.prune(apply=True)

        self.assertTrue((self.height / "notes.txt").is_file())
        self.assertEqual(report["removed"], [])


class PruneCacheCommandTest(unittest.TestCase):
    def test_the_command_is_a_dry_run_unless_apply(self):
        import contextlib
        import io
        from unittest import mock

        with tempfile.TemporaryDirectory() as directory:
            stale = Path(directory) / "world-height" / f"{'0' * 64}-mujoco-3.13.0"
            stale.mkdir(parents=True)
            (stale / "manifest.json").write_text(json.dumps({"mjcf": "/nowhere/world.xml"}), encoding="utf-8")
            with mock.patch.object(urban_cache, "CACHE_ROOT", Path(directory)), \
                    mock.patch.object(urban_assets, "catalog", return_value={}), \
                    mock.patch.object(urban_cache, "current_mujoco_version", return_value="3.13.0"):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(urban_assets.main(["prune-cache", "--json"]), 0)
                report = json.loads(output.getvalue())
                self.assertFalse(report["applied"])
                self.assertTrue(stale.is_dir())
                with contextlib.redirect_stdout(io.StringIO()):
                    urban_assets.main(["prune-cache", "--apply"])
                self.assertFalse(stale.exists())


if __name__ == "__main__":
    unittest.main()
