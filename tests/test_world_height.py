import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import world_height  # noqa: E402
import urban_composition  # noqa: E402
from tools import urban_mobility  # noqa: E402,F401  (puts the Business Pack tools on sys.path)
import multi_car  # noqa: E402

try:
    import mujoco  # noqa: F401
except ImportError:
    mujoco = None

# MJCF frame: X=North, Y=-East, Z=Up. A plane at z=0, a 5 m roof centered at
# north=10, and a visual-only slab above that roof.
WORLD = """
<mujoco>
  <worldbody>
    <geom name="ground" type="plane" size="100 100 0.1"/>
    <geom name="roof" type="box" pos="10 0 2.5" size="2 2 2.5"/>
    <geom name="banner" type="box" pos="10 0 9" size="2 2 0.1" contype="0" conaffinity="0"/>
  </worldbody>
</mujoco>
"""
# The same roof with no ground below the rest of the World.
FLOATING = """
<mujoco>
  <worldbody>
    <geom name="roof" type="box" pos="10 0 2.5" size="2 2 2.5"/>
  </worldbody>
</mujoco>
"""


class WorldHeightTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.work = Path(directory.name)

    def mjcf(self, text: str, name: str = "world.xml") -> Path:
        path = self.work / name
        path.write_text(text, encoding="utf-8")
        return path

    @unittest.skipIf(mujoco is None, "MuJoCo Python is not installed")
    def test_ray_returns_the_top_colliding_surface(self):
        ground = world_height.ray_ground(self.mjcf(WORLD), cache_dir=self.work / "cache")
        self.assertAlmostEqual(ground(0.0, 10.0), 5.0, places=6, msg="roof, below the visual-only banner")
        self.assertAlmostEqual(ground(0.0, 0.0), 0.0, places=6, msg="open ground")
        self.assertAlmostEqual(ground(-1.5, 10.0), 5.0, places=6, msg="east=-1.5 is MJCF y=+1.5, still on the roof")

    @unittest.skipIf(mujoco is None, "MuJoCo Python is not installed")
    def test_ray_without_geometry_below_is_an_error(self):
        ground = world_height.ray_ground(self.mjcf(FLOATING), cache_dir=self.work / "cache")
        with self.assertRaisesRegex(world_height.WorldHeightError, "no World geometry below"):
            ground(0.0, 50.0)

    @unittest.skipIf(mujoco is None, "MuJoCo Python is not installed")
    def test_compiled_world_is_reused_from_the_cache(self):
        path = self.mjcf(WORLD)
        cache = self.work / "cache"
        world_height.load_model(path, cache_dir=cache)
        with mock.patch.object(mujoco.MjModel, "from_xml_path", side_effect=AssertionError("recompiled")):
            model = world_height.load_model(path, cache_dir=cache)
        self.assertEqual(model.ngeom, 3)

    def test_fingerprint_follows_referenced_files(self):
        terrain = self.work / "terrain.hf"
        terrain.write_bytes(b"one")
        path = self.mjcf('<mujoco><asset><hfield name="t" file="terrain.hf" size="1 1 1 1"/></asset></mujoco>')
        before = world_height.fingerprint(path)
        terrain.write_bytes(b"two")
        self.assertNotEqual(world_height.fingerprint(path), before)

    def test_city_ground_uses_the_receipt_mjcf(self):
        receipt = self.work / "city-world-receipt.json"
        receipt.write_text(json.dumps({"mjcf": {"path": "city-world.xml"}}), encoding="utf-8")
        with mock.patch.object(world_height, "ray_ground", return_value="ray") as ray:
            self.assertEqual(urban_composition.city_ground(receipt), "ray")
        ray.assert_called_once_with(self.work / "city-world.xml")

    def test_city_ground_falls_back_to_terrain_without_mujoco(self):
        receipt = self.work / "city-world-receipt.json"
        receipt.write_text(json.dumps({"mjcf": {"path": "city-world.xml"}}), encoding="utf-8")
        stderr = io.StringIO()
        with mock.patch.object(world_height, "ray_ground", side_effect=ImportError("mujoco")), \
                mock.patch.object(multi_car, "terrain_height_mjcf", return_value=3.25) as terrain, \
                contextlib.redirect_stderr(stderr):
            ground = urban_composition.city_ground(receipt)
            self.assertEqual(ground(2.0, 7.0), 3.25)
        terrain.assert_called_once_with({"mjcf": {"path": "city-world.xml"}}, 7.0, -2.0)
        self.assertIn("rooftops are ignored", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
