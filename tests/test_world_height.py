import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import world_height  # noqa: E402
import urban_composition  # noqa: E402
from tools import urban_mobility  # noqa: E402,F401  (puts the Business Pack tools on sys.path)
import multi_car  # noqa: E402

try:
    import mujoco
except ImportError:
    mujoco = None

needs_mujoco = unittest.skipIf(mujoco is None, "MuJoCo Python is not installed")

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
# A road slab (a surface 2 cm thick) with a visual-only line painted on it, the
# line's bottom exactly on the slab's top (as an authoring tool writes a lane
# marking): the ground under the line is the slab's top.
# Turned in a body (as an exported World stands in its frame), a ray started
# inside the line meets the line's underside, and one started just below that
# is inside the road and meets the road's underside.
PAINTED_ROAD = """
<mujoco>
  <worldbody>
    <geom name="ground" type="plane" size="100 100 0.1"/>
    <body quat="0.7071067811865476 0 0 -0.7071067811865476"><body pos="-20 0 0">
      <geom name="road" type="box" pos="0 0 0.01" size="3 3 0.01"/>
      <geom name="line" type="box" pos="0 0 0.022" size="0.075 3 0.002" contype="0" conaffinity="0" group="2"/>
    </body></body>
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


def city(mesh_count: int) -> str:
    """A City-like World: an hfield terrain, boxes, and mesh 'buildings' of rising height."""
    meshes, geoms = [], []
    for index in range(mesh_count):
        north, top = 10.0 * index, 1.0 + index
        vertices = " ".join(
            f"{north + dx} {dy} {z}" for z in (0.0, top) for dx in (-1, 1) for dy in (-1, 1)
        )
        meshes.append(f'<mesh name="bldg_{index}" vertex="{vertices}"/>')
        geoms.append(f'<geom type="mesh" mesh="bldg_{index}"/>')
    return f"""
<mujoco>
  <asset>
    <hfield name="terrain" nrow="2" ncol="2" elevation="0 0 0 0" size="500 500 1 0.1"/>
    {''.join(meshes)}
  </asset>
  <worldbody>
    <geom type="hfield" hfield="terrain"/>
    <geom type="box" pos="-20 0 0.5" size="1 1 0.5"/>
    <geom type="box" pos="-30 0 1.5" size="1 1 1.5"/>
    {''.join(geoms)}
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

    def quiet(self):
        return contextlib.redirect_stdout(io.StringIO())

    @needs_mujoco
    def test_ray_returns_the_top_colliding_surface(self):
        with self.quiet():
            ground = world_height.ray_ground(self.mjcf(WORLD), cache_dir=self.work / "cache")
        self.assertAlmostEqual(ground(0.0, 10.0), 5.0, places=6, msg="roof, below the visual-only banner")
        self.assertAlmostEqual(ground(0.0, 0.0), 0.0, places=6, msg="open ground")
        self.assertAlmostEqual(ground(-1.5, 10.0), 5.0, places=6, msg="east=-1.5 is MJCF y=+1.5, still on the roof")

    @needs_mujoco
    def test_a_line_painted_on_a_road_leaves_the_road_as_the_ground(self):
        with self.quiet():
            ground = world_height.ray_ground(self.mjcf(PAINTED_ROAD), cache_dir=self.work / "cache")
        # MJCF y=20 is east=-20: under the line, and beside it on the road.
        self.assertAlmostEqual(ground(-20.0, 0.0), 0.02, places=6, msg="under the visual-only line: the road's top")
        self.assertAlmostEqual(ground(-21.0, 0.0), 0.02, places=6, msg="on the road beside the line")
        self.assertAlmostEqual(ground(0.0, 0.0), 0.0, places=6, msg="off the road")

    @needs_mujoco
    def test_ray_without_geometry_below_is_an_error(self):
        with self.quiet():
            ground = world_height.ray_ground(self.mjcf(FLOATING), cache_dir=self.work / "cache")
        with self.assertRaisesRegex(world_height.WorldHeightError, "no World geometry below"):
            ground(0.0, 50.0)

    @needs_mujoco
    def test_compiled_world_is_reused_from_the_cache(self):
        path = self.mjcf(WORLD)
        cache = self.work / "cache"
        with self.quiet():
            world_height.load_models(path, cache_dir=cache)
        output = io.StringIO()
        with mock.patch.object(mujoco.MjModel, "from_xml_path", side_effect=AssertionError("recompiled")), \
                contextlib.redirect_stdout(output):
            [model] = world_height.load_models(path, cache_dir=cache)
        self.assertEqual(model.ngeom, 3)
        self.assertIn("loaded from cache", output.getvalue())

    @needs_mujoco
    def test_another_mujoco_version_does_not_reuse_the_cache(self):
        path = self.mjcf(WORLD)
        cache = self.work / "cache"
        with self.quiet():
            world_height.load_models(path, cache_dir=cache)
        output = io.StringIO()
        with mock.patch.object(mujoco, "__version__", "0.0.0"), contextlib.redirect_stdout(output):
            world_height.load_models(path, cache_dir=cache)
        self.assertIn("compiling", output.getvalue())
        self.assertEqual(len([entry for entry in cache.iterdir() if entry.is_dir()]), 2)

    def test_split_keeps_every_geom_once_and_the_terrain_in_chunk_zero(self):
        chunks = world_height.split_world(self.mjcf(city(7)), chunks=3)
        self.assertEqual(len(chunks), 3)
        roots = [ET.fromstring(text) for text in chunks]
        geoms = [geom for root in roots for geom in root.iter("geom")]
        self.assertEqual(len(geoms), 7 + 3, "7 meshes, 2 boxes, 1 terrain")
        self.assertEqual(sorted(g.get("mesh") for g in geoms if g.get("mesh")), sorted(f"bldg_{i}" for i in range(7)))
        self.assertEqual([len(list(root.iter("hfield"))) for root in roots], [1, 0, 0])
        for root in roots:
            meshes = {mesh.get("name") for mesh in root.iter("mesh")}
            used = {geom.get("mesh") for geom in root.iter("geom") if geom.get("mesh")}
            self.assertEqual(meshes, used, "each chunk carries exactly the meshes its geoms use")

    def test_split_makes_file_references_absolute(self):
        (self.work / "terrain").mkdir()
        (self.work / "terrain/terrain.hf").write_bytes(b"")
        text = '<mujoco><asset><hfield name="t" file="terrain\\terrain.hf" size="1 1 1 1"/></asset></mujoco>'
        [chunk] = world_height.split_world(self.mjcf(text))
        reference = ET.fromstring(chunk).find("asset/hfield").get("file")
        self.assertEqual(Path(reference), (self.work / "terrain/terrain.hf").resolve())

    def test_small_worlds_compile_as_one_chunk(self):
        self.assertEqual(world_height.chunk_count(500, cpu_count=22), 1)
        self.assertEqual(world_height.chunk_count(18088, cpu_count=22), 8)
        self.assertEqual(world_height.chunk_count(18088, cpu_count=4), 4)

    @needs_mujoco
    def test_parallel_chunks_give_the_same_heights_and_report_progress(self):
        path = self.mjcf(city(6))
        with self.quiet():
            whole = world_height.WorldHeight([mujoco.MjModel.from_xml_path(str(path))])
        output = io.StringIO()
        with mock.patch.object(world_height, "chunk_count", return_value=3), contextlib.redirect_stdout(output):
            chunked = world_height.ray_ground(path, cache_dir=self.work / "cache")
        self.assertEqual(len(chunked.models), 3)
        for east, north in [(0.0, 0.0), (0.0, 30.0), (0.0, 50.0), (0.0, -30.0), (0.0, -20.0), (7.0, 7.0)]:
            self.assertAlmostEqual(chunked(east, north), whole(east, north), places=6, msg=(east, north))
        events = [
            json.loads(line.split(" ", 1)[1])
            for line in output.getvalue().splitlines() if line.startswith("[HAKO_PROGRESS] ")
        ]
        self.assertEqual([(e["phase"], e["current"], e["total"]) for e in events],
                         [("world_height_model", step, 3) for step in range(4)])

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
