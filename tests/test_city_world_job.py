"""The City World job contract (schemas/city-world-job.yaml) and its checker
(tools/city_world_job.py), and the check at `urban_assets.py register-city`."""

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import city_world_job  # noqa: E402
import urban_assets  # noqa: E402

MJCF = """<mujoco model="test_world">
  <asset><hfield name="plateau_terrain" file="{hf}" size="50 40 1 0.1"/></asset>
  <worldbody>
    <geom name="plateau_ground" type="hfield" hfield="plateau_terrain"/>
    <body name="building_a"><geom name="geom_a" type="box" size="1 1 1" pos="5 5 1"/></body>
  </worldbody>
</mujoco>
"""


def make_job(root: Path, name: str = "test-city") -> Path:
    """A City World job that follows the contract; returns its receipt."""
    job = root / name
    world = job / "build/world"
    terrain = job / "build/components/terrain"
    for folder in (world, terrain, job / "viewer"):
        folder.mkdir(parents=True)
    hfield = terrain / "terrain.hf"
    hfield.write_bytes(struct.pack("<ii4f", 2, 2, 10.0, 10.0, 11.0, 11.0))
    (terrain / "terrain.xml").write_text("<mujoco/>", encoding="utf-8")
    (terrain / "terrain-receipt.json").write_text(json.dumps({"hfield": {
        "path": str(hfield), "sha256": hashlib.sha256(hfield.read_bytes()).hexdigest()}}), encoding="utf-8")
    mjcf = world / "city-world.xml"
    mjcf.write_text(MJCF.format(hf=hfield), encoding="utf-8")
    glb = world / "city-world.glb"
    glb.write_bytes(b"glTF-test")
    (job / "viewer/city-world-colliders.glb").write_bytes(b"glTF-test")
    receipt = world / "city-world-receipt.json"
    receipt.write_text(json.dumps({
        "schema_version": 1,
        "coordinate_frame": {
            "origin": {"latitude": 35.0, "longitude": 139.0, "altitude_offset_m": 10.0},
            "half_extent_m": {"north_south": 50.0, "east_west": 40.0},
            "coordinate_systems": {"mjcf": "X=North,Y=-East,Z=Up", "glb": "X=East,Y=Up,Z=-North"},
        },
        "mjcf": {"path": str(mjcf), "sha256": hashlib.sha256(mjcf.read_bytes()).hexdigest()},
        "glb": {"path": str(glb), "bytes": glb.stat().st_size},
        "components": {"terrain_xml": str(terrain / "terrain.xml"), "extra_mjcf": []},
    }), encoding="utf-8")
    return receipt


class CityWorldJobTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.receipt = make_job(self.root)
        self.job = self.receipt.parents[2]

    def problems(self, target=None, severity="error"):
        _job, found = city_world_job.check(target or self.job, workspace=self.root)
        return [f"{problem.where}: {problem.message}" for problem in found if problem.severity == severity]

    def edit_receipt(self, change):
        data = json.loads(self.receipt.read_text(encoding="utf-8"))
        change(data)
        self.receipt.write_text(json.dumps(data), encoding="utf-8")

    def test_a_job_that_follows_the_contract_passes_from_its_folder_or_receipt(self):
        self.assertEqual(self.problems(), [])
        self.assertEqual(self.problems(severity="warning"), [])
        job, _ = city_world_job.check(self.receipt, workspace=self.root)
        self.assertEqual(job, self.job.resolve())

    def test_the_layout(self):
        (self.job / "viewer/city-world-colliders.glb").unlink()
        self.assertEqual(len(self.problems()), 1)
        self.assertIn("viewer/city-world-colliders.glb: missing", self.problems()[0])
        stray = self.root / "elsewhere/city-world-receipt.json"
        stray.parent.mkdir()
        stray.write_text("{}", encoding="utf-8")
        self.assertIn("must sit at <job>/build/world/city-world-receipt.json", self.problems(stray)[0])

    def test_the_frames_must_match_exactly(self):
        self.edit_receipt(lambda data: data["coordinate_frame"]["coordinate_systems"].update(mjcf="X=East,Y=North,Z=Up"))
        (problem,) = self.problems()
        self.assertIn("coordinate_systems.mjcf: must be exactly 'X=North,Y=-East,Z=Up'", problem)

    def test_required_fields_types_and_absolute_paths(self):
        def change(data):
            del data["coordinate_frame"]["origin"]["latitude"]
            data["coordinate_frame"]["half_extent_m"]["east_west"] = 0
            data["glb"]["path"] = "build/world/city-world.glb"
            data["kind"] = "town"
        self.edit_receipt(change)
        found = "\n".join(self.problems())
        self.assertIn("coordinate_frame.origin.latitude: missing", found)
        self.assertIn("half_extent_m.east_west: must be greater than 0", found)
        self.assertIn("glb.path: must be an absolute path", found)
        self.assertIn("kind: must be one of ['city', 'plain']", found)

    def test_hashes_are_checked_when_given(self):
        mjcf = self.job / "build/world/city-world.xml"
        mjcf.write_text(mjcf.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        (problem,) = self.problems()
        self.assertIn("mjcf.sha256: does not match mjcf.path", problem)

    def test_the_terrain_hfield(self):
        hfield = self.job / "build/components/terrain/terrain.hf"
        hfield.write_bytes(struct.pack("<ii3f", 2, 2, 0.0, 0.0, 0.0))
        found = "\n".join(self.problems())
        self.assertIn("hfield.sha256: does not match", found)
        self.assertIn("should be 24 bytes, got 20", found)
        (self.job / "build/components/terrain/terrain-receipt.json").unlink()
        self.assertIn("no terrain-receipt.json beside", self.problems()[0])

    def test_the_world_mjcf(self):
        mjcf = self.job / "build/world/city-world.xml"
        text = MJCF.format(hf="x.hf").replace('type="hfield" hfield="plateau_terrain"', 'type="plane" size="1 1 1"')
        text = text.replace("<asset>", '<compiler angle="radian"/><asset>').replace("building_a", "car_0_body")
        mjcf.write_text(text, encoding="utf-8")
        self.edit_receipt(lambda data: data["mjcf"].pop("sha256"))
        found = "\n".join(self.problems())
        self.assertIn("<compiler> is not allowed at the top level", found)
        self.assertIn("an hfield geom in worldbody", found)
        self.assertIn("names reserved for vehicles: car_0_body", found)

    def test_optional_building_heights_and_the_workspace_are_warnings(self):
        buildings = self.job / "build/components/buildings/buildings.xml"
        buildings.parent.mkdir(parents=True)
        buildings.write_text("<mujoco/>", encoding="utf-8")
        self.edit_receipt(lambda data: data["components"].update(buildings_xml=str(buildings)))
        self.assertEqual(self.problems(), [])
        self.assertIn("city-max-clearance", self.problems(severity="warning")[0])
        _job, found = city_world_job.check(self.job, workspace=self.root / "other")
        self.assertIn("outside the workspace", found[-1].message)

    def test_the_schema_holds_the_frames_the_builders_require(self):
        schema = city_world_job.load_schema()
        self.assertEqual(schema["schema"], "hakoniwa.city-world-job/v1")
        self.assertEqual(schema["frames"], {"mjcf": "X=North,Y=-East,Z=Up", "glb": "X=East,Y=Up,Z=-North"})
        self.assertEqual(schema["layout"]["receipt"], "build/world/city-world-receipt.json")


class PlainWorldJobTest(unittest.TestCase):
    """tools/plain_world.py writes a job that follows the contract."""

    def test_a_plain_world_job_passes(self):
        try:
            import trimesh  # noqa: F401  (plain_world writes its GLB with it)
        except ImportError:
            self.skipTest("trimesh is not installed")
        from types import SimpleNamespace

        import plain_world

        world = SimpleNamespace(ground_size_m=(30.0, 20.0), ground_rgba=(0.4, 0.5, 0.4, 1.0), obstacles=[],
                                contact=SimpleNamespace(ground_friction=(1.0, 0.005, 0.0001)))
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(plain_world, "_fpv_generator", return_value=(lambda path: world, None)), \
                mock.patch.object(plain_world, "_obstacle_bodies", return_value=[]):
            course = Path(directory) / "course.yaml"
            course.write_text("{}", encoding="utf-8")
            receipt = plain_world.materialize(course, jobs_dir=Path(directory) / "jobs")
            job, problems = city_world_job.check(receipt, workspace=Path(directory))
            self.assertEqual([p.as_json() for p in problems], [])
            self.assertEqual(json.loads(receipt.read_text(encoding="utf-8"))["kind"], "plain")


class RegisterCityCheckTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.receipt = make_job(self.root)
        patch = mock.patch.object(urban_assets, "register_city", return_value=self.root / "city.asset.yaml")
        self.register = patch.start()
        self.addCleanup(patch.stop)

    def run_cli(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = urban_assets.main(["register-city", "--receipt", str(self.receipt), "--no-precompile", *extra])
        return code, out.getvalue() + err.getvalue()

    def test_a_job_that_follows_the_contract_is_registered(self):
        code, output = self.run_cli()
        self.assertEqual(code, 0, output)
        self.register.assert_called_once()

    def test_a_job_that_breaks_it_is_not_registered_unless_asked(self):
        (self.receipt.parents[2] / "viewer/city-world-colliders.glb").unlink()
        code, output = self.run_cli()
        self.assertEqual(code, 1)
        self.assertIn("Not registered", output)
        self.register.assert_not_called()
        code, _ = self.run_cli("--no-check")
        self.assertEqual(code, 0)
        self.register.assert_called_once()


if __name__ == "__main__":
    unittest.main()
