"""tools/hakoniwa_runtime.py: the Launcher runtime options and the explicit recovery
(hakoniwa-core-cpp#62: the fail-stop Launcher refuses runtime.cleanup_mmap_on_start)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import hakoniwa_runtime  # noqa: E402


class LauncherRuntimeTest(unittest.TestCase):
    def test_fail_stop_launcher_gets_health_checks_not_the_cleanup(self):
        with mock.patch.object(hakoniwa_runtime, "launcher_kind", return_value="fail-stop"):
            runtime = hakoniwa_runtime.launcher_runtime(Path("/python"))
        self.assertNotIn("cleanup_mmap_on_start", runtime)
        self.assertGreaterEqual(runtime["health_check_timeout_sec"], 3.0)  # Windows process start

    def test_former_launcher_keeps_the_cleanup(self):
        with mock.patch.object(hakoniwa_runtime, "launcher_kind", return_value="cleanup"):
            self.assertEqual(hakoniwa_runtime.launcher_runtime(Path("/python")),
                             {"cleanup_mmap_on_start": True})

    def test_too_old_launcher_is_refused(self):
        with mock.patch.object(hakoniwa_runtime, "launcher_kind", return_value="none"):
            with self.assertRaises(hakoniwa_runtime.HakoniwaRuntimeError):
                hakoniwa_runtime.launcher_runtime(Path("/python"))


class RecoverTest(unittest.TestCase):
    def runtime(self, directory: Path) -> Path:
        for name in ("mmap-0x100.bin", "mmap-0xff.bin", "flock.bin", "pdu_init.lock",
                     "pro_init.lock", "keep.txt"):
            (directory / name).write_text("x", encoding="utf-8")
        config = directory / "cpp_core_config.json"
        config.write_text(json.dumps({"core_mmap_path": str(directory)}), encoding="utf-8")
        return config

    def test_without_hako_cmd_recover_removes_only_the_runtime_files_lock_last(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            config = self.runtime(directory)
            with mock.patch.object(hakoniwa_runtime, "hako_cmd", return_value=None):
                planned = hakoniwa_runtime.recover(hakoniwa_runtime.runtime_directory(config), dry_run=True)
                self.assertTrue(planned[-1].endswith("flock.bin"))
                self.assertEqual(len(planned), 5)
                hakoniwa_runtime.recover(hakoniwa_runtime.runtime_directory(config))
            self.assertEqual(sorted(p.name for p in directory.iterdir()), ["cpp_core_config.json", "keep.txt"])

    def test_an_older_hako_cmd_has_no_recover(self):
        with mock.patch.object(hakoniwa_runtime.subprocess, "run",
                               return_value=mock.Mock(stdout="hako-cmd version 1.0.1\n", returncode=0)):
            self.assertEqual(hakoniwa_runtime.hako_cmd_version("hako-cmd"), (1, 0, 1))
            self.assertFalse(hakoniwa_runtime._supports("hako-cmd", "recover"))

    def test_check_without_hako_cmd_is_unknown(self):
        with mock.patch.object(hakoniwa_runtime, "hako_cmd", return_value=None):
            self.assertEqual(hakoniwa_runtime.check()["status"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
