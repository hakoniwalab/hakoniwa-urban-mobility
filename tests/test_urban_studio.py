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
        patch = mock.patch.object(urban_studio, "USER_SCENARIOS", self.work / "scenarios")
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
        self.assertTrue(hexa["preview"])

    def test_a_vehicle_preview_lists_its_parts_and_serves_only_those(self):
        status, info = self.call("GET", "/api/assets/golf-cart/preview")
        self.assertEqual(status, 200)
        self.assertEqual(info["parts"][0]["url"], "/api/assets/golf-cart/preview/0")
        self.assertEqual(info["parts"][0]["basis"], "flu")
        with urlopen(f"http://127.0.0.1:{self.port}/api/assets/golf-cart/preview/0", timeout=10) as response:
            self.assertEqual(response.headers["Content-Type"], "model/gltf-binary")
            self.assertEqual(response.read(4), b"glTF")
        for path in ("/api/assets/golf-cart/preview/99", "/api/assets/golf-cart/preview/..%2F..",
                     "/api/assets/plain-ground/preview", "/api/assets/nope/preview"):
            with self.subTest(path=path):
                self.assertEqual(self.call("GET", path)[0], 404)

    def test_a_fleet_composition_counts_its_drones_and_saves_as_the_fleet_route(self):
        _, listed = self.call("GET", "/api/compositions")
        self.assertEqual(next(item for item in listed if item["id"] == "city-drone-fleet")["vehicles"], 30)
        # As Compose saves it: an empty vehicles list next to the fleet.
        composition = {"world": "plain-ground", "vehicles": [], "fleets": [{
            "name": "Fleet", "asset": "drone-core-quad", "control": "api", "count": 12,
            "spacing_m": 1.5, "area": {"east_m": -5.0, "north_m": 3.0},
        }]}
        status, plan = self.call("PUT", "/api/compositions/studio-fleet", composition)
        self.assertEqual((status, plan["route"]), (200, "fleet"))
        _, listed = self.call("GET", "/api/compositions")
        self.assertEqual(next(item for item in listed if item["id"] == "studio-fleet")["vehicles"], 12)

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

    def test_composition_list_describes_the_world_and_vehicle_makeup(self):
        _, listed = self.call("GET", "/api/compositions")
        by_id = {item["id"]: item for item in listed}
        hexa = by_id["plain-hexa-rc"]
        self.assertEqual((hexa["world"], hexa["world_kind"]), ("plain-ground", "plain"))
        self.assertTrue(hexa["world_title"])
        [drone] = hexa["vehicle_list"]
        self.assertEqual((drone["name"], drone["asset"], drone["control"]), ("Drone-1", "eams-hexa", "rc"))
        self.assertTrue(drone["title"])
        self.assertIsInstance(hexa["updated_at"], float)
        [fleet] = by_id["plain-drone-fleet"]["fleets"]
        self.assertEqual((fleet["asset"], fleet["count"], fleet["control"]), ("drone-core-quad", 10, "api"))

    def test_saving_an_example_copy_keeps_its_relative_path_params_valid(self):
        import urban_assets

        catalog = urban_assets.catalog()
        composition = {"world": "plain-ground", "vehicles": [
            {"name": "Car-1", "asset": "golf-cart", "control": "api",
             "params": {"scenario": "../scenarios/golf-cart-demo-convoy.yaml"}},
        ]}
        urban_studio.relocate_path_params(composition, catalog, self.work / "compositions")
        self.assertEqual(
            composition["vehicles"][0]["params"]["scenario"],
            "${repo:hakoniwa-urban-mobility}/recipes/scenarios/golf-cart-demo-convoy.yaml",
        )
        # An already valid reference is left alone; a missing file is rejected.
        urban_studio.relocate_path_params(composition, catalog, self.work / "compositions")
        self.assertTrue(composition["vehicles"][0]["params"]["scenario"].startswith("${repo:"))
        composition["vehicles"][0]["params"]["scenario"] = "../scenarios/no-such-scenario.yaml"
        with self.assertRaisesRegex(urban_studio.StudioError, "no-such-scenario"):
            urban_studio.relocate_path_params(composition, catalog, self.work / "compositions")

    def write_saved(self, composition_id, scenario):
        import yaml

        directory = self.work / "compositions"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{composition_id}.yaml"
        path.write_text(yaml.safe_dump({
            "schema": "hakoniwa.composition/v1", "id": composition_id, "world": "plain-ground",
            "vehicles": [{"name": "Car-1", "asset": "golf-cart", "control": "api",
                          "params": {"scenario": scenario}}],
        }), encoding="utf-8")
        return path

    def test_running_an_old_saved_copy_repairs_its_path_params_first(self):
        path = self.write_saved("old-copy", "../scenarios/golf-cart-demo-convoy.yaml")
        status, job = self.call("POST", "/api/compositions/old-copy/configure")
        self.assertEqual(status, 202)
        finished = self.wait(job["id"])
        self.assertIn("のファイル参照を保存先に合わせて修正しました", finished["lines"][0])
        self.assertIn("${repo:hakoniwa-urban-mobility}/recipes/scenarios/golf-cart-demo-convoy.yaml",
                      path.read_text(encoding="utf-8"))

    def test_running_a_saved_copy_with_a_missing_file_stops_before_configure(self):
        self.write_saved("broken-copy", "../scenarios/no-such-scenario.yaml")
        status, body = self.call("POST", "/api/compositions/broken-copy/configure")
        self.assertEqual(status, 400)
        self.assertIn("no-such-scenario", body["error"])
        self.assertIn("Compose で開いて", body["error"])
        # stop still runs for a Composition that cannot be repaired.
        status, _ = self.call("POST", "/api/compositions/broken-copy/stop")
        self.assertNotEqual(status, 400)

    def route(self, points=3, **overrides):
        scenario = {
            "schema_version": 2, "name": "Test loop", "loop_count": "forever",
            "meta": {"world": "plain-ground"},
            "vehicles": [{"name": "Car-1", "route_offset_m": 0.0}],
            "control": {"speed_m_s": 1.0, "lookahead_m": 2.5, "position_gain": 0.8,
                        "wheelbase_m": 1.55, "max_steering_deg": 32.0},
            "route": {"closed": True, "points": [
                {"name": f"p{index}", "east_m": 10.0 * (index % 2), "north_m": 10.0 * (index // 2)}
                for index in range(points)
            ]},
        }
        scenario.update(overrides)
        return scenario

    def test_route_scenarios_list_examples_with_a_location_free_reference(self):
        _, listed = self.call("GET", "/api/scenarios")
        convoy = next(item for item in listed if item["id"] == "golf-cart-demo-convoy")
        self.assertFalse(convoy["editable"])
        self.assertEqual(convoy["reference"],
                         "${repo:hakoniwa-urban-mobility}/recipes/scenarios/golf-cart-demo-convoy.yaml")
        self.assertEqual(convoy["vehicles"], ["Car-1", "Car-2"])
        self.assertGreaterEqual(convoy["points"], 3)
        status, loaded = self.call("GET", "/api/scenarios/golf-cart-demo-convoy")
        self.assertEqual((status, loaded["scenario"]["schema_version"]), (200, 2))

    def test_a_saved_route_is_listed_with_its_world_and_business_pack_reference(self):
        status, saved = self.call("PUT", "/api/scenarios/my-loop", self.route())
        self.assertEqual(status, 200)
        self.assertTrue((self.work / "scenarios/my-loop.yaml").is_file())
        self.assertEqual((saved["world"], saved["editable"], saved["points"]), ("plain-ground", True, 3))
        _, listed = self.call("GET", "/api/scenarios")
        self.assertIn("my-loop", [item["id"] for item in listed])

    def test_saving_a_route_reports_segments_blocked_by_buildings(self):
        building = {"id": "bldg_a", "vertices": [[-5, -5], [5, -5], [5, 5], [-5, 5]], "holes": []}
        with mock.patch.object(urban_studio, "world_footprints", return_value={"buildings": [building]}):
            status, saved = self.call("PUT", "/api/scenarios/through-a-building", self.route())
            _, loaded = self.call("GET", "/api/scenarios/through-a-building")
        self.assertEqual(status, 200)
        self.assertTrue(saved["conflicts"])
        self.assertEqual(saved["conflicts"][0]["building"], "bldg_a")
        self.assertEqual(loaded["conflicts"], saved["conflicts"])

    def test_a_saved_route_can_be_deleted_unless_a_composition_uses_it(self):
        self.call("PUT", "/api/scenarios/my-loop", self.route())
        compositions = self.work / "compositions"
        compositions.mkdir(parents=True, exist_ok=True)
        user = compositions / "with-route.yaml"
        # A relative scenario path resolves against the Composition file.
        user.write_text(
            "world: plain-ground\nvehicles:\n  - name: Car-1\n    asset: golf-cart\n    control: api\n"
            "    params: {scenario: ../scenarios/my-loop.yaml}\n",
            encoding="utf-8",
        )
        status, body = self.call("DELETE", "/api/scenarios/my-loop")
        self.assertEqual(status, 409)
        self.assertIn("with-route", body["error"])
        self.assertTrue((self.work / "scenarios/my-loop.yaml").is_file())

        user.unlink()
        status, body = self.call("DELETE", "/api/scenarios/my-loop")
        self.assertEqual((status, body), (200, {"deleted": "my-loop"}))
        self.assertFalse((self.work / "scenarios/my-loop.yaml").exists())
        _, listed = self.call("GET", "/api/scenarios")
        self.assertNotIn("my-loop", [item["id"] for item in listed])
        self.assertEqual(self.call("DELETE", "/api/scenarios/my-loop")[0], 404)

    def test_an_example_route_cannot_be_deleted(self):
        status, body = self.call("DELETE", "/api/scenarios/golf-cart-demo-convoy")
        self.assertEqual(status, 400)
        self.assertIn("例のルート", body["error"])
        self.assertTrue((urban_studio.EXAMPLE_SCENARIOS / "golf-cart-demo-convoy.yaml").is_file())

    def test_an_invalid_route_is_rejected_and_not_saved(self):
        status, body = self.call("PUT", "/api/scenarios/short", self.route(points=2))
        self.assertEqual(status, 400)
        self.assertIn("three points", body["error"])
        self.assertFalse((self.work / "scenarios/short.yaml").exists())
        self.assertFalse((self.work / "scenarios/short.partial.yaml").exists())
        status, _ = self.call("PUT", "/api/scenarios/Bad_Id", self.route())
        self.assertEqual(status, 400)

    def test_composition_summary_keeps_unknown_assets_recognisable(self):
        summary = urban_studio.composition_summary(
            {"world": "no-such-world", "vehicles": [{"name": "X", "asset": "no-such-asset", "control": "rc"}]}, {}
        )
        self.assertEqual((summary["world_title"], summary["world_kind"]), ("no-such-world", None))
        self.assertEqual(summary["vehicle_list"][0]["title"], "no-such-asset")

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
        self.assertEqual((status, body), (200, {"url": None, "collider_url": None}))

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

    def test_city_footprints_come_from_the_lod1_outlines(self):
        self.fake_world()
        (self.work / "city-world-lod1.json").write_text(json.dumps({"polygons": [
            {"id": "bldg_a", "vertices": [[0, 0], [10, 0], [10, 5]], "zmax": 12.0,
             "interior_rings": [[[6, 1], [8, 1], [8, 2]]]},
            {"id": "degenerate", "vertices": [[0, 0], [1, 1]]},
        ]}), encoding="utf-8")
        status, body = self.call("GET", "/api/worlds/test-city/footprints")
        self.assertEqual(status, 200)
        self.assertEqual(body["buildings"], [{
            "id": "bldg_a", "vertices": [[0, 0], [10, 0], [10, 5]],
            "holes": [[[6, 1], [8, 1], [8, 2]]], "height_m": 12.0,
        }])

    def test_plain_worlds_have_no_footprints(self):
        self.fake_world(kind="plain")
        _, body = self.call("GET", "/api/worlds/course/footprints")
        self.assertEqual(body["buildings"], [])

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


class CityTestBase(StudioTestBase):
    """A fake City World Web UI jobs folder and fake tool scripts."""

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


class CityPageTest(CityTestBase):
    """GET /api/cities and the City World Web UI commands, on a fake jobs folder."""

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


class CachePageTest(CityTestBase):
    """GET /api/cache and POST /api/cache/prune, on a fake cache and a fake urban_assets.py."""

    def setUp(self):
        super().setUp()
        import urban_cache

        self.cache = self.work / "urban-cache"
        stale = self.cache / "world-height" / f"{'0' * 64}-mujoco-3.13.0"
        stale.mkdir(parents=True)
        (stale / "chunk-00.mjb").write_bytes(b"m" * 64)
        (stale / "manifest.json").write_text(
            json.dumps({"layout": 2, "mjcf": str(self.jobs / "deleted/build/world/city-world.xml")}),
            encoding="utf-8",
        )
        for patch in (mock.patch.object(urban_cache, "CACHE_ROOT", self.cache),
                      mock.patch.object(urban_studio, "city_world_cache",
                                        return_value={"available": False, "command": "x"})):
            patch.start()
            self.addCleanup(patch.stop)

    def test_cache_state_reports_what_prune_would_remove(self):
        status, state = self.call("GET", "/api/cache")
        self.assertEqual(status, 200)
        [entry] = state["urban"]["world_height"]
        self.assertTrue(entry["remove"])
        self.assertEqual(entry["reason"], "City World job deleted")
        self.assertEqual(state["urban"]["reclaimable_bytes"], entry["size_bytes"])
        self.assertIsNone(state["prune_job"])
        self.assertFalse(state["city_world"]["available"])

    def test_prune_runs_the_urban_assets_command(self):
        status, job = self.call("POST", "/api/cache/prune")
        self.assertEqual(status, 202)
        self.assertEqual(self.wait(job["id"])["lines"], ["ran prune-cache --apply"])
        _, state = self.call("GET", "/api/cache")
        self.assertEqual(state["prune_job"]["state"], "succeeded")

    def test_prune_waits_for_other_studio_commands(self):
        runner = self.server.RequestHandlerClass.runner
        busy = runner.launch("city:tokyo", "register", [[sys.executable, "-c", "import time; time.sleep(2)"]])
        status, body = self.call("POST", "/api/cache/prune")
        self.assertEqual(status, 409)
        self.assertIn("register of city:tokyo", body["error"])
        self.wait(busy.id)


class CityWorldCacheSummaryTest(unittest.TestCase):
    def test_missing_business_pack_tool_is_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(urban_studio, "BUSINESS_PACK", Path(directory)):
            summary = urban_studio.city_world_cache()
        self.assertFalse(summary["available"])
        self.assertIn("cache-clean", summary["command"])


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
