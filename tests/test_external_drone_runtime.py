from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import drone_one  # noqa: E402
import urban_simulation  # noqa: E402


class ExternalDroneRuntimeTest(unittest.TestCase):
    def test_launcher_uses_provider_service_and_config_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config"
            config.mkdir()
            executable = root / "service"
            executable.touch()
            runtime_dir = root / "runtime"
            runtime_dir.mkdir()
            marker = {"external_runtime": {
                "service_executable": str(executable), "config_dir": str(runtime_dir)}}
            (config / "mujoco-city-fleet.json").write_text(json.dumps(marker), encoding="utf-8")
            launcher = config / "launcher.json"
            launcher.write_text(json.dumps({"assets": [
                {"name": "drone-service-1", "command": "old", "args": ["old-config", "pdu.json"]}]}),
                encoding="utf-8")
            drone_one.patch_external_launcher(launcher, types.SimpleNamespace(recipe_config=config))
            [service] = json.loads(launcher.read_text(encoding="utf-8"))["assets"]
        self.assertEqual(service["command"], str(executable))
        self.assertEqual(service["args"], [str(runtime_dir), "pdu.json"])

    def test_runtime_placeholders_come_from_the_selected_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config"
            config.mkdir()
            provider = root / "provider"
            provider.mkdir()
            service = root / "api.json"
            service.touch()
            (config / "mujoco-city-fleet.json").write_text(json.dumps({"external_runtime": {
                "drone_root": str(provider), "service_config": str(service)}}), encoding="utf-8")
            paths = types.SimpleNamespace(recipe_config=config, recipe_validation=root / "validation")
            original = urban_simulation.multi_car.foundation_python if hasattr(urban_simulation, "multi_car") else None
            import multi_car
            saved = multi_car.foundation_python
            multi_car.foundation_python = lambda: Path(sys.executable)
            try:
                runtime = urban_simulation.drone_runtime(paths, root / "pdu.json")
            finally:
                multi_car.foundation_python = saved
        self.assertEqual(runtime.values["drone_root"], provider)
        self.assertEqual(runtime.values["service_config"], service)


if __name__ == "__main__":
    unittest.main()
