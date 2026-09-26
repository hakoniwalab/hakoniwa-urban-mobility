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
from tools import urban_mobility  # noqa: E402
import drone_one  # noqa: E402
import multi_car  # noqa: E402

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
        self.assets = urban_assets.catalog([urban_assets.REPOSITORY_ASSETS, self.work / "assets"])

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
        self.assertEqual(inputs["ackermann_vehicles"]["vehicles"], expected["ackermann_vehicles"]["vehicles"])
        self.assertEqual(inputs["ackermann_runtime"], expected["ackermann_runtime"])
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
            recipe = urban_mobility.composition_recipe(self.composition())
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
                mock.patch.object(urban_mobility, "apply_composition_controls") as controls, \
                mock.patch.object(multi_car, "refresh_runtime_initial_body_poses") as refresh:
            urban_mobility.prepare_start(context, composition)
        controls.assert_called_once_with(context, composition)
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
        with self.assertRaisesRegex(urban_mobility.UrbanMobilityError, "run configure"):
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
        recipe_path = self.work / "workspace/config" / urban_mobility.DRONE_RECIPE_FILE
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            return mock.Mock(returncode=0)

        with self.catalog(), \
                mock.patch.object(urban_mobility, "drone_recipe_path", return_value=recipe_path), \
                mock.patch.object(urban_composition, "city_terrain_height", return_value=lambda east, north: 0.0), \
                mock.patch.object(urban_mobility.subprocess, "run", side_effect=run):
            composition = self.drone()
            self.assertEqual(urban_mobility.drone_composition_command("configure", composition), 0)
            self.assertEqual(calls[-1][-3:], ["configure", "--recipe", str(recipe_path)])
            self.assertEqual(drone_one.base.load_simple_yaml(recipe_path)["control"], {"mode": "ps4-rc"})
            # A placement edit rewrites the tool recipe before start.
            self.drone(spawn={"east_m": 7.0, "north_m": 0.0, "yaw_deg": 0.0})
            self.assertEqual(urban_mobility.drone_composition_command("start", composition), 0)
            self.assertEqual(calls[-1][-1], "start")
            spawn = drone_one.base.load_simple_yaml(recipe_path)["drone"]["spawn_pose_enu"]
            self.assertEqual(spawn["east_m"], 7.0)

    def test_drone_start_requires_configure(self):
        with mock.patch.object(urban_mobility, "drone_recipe_path", return_value=self.work / "missing.yaml"):
            with self.assertRaisesRegex(urban_mobility.UrbanMobilityError, "not configured"):
                urban_mobility.drone_composition_command("start", self.drone())


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
            recipe = urban_mobility.composition_recipe(self.integrated())
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
        with self.catalog(), mock.patch.object(urban_mobility, "root", return_value=context_root), \
                mock.patch.object(urban_composition, "city_terrain_height", return_value=lambda east, north: 1.0):
            config, drone = urban_mobility.composition_outputs(self.context(), composition)
            urban_mobility.write_composition_outputs(self.context(), config, drone)
        (context_root / "validation/urban-inputs.json").write_text(
            json.dumps({"composition": str(composition)}), encoding="utf-8"
        )
        return context_root

    def start(self, composition: Path, context_root: Path):
        with self.catalog(), mock.patch.object(urban_mobility, "root", return_value=context_root), \
                mock.patch.object(urban_composition, "city_terrain_height", return_value=lambda east, north: 1.0), \
                mock.patch.object(drone_one, "_paths"), \
                mock.patch.object(drone_one, "read_selected_recipe"), \
                mock.patch.object(drone_one, "load_runtime_recipe") as load_runtime, \
                mock.patch.object(drone_one, "refresh_runtime_spawn"), \
                mock.patch.object(drone_one, "refresh_runtime_controller_params"), \
                mock.patch.object(multi_car, "resolve_config", return_value={}), \
                mock.patch.object(urban_mobility, "apply_composition_controls") as controls, \
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
        drone = drone_one.base.load_simple_yaml(context_root / "config" / urban_mobility.DRONE_RECIPE_FILE)
        self.assertEqual(drone["drone"]["spawn_pose_enu"]["east_m"], 9.0)
        config = json.loads((context_root / "config/urban-composition.json").read_text(encoding="utf-8"))
        self.assertEqual(config["inputs"]["ackermann_vehicles"]["vehicles"][0]["spawn_pose_enu"]["east_m"], 44.0)

    def test_non_placement_edit_writes_nothing(self):
        context_root = self.configure_state(self.integrated())
        drone_path = context_root / "config" / urban_mobility.DRONE_RECIPE_FILE
        before = drone_path.read_text(encoding="utf-8")
        edited = self.integrated(
            car={"control": "rc", "params": {}},
            drone={"spawn": {"east_m": 9.0, "north_m": -40.0, "yaw_deg": 30.0}},
        )
        with self.assertRaisesRegex(urban_mobility.UrbanMobilityError, "run configure"):
            self.start(edited, context_root)
        self.assertEqual(drone_path.read_text(encoding="utf-8"), before)


class ControlsTest(IntegratedFixture):
    """Manifest controls must reproduce the control processes the tools write today."""

    PYTHON = "/foundation/python"

    def car_runtime(self, work: Path):
        with mock.patch.object(multi_car, "foundation_python", return_value=Path(self.PYTHON)):
            return urban_mobility.car_runtime(work)

    def drone_runtime(self, paths, pdu_def: Path):
        with mock.patch.object(multi_car, "foundation_python", return_value=Path(self.PYTHON)):
            return urban_mobility.drone_runtime(paths, pdu_def)

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
        context = urban_mobility.RecipeContext(
            path=ROOT / "recipes/usecases/urban-car-rc.yaml", data={}, recipe_id="urban-car-rc", use_case="car-rc",
        )
        work = self.work / "recipe"
        (work / "config").mkdir(parents=True)
        (work / "config/launcher.json").write_text(json.dumps({"assets": [
            {"name": "urban-car-fleet-plant"}, {"name": "urban-car-1-ps5-controller"},
            {"name": "urban-vehicle-web-bridge"},
        ]}), encoding="utf-8")
        with self.catalog(), mock.patch.object(urban_mobility, "root", return_value=work), \
                mock.patch.object(multi_car, "foundation_python", return_value=Path(self.PYTHON)):
            urban_mobility.apply_composition_controls(context, self.composition(vehicle={"params": {"max_speed": 1.5}}))
        assets = json.loads((work / "config/launcher.json").read_text(encoding="utf-8"))["assets"]
        self.assertEqual([asset["name"] for asset in assets],
                         ["urban-car-fleet-plant", "control-car-1-rc", "urban-vehicle-web-bridge"])
        self.assertIn("1.5", assets[1]["args"])


if __name__ == "__main__":
    unittest.main()
