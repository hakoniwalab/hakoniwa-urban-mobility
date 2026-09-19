import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tools import urban_lifecycle as lifecycle


class UrbanLifecycleTest(unittest.TestCase):
    def spec(self, root: Path) -> lifecycle.LifecycleSpec:
        return lifecycle.LifecycleSpec(
            recipe_id="urban-mobility-rc",
            recipe_root=root,
            launcher=root / "config/launcher.json",
            session=root / "runtime/launcher-session.json",
            viewer_url="http://127.0.0.1:8000/viewer",
        )

    def write_session(self, spec, *, launcher=None, state="RUNNING", pid=123):
        spec.session.parent.mkdir(parents=True, exist_ok=True)
        spec.session.write_text(
            json.dumps({
                "state": state,
                "pid": pid,
                "launch_file": str(launcher or spec.launcher),
            }),
            encoding="utf-8",
        )

    def test_rejects_session_owned_by_another_recipe(self):
        with tempfile.TemporaryDirectory() as directory:
            spec = self.spec(Path(directory))
            self.write_session(spec, launcher=Path(directory) / "other/launcher.json")
            with self.assertRaisesRegex(lifecycle.LifecycleError, "does not belong"):
                lifecycle.read_session(spec)

    def test_start_rejects_an_existing_running_session(self):
        with tempfile.TemporaryDirectory() as directory:
            spec = self.spec(Path(directory))
            self.write_session(spec)
            with (
                mock.patch.object(lifecycle, "process_alive", return_value=True),
                self.assertRaisesRegex(lifecycle.LifecycleError, "already RUNNING"),
            ):
                lifecycle.preflight_start(spec)

    def test_start_rejects_ports_owned_by_another_recipe(self):
        with tempfile.TemporaryDirectory() as directory:
            spec = self.spec(Path(directory))
            with (
                mock.patch.object(lifecycle, "listening", side_effect=lambda port: port == 8000),
                self.assertRaisesRegex(lifecycle.LifecycleError, "8000"),
            ):
                lifecycle.preflight_start(spec)

    def test_viewer_requires_own_running_session_and_http(self):
        with tempfile.TemporaryDirectory() as directory:
            spec = self.spec(Path(directory))
            self.write_session(spec)
            with (
                mock.patch.object(lifecycle, "process_alive", return_value=True),
                mock.patch.object(lifecycle, "http_ready", return_value=False),
                self.assertRaisesRegex(lifecycle.LifecycleError, "not ready"),
            ):
                lifecycle.require_viewer_ready(spec)

    def test_status_keeps_launcher_running_and_demo_ready_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            spec = self.spec(Path(directory))
            self.write_session(spec)
            with (
                mock.patch.object(lifecycle, "process_alive", return_value=True),
                mock.patch.object(lifecycle, "http_ready", return_value=True),
                mock.patch.object(lifecycle, "listening", return_value=False),
            ):
                report = lifecycle.status_report(spec)
            self.assertTrue(report["launcher_running"])
            self.assertTrue(report["http_ready"])
            self.assertFalse(report["websocket_listening"])
            self.assertFalse(report["demo_ready"])

    def test_verify_stopped_rejects_a_residual_listener(self):
        with tempfile.TemporaryDirectory() as directory:
            spec = self.spec(Path(directory))
            self.write_session(spec, state="TERMINATED")
            with (
                mock.patch.object(lifecycle, "listening", side_effect=lambda port: port == 8765),
                self.assertRaisesRegex(lifecycle.LifecycleError, "8765"),
            ):
                lifecycle.verify_stopped(spec)


if __name__ == "__main__":
    unittest.main()
