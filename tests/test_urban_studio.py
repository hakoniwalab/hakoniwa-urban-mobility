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


class StudioServerTest(unittest.TestCase):
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
