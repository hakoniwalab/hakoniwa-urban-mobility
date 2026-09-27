import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import urban_assets  # noqa: E402
import urban_composition  # noqa: E402
import urban_controls  # noqa: E402
import urban_simulation  # noqa: E402
import urban_realtime  # noqa: E402
import plain_world  # noqa: E402
from tools import urban_mobility  # noqa: E402
import drone_one  # noqa: E402
import multi_car  # noqa: E402

FPV_ASSETS = urban_assets.WORKSPACE / "hakoniwa-fpv-drone/assets"
RECEIPT = Path("C:/cities/jobs/hokkaido-01100-lat43.062-lon141.355/build/world/city-world-receipt.json")
CITY_ID = "hokkaido-01100-lat43.062-lon141.355"


def resolve_like_multi_car(raw: str) -> Path:
    path = Path(raw)
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


class Fixture(unittest.TestCase):
    """Temporary Asset catalog: the tracked vehicles plus two City Assets."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.work = Path(self.directory.name)
        city = self.work / "assets/cities" / f"{CITY_ID}.asset.yaml"
        city.parent.mkdir(parents=True)
        city.write_text(yaml.safe_dump({
            "schema": "hakoniwa.asset/v1", "id": CITY_ID, "kind": "city",
            "version": 1, "receipt": RECEIPT.as_posix(),
        }), encoding="utf-8")
        # A City whose receipt exists, for adapters whose tools check the file.
        self.receipt = self.work / "jobs/test-city/build/world/city-world-receipt.json"
        self.receipt.parent.mkdir(parents=True)
        self.receipt.write_text("{}", encoding="utf-8")
        urban_assets.register_city(self.receipt, directory=self.work / "assets")
        self.assets = urban_assets.catalog([urban_assets.REPOSITORY_ASSETS, FPV_ASSETS, self.work / "assets"])
        # Flat ground at 0 m unless a test passes its own ground function; the
        # fixture City receipts have no World model to ray-cast.
        for name in ("city_ground", "plain_ground"):
            ground = mock.patch.object(urban_composition, name, return_value=lambda east, north: 0.0)
            ground.start()
            self.addCleanup(ground.stop)
        # Generated plain World models go to the test directory, not the workspace.
        for module, name, directory in (
            (urban_composition, "PLAIN_WORLD_CACHE", "plain-world-cache"),
            (plain_world, "JOBS_DIR", "plain-world-jobs"),
        ):
            cache = mock.patch.object(module, name, self.work / directory)
            cache.start()
            self.addCleanup(cache.stop)

    def composition(self, **overrides) -> Path:
        vehicle = {
            "name": "Car-1", "asset": "golf-cart", "control": "rc",
            "spawn": {"east_m": 0.0, "north_m": 0.0, "yaw_deg": 0.0},
        }
        vehicle.update(overrides.pop("vehicle", {}))
        data = {
            "schema": "hakoniwa.composition/v1", "id": "test", "world": CITY_ID,
            "vehicles": overrides.pop("vehicles", [vehicle]),
            "viewer": {"http_port": 8000, "web_bridge_port": 18765},
        }
        data.update(overrides)
        path = self.work / "composition.yaml"
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
        return path

    def drone(self, **vehicle) -> Path:
        entry = {"name": "Drone-1", "asset": "eams-hexa", "control": "rc",
                 "spawn": {"east_m": 0.0, "north_m": 0.0, "yaw_deg": 0.0}}
        entry.update(vehicle)
        return self.composition(world="test-city", vehicles=[entry])

    def load(self, path: Path):
        return urban_composition.load(path, self.assets)

    def catalog(self):
        return mock.patch.object(urban_composition.urban_assets, "catalog", return_value=self.assets)


class CompositionTest(Fixture):
    def test_spawn_height_comes_from_the_asset_not_the_user(self):
        with self.assertRaisesRegex(urban_composition.CompositionError, "height is computed"):
            self.load(self.composition(vehicle={"spawn": {"east_m": 0, "north_m": 0, "yaw_deg": 0, "up_m": 3}}))

    def test_rejects_unknown_asset_and_undeclared_control(self):
        with self.assertRaisesRegex(urban_composition.CompositionError, "unknown vehicle Asset"):
            self.load(self.composition(vehicle={"asset": "no-such-car"}))
        with self.assertRaisesRegex(urban_composition.CompositionError, "offers controls"):
            self.load(self.composition(vehicle={"control": "autopilot"}))

    def test_api_control_requires_its_declared_params(self):
        with self.assertRaisesRegex(urban_composition.CompositionError, "requires param scenario"):
            self.load(self.composition(vehicle={"control": "api"}))

    def test_rejects_duplicate_vehicle_names(self):
        vehicle = {"name": "Car-1", "asset": "golf-cart", "control": "rc",
                   "spawn": {"east_m": 0, "north_m": 0, "yaw_deg": 0}}
        with self.assertRaisesRegex(urban_composition.CompositionError, "unique"):
            self.load(self.composition(vehicles=[vehicle, dict(vehicle)]))

    def test_register_city_derives_the_id_from_the_city_world_job(self):
        receipt = self.work / "jobs" / CITY_ID / "build/world/city-world-receipt.json"
        receipt.parent.mkdir(parents=True)
        receipt.write_text("{}", encoding="utf-8")
        manifest = urban_assets.register_city(receipt, directory=self.work / "user")
        asset = urban_assets.load_manifest(manifest)
        self.assertEqual((asset.id, asset.kind), (CITY_ID, "city"))
        self.assertEqual(asset.resolve(asset.data["receipt"]), receipt.resolve())

    def test_simple_yaml_strings_survive_the_drone_one_reader(self):
        values = {"number_like": "123", "bool_like": "true", "path": "C:/city data/大阪/receipt.json",
                  "plain": "fleet-rpc", "nested": {"offsets": [0.0, 1.5], "flag": False, "count": 3}}
        path = self.work / "strings.yaml"
        path.write_text(urban_composition.render_simple_yaml(values) + "\n", encoding="utf-8")
        self.assertEqual(drone_one.base.load_simple_yaml(path), values)


class CarCompositionTest(Fixture):
    def test_city_car_composition_matches_the_urban_car_one_template(self):
        template = yaml.safe_load(
            (ROOT / "recipes/experiments/urban-car-one.yaml").read_text(encoding="utf-8")
        )
        expected = template["inputs"]
        actual = urban_composition.to_car_config(
            self.load(ROOT / "recipes/compositions/city-golf-cart-rc.yaml"), "urban-car-rc"
        )
        inputs = actual["inputs"]

        self.assertEqual(actual["id"], "urban-car-rc")
        self.assertEqual(actual["composition"]["output"]["recipe_id"], "urban-car-rc")
        self.assertEqual(
            resolve_like_multi_car(inputs["business_pack_city_receipt"]["path"]), RECEIPT.resolve()
        )
        for got, want in zip(inputs["ackermann_vehicles"]["types"], expected["ackermann_vehicles"]["types"], strict=True):
            self.assertEqual(got["type"], want["type"])
            for key in ("mjcf", "contract", "view_model"):
                self.assertEqual(resolve_like_multi_car(got[key]), resolve_like_multi_car(want[key]), key)
        # Same vehicle, but the height is absolute: World surface + Asset clearance.
        [got_vehicle] = inputs["ackermann_vehicles"]["vehicles"]
        [want_vehicle] = expected["ackermann_vehicles"]["vehicles"]
        got_spawn, want_spawn = dict(got_vehicle.pop("spawn_pose_enu")), dict(want_vehicle.pop("spawn_pose_enu"))
        self.assertEqual(got_vehicle, want_vehicle)
        self.assertEqual(got_spawn.pop("up_m"), 0.0 + want_spawn.pop("ground_clearance_m"))
        self.assertEqual(got_spawn, want_spawn)
        # Intentional difference: the Urban real-time pacer paces the plant,
        # so the plant's own sleep-based sync is off.
        self.assertEqual(inputs["ackermann_runtime"], {**expected["ackermann_runtime"], "realtime_sync_cycle_msec": 0})
        got_view = dict(inputs["browser_visualization"])
        want_view = dict(expected["browser_visualization"])
        self.assertEqual(
            resolve_like_multi_car(got_view.pop("threejs_root")),
            resolve_like_multi_car(want_view.pop("threejs_root")),
        )
        self.assertEqual(got_view, want_view)

    def car(self, name: str, control: str = "rc", scenario: str | None = None, east: float = 0.0) -> dict:
        entry = {"name": name, "asset": "golf-cart", "control": control,
                 "spawn": {"east_m": east, "north_m": 0.0, "yaw_deg": 0.0}}
        if scenario is not None:
            entry["params"] = {"scenario": scenario}
        return entry

    def test_api_cars_share_one_auto_started_route_scenario(self):
        scenario = str(ROOT / "recipes/scenarios/golf-cart-demo-loop.yaml")
        config = urban_composition.to_car_config(self.load(self.composition(vehicles=[
            self.car("Car-1", "api", scenario), self.car("Car-2", east=5.0),
        ])), "urban-car-rc")
        vehicles = config["inputs"]["ackermann_vehicles"]
        self.assertEqual(vehicles["route_scenario"], {"path": scenario, "auto_start": True})
        self.assertEqual([vehicle["control_mode"] for vehicle in vehicles["vehicles"]],
                         ["external_python", "ps5"])

    def test_car_height_is_world_surface_plus_clearance(self):
        config = urban_composition.to_car_config(
            self.load(self.composition()), "urban-car-rc", ground=lambda east, north: 21.054
        )
        spawn = config["inputs"]["ackermann_vehicles"]["vehicles"][0]["spawn_pose_enu"]
        self.assertAlmostEqual(spawn["up_m"], 21.054 + 0.45)
        self.assertNotIn("ground_clearance_m", spawn)

    def test_api_cars_with_different_scenarios_are_rejected(self):
        composition = self.load(self.composition(vehicles=[
            self.car("Car-1", "api", "a.yaml"), self.car("Car-2", "api", "b.yaml", east=5.0),
        ]))
        with self.assertRaisesRegex(urban_composition.CompositionError, "share one route scenario"):
            urban_composition.to_car_config(composition, "urban-car-rc")

    def test_car_rc_params_do_not_change_the_car_config(self):
        # Params reach only the control process (tools/urban_controls.py).
        default = urban_composition.to_car_config(self.load(self.composition()), "urban-car-rc")
        tuned = urban_composition.to_car_config(
            self.load(self.composition(vehicle={"params": {"max_speed": 2.0}})), "urban-car-rc"
        )
        self.assertTrue(urban_composition.placement_only_change(default, tuned))

    def test_multi_car_accepts_a_scenario_for_explicit_external_vehicles(self):
        scenario = {"path": str(ROOT / "recipes/scenarios/golf-cart-demo-loop.yaml")}
        resolved = multi_car.resolve_explicit_route_scenario(
            scenario, [{"name": "Car-1", "control_mode": "external_python"},
                       {"name": "Car-2", "control_mode": "ps5"}],
        )
        self.assertEqual((resolved["vehicles"], resolved["auto_start_scenario"]), (["Car-1"], True))
        with self.assertRaisesRegex(multi_car.RecipeError, "unconfigured vehicles"):
            multi_car.resolve_explicit_route_scenario(scenario, [{"name": "Car-9", "control_mode": "external_python"}])
        with self.assertRaisesRegex(multi_car.RecipeError, "must use external_python"):
            multi_car.resolve_explicit_route_scenario(scenario, [{"name": "Car-1", "control_mode": "ps5"}])

    def test_launcher_runs_the_scenario_beside_rc_cars(self):
        scenario = ROOT / "recipes/scenarios/golf-cart-demo-loop.yaml"
        source = {
            "plant": ROOT / "build/bin/urban-car-hakoniwa-asset",
            "core_config": ROOT / "core.json",
            "ps5_sender": ROOT / "apps/car/ps5_ackermann_sender.py",
            "ps5_mapping": ROOT / "config/car/dualsense-controller.json",
            "scenario_executor": ROOT / "apps/car/scenario_executor.py",
        }
        with mock.patch.object(multi_car, "paths", return_value=source), \
                mock.patch.object(multi_car, "foundation_python", return_value=Path("/python")), \
                mock.patch.object(multi_car, "foundation_install", return_value=Path("/install")), \
                mock.patch.object(multi_car, "launcher_supports_cleanup", return_value=True):
            path = multi_car.materialize_launcher(
                {"manifest": self.work / "manifest.json", "pdu_def": self.work / "pdudef.json"},
                self.work, 2,
                [{"name": "Car-1", "prefix": "car_1_", "control_mode": "ps5"},
                 {"name": "Car-2", "prefix": "car_2_", "control_mode": "external_python"}],
                route_scenario={"scenario": scenario, "auto_start_scenario": True, "vehicles": ["Car-2"]},
            )
        launcher = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(
            [asset["name"] for asset in launcher["assets"]],
            ["urban-car-fleet-plant", "urban-car-1-ps5-controller", "urban-car-scenario-executor"],
        )
        self.assertIn(str(scenario), launcher["assets"][2]["args"])

    def test_composition_selects_the_car_managed_recipe(self):
        with self.catalog():
            recipe = urban_simulation.plan(self.composition()).managed_recipe
        self.assertEqual(recipe, ROOT / "recipes/usecases/urban-car-rc.yaml")


class CarPlacementTest(Fixture):
    def configure(self, context_root: Path, composition: Path) -> None:
        config = urban_composition.to_car_config(self.load(composition), "urban-car-rc")
        (context_root / "config").mkdir(parents=True, exist_ok=True)
        (context_root / "validation").mkdir(parents=True, exist_ok=True)
        (context_root / "config/urban-composition.json").write_text(json.dumps(config), encoding="utf-8")
        (context_root / "validation/urban-inputs.json").write_text(
            json.dumps({"composition": str(composition)}), encoding="utf-8"
        )

    def start(self, composition: Path, context_root: Path):
        context = urban_mobility.RecipeContext(
            path=ROOT / "recipes/usecases/urban-car-rc.yaml", data={},
            recipe_id="urban-car-rc", use_case="car-rc",
        )
        with self.catalog(), \
                mock.patch.object(urban_mobility, "root", return_value=context_root), \
                mock.patch.object(multi_car, "resolve_config", return_value={}), \
                mock.patch.object(urban_simulation, "apply_managed_runtime") as controls, \
                mock.patch.object(multi_car, "refresh_runtime_initial_body_poses") as refresh:
            urban_mobility.prepare_start(context, composition)
        [(target, applied)] = [call.args for call in controls.call_args_list]
        self.assertEqual((target.recipe_id, target.work, applied), ("urban-car-rc", context_root, composition))
        return refresh

    def test_placement_only_edit_is_applied_at_start(self):
        context_root = self.work / "recipe"
        self.configure(context_root, self.composition())
        moved = self.composition(vehicle={"spawn": {"east_m": 5.0, "north_m": 1.0, "yaw_deg": 45.0}})
        self.start(moved, context_root).assert_called_once()
        config = json.loads((context_root / "config/urban-composition.json").read_text(encoding="utf-8"))
        spawn = config["inputs"]["ackermann_vehicles"]["vehicles"][0]["spawn_pose_enu"]
        self.assertEqual((spawn["east_m"], spawn["north_m"], spawn["yaw_deg"]), (5.0, 1.0, 45.0))

    def test_non_placement_edit_requires_configure(self):
        context_root = self.work / "recipe"
        self.configure(context_root, self.composition())
        with self.assertRaisesRegex(urban_simulation.SimulationError, "run configure"):
            self.start(self.composition(vehicle={"name": "Car-9"}), context_root)


class DroneCompositionTest(Fixture):
    def render(self, recipe: dict) -> Path:
        path = self.work / "drone-recipe.yaml"
        path.write_text(urban_composition.render_simple_yaml(recipe) + "\n", encoding="utf-8")
        return path

    def test_city_drone_composition_matches_the_urban_drone_one_recipe(self):
        template_path = ROOT / "recipes/experiments/urban-drone-one.yaml"
        template = drone_one.base.load_simple_yaml(template_path)
        clearance = self.assets["eams-hexa"].data["spawn"]["ground_clearance_m"]
        ground = template["drone"]["spawn_pose_enu"]["up_m"] - clearance
        recipe = urban_composition.to_drone_recipe(
            self.load(self.drone()), ground=lambda east, north: ground
        )
        # Parsed by the real tool reader, including its file-existence checks.
        loaded = drone_one.load_urban_recipe(self.render(recipe))

        def template_path_value(value: str) -> Path:
            return (template_path.parent / value).resolve()

        self.assertEqual(loaded.fleet_experiment, template_path_value(template["fleet_experiment"]["path"]))
        self.assertEqual(loaded.mission, template_path_value(template["mission"]["path"]))
        self.assertEqual(loaded.city_receipt, self.receipt.resolve())
        self.assertEqual(loaded.drone_profile, template["drone"]["profile"])
        self.assertEqual(loaded.spawn_pose_enu, template["drone"]["spawn_pose_enu"])
        self.assertEqual(loaded.launch_area, template["drone"]["launch_area"])
        self.assertEqual(loaded.control_mode, template["control"]["mode"])
        self.assertIsNone(loaded.controller_params)
        self.assertEqual(loaded.map_layout, template["viewer"]["map_layout"])
        self.assertEqual(loaded.collider_overlay_default, template["viewer"]["collider_overlay_default"])

    def test_drone_height_is_ground_plus_asset_clearance(self):
        recipe = urban_composition.to_drone_recipe(
            self.load(self.drone(spawn={"east_m": 3.0, "north_m": -4.0, "yaw_deg": 90.0})),
            ground=lambda east, north: 2.0 if (east, north) == (3.0, -4.0) else 999.0,
        )
        self.assertEqual(recipe["drone"]["spawn_pose_enu"]["up_m"], 2.5)

    def test_drone_api_mission_resolves_against_the_composition(self):
        mission = self.work / "missions/patrol.json"
        mission.parent.mkdir()
        mission.write_text("{}", encoding="utf-8")
        recipe = urban_composition.to_drone_recipe(
            self.load(self.drone(control="api", params={"mission": "missions/patrol.json"})),
            ground=lambda east, north: 0.0,
        )
        loaded = drone_one.load_urban_recipe(self.render(recipe))
        self.assertEqual((loaded.control_mode, loaded.mission), ("fleet-rpc", mission.resolve()))

    def test_drone_adapter_runs_one_drone_only(self):
        drones = [{"name": f"Drone-{index}", "asset": "eams-hexa", "control": "rc",
                   "spawn": {"east_m": index, "north_m": 0, "yaw_deg": 0}} for index in (1, 2)]
        composition = self.load(self.composition(world="test-city", vehicles=drones))
        with self.assertRaisesRegex(urban_composition.CompositionError, "exactly one Drone"):
            urban_composition.to_drone_recipe(composition, ground=lambda east, north: 0.0)

    def test_drone_only_composition_is_run_by_drone_one(self):
        recipe_path = self.work / "workspace/config" / urban_simulation.DRONE_RECIPE_FILE
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            return mock.Mock(returncode=0)

        with self.catalog(), \
                mock.patch.object(urban_simulation, "drone_recipe_path", return_value=recipe_path), \
                mock.patch.object(urban_composition, "city_ground", return_value=lambda east, north: 0.0), \
                mock.patch.object(urban_simulation.subprocess, "run", side_effect=run):
            composition = self.drone()
            self.assertEqual(urban_simulation.drone_command("configure", composition), 0)
            self.assertEqual(calls[-1][-3:], ["configure", "--recipe", str(recipe_path)])
            self.assertEqual(drone_one.base.load_simple_yaml(recipe_path)["control"], {"mode": "ps4-rc"})
            # A placement edit rewrites the tool recipe before start.
            self.drone(spawn={"east_m": 7.0, "north_m": 0.0, "yaw_deg": 0.0})
            self.assertEqual(urban_simulation.drone_command("start", composition), 0)
            self.assertEqual(calls[-1][-1], "start")
            spawn = drone_one.base.load_simple_yaml(recipe_path)["drone"]["spawn_pose_enu"]
            self.assertEqual(spawn["east_m"], 7.0)

    def test_drone_start_requires_configure(self):
        with mock.patch.object(urban_simulation, "drone_recipe_path", return_value=self.work / "missing.yaml"):
            with self.assertRaisesRegex(urban_simulation.SimulationError, "not configured"):
                urban_simulation.drone_command("start", self.drone())


class IntegratedFixture(Fixture):
    SCENARIO = ROOT / "recipes/scenarios/golf-cart-demo-loop.yaml"

    def integrated(self, *, drone: dict | None = None, car: dict | None = None, mirror: bool = True) -> Path:
        car_entry = {"name": "Car-1", "asset": "golf-cart", "control": "api",
                     "params": {"scenario": str(self.SCENARIO)},
                     "spawn": {"east_m": 45.0, "north_m": 7.5, "yaw_deg": 0.0}}
        car_entry.update(car or {})
        drone_entry = {"name": "Drone-1", "asset": "eams-hexa", "control": "rc",
                       "spawn": {"east_m": 5.0, "north_m": -45.0, "yaw_deg": 0.0}}
        drone_entry.update(drone or {})
        extra = {"interactions": [{"type": "drone-mirror", "drone": "Drone-1"}]} if mirror else {}
        # No viewer ports: the integrated default WebBridge port is under test.
        return self.composition(world="test-city", vehicles=[car_entry, drone_entry], viewer={}, **extra)

    def outputs(self, composition: Path, ground: float = 1.0):
        drone_path = self.work / "out/urban-composition-drone.yaml"
        config, drone = urban_composition.to_integrated(
            self.load(composition), "urban-mobility-rc", drone_path, ground=lambda east, north: ground
        )
        drone_path.parent.mkdir(parents=True, exist_ok=True)
        drone_path.write_text(urban_composition.render_simple_yaml(drone) + "\n", encoding="utf-8")
        config_path = self.work / "out/urban-composition.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        return config, config_path, drone_path


class IntegratedCompositionTest(IntegratedFixture):
    def test_integrated_config_references_the_drone_recipe_and_car_scenario(self):
        config, _, drone_path = self.outputs(self.integrated())
        self.assertEqual(config["id"], "urban-mobility-rc")
        self.assertEqual(config["scenarios"], {"drone": str(drone_path), "car": str(self.SCENARIO)})
        self.assertEqual(config["inputs"]["browser_visualization"]["web_bridge_port"], 8765)
        mirror = config["inputs"]["drone_mirrors"][0]
        self.assertEqual((mirror["name"], mirror["mjcf_body"], mirror["restitution_coefficient"]),
                         ("Drone-1", "drone_base", 0.3))
        self.assertTrue(Path(mirror["pdu_types"]).is_file())
        drone = drone_one.base.load_simple_yaml(drone_path)["drone"]
        self.assertEqual(drone["spawn_pose_enu"]["up_m"],
                         drone["rooftop"]["surface_height_m"] + drone["rooftop"]["base_clearance_m"])

    def test_urban_composer_accepts_the_adapted_composition(self):
        import urban_composer

        _, config_path, drone_path = self.outputs(self.integrated(), ground=2.25)
        with mock.patch.object(multi_car, "resolve_config", wraps=multi_car.resolve_config) as resolve:
            try:
                _, resolved, drone_scenario = urban_composer.load_composition(config_path)
            except multi_car.RecipeError as error:
                self.skipTest(f"multi_car needs more of the City World than the fixture has: {error}")
        resolve.assert_called_once()
        self.assertEqual(resolved["route_scenario"]["vehicles"], ["Car-1"])
        self.assertEqual(drone_scenario["path"], drone_path.resolve())
        self.assertEqual(drone_scenario["spawn"]["up_m"], 2.75)
        self.assertEqual(drone_scenario["recipe"].control_mode, "ps4-rc")

    def test_rc_only_cars_need_no_car_scenario(self):
        config, _, _ = self.outputs(self.integrated(car={"control": "rc", "params": {}}, mirror=False))
        self.assertNotIn("car", config["scenarios"])
        self.assertNotIn("drone_mirrors", config["inputs"])

    def test_integrated_drone_api_selects_fleet_rpc(self):
        mission = self.work / "mission.json"
        mission.write_text("{}", encoding="utf-8")
        composition = self.integrated(drone={"control": "api", "params": {"mission": str(mission)}})
        _, _, drone_path = self.outputs(composition)
        drone = drone_one.load_urban_recipe(drone_path)
        self.assertEqual((drone.control_mode, drone.mission), ("fleet-rpc", mission.resolve()))

    def test_mirror_must_name_a_drone(self):
        path = self.integrated(mirror=False)
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        data["interactions"] = [{"type": "drone-mirror", "drone": "Car-1"}]
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
        with self.assertRaisesRegex(urban_composition.CompositionError, "not a Drone vehicle"):
            self.load(path)

    def test_merged_launcher_keeps_every_selected_car_control(self):
        import urban_composer

        work = self.work / "recipe"
        (work / "config").mkdir(parents=True)
        car_launcher = {"assets": [
            {"name": "urban-car-fleet-plant", "args": []},
            {"name": "urban-car-2-ps5-controller", "args": []},
            {"name": "urban-car-scenario-executor", "args": []},
            {"name": "urban-vehicle-web-bridge", "args": []},
            {"name": "urban-vehicle-http-server", "args": []},
        ]}
        (work / "config/launcher.json").write_text(json.dumps(car_launcher), encoding="utf-8")
        drone_launcher = {"defaults": {}, "assets": [
            {"name": "drone-service-1", "args": ["config", "pdu.json"]},
            {"name": "urban-drone-ps4-controller", "args": ["rc.py", "pdu.json"]},
            {"name": "visual-state-publisher", "args": []},
        ]}
        with mock.patch.object(multi_car, "foundation_install", return_value=Path("/install")):
            path = urban_composer._merge_launchers({"work": work}, drone_launcher)
        names = [asset["name"] for asset in json.loads(path.read_text(encoding="utf-8"))["assets"]]
        self.assertEqual(names, [
            "drone-service-1", "urban-car-fleet-plant", "visual-state-publisher",
            "urban-car-2-ps5-controller", "urban-car-scenario-executor",
            "urban-drone-ps4-controller", "urban-vehicle-web-bridge", "urban-vehicle-http-server",
        ])

    def test_composition_selects_the_integrated_managed_recipe(self):
        with self.catalog():
            recipe = urban_simulation.plan(self.integrated()).managed_recipe
        self.assertEqual(recipe, ROOT / "recipes/experiments/urban-mobility-rc.yaml")


class IntegratedPlacementTest(IntegratedFixture):
    def context(self):
        return urban_mobility.RecipeContext(
            path=ROOT / "recipes/experiments/urban-mobility-rc.yaml", data={},
            recipe_id="urban-mobility-rc", use_case="drone-car-distributed",
        )

    def configure_state(self, composition: Path) -> Path:
        context_root = self.work / "recipe"
        (context_root / "validation").mkdir(parents=True, exist_ok=True)
        target = urban_simulation.ManagedTarget(
            managed_recipe=self.context().path, recipe_id="urban-mobility-rc",
            use_case="drone-car-distributed", work=context_root,
        )
        with self.catalog(), \
                mock.patch.object(urban_composition, "city_ground", return_value=lambda east, north: 1.0):
            config, drone = urban_simulation.composition_outputs(target, composition)
            urban_simulation.write_composition_outputs(target, config, drone)
        (context_root / "validation/urban-inputs.json").write_text(
            json.dumps({"composition": str(composition)}), encoding="utf-8"
        )
        return context_root

    def start(self, composition: Path, context_root: Path):
        with self.catalog(), mock.patch.object(urban_mobility, "root", return_value=context_root), \
                mock.patch.object(urban_composition, "city_ground", return_value=lambda east, north: 1.0), \
                mock.patch.object(drone_one, "_paths"), \
                mock.patch.object(drone_one, "read_selected_recipe"), \
                mock.patch.object(drone_one, "load_runtime_recipe") as load_runtime, \
                mock.patch.object(drone_one, "refresh_runtime_spawn"), \
                mock.patch.object(drone_one, "refresh_runtime_controller_params"), \
                mock.patch.object(multi_car, "resolve_config", return_value={}), \
                mock.patch.object(urban_simulation, "apply_managed_runtime") as controls, \
                mock.patch.object(multi_car, "refresh_runtime_initial_body_poses") as car_poses:
            urban_mobility.prepare_start(self.context(), composition)
        controls.assert_called_once()
        return load_runtime, car_poses

    def test_drone_and_car_placement_edits_apply_at_start(self):
        context_root = self.configure_state(self.integrated())
        moved = self.integrated(
            drone={"spawn": {"east_m": 9.0, "north_m": -40.0, "yaw_deg": 30.0}},
            car={"spawn": {"east_m": 44.0, "north_m": 7.0, "yaw_deg": 10.0}},
        )
        load_runtime, car_poses = self.start(moved, context_root)
        load_runtime.assert_called_once()
        car_poses.assert_called_once()
        drone = drone_one.base.load_simple_yaml(context_root / "config" / urban_simulation.DRONE_RECIPE_FILE)
        self.assertEqual(drone["drone"]["spawn_pose_enu"]["east_m"], 9.0)
        config = json.loads((context_root / "config/urban-composition.json").read_text(encoding="utf-8"))
        self.assertEqual(config["inputs"]["ackermann_vehicles"]["vehicles"][0]["spawn_pose_enu"]["east_m"], 44.0)

    def test_non_placement_edit_writes_nothing(self):
        context_root = self.configure_state(self.integrated())
        drone_path = context_root / "config" / urban_simulation.DRONE_RECIPE_FILE
        before = drone_path.read_text(encoding="utf-8")
        edited = self.integrated(
            car={"control": "rc", "params": {}},
            drone={"spawn": {"east_m": 9.0, "north_m": -40.0, "yaw_deg": 30.0}},
        )
        with self.assertRaisesRegex(urban_simulation.SimulationError, "run configure"):
            self.start(edited, context_root)
        self.assertEqual(drone_path.read_text(encoding="utf-8"), before)


class ControlsTest(IntegratedFixture):
    """Manifest controls must reproduce the control processes the tools write today."""

    PYTHON = "/foundation/python"

    def car_runtime(self, work: Path):
        with mock.patch.object(multi_car, "foundation_python", return_value=Path(self.PYTHON)):
            return urban_simulation.car_runtime(work)

    def drone_runtime(self, paths, pdu_def: Path):
        with mock.patch.object(multi_car, "foundation_python", return_value=Path(self.PYTHON)):
            return urban_simulation.drone_runtime(paths, pdu_def)

    def processes(self, composition: Path, runtimes: dict) -> list[dict]:
        return urban_controls.control_processes(self.load(composition), runtimes)

    def car_launcher(self, work: Path, vehicles: list[dict], route_scenario: dict | None = None) -> list[dict]:
        source = {
            "plant": ROOT / "build/bin/urban-car-hakoniwa-asset",
            "core_config": ROOT / "core.json",
            "ps5_sender": ROOT / "apps/car/ps5_ackermann_sender.py",
            "ps5_mapping": ROOT / "config/car/dualsense-controller.json",
            "scenario_executor": ROOT / "apps/car/scenario_executor.py",
        }
        with mock.patch.object(multi_car, "paths", return_value=source), \
                mock.patch.object(multi_car, "foundation_python", return_value=Path(self.PYTHON)), \
                mock.patch.object(multi_car, "foundation_install", return_value=Path("/install")), \
                mock.patch.object(multi_car, "launcher_supports_cleanup", return_value=True):
            path = multi_car.materialize_launcher(
                {"manifest": work / "manifest.json", "pdu_def": work / "config/car/urban-car-pdudef.json"},
                work / "out", 2, vehicles, route_scenario=route_scenario,
            )
        return json.loads(path.read_text(encoding="utf-8"))["assets"]

    def assertSameProcess(self, generated: dict, legacy: dict):
        def normalized(asset: dict) -> dict:
            def value(item):
                try:
                    return float(item)
                except ValueError:
                    return str(Path(item)) if ("/" in item or "\\" in item) else item
            return {
                "command": str(Path(asset["command"])),
                "args": [value(str(item)) for item in asset["args"]],
                "cwd": asset.get("cwd"),
                "depends_on": asset["depends_on"],
                "activation_timing": asset["activation_timing"],
                "delay_sec": asset["delay_sec"],
            }
        self.assertEqual(normalized(generated), normalized(legacy))

    def test_car_rc_matches_the_multi_car_ps5_sender(self):
        [generated] = self.processes(self.composition(), {"ackermann-mujoco": self.car_runtime(self.work)})
        legacy = self.car_launcher(self.work, [{"name": "Car-1", "prefix": "car_1_", "control_mode": "ps5"}])
        self.assertSameProcess(generated, legacy[1])
        self.assertEqual(generated["name"], "control-car-1-rc")

    def test_car_api_matches_the_multi_car_scenario_executor(self):
        composition = self.composition(vehicles=[
            {"name": name, "asset": "golf-cart", "control": "api", "params": {"scenario": str(self.SCENARIO)},
             "spawn": {"east_m": east, "north_m": 0.0, "yaw_deg": 0.0}}
            for name, east in (("Car-1", 0.0), ("Car-2", 5.0))
        ])
        generated = self.processes(composition, {"ackermann-mujoco": self.car_runtime(self.work)})
        legacy = self.car_launcher(
            self.work,
            [{"name": "Car-1", "prefix": "car_1_", "control_mode": "external_python"},
             {"name": "Car-2", "prefix": "car_2_", "control_mode": "external_python"}],
            route_scenario={"scenario": self.SCENARIO, "auto_start_scenario": True},
        )
        self.assertEqual(len(generated), 1, "one executor drives every api Car")
        self.assertSameProcess(generated[0], legacy[1])

    def drone_paths(self):
        from types import SimpleNamespace

        paths = SimpleNamespace(recipe_config=self.work / "drone/config", recipe_validation=self.work / "drone/validation")
        paths.recipe_config.mkdir(parents=True)
        return paths

    def drone_launcher(self, paths) -> Path:
        path = paths.recipe_config / "launcher.json"
        path.write_text(json.dumps({"assets": [
            {"name": "drone-service-1", "args": []},
            {"name": "show-runner", "command": self.PYTHON, "args": [], "depends_on": ["drone-service-1"]},
        ]}), encoding="utf-8")
        return path

    def test_drone_rc_matches_the_drone_one_ps4_controller(self):
        paths = self.drone_paths()
        pdu_def = paths.recipe_config / "pdudef/drone-pdudef-current.json"
        [generated] = self.processes(self.drone(), {"drone-core": self.drone_runtime(paths, pdu_def)})
        legacy_path = drone_one.patch_rc_launcher(
            self.drone_launcher(paths), paths=paths, drone_root=drone_one.DEFAULT_DRONE_ROOT
        )
        legacy = json.loads(legacy_path.read_text(encoding="utf-8"))["assets"][1]
        self.assertSameProcess(generated, legacy)

    def test_drone_api_matches_the_drone_one_mission(self):
        paths = self.drone_paths()
        mission = self.work / "mission.json"
        mission.write_text("{}", encoding="utf-8")
        composition = self.drone(control="api", params={"mission": str(mission)})
        [generated] = self.processes(
            composition, {"drone-core": self.drone_runtime(paths, paths.recipe_config / "pdudef/x.json")}
        )
        legacy_path = drone_one.patch_launcher(
            self.drone_launcher(paths), paths=paths, drone_root=drone_one.DEFAULT_DRONE_ROOT,
            mission_path=mission.resolve(),
        )
        legacy = json.loads(legacy_path.read_text(encoding="utf-8"))["assets"][1]
        self.assertSameProcess(generated, legacy)

    def test_params_reach_the_control_arguments(self):
        [process] = self.processes(
            self.composition(vehicle={"params": {"max_speed": 2.0}}),
            {"ackermann-mujoco": self.car_runtime(self.work)},
        )
        args = process["args"]
        self.assertEqual(float(args[args.index("--max-speed") + 1]), 2.0)

    def test_composition_replaces_the_program_with_placeholders(self):
        script = self.work / "my_driver.py"
        script.write_text("", encoding="utf-8")
        [process] = self.processes(
            self.composition(vehicle={
                "program": "my_driver.py",
                "args": ["--robot", "${vehicle.name}", "--pdu-def", "${runtime.pdu_def}"],
            }),
            {"ackermann-mujoco": self.car_runtime(self.work)},
        )
        self.assertEqual(process["args"], [
            str(script.resolve()), "--robot", "Car-1",
            "--pdu-def", str(self.work / "config/car/urban-car-pdudef.json"),
        ])
        self.assertEqual(process["cwd"], str(self.work))

    def test_placeholder_errors_name_the_problem(self):
        runtime = self.car_runtime(self.work)
        cases = {
            "${runtime.nope}": "provides no",
            "${vehicle.color}": "unknown placeholder",
            "${oops}": "unknown placeholder",
        }
        for arg, message in cases.items():
            composition = self.composition(vehicle={"program": "x.py", "args": [arg]})
            with self.subTest(arg=arg), self.assertRaisesRegex(urban_controls.ControlError, message):
                self.processes(composition, {"ackermann-mujoco": runtime})

    def test_program_and_args_replace_together(self):
        with self.assertRaisesRegex(urban_composition.CompositionError, "together"):
            self.load(self.composition(vehicle={"program": "x.py"}))

    def test_apply_controls_replaces_every_legacy_control(self):
        launcher = {"assets": [
            {"name": "drone-service-1"}, {"name": "urban-car-fleet-plant"},
            {"name": "urban-car-2-ps5-controller"}, {"name": "urban-car-scenario-executor"},
            {"name": "urban-drone-ps4-controller"}, {"name": "urban-vehicle-web-bridge"},
        ]}
        urban_controls.apply_controls(launcher, [{"name": "control-car-1-rc"}, {"name": "control-drone-1-rc"}])
        self.assertEqual([asset["name"] for asset in launcher["assets"]], [
            "drone-service-1", "urban-car-fleet-plant", "control-car-1-rc", "control-drone-1-rc",
            "urban-vehicle-web-bridge",
        ])

    def test_drone_one_applies_the_composition_controls_file(self):
        paths = self.drone_paths()
        path = drone_one.patch_rc_launcher(self.drone_launcher(paths), paths=paths, drone_root=drone_one.DEFAULT_DRONE_ROOT)
        (paths.recipe_config / urban_controls.CONTROLS_FILE).write_text(
            json.dumps([{"name": "control-drone-1-rc", "args": ["x"]}]), encoding="utf-8"
        )
        drone_one.apply_composition_controls(path, paths)
        names = [asset["name"] for asset in json.loads(path.read_text(encoding="utf-8"))["assets"]]
        self.assertEqual(names, ["drone-service-1", "control-drone-1-rc"])

    def test_configured_launcher_gets_the_manifest_controls(self):
        work = self.work / "recipe"
        target = urban_simulation.ManagedTarget(
            managed_recipe=ROOT / "recipes/usecases/urban-car-rc.yaml", recipe_id="urban-car-rc",
            use_case="car-rc", work=work,
        )
        (work / "config").mkdir(parents=True)
        (work / "config/launcher.json").write_text(json.dumps({"assets": [
            {"name": "urban-car-fleet-plant"}, {"name": "urban-car-1-ps5-controller"},
            {"name": "urban-vehicle-web-bridge"},
        ]}), encoding="utf-8")
        with self.catalog(), mock.patch.object(multi_car, "foundation_python", return_value=Path(self.PYTHON)):
            urban_simulation.apply_managed_runtime(target, self.composition(vehicle={"params": {"max_speed": 1.5}}))
        assets = json.loads((work / "config/launcher.json").read_text(encoding="utf-8"))["assets"]
        self.assertEqual([asset["name"] for asset in assets],
                         ["urban-car-fleet-plant", "urban-realtime-pacer", "control-car-1-rc", "urban-vehicle-web-bridge"])
        self.assertIn("1.5", assets[2]["args"])
        self.assertEqual(assets[1]["args"][-1], "100", "the Car plant's Conductor max_delay")


try:
    import mujoco  # noqa: F401
except ImportError:
    mujoco = None


class FpvCompositionTest(Fixture):
    """Plain World + FPV Drone through tools/fpv.py."""

    def fpv(self, **vehicle) -> Path:
        entry = {"name": "Drone-1", "asset": "fpv-drone-master3x", "control": "rc",
                 "spawn": {"east_m": 0.0, "north_m": 0.0, "yaw_deg": 90.0}}
        entry.update(vehicle)
        return self.composition(world="fpv-training-course", vehicles=[entry], viewer={})

    def test_catalog_reads_manifests_owned_by_source_repositories(self):
        names = {path.name for path in urban_assets.source_repository_manifests()}
        self.assertLessEqual({"fpv-drone-master3x.asset.yaml", "fpv-training-course.asset.yaml"}, names)
        self.assertEqual(self.assets["fpv-training-course"].kind, "plain")

    def test_spawn_is_ned_with_the_asset_clearance(self):
        spawn = urban_composition.to_fpv_spawn(
            self.load(self.fpv(spawn={"east_m": 4.0, "north_m": 17.0, "yaw_deg": 0.0})),
            ground=lambda east, north: 5.0,
        )
        self.assertEqual(spawn["position_meter"], [17.0, 4.0, -5.016])
        self.assertEqual(spawn["angle_degree"], [0.0, 0.0, 90.0], "ENU yaw 0 (east) is NED yaw 90")
        facing_north = urban_composition.to_fpv_spawn(self.load(self.fpv()), ground=lambda east, north: 0.0)
        self.assertEqual(facing_north["angle_degree"], [0.0, 0.0, 0.0])

    def test_plain_world_model_holds_the_course_without_a_vehicle(self):
        import xml.etree.ElementTree as ET

        world = urban_composition.plain_world_yaml(self.load(self.fpv()))
        mjcf = urban_composition.plain_world_mjcf(world)
        self.assertEqual(mjcf.parent, self.work / "plain-world-cache")
        root = ET.parse(mjcf).getroot()
        self.assertIsNotNone(root.find("./worldbody/geom[@name='ground']"))
        self.assertIsNotNone(root.find("./worldbody/body[@name='course_tower']"))
        self.assertIsNone(root.find(".//body[@name='drone_base']"))
        self.assertEqual(urban_composition.plain_world_mjcf(world), mjcf, "cached")

    @unittest.skipIf(mujoco is None, "MuJoCo Python is not installed")
    def test_fpv_spawn_lands_on_course_obstacles(self):
        import world_height

        world = urban_composition.plain_world_yaml(self.load(self.fpv()))
        ground = world_height.ray_ground(urban_composition.plain_world_mjcf(world), cache_dir=self.work / "heights")
        # fpv-training-course.yaml: tower 5 m tall at MuJoCo (17, -4), low bar 1.3 m at (11, 3.5).
        self.assertAlmostEqual(ground(4.0, 17.0), 5.0, places=6)
        self.assertAlmostEqual(ground(-3.5, 11.0), 1.3, places=6)
        self.assertAlmostEqual(ground(0.0, 0.0), 0.0, places=6)

    def test_fpv_rc_matches_the_fpv_tool_remote_controller(self):
        with mock.patch.object(multi_car, "foundation_python", return_value=Path("/foundation/python")):
            runtime = urban_simulation.fpv_runtime()
        [process] = urban_controls.control_processes(self.load(self.fpv()), {"drone-core": runtime})
        fpv_root = urban_assets.WORKSPACE / "hakoniwa-fpv-drone"
        drone_core = urban_assets.WORKSPACE / "hakoniwa-drone-core"
        # tools/fpv.py writes fpv-remote-controller with these fields.
        self.assertEqual(process["args"], [
            "-u", str((fpv_root / "tools/fpv_rc_bootstrap.py").resolve()),
            str(drone_core / "config/pdudef/drone-pdudef-1.json"),
            str((drone_core / "drone_api/rc/rc_config/ps4-control.json").resolve()),
            "--rc-root", str((drone_core / "drone_api/rc").resolve()),
        ])
        self.assertEqual(process["cwd"], str(fpv_root.resolve()))
        self.assertEqual(process["depends_on"], ["fpv-drone-service"])
        self.assertEqual(process["activation_timing"], "after_start")

    def fpv_runtime_dir(self, output: Path, clearance: float = 0.016) -> None:
        vehicle = output / "runtime/vehicle"
        vehicle.mkdir(parents=True)
        (vehicle / "report.json").write_text(json.dumps({"initial_pose": {"mujoco_z_m": clearance}}), encoding="utf-8")
        (vehicle / "drone_config_0.json").write_text(json.dumps({"components": {"droneDynamics": {
            "position_meter": [0.0, 0.0, -0.016], "angle_degree": [0.0, 0.0, 0.0]}}}), encoding="utf-8")
        (output / "runtime/launcher.json").write_text(json.dumps({"assets": [
            {"name": "fpv-drone-service", "args": ["vehicle", "pdudef.json", "--real-sleep-msec", "0"]},
            {"name": "fpv-realtime-pacer"}, {"name": "fpv-remote-controller"}, {"name": "fpv-threejs-http-server"},
        ]}), encoding="utf-8")

    def run_fpv(self, command: str, composition: Path, output_root: Path, calls: list):
        def run(arguments, **kwargs):
            calls.append(arguments)
            if arguments[2] == "configure":
                self.fpv_runtime_dir(Path(arguments[4]))
            return mock.Mock(returncode=0)

        with self.catalog(), mock.patch.object(urban_simulation, "FPV_OUTPUT_ROOT", output_root), \
                mock.patch.object(urban_simulation.subprocess, "run", side_effect=run), \
                mock.patch.object(multi_car, "foundation_python", return_value=Path("/foundation/python")):
            return urban_simulation.fpv_command(command, composition)

    def test_configure_generates_on_the_world_and_applies_spawn_and_controls(self):
        output_root, calls = self.work / "fpv", []
        composition = self.fpv(spawn={"east_m": 2.0, "north_m": 3.0, "yaw_deg": 90.0})
        self.assertEqual(self.run_fpv("configure", composition, output_root, calls), 0)
        arguments = calls[0]
        self.assertEqual(arguments[2:5], ["configure", "--output", str(output_root / "test")])
        world = arguments[arguments.index("--world") + 1]
        self.assertTrue(world.endswith("fpv-training-course.yaml"))
        self.assertIn("--threejs", arguments)
        self.assertTrue(arguments[arguments.index("--assembly") + 1].endswith("master3x-visual-demo.assembly.json"))
        runtime = output_root / "test/runtime"
        dynamics = json.loads((runtime / "vehicle/drone_config_0.json").read_text(encoding="utf-8"))
        self.assertEqual(dynamics["components"]["droneDynamics"]["position_meter"], [3.0, 2.0, -0.016])
        names = [asset["name"] for asset in json.loads((runtime / "launcher.json").read_text(encoding="utf-8"))["assets"]]
        # The Urban pacer replaces tools/fpv.py's, right after the Drone service.
        self.assertEqual(names, ["fpv-drone-service", "urban-realtime-pacer", "control-drone-1-rc",
                                 "fpv-threejs-http-server"])

    def test_start_applies_a_moved_spawn_and_rejects_a_new_world(self):
        output_root, calls = self.work / "fpv", []
        self.run_fpv("configure", self.fpv(), output_root, calls)
        moved = self.fpv(spawn={"east_m": 6.0, "north_m": -1.0, "yaw_deg": 90.0})
        self.assertEqual(self.run_fpv("start", moved, output_root, calls), 0)
        self.assertEqual(calls[-1][2], "start")
        dynamics = json.loads((output_root / "test/runtime/vehicle/drone_config_0.json").read_text(encoding="utf-8"))
        self.assertEqual(dynamics["components"]["droneDynamics"]["position_meter"][:2], [-1.0, 6.0])
        other_world = self.work / "other-world.yaml"
        other_world.write_text(
            (FPV_ASSETS.parent / "recipes/environments/fpv-training-course.yaml").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        manifest = self.work / "assets/other-course.asset.yaml"
        manifest.write_text(yaml.safe_dump({"schema": "hakoniwa.asset/v1", "id": "other-course", "kind": "plain",
                                            "version": 1, "world": str(other_world)}), encoding="utf-8")
        self.assets = urban_assets.catalog([urban_assets.REPOSITORY_ASSETS, FPV_ASSETS, self.work / "assets"])
        path = self.fpv()
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        data["world"] = "other-course"
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
        with self.assertRaisesRegex(urban_simulation.SimulationError, "World or FPV Asset"):
            self.run_fpv("start", path, output_root, calls)

    def test_manifest_clearance_must_match_the_generated_vehicle(self):
        output_root = self.work / "fpv"
        self.fpv_runtime_dir(output_root / "test", clearance=0.03)
        with self.catalog(), mock.patch.object(multi_car, "foundation_python", return_value=Path("/python")), \
                self.assertRaisesRegex(urban_simulation.SimulationError, "update"):
            urban_simulation.apply_fpv_composition(self.fpv(), output_root / "test")

    def test_fpv_route_needs_exactly_one_fpv_drone(self):
        hexa = self.composition(world="fpv-training-course", viewer={}, vehicles=[{
            "name": "Drone-1", "asset": "eams-hexa", "control": "rc",
            "spawn": {"east_m": 0, "north_m": 0, "yaw_deg": 0}}])
        with self.assertRaisesRegex(urban_composition.CompositionError, "exactly one FPV Drone"):
            urban_composition.fpv_vehicle(self.load(hexa))

    def city_fpv(self) -> Path:
        return self.composition(world="test-city", viewer={}, vehicles=[{
            "name": "Drone-1", "asset": "fpv-drone-master3x", "control": "rc",
            "spawn": {"east_m": 0, "north_m": 0, "yaw_deg": 0}}])

    def test_city_fpv_is_generated_on_open_ground_then_composed(self):
        with self.catalog():
            _, output, selection = urban_simulation.fpv_selection(self.city_fpv())
            self.assertEqual(urban_simulation.plan(self.city_fpv()).route, "fpv")
        self.assertEqual(Path(selection["world"]), urban_simulation.FPV_CITY_GROUND)
        self.assertEqual(Path(selection["city_receipt"]), self.receipt.resolve())

    def test_city_fpv_configure_composes_before_applying_the_spawn(self):
        output_root, calls = self.work / "fpv", []
        with mock.patch.object(urban_simulation, "compose_fpv_city") as compose:
            self.assertEqual(self.run_fpv("configure", self.city_fpv(), output_root, calls), 0)
        compose.assert_called_once_with(output_root / "test", self.receipt.resolve())
        world = calls[0][calls[0].index("--world") + 1]
        self.assertEqual(Path(world), urban_simulation.FPV_CITY_GROUND)


class PlanTest(IntegratedFixture):
    """urban_simulation.plan() picks one route per Composition shape."""

    def plan(self, composition: Path):
        with self.catalog():
            return urban_simulation.plan(composition)

    def test_routes(self):
        def fpv():
            return self.composition(world="fpv-training-course", viewer={}, vehicles=[{
                "name": "Drone-1", "asset": "fpv-drone-master3x", "control": "rc",
                "spawn": {"east_m": 0, "north_m": 0, "yaw_deg": 0}}])

        # Each fixture Composition overwrites the same file, so build it per case.
        cases = {
            "car": (self.composition, "recipes/usecases/urban-car-rc.yaml"),
            "drone": (self.drone, None),
            "integrated": (self.integrated, "recipes/experiments/urban-mobility-rc.yaml"),
            "fpv": (fpv, None),
        }
        for route, (make, recipe) in cases.items():
            with self.subTest(route=route):
                selected = self.plan(make())
                self.assertEqual(selected.route, route)
                self.assertEqual(selected.managed_recipe, None if recipe is None else ROOT / recipe)

    def test_plan_json_describes_the_composition(self):
        selected = self.plan(self.integrated()).to_json()
        self.assertEqual(selected["route"], "integrated")
        self.assertEqual(selected["world"], {"id": "test-city", "kind": "city"})
        self.assertEqual([vehicle["name"] for vehicle in selected["vehicles"]], ["Car-1", "Drone-1"])
        self.assertEqual(selected["simulators"], ["ackermann-mujoco", "drone-core"])
        self.assertTrue(selected["workspace"].endswith("urban-mobility-rc"))
        json.dumps(selected)

    def test_unsupported_shapes_name_the_limit(self):
        fpv_with_car = self.composition(world="fpv-training-course", viewer={}, vehicles=[
            {"name": "Car-1", "asset": "golf-cart", "control": "rc",
             "spawn": {"east_m": 0, "north_m": 0, "yaw_deg": 0}},
            {"name": "Drone-1", "asset": "fpv-drone-master3x", "control": "rc",
             "spawn": {"east_m": 5, "north_m": 0, "yaw_deg": 0}},
        ])
        with self.assertRaisesRegex(urban_simulation.SimulationError, "exactly one FPV Drone"):
            self.plan(fpv_with_car)

    def test_plan_command_prints_json_without_running_tools(self):
        output = io.StringIO()
        with self.catalog(), mock.patch.object(urban_simulation.subprocess, "run") as run, \
                contextlib.redirect_stdout(output):
            self.assertEqual(urban_simulation.run("plan", self.composition()), 0)
        run.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["route"], "car")

    def test_managed_routes_run_urban_mobility_with_the_recipe(self):
        composition = self.integrated()
        with self.catalog(), mock.patch.object(urban_simulation.subprocess, "run",
                                               return_value=mock.Mock(returncode=0)) as run:
            self.assertEqual(urban_simulation.run("configure", composition), 0)
        arguments = run.call_args.args[0]
        self.assertEqual(Path(arguments[1]), ROOT / "tools/urban_mobility.py")
        self.assertEqual(arguments[2:], [
            "configure", "--recipe", str(ROOT / "recipes/experiments/urban-mobility-rc.yaml"),
            "--composition", str(composition.resolve()),
        ])


class PlainWorldTest(IntegratedFixture):
    """A plain World runs Cars and the EAMS Hexa through its City World job."""

    COURSE = FPV_ASSETS.parent / "recipes/environments/fpv-training-course.yaml"

    def on_plain(self, *vehicles: dict, world: str = "plain-ground") -> Path:
        return self.composition(world=world, viewer={}, vehicles=list(vehicles))

    def car(self, name="Car-1", east=0.0):
        return {"name": name, "asset": "golf-cart", "control": "rc",
                "spawn": {"east_m": east, "north_m": 0.0, "yaw_deg": 0.0}}

    def hexa(self):
        return {"name": "Drone-1", "asset": "eams-hexa", "control": "rc",
                "spawn": {"east_m": 5.0, "north_m": 5.0, "yaw_deg": 0.0}}

    def test_default_plain_world_is_in_the_catalog(self):
        default = urban_assets.catalog()["plain-ground"]
        self.assertEqual(default.kind, "plain")
        self.assertTrue(default.resolve(default.data["world"]).is_file())

    def test_job_has_the_city_world_layout_the_builders_read(self):
        receipt_path = plain_world.materialize(self.COURSE)
        job = receipt_path.parents[2]
        self.assertEqual(job.parent, self.work / "plain-world-jobs")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(receipt["kind"], "plain")
        mjcf, glb, loaded = multi_car.city_inputs(receipt_path)
        self.assertEqual(multi_car.city_collider_glb(receipt_path), job / "viewer/city-world-colliders.glb")
        self.assertEqual(multi_car.terrain_height_mjcf(loaded, 3.0, -4.0), 0.0)
        resolved = drone_one.city.city_drone._resolve_city_world(receipt_path)
        self.assertEqual(resolved.half_extent_m, {"north_south": 30.0, "east_west": 30.0})
        self.assertEqual(plain_world.materialize(self.COURSE), receipt_path, "cached")

    def test_obstacles_keep_their_yaw_as_quaternions(self):
        import math
        import xml.etree.ElementTree as ET

        mjcf, _, _ = multi_car.city_inputs(plain_world.materialize(self.COURSE))
        root = ET.parse(mjcf).getroot()
        self.assertIsNone(root.find("./compiler"), "a City World MJCF carries no compiler settings")
        tower = root.find("./worldbody/body[@name='course_tower']")
        self.assertIsNone(tower.get("euler"))
        w, _, _, z = (float(value) for value in tower.get("quat").split())
        self.assertAlmostEqual(math.degrees(2 * math.atan2(z, w)), 20.0, places=6)
        self.assertEqual(root.find("./worldbody/geom").get("type"), "hfield")

    def test_glb_holds_the_ground_and_every_obstacle_geom(self):
        import trimesh

        receipt = json.loads(plain_world.materialize(self.COURSE).read_text(encoding="utf-8"))
        scene = trimesh.load(receipt["glb"]["path"])
        # 1 ground + 5 gates x 4 bars + 3 pylons + 2 boxes.
        self.assertEqual(len(scene.geometry), 26)
        low, high = scene.bounds
        self.assertAlmostEqual(low[0], -30.0, places=6)
        self.assertAlmostEqual(high[1], 5.1, places=6, msg="GLB Y is up; the high gate tops out at 5.1 m")

    def test_plain_world_routes_like_a_city(self):
        cases = {
            "car": (self.car(),),
            "drone": (self.hexa(),),
            "integrated": (self.car(), self.hexa()),
        }
        for route, vehicles in cases.items():
            with self.subTest(route=route), self.catalog():
                self.assertEqual(urban_simulation.plan(self.on_plain(*vehicles)).route, route)

    def test_car_config_reads_the_plain_world_job(self):
        config = urban_composition.to_car_config(self.load(self.on_plain(self.car())), "urban-car-rc")
        receipt = Path(config["inputs"]["business_pack_city_receipt"]["path"])
        self.assertEqual(receipt.parents[3], self.work / "plain-world-jobs")
        self.assertEqual(config["inputs"]["ackermann_vehicles"]["vehicles"][0]["spawn_pose_enu"]["up_m"], 0.45)

    def test_plain_world_viewer_opens_threejs_directly(self):
        receipt = plain_world.materialize(self.COURSE)
        resolved = {"city_receipt": receipt, "visualization": {
            "http_port": 8000, "threejs_root": urban_assets.WORKSPACE / "hakoniwa-threejs-drone"}}
        url = multi_car.map_viewer_url(resolved, urban_assets.WORKSPACE / "x/threejs/viewer-config.json")
        self.assertTrue(url.startswith("http://127.0.0.1:8000/hakoniwa-threejs-drone/index.html?viewerConfigPath="))
        self.assertNotIn("originLat", url)

    def test_register_world_adds_a_plain_world_asset(self):
        manifest = urban_assets.register_world(self.COURSE, "my-course", directory=self.work / "user")
        asset = urban_assets.load_manifest(manifest)
        self.assertEqual((asset.id, asset.kind), ("my-course", "plain"))
        self.assertEqual(asset.resolve(asset.data["world"]), self.COURSE.resolve())

    @unittest.skipIf(mujoco is None, "MuJoCo Python is not installed")
    def test_plain_world_job_heights_match_the_course(self):
        import world_height

        mjcf, _, _ = multi_car.city_inputs(plain_world.materialize(self.COURSE))
        with contextlib.redirect_stdout(io.StringIO()):
            ground = world_height.ray_ground(mjcf, cache_dir=self.work / "heights")
        self.assertAlmostEqual(ground(4.0, 17.0), 5.0, places=6)
        self.assertAlmostEqual(ground(-3.5, 11.0), 1.3, places=6)
        self.assertAlmostEqual(ground(20.0, -20.0), 0.0, places=6)


def _drone_core_mujoco() -> Path | None:
    try:
        from mujoco_model_compiler import find_mujoco_library

        return find_mujoco_library(urban_simulation.DRONE_CORE)
    except Exception:  # noqa: BLE001 - the runtime is optional for this test
        return None


class FpvCityCompositionTest(Fixture):
    """compose_fpv_city puts the generated FPV vehicle into a City World."""

    VEHICLE = """<mujoco model="fpv">
  <compiler angle="degree" inertiafromgeom="true" inertiagrouprange="5 5"/>
  <option timestep="0.001" integrator="RK4"/>
  <worldbody>
    <light name="sun" pos="0 0 4"/>
    <geom name="ground" type="plane" size="5 5 0.1"/>
    <body name="drone_base" pos="0 0 0.016">
      <freejoint name="drone_freejoint"/>
      <geom name="frame" type="box" size="0.1 0.1 0.02" mass="0" group="2"/>
      <geom name="frame_inertial" type="box" size="0.1 0.1 0.02" mass="0.5" group="5" contype="0" conaffinity="0"/>
    </body>
  </worldbody>
</mujoco>
"""

    def fpv_output(self) -> Path:
        output = self.work / "fpv-out"
        vehicle = output / "runtime/vehicle"
        (output / "runtime/threejs/assets").mkdir(parents=True)
        vehicle.mkdir(parents=True)
        (vehicle / "drone.xml").write_text(self.VEHICLE, encoding="utf-8")
        (vehicle / "drone_config_0.json").write_text(json.dumps({"components": {"droneDynamics": {
            "mujoco": {"modelPath": "drone.xml"}}}}), encoding="utf-8")
        (output / "runtime/threejs/scene-config.json").write_text(json.dumps({
            "environments": [{"name": "fpv-training-course", "type": "fpv-course", "model": "./fpv-course.json"}],
            "main_camera": {"far": 1000}}), encoding="utf-8")
        return output

    @unittest.skipIf(_drone_core_mujoco() is None, "Drone Core MuJoCo runtime is not installed")
    def test_vehicle_joins_the_city_as_a_compiled_model_and_the_view_shows_the_city(self):
        import xml.etree.ElementTree as ET

        # A plain World job has the City World layout; it stands in for a City here.
        receipt = plain_world.materialize(FPV_ASSETS.parent / "recipes/environments/fpv-training-course.yaml")
        output = self.fpv_output()
        with contextlib.redirect_stdout(io.StringIO()):
            mjb = urban_simulation.compose_fpv_city(output, receipt)
        vehicle = output / "runtime/vehicle"
        body_only = ET.parse(vehicle / "fpv-body.xml").getroot()
        self.assertEqual([child.tag for child in body_only.find("worldbody")], ["body"], "ground and light removed")
        composed = ET.parse(vehicle / "fpv-city.xml").getroot()
        self.assertIsNotNone(composed.find(".//body[@name='drone_base']"))
        self.assertIsNotNone(composed.find(".//body[@name='course_tower']"))
        self.assertTrue(mjb.is_file())
        config = json.loads((vehicle / "drone_config_0.json").read_text(encoding="utf-8"))
        self.assertEqual(config["components"]["droneDynamics"]["mujoco"]["modelPath"], str(mjb))
        scene = json.loads((output / "runtime/threejs/scene-config.json").read_text(encoding="utf-8"))
        self.assertEqual(scene["environments"], [{"name": "city", "model": "./assets/city-world.glb"}])
        self.assertTrue((output / "runtime/threejs/assets/city-world.glb").is_file())
        output_text = io.StringIO()
        with contextlib.redirect_stdout(output_text):
            urban_simulation.compose_fpv_city(output, receipt)
        self.assertIn("Reusing compiled FPV City model", output_text.getvalue())


class RealtimePacerTest(IntegratedFixture):
    """Every route runs the Urban real-time pacer beside its Conductor owner."""

    def test_pacer_delta_fits_each_conductor_max_delay(self):
        for conductor, max_delay in (("urban-car-fleet-plant", "100"), ("drone-service-1", "20")):
            asset = urban_realtime.pacer_asset("/python", conductor)
            with self.subTest(conductor=conductor):
                self.assertEqual(asset["activation_timing"], "before_start")
                self.assertEqual(asset["depends_on"], [conductor])
                args = asset["args"]
                self.assertEqual(args[args.index("--max-delay-msec") + 1], max_delay)
                self.assertLessEqual(int(args[args.index("--delta-msec") + 1]), int(max_delay))
                self.assertTrue(Path(args[1]).is_file() and Path(args[2]).is_file())
        with self.assertRaisesRegex(urban_realtime.RealtimeError, "unknown Conductor owner"):
            urban_realtime.pacer_asset("/python", "somebody")

    def test_pacer_rejects_a_delta_beyond_max_delay_before_touching_hakoniwa(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("realtime_pacer", urban_realtime.PACER)
        pacer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pacer)
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(pacer.main(["cfg.json", "--delta-msec", "30", "--max-delay-msec", "20"]), 2)
        self.assertIn("deadlock", stderr.getvalue())

    def test_apply_pacer_follows_the_conductor_and_stops_drone_sleeps(self):
        launcher = {"assets": [
            {"name": "drone-service-1", "args": ["fleet.json", "pdudef.json"]},
            {"name": "urban-car-fleet-plant", "args": []},
            {"name": "fpv-realtime-pacer"},
            {"name": "control-drone-1-rc"},
        ]}
        pacer = urban_realtime.pacer_asset("/python", "drone-service-1")
        urban_realtime.apply_pacer(launcher, pacer, drone_services=("drone-service-1",))
        self.assertEqual([asset["name"] for asset in launcher["assets"]],
                         ["drone-service-1", "urban-realtime-pacer", "urban-car-fleet-plant", "control-drone-1-rc"])
        self.assertEqual(launcher["assets"][0]["args"][-2:], ["--real-sleep-msec", "0"])
        launcher["assets"][0]["args"][-1] = "5"
        urban_realtime.apply_pacer(launcher, pacer, drone_services=("drone-service-1",))
        self.assertEqual(launcher["assets"][0]["args"].count("--real-sleep-msec"), 1)
        self.assertEqual(launcher["assets"][0]["args"][-1], "0")
        self.assertEqual([asset["name"] for asset in launcher["assets"]].count("urban-realtime-pacer"), 1)
        with self.assertRaisesRegex(urban_realtime.RealtimeError, "no Conductor owner"):
            urban_realtime.apply_pacer({"assets": []}, pacer)

    def test_integrated_route_paces_from_the_drone_service(self):
        work = self.work / "recipe"
        (work / "config").mkdir(parents=True)
        (work / "config/launcher.json").write_text(json.dumps({"assets": [
            {"name": "drone-service-1", "args": ["fleet.json", "pdudef.json"]},
            {"name": "urban-car-fleet-plant", "args": ["--external-conductor"]},
            {"name": "urban-car-scenario-executor"}, {"name": "urban-drone-ps4-controller"},
        ]}), encoding="utf-8")
        target = urban_simulation.ManagedTarget(
            managed_recipe=ROOT / "recipes/experiments/urban-mobility-rc.yaml", recipe_id="urban-mobility-rc",
            use_case="drone-car-distributed", work=work,
        )
        from types import SimpleNamespace

        paths = SimpleNamespace(recipe_config=self.work / "drone/config", recipe_validation=self.work / "drone/v")
        with self.catalog(), mock.patch.object(multi_car, "foundation_python", return_value=Path("/python")), \
                mock.patch.object(drone_one, "_paths", return_value=paths):
            urban_simulation.apply_managed_runtime(target, self.integrated())
        assets = json.loads((work / "config/launcher.json").read_text(encoding="utf-8"))["assets"]
        names = [asset["name"] for asset in assets]
        self.assertEqual(names[:2], ["drone-service-1", "urban-realtime-pacer"])
        self.assertEqual(assets[1]["args"][-1], "20")
        self.assertEqual(assets[0]["args"][-2:], ["--real-sleep-msec", "0"])
        self.assertNotIn("--real-sleep-msec", assets[2]["args"], "the Car plant has no per-step sleep flag")

    def drone_launcher(self, paths) -> Path:
        path = paths.recipe_config / "launcher.json"
        path.write_text(json.dumps({"assets": [
            {"name": "drone-service-1", "args": ["fleet.json", "pdudef.json"]},
            {"name": "urban-drone-ps4-controller", "args": []},
        ]}), encoding="utf-8")
        return path

    def test_drone_one_applies_the_pacer_from_the_controls_file(self):
        from types import SimpleNamespace

        paths = SimpleNamespace(recipe_config=self.work / "drone/config")
        paths.recipe_config.mkdir(parents=True)
        (paths.recipe_config / urban_controls.CONTROLS_FILE).write_text(json.dumps({
            "processes": [{"name": "control-drone-1-rc"}],
            "pacer": urban_realtime.pacer_asset("/python", "drone-service-1"),
            "drone_services": ["drone-service-1"],
        }), encoding="utf-8")
        path = drone_one.apply_composition_controls(self.drone_launcher(paths), paths)
        assets = json.loads(path.read_text(encoding="utf-8"))["assets"]
        self.assertEqual([asset["name"] for asset in assets],
                         ["drone-service-1", "urban-realtime-pacer", "control-drone-1-rc"])
        self.assertEqual(assets[0]["args"][-2:], ["--real-sleep-msec", "0"])

    def test_write_drone_controls_includes_the_pacer(self):
        from types import SimpleNamespace

        paths = SimpleNamespace(recipe_config=self.work / "drone/config", recipe_validation=self.work / "drone/v")
        paths.recipe_config.mkdir(parents=True)
        with self.catalog(), mock.patch.object(drone_one, "_paths", return_value=paths), \
                mock.patch.object(multi_car, "foundation_python", return_value=Path("/python")):
            written = urban_simulation.write_drone_controls(self.drone())
        data = json.loads(written.read_text(encoding="utf-8"))
        self.assertEqual(data["pacer"]["depends_on"], ["drone-service-1"])
        self.assertEqual([process["name"] for process in data["processes"]], ["control-drone-1-rc"])


if __name__ == "__main__":
    unittest.main()
