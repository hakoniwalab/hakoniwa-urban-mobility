import json
import math
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import asset_preview  # noqa: E402
import urban_assets  # noqa: E402


class PreviewFixture(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def write(self, relative, data):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
        return path

    def asset(self, preview):
        manifest = self.root / "vehicle.asset.yaml"
        return urban_assets.Asset(id="vehicle", kind="vehicle", path=manifest, data={"preview": preview})


class AssetPreviewTest(PreviewFixture):
    def test_a_drone_type_places_each_rotor_with_its_scale(self):
        self.write("models/frame.glb", "glb")
        self.write("models/prop.glb", "glb")
        self.write("config/types.json", {"hexa": {
            "model": {"model_path": "../models/frame.glb", "pos": [0, 0, 0], "hpr": [0, 0, 0]},
            "rotors": [{
                "model": {"model_path": "../models/prop.glb", "pos": [0, 0, 0.1], "hpr": [0, 0, 0], "scale": 1.5},
                "pos": [0.5, 0.25, 0], "hpr": [0, 0, 90],
            }],
        }})
        parts = asset_preview.preview_parts(self.asset({
            "format": "drone-type", "path": str(self.root / "config/types.json"), "type": "hexa",
        }))
        self.assertEqual([part["path"].name for part in parts], ["frame.glb", "prop.glb"])
        self.assertEqual({part["basis"] for part in parts}, {"three"})
        rotor = parts[1]
        self.assertEqual(rotor["position"], [0.5, 0.25, 0.1])
        self.assertEqual(rotor["scale"], 1.5)
        # hpr is in degrees: a 90° yaw.
        half = math.sqrt(0.5)
        self.assertEqual(rotor["quaternion"], [0.0, 0.0, round(half, 6), round(half, 6)])

    def test_a_view_model_chains_mounts_in_radians(self):
        for name in ("body", "wheel"):
            self.write(f"model/parts/{name}.glb", "glb")
        view = self.write("model/view-model.json", {
            "format": "hako_viewer_model",
            "coordinate_system": "mujoco",
            "assets": [{"id": "body", "path": "parts/body.glb"}, {"id": "wheel", "path": "parts/wheel.glb"}],
            "base": {"name": "vehicle", "asset": "body", "mount": {"xyz": [0, 0, 0], "rpy": [0, 0, 0]}},
            # The wheel comes before its parent in the file.
            "movable_parts": [
                {"name": "wheel", "parent": "steer", "asset": "wheel", "mount": {"xyz": [1.0, 0, 0], "rpy": [0, 0, 0]}},
                {"name": "steer", "parent": "vehicle", "mount": {"xyz": [0.5, 0.5, 0], "rpy": [0, 0, math.pi / 2]}},
            ],
        })
        parts = asset_preview.preview_parts(self.asset({"format": "view-model", "path": str(view)}))
        self.assertEqual([part["path"].name for part in parts], ["body.glb", "wheel.glb"])
        self.assertEqual({part["basis"] for part in parts}, {"flu"})
        # The steer mount turns 90° left, so the wheel's 1 m forward lands 1 m to the left.
        self.assertEqual(parts[1]["position"], [0.5, 1.5, 0.0])

    def test_an_asset_without_a_preview_has_no_parts(self):
        self.assertEqual(asset_preview.preview_parts(self.asset(None)), [])

    def test_missing_models_are_reported(self):
        self.write("config/types.json", {"quad": {"model": {"model_path": "missing.glb"}, "rotors": []}})
        with self.assertRaisesRegex(asset_preview.PreviewError, "not found"):
            asset_preview.preview_parts(self.asset({
                "format": "drone-type", "path": str(self.root / "config/types.json"), "type": "quad",
            }))

    def test_invalid_previews_are_rejected(self):
        for preview, pattern in (
            ({"format": "obj", "path": "x"}, "preview.format"),
            ({"format": "view-model"}, "no path"),
            ({"format": "drone-type", "path": "x"}, "needs a type"),
        ):
            with self.subTest(preview=preview), self.assertRaisesRegex(asset_preview.PreviewError, pattern):
                asset_preview.validate(preview, "vehicle")

    def test_the_urban_vehicles_declare_previews(self):
        catalog = urban_assets.catalog()
        for asset_id, count in (("eams-hexa", 7), ("drone-core-quad", 5), ("golf-cart", 5)):
            with self.subTest(asset=asset_id):
                self.assertEqual(len(asset_preview.preview_parts(catalog[asset_id])), count)


if __name__ == "__main__":
    unittest.main()
