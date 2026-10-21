from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import drone_one  # noqa: E402
import urban_simulation  # noqa: E402


class ExternalDroneRuntimeTest(unittest.TestCase):
    def test_external_jointless_body_names_survive_city_fusion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            body_model = root / "body.xml"
            city_model = root / "city.xml"
            body_model.write_text(
                "<mujoco><worldbody><body name='ProviderDrone-1'><freejoint/>"
                "<geom type='sphere' size='.1' mass='1'/>"
                "<body name='prop1'/><body name='prop2'/><body name='prop3'/>"
                "<body name='prop4'/></body></worldbody></mujoco>",
                encoding="utf-8",
            )
            city_model.write_text(
                "<mujoco><worldbody><body name='static-city'><body name='block'>"
                "<geom type='box' size='.1 .1 .1'/></body></body></worldbody></mujoco>",
                encoding="utf-8",
            )

            names = drone_one._top_level_body_names(body_model)
            output = root / "composed.xml"
            drone_one.compose_drone_city_model(body_model, city_model, output, names)

            model = ET.parse(output).getroot()
            self.assertEqual(model.find("./option/flag").get("sleep"), "enable")
            self.assertEqual(names, ["ProviderDrone-1"])
            for name in names:
                self.assertEqual(
                    model.find(f"./worldbody/body[@name='{name}']").get("sleep"),
                    "never",
                )
            mujoco = __import__("mujoco")
            compiled = mujoco.MjModel.from_xml_path(str(output))
            body_names = {mujoco.mj_id2name(
                compiled, mujoco.mjtObj.mjOBJ_BODY, index
            ) for index in range(compiled.nbody)}
            self.assertTrue(
                {"ProviderDrone-1", "prop1", "prop2", "prop3", "prop4"}
                <= body_names
            )
            self.assertEqual(compiled.nbody, 6)

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
