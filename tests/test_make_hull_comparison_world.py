"""tools/make_hull_comparison_world.py: one bridge of a City World as one
convex hull (demo 3-3), on a small City World job made here."""

import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import city_world_job  # noqa: E402
import make_hull_comparison_world as hull  # noqa: E402

FACES = "0 1 2 5 4 3 0 3 4 0 4 1 1 4 5 1 5 2 2 5 3 2 3 0"
BRIDGE_A = "brid_75151184-6661-4762-9c6e-b10e53e886e2"
BRIDGE_B = "brid_f2425831-2c4d-4676-b351-267487ecb102"


def _prism(x: float) -> str:
    """A thin triangular piece as Envsim writes one (top triangle, then 2 cm below)."""
    top = [(x, 0, 5), (x + 2, 0, 5), (x, 2, 5)]
    return " ".join(f"{a} {b} {c}" for a, b, c in top + [(a, b, c - 0.02) for a, b, c in top])


def make_source(root: Path) -> Path:
    """An Envsim build with two bridges (A: 2 pieces, B: 1), its exported
    job (Urban's studio-cities layout), and the exported job's receipt."""
    build = root / "city-worlds/tocho/build"
    (build / "world").mkdir(parents=True)
    (build / "components/terrain").mkdir(parents=True)
    (build / "components/bridges/debug").mkdir(parents=True)
    (build / "components/terrain/terrain.hf").write_bytes(struct.pack("<ii4f", 2, 2, 0, 0, 0, 1))
    (build / "components/terrain/terrain.xml").write_text("<mujoco/>", encoding="utf-8")
    (build / "components/terrain/terrain-receipt.json").write_text(
        json.dumps({"hfield": {"path": str(build / "components/terrain/terrain.hf")}}), encoding="utf-8")
    pieces = [("bridge_piece_000000", BRIDGE_B, 20), ("bridge_piece_000001", BRIDGE_A, 0),
              ("bridge_piece_000002", BRIDGE_A, 3)]
    meshes = "\n".join(f'    <mesh name="{name}" vertex="{_prism(x)}" face="{FACES}" />' for name, _, x in pieces)
    geoms = "\n".join(f'    <geom name="{name}" type="mesh" mesh="{name}" rgba="0.32 0.48 0.62 1" contype="1" '
                      'conaffinity="0" />' for name, _, _ in pieces)
    (build / "world/city-world.xml").write_text(
        '<mujoco model="plateau_city_world">\n  <size nstack="1000" />\n  <asset>\n'
        '    <hfield name="plateau_terrain" file="../components/terrain/terrain.hf" size="10 10 1 1" />\n'
        f'{meshes}\n  </asset>\n  <worldbody>\n'
        '    <geom name="plateau_terrain" type="hfield" hfield="plateau_terrain" />\n'
        f'{geoms}\n  </worldbody>\n</mujoco>\n', encoding="utf-8")
    (build / "world/city-world.glb").write_bytes(b"glTF")
    (build / "components/bridges/debug/bridge-surfaces.json").write_text(json.dumps(
        {"pieces": [{"id": name, "bridge_id": bridge} for name, bridge, _ in pieces]}), encoding="utf-8")
    job = root / "studio-cities/tocho"
    (job / "build/world").mkdir(parents=True)
    (job / "viewer").mkdir()
    (job / "viewer/city-world-colliders.glb").write_bytes(b"colliders")
    (job / "viewer/city-world-colliders-receipt.json").write_text("{}", encoding="utf-8")
    receipt = job / "build/world/city-world-receipt.json"
    xml = build / "world/city-world.xml"
    receipt.write_text(json.dumps({
        "schema_version": 1,
        "coordinate_frame": {"schema_version": 1,
                             "origin": {"latitude": 35.0, "longitude": 139.0, "altitude_offset_m": 1.0},
                             "half_extent_m": {"north_south": 10.0, "east_west": 10.0},
                             "coordinate_systems": {"mjcf": "X=North,Y=-East,Z=Up", "glb": "X=East,Y=Up,Z=-North"}},
        "mjcf": {"path": str(xml), "sha256": hashlib.sha256(xml.read_bytes()).hexdigest()},
        "glb": {"path": str(build / "world/city-world.glb")},
        "components": {"terrain_xml": str(build / "components/terrain/terrain.xml")},
    }), encoding="utf-8")
    return receipt


class HullWorldTest(unittest.TestCase):
    def test_the_bridge_becomes_one_mesh_and_everything_else_stays(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_source(root)
            before = (root / "city-worlds/tocho/build/world/city-world.xml").read_text(encoding="utf-8")
            out = root / "comparison worlds/箱庭 hull"
            summary = hull.make_hull_world(source, "brid_75151184", out, "tocho")
            self.assertEqual(summary["bridge_id"], BRIDGE_A)
            self.assertEqual(summary["pieces_replaced"], 2)
            self.assertEqual(summary["hull_geom"], "bridge_hull_75151184")
            self.assertEqual(summary["hull_vertices"], 12)
            xml = (out / "build/world/city-world.xml").read_text(encoding="utf-8")
            self.assertNotIn("bridge_piece_000001", xml)
            self.assertNotIn("bridge_piece_000002", xml)
            self.assertIn('<geom name="bridge_piece_000000"', xml)   # the other bridge is kept
            self.assertEqual(xml.count('name="bridge_hull_75151184"'), 2)
            self.assertIn('<geom name="bridge_hull_75151184" type="mesh" mesh="bridge_hull_75151184" '
                          'rgba="0.32 0.48 0.62 1" contype="1" conaffinity="0" />', xml)
            mesh = next(line for line in xml.splitlines() if '<mesh name="bridge_hull_75151184"' in line)
            self.assertNotIn("face=", mesh)  # vertices only: MuJoCo takes the convex hull
            self.assertEqual(len(mesh.split('vertex="')[1].split('"')[0].split()), 36)
            # The source is not changed; the terrain stays relative and is carried along.
            self.assertEqual((root / "city-worlds/tocho/build/world/city-world.xml").read_text(encoding="utf-8"), before)
            self.assertIn('file="../components/terrain/terrain.hf"', xml)
            self.assertTrue((out / "build/components/terrain/terrain.hf").is_file())
            self.assertEqual((out / "viewer/city-world-colliders.glb").read_bytes(), b"colliders")
            receipt = json.loads((out / "build/world/city-world-receipt.json").read_text(encoding="utf-8"))
            self.assertEqual(Path(receipt["mjcf"]["path"]), out.resolve() / "build/world/city-world.xml")
            self.assertEqual(receipt["mjcf"]["sha256"],
                             hashlib.sha256((out / "build/world/city-world.xml").read_bytes()).hexdigest())
            self.assertEqual(receipt["glb"], json.loads(source.read_text(encoding="utf-8"))["glb"])
            self.assertEqual(receipt["comparison_only"]["source_city"], "tocho")
            self.assertEqual(receipt["comparison_only"]["bridge_id"], BRIDGE_A)
            # It is a City World job Urban registers.
            _job, problems = city_world_job.check(out / "build/world/city-world-receipt.json")
            self.assertEqual([problem for problem in problems if problem.severity == "error"], [])
            self.assertFalse(list(out.parent.glob("*.partial")))

    def test_the_result_compiles_in_mujoco(self):
        try:
            import mujoco
        except ImportError:
            self.skipTest("MuJoCo Python is not installed")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            out = root / "hull"
            hull.make_hull_world(make_source(root), BRIDGE_A, out)
            model = mujoco.MjModel.from_xml_path(str(out / "build/world/city-world.xml"))
            names = {model.geom(index).name for index in range(model.ngeom)}
            self.assertEqual(names, {"plateau_terrain", "bridge_piece_000000", "bridge_hull_75151184"})

    def test_an_unknown_or_ambiguous_bridge_and_an_existing_folder_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_source(root)
            with self.assertRaisesRegex(hull.HullWorldError, "見つかりません.*brid_75151184"):
                hull.make_hull_world(source, "brid_00000000", root / "a")
            with self.assertRaisesRegex(hull.HullWorldError, "2 個"):
                hull.make_hull_world(source, "brid_", root / "a")
            self.assertFalse((root / "a").exists())
            hull.make_hull_world(source, "brid_7515", root / "a")
            with self.assertRaisesRegex(hull.HullWorldError, "--force"):
                hull.make_hull_world(source, "brid_7515", root / "a")
            hull.make_hull_world(source, "brid_f242", root / "a", force=True)
            self.assertIn("bridge_hull_f2425831", (root / "a/build/world/city-world.xml").read_text(encoding="utf-8"))

    def test_hull_names_and_relative_files(self):
        self.assertEqual(hull.hull_name(BRIDGE_A), "bridge_hull_75151184")
        self.assertEqual(hull.relative_files('<a file="../x.hf"/><b file="/abs/y"/><c file="C:\\d\\z"/>'),
                         ["../x.hf"])


if __name__ == "__main__":
    unittest.main()
