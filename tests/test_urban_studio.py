import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import urban_studio  # noqa: E402

# Stands in for tools/urban_simulation.py: prints a line, a progress event, and exits.
FAKE_SIMULATION = """
import sys, time
print("running", sys.argv[1], flush=True)
print('[HAKO_PROGRESS] {"phase":"world_height_model","current":1,"total":4}', flush=True)
time.sleep(float(__import__("os").environ.get("FAKE_DELAY", "0")))
print("done", flush=True)
sys.exit(3 if sys.argv[1] == "stop" else 0)
"""


class StudioTestBase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.work = Path(directory.name)
        patch = mock.patch.object(urban_studio, "USER_COMPOSITIONS", self.work / "compositions")
        patch.start()
        self.addCleanup(patch.stop)
        fake = self.work / "fake_simulation.py"
        fake.write_text(FAKE_SIMULATION, encoding="utf-8")
        self.server = urban_studio.make_server(0, urban_studio.JobRunner(simulation=fake))
        self.port = self.server.server_address[1]
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, method: str, path: str, body=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = Request(f"http://127.0.0.1:{self.port}{path}", data=data, method=method,
                          headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())

    def composition(self, world="plain-ground", **vehicle):
        entry = {"name": "Drone-1", "asset": "eams-hexa", "control": "rc",
                 "spawn": {"east_m": 1.0, "north_m": 2.0, "yaw_deg": 0.0}}
        entry.update(vehicle)
        return {"world": world, "vehicles": [entry]}

    def wait(self, job_id: str) -> dict:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            _, job = self.call("GET", f"/api/jobs/{job_id}")
            if job["state"] != "running":
                return job
            time.sleep(0.05)
        self.fail("job did not finish")


class StudioServerTest(StudioTestBase):
    def test_frontend_is_served(self):
        with urlopen(f"http://127.0.0.1:{self.port}/", timeout=10) as response:
            self.assertIn(b"Urban Studio", response.read())
            self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_asset_catalog_lists_worlds_before_vehicles_with_their_controls(self):
        status, assets = self.call("GET", "/api/assets")
        self.assertEqual(status, 200)
        kinds = [asset["kind"] for asset in assets]
        self.assertEqual(kinds, sorted(kinds, key=lambda kind: {"city": 0, "plain": 1, "vehicle": 2}[kind]))
        cart = next(asset for asset in assets if asset["id"] == "golf-cart")
        self.assertEqual(set(cart["controls"]), {"rc", "api"})
        self.assertIn("scenario", cart["controls"]["api"]["params"])
        hexa = next(asset for asset in assets if asset["id"] == "eams-hexa")
        self.assertIn("drone-mirror", hexa["interactions"])

    def test_examples_are_listed_read_only_and_saving_makes_an_editable_copy(self):
        _, listed = self.call("GET", "/api/compositions")
        example = next(item for item in listed if item["id"] == "plain-hexa-rc")
        self.assertFalse(example["editable"])
        _, loaded = self.call("GET", "/api/compositions/plain-hexa-rc")
        self.assertFalse(loaded["editable"])
        status, plan = self.call("PUT", "/api/compositions/plain-hexa-rc", loaded["composition"])
        self.assertEqual((status, plan["route"]), (200, "drone"))
        _, listed = self.call("GET", "/api/compositions")
        self.assertTrue(next(item for item in listed if item["id"] == "plain-hexa-rc")["editable"])

    def test_save_validates_and_writes_the_id_and_schema(self):
        status, plan = self.call("PUT", "/api/compositions/my-run", self.composition())
        self.assertEqual((status, plan["route"], plan["composition"]), (200, "drone", "my-run"))
        saved = (self.work / "compositions/my-run.yaml").read_text(encoding="utf-8")
        self.assertIn("schema: hakoniwa.composition/v1", saved)
        self.assertIn("id: my-run", saved)
        self.assertEqual(Path(plan["path"]), self.work / "compositions/my-run.yaml")

    def test_invalid_compositions_are_not_saved(self):
        status, body = self.call("PUT", "/api/compositions/bad", self.composition(asset="no-such-asset"))
        self.assertEqual(status, 400)
        self.assertIn("unknown vehicle Asset", body["error"])
        self.assertFalse((self.work / "compositions/bad.yaml").exists())
        self.assertEqual(list((self.work / "compositions").glob("*")), [], "no staging file left")

    def test_ids_are_restricted(self):
        for bad in ("..%2Fescape", "Upper", "with%20space"):
            with self.subTest(id=bad):
                status, body = self.call("GET", f"/api/compositions/{bad}")
                self.assertEqual(status, 400, body)

    def test_plan_endpoint_and_missing_composition(self):
        self.call("PUT", "/api/compositions/my-run", self.composition())
        status, plan = self.call("GET", "/api/compositions/my-run/plan")
        self.assertEqual((status, plan["route"]), (200, "drone"))
        status, _ = self.call("GET", "/api/compositions/nothing-here/plan")
        self.assertEqual(status, 404)

    def test_commands_run_as_jobs_with_output_and_progress(self):
        self.call("PUT", "/api/compositions/my-run", self.composition())
        status, job = self.call("POST", "/api/compositions/my-run/configure")
        self.assertEqual((status, job["state"], job["command"]), (202, "running", "configure"))
        finished = self.wait(job["id"])
        self.assertEqual((finished["state"], finished["exit_code"]), ("succeeded", 0))
        self.assertEqual(finished["lines"], ["running configure", "done"])
        self.assertEqual(finished["progress"]["percent"], 25.0)
        _, tail = self.call("GET", f"/api/jobs/{job['id']}?since=1")
        self.assertEqual(tail["lines"], ["done"])

    def test_failed_commands_report_their_exit_code(self):
        self.call("PUT", "/api/compositions/my-run", self.composition())
        _, job = self.call("POST", "/api/compositions/my-run/stop")
        self.assertEqual(self.wait(job["id"])["state"], "failed")
        self.assertEqual(self.wait(job["id"])["exit_code"], 3)

    def test_one_command_at_a_time_per_composition(self):
        self.call("PUT", "/api/compositions/my-run", self.composition())
        with mock.patch.dict("os.environ", {"FAKE_DELAY": "1.5"}):
            _, first = self.call("POST", "/api/compositions/my-run/configure")
            status, body = self.call("POST", "/api/compositions/my-run/start")
        self.assertEqual(status, 409)
        self.assertIn("still running", body["error"])
        self.wait(first["id"])

    def test_unknown_command_and_api(self):
        self.call("PUT", "/api/compositions/my-run", self.composition())
        status, _ = self.call("POST", "/api/compositions/my-run/explode")
        self.assertEqual(status, 400)
        status, _ = self.call("GET", "/api/nothing")
        self.assertEqual(status, 404)

    def test_viewer_is_empty_before_configure(self):
        self.call("PUT", "/api/compositions/never-configured", self.composition(
            name="Drone-1", asset="fpv-drone-master3x"))
        with mock.patch.object(urban_studio, "USER_COMPOSITIONS", self.work / "compositions"):
            status, body = self.call("GET", "/api/compositions/never-configured/viewer")
        self.assertEqual((status, body), (200, {"url": None}))

    def fake_world(self, kind="city"):
        job = self.work / "job"
        (job / "build").mkdir(parents=True, exist_ok=True)
        (job / "build" / "city.glb").write_bytes(b"glTF-fake")
        receipt = job / "city-world-receipt.json"
        receipt.write_text(json.dumps({
            "coordinate_frame": {"half_extent_m": {"north_south": 50, "east_west": 60},
                                 "origin": {"latitude": 35.0, "longitude": 138.0, "altitude": 0}},
            "glb": {"path": "build/city.glb"},
        }), encoding="utf-8")
        asset = mock.Mock(kind=kind, data={"title": "Test City"})
        patch = mock.patch.object(urban_studio, "world_receipt", return_value=(asset, receipt))
        patch.start()
        self.addCleanup(patch.stop)
        grounds = mock.patch.dict(urban_studio._grounds, clear=True)
        grounds.start()
        self.addCleanup(grounds.stop)

    def test_world_info_has_the_extent_glb_and_map_origin(self):
        self.fake_world()
        status, info = self.call("GET", "/api/worlds/test-city")
        self.assertEqual(status, 200)
        self.assertEqual(info["half_extent_m"], {"north_south": 50, "east_west": 60})
        self.assertEqual(info["glb"], "/api/worlds/test-city/glb")
        self.assertTrue(info["map"])
        self.assertEqual(info["origin"], {"latitude": 35.0, "longitude": 138.0})
        with urlopen(f"http://127.0.0.1:{self.port}{info['glb']}", timeout=10) as response:
            self.assertEqual(response.headers["Content-Type"], "model/gltf-binary")
            self.assertEqual(response.read(), b"glTF-fake")

    def test_plain_worlds_have_no_map(self):
        self.fake_world(kind="plain")
        _, info = self.call("GET", "/api/worlds/course")
        self.assertFalse(info["map"])
        self.assertNotIn("origin", info)

    def test_height_loads_the_world_model_once(self):
        self.fake_world()
        ground = mock.Mock(side_effect=lambda east, north: 20.0 + east)
        with mock.patch("urban_composition.city_ground", return_value=ground) as city_ground:
            _, first = self.call("GET", "/api/worlds/test-city/height?east=1.5&north=2")
            _, second = self.call("GET", "/api/worlds/test-city/height?east=3&north=2")
        self.assertEqual(first["ground_m"], 21.5)
        self.assertEqual(second["ground_m"], 23.0)
        self.assertEqual(city_ground.call_count, 1)

    def test_height_needs_numeric_coordinates(self):
        self.fake_world()
        status, _ = self.call("GET", "/api/worlds/test-city/height?east=x&north=2")
        self.assertEqual(status, 400)

    def test_unknown_world_is_404(self):
        status, _ = self.call("GET", "/api/worlds/no-such-world")
        self.assertEqual(status, 404)

    def test_a_city_without_its_receipt_is_listed_unavailable_and_not_loadable(self):
        import urban_assets

        receipt = self.work / "jobs/gone/build/world/city-world-receipt.json"
        receipt.parent.mkdir(parents=True)
        receipt.write_text("{}", encoding="utf-8")
        manifest = urban_assets.register_city(receipt, directory=self.work / "user-assets")
        receipt.unlink()
        catalog = {"gone": urban_assets.load_manifest(manifest)}
        with mock.patch("urban_assets.catalog", return_value=catalog):
            _, assets = self.call("GET", "/api/assets")
            status, body = self.call("GET", "/api/worlds/gone")
        self.assertEqual([(asset["id"], asset["available"]) for asset in assets], [("gone", False)])
        self.assertEqual(status, 404)
        self.assertIn("receipt", body["error"])


class CityPageTest(StudioTestBase):
    """GET /api/cities and the City World Web UI commands, on a fake jobs folder."""

    def setUp(self):
        super().setUp()
        self.recipe = self.work / "city-world-web-ui"
        self.jobs = self.recipe / "runtime/jobs"
        self.jobs.mkdir(parents=True)
        fake_tool = self.work / "fake_tool.py"
        fake_tool.write_text("import sys\nprint('ran', *sys.argv[1:], flush=True)\n", encoding="utf-8")
        self.registered = {}
        for name, value in {"CITY_RECIPE_ROOT": self.recipe, "URBAN_ASSETS": fake_tool,
                            "CITY_WEB_UI": fake_tool, "CITY_WEB_PORT": 1}.items():
            patch = mock.patch.object(urban_studio, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch("urban_assets.catalog", side_effect=lambda: self.registered)
        patch.start()
        self.addCleanup(patch.stop)
        self.user_assets = self.work / "user-assets"
        patch = mock.patch("urban_assets.USER_ASSETS", self.user_assets)
        patch.start()
        self.addCleanup(patch.stop)

    def make_job(self, name, finished=True):
        job = self.jobs / name
        (job / "build/world").mkdir(parents=True)
        receipt = job / "build/world/city-world-receipt.json"
        receipt.write_text("{}", encoding="utf-8")
        if finished:
            (job / "artifacts").mkdir()
            (job / "artifacts/result-manifest.json").write_text("{}", encoding="utf-8")
        return receipt

    def register(self, name, receipt, version):
        self.registered[name] = mock.Mock(kind="city", data={"receipt": str(receipt), "version": version})

    def wait_registration(self, name):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            _, state = self.call("GET", "/api/cities")
            job = next(job for job in state["jobs"] if job["id"] == name)
            if job["registration"] and job["registration"]["state"] != "running":
                return job["registration"]
            time.sleep(0.05)
        self.fail("registration did not finish")

    def test_a_finished_unregistered_job_is_registered_once(self):
        receipt = self.make_job("tokyo")
        registration = self.wait_registration("tokyo")
        self.assertEqual(registration["state"], "succeeded")
        self.assertIn(f"ran register-city --receipt {receipt.resolve()}", registration["lines"])
        _, state = self.call("GET", "/api/cities")
        self.assertEqual(len([job for job in self.server.RequestHandlerClass.runner.jobs.values()
                              if job.composition == "city:tokyo"]), 1)
        self.assertTrue(state["jobs"][0]["finished"])

    def test_registered_and_unfinished_jobs_are_left_alone(self):
        receipt = self.make_job("osaka")
        self.register("osaka", receipt.resolve(), receipt.stat().st_mtime_ns)
        self.make_job("nagoya", finished=False)
        _, state = self.call("GET", "/api/cities")
        jobs = {job["id"]: job for job in state["jobs"]}
        self.assertTrue(jobs["osaka"]["registered"])
        self.assertIsNone(jobs["osaka"]["registration"])
        self.assertFalse(jobs["nagoya"]["finished"])
        self.assertIsNone(jobs["nagoya"]["registration"])

    def test_a_regenerated_city_is_registered_again(self):
        receipt = self.make_job("osaka")
        self.register("osaka", receipt.resolve(), receipt.stat().st_mtime_ns - 1)
        self.assertEqual(self.wait_registration("osaka")["state"], "succeeded")

    def test_a_deleted_job_unregisters_its_city(self):
        import shutil
        import urban_assets

        receipt = self.make_job("hokkaido-a")
        manifest = urban_assets.register_city(receipt, directory=self.user_assets)
        shutil.rmtree(self.jobs / "hokkaido-a")

        _, state = self.call("GET", "/api/cities")

        self.assertEqual(state["unregistered"], ["hokkaido-a"])
        self.assertFalse(manifest.exists())
        _, state = self.call("GET", "/api/cities")
        self.assertEqual(state["unregistered"], [])

    def test_a_city_recreated_under_the_same_id_is_registered_again(self):
        import os
        import shutil

        self.make_job("sapporo")
        self.assertEqual(self.wait_registration("sapporo")["state"], "succeeded")
        runner = self.server.RequestHandlerClass.runner
        count = lambda: len([job for job in runner.jobs.values() if job.composition == "city:sapporo"])  # noqa: E731
        # The fake registration writes no manifest; an unchanged receipt is not re-registered.
        self.call("GET", "/api/cities")
        self.assertEqual(count(), 1)
        shutil.rmtree(self.jobs / "sapporo")
        receipt = self.make_job("sapporo")
        stat = receipt.stat()
        os.utime(receipt, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
        self.assertEqual(self.wait_registration("sapporo")["state"], "succeeded")
        self.assertEqual(count(), 2)

    def test_web_ui_start_configures_first_and_reports_not_running(self):
        self.recipe.rename(self.work / "moved")  # not configured yet
        status, job = self.call("POST", "/api/cities/web-ui/start")
        self.assertEqual(status, 202)
        finished = self.wait(job["id"])
        self.assertEqual(finished["lines"], ["ran configure", "ran start"])
        _, state = self.call("GET", "/api/cities")
        self.assertFalse(state["web_ui"]["running"])
        self.assertEqual(state["web_ui"]["job"]["command"], "start")
        self.assertEqual(state["jobs"], [])

    def test_web_ui_start_skips_configure_once_configured(self):
        _, job = self.call("POST", "/api/cities/web-ui/start")
        self.assertEqual(self.wait(job["id"])["lines"], ["ran start"])
        _, job = self.call("POST", "/api/cities/web-ui/stop")
        self.assertEqual(self.wait(job["id"])["lines"], ["ran stop"])

    def test_unknown_web_ui_command(self):
        status, _ = self.call("POST", "/api/cities/web-ui/explode")
        self.assertEqual(status, 404)


class ProgressParseTest(unittest.TestCase):
    def test_percent_comes_from_current_and_total(self):
        event = urban_studio.parse_progress('[HAKO_PROGRESS] {"phase":"x","current":3,"total":8}')
        self.assertEqual(event["percent"], 37.5)

    def test_heartbeats_without_a_total_have_no_percent(self):
        event = urban_studio.parse_progress('[HAKO_PROGRESS] {"phase":"mujoco_compile","elapsed_sec":20}')
        self.assertNotIn("percent", event)
        self.assertEqual(event["elapsed_sec"], 20)

    def test_other_lines_are_not_progress(self):
        self.assertIsNone(urban_studio.parse_progress("World height model: compiled 1/8"))
        self.assertIsNone(urban_studio.parse_progress("[HAKO_PROGRESS] not json"))


if __name__ == "__main__":
    unittest.main()
