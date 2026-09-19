import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


mission = load_module(
    "urban_city_fleet_mission", ROOT / "apps/drone/city_fleet_mission.py"
)
drone_one = load_module("urban_drone_one", ROOT / "tools/drone_one.py")


class DroneMissionTest(unittest.TestCase):
    def test_detects_stable_dem_contact_window(self):
        samples = [
            (0.0, -2.56, 0.02),
            (0.4, -2.55, 0.01),
            (0.8, -2.56, 0.01),
        ]
        self.assertTrue(
            mission.settled_window(
                samples,
                stable_sec=0.75,
                maximum_speed_m_s=0.08,
                maximum_height_span_m=0.03,
            )
        )

    def test_rejects_dem_contact_window_while_still_falling(self):
        samples = [
            (0.0, -5.0, 4.0),
            (0.4, -3.8, 2.0),
            (0.8, -2.8, 0.4),
        ]
        self.assertFalse(
            mission.settled_window(
                samples,
                stable_sec=0.75,
                maximum_speed_m_s=0.08,
                maximum_height_span_m=0.03,
            )
        )

    def test_resolves_safe_relative_target_from_city_marker(self):
        marker = {
            "flight_plan": {
                "resolved_flight_altitude_m": 18.5,
                "spawn_points": [{"x_m": 4.0, "y_m": -3.0}],
            }
        }
        resolved = mission.resolve_mission(
            marker, {"drone": "Drone-1", "move_offset_m": [2.0, 0.0]}
        )
        self.assertEqual(resolved["target"], [6.0, -3.0, 18.5])
        self.assertEqual(resolved["takeoff_altitude_m"], 18.5)

    def test_rejects_target_outside_verified_launch_margin(self):
        marker = {
            "flight_plan": {
                "resolved_flight_altitude_m": 18.5,
                "spawn_points": [{"x_m": 0.0, "y_m": 0.0}],
            }
        }
        with self.assertRaises(mission.MissionError):
            mission.resolve_mission(marker, {"move_offset_m": [3.1, 0.0]})

    def test_accepts_landed_state_below_city_flight_altitude(self):
        state = types.SimpleNamespace(
            ok=True,
            mode="Idle",
            message="Landed",
            current_pose=types.SimpleNamespace(
                position=types.SimpleNamespace(z=0.9)
            ),
        )
        marker = {"flight_plan": {"resolved_flight_altitude_m": 4.384}}
        self.assertTrue(mission.landed_on_city_surface(state, marker))

    def test_rejects_non_landed_state(self):
        state = types.SimpleNamespace(
            ok=True,
            mode="Landing",
            message="descending",
            current_pose=types.SimpleNamespace(
                position=types.SimpleNamespace(z=0.9)
            ),
        )
        marker = {"flight_plan": {"resolved_flight_altitude_m": 4.384}}
        self.assertFalse(mission.landed_on_city_surface(state, marker))


class DroneOneToolTest(unittest.TestCase):
    def test_urban_recipe_resolves_all_paths_relative_to_recipe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("fleet.yaml", "city.json", "mission.json"):
                (root / name).write_text("{}", encoding="utf-8")
            recipe_path = root / "urban-drone-one.yaml"
            recipe_path.write_text(
                """version: 1
id: test-drone
fleet_experiment:
  path: fleet.yaml
city_world:
  receipt: city.json
drone:
  profile: eams-nominal-9kg
  spawn_pose_enu:
    east_m: 12.5
    north_m: -3.0
    up_m: 8.5
    yaw_deg: 45.0
  launch_area:
    mode: auto
    offset_m: [1.0, 2.0, 0.0]
    search_radius_m: 50.0
control:
  mode: fleet-rpc
mission:
  path: mission.json
viewer:
  map_layout: split
  collider_overlay_default: true
""",
                encoding="utf-8",
            )

            recipe = drone_one.load_urban_recipe(recipe_path)

            self.assertEqual(recipe.fleet_experiment, (root / "fleet.yaml").resolve())
            self.assertEqual(recipe.city_receipt, (root / "city.json").resolve())
            self.assertEqual(recipe.mission, (root / "mission.json").resolve())
            self.assertEqual(recipe.spawn_pose_enu, {
                "east_m": 12.5,
                "north_m": -3.0,
                "up_m": 8.5,
                "yaw_deg": 45.0,
            })
            self.assertEqual(recipe.control_mode, "fleet-rpc")
            self.assertEqual(recipe.map_layout, "split")
            self.assertTrue(recipe.collider_overlay_default)

    def test_open_viewer_colliders_are_opt_in(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            collider_config = root / (
                "web/map-viewer/thirdparty/hakoniwa-threejs-drone/"
                "config/viewer-config-fleets-colliders.json"
            )
            collider_config.parent.mkdir(parents=True)
            collider_config.write_text("{}", encoding="utf-8")
            opened = []
            with (
                mock.patch.object(
                    drone_one, "_paths",
                    return_value=types.SimpleNamespace(recipe_root=root),
                ),
                mock.patch.object(
                    drone_one.base, "viewer_url",
                    return_value=(
                        "http://viewer/?viewerConfigName="
                        "viewer-config-fleets.json"
                    ),
                ),
                mock.patch.object(
                    drone_one.base, "open_browser",
                    side_effect=lambda url: opened.append(url) or True,
                ),
            ):
                self.assertEqual(drone_one.open_viewer(), 0)
                self.assertEqual(
                    drone_one.open_viewer(show_colliders=True), 0
                )
            self.assertIn("viewer-config-fleets.json", opened[0])
            self.assertIn("layout=three-main", opened[0])
            self.assertIn("viewer-config-fleets-colliders.json", opened[1])

    def test_city_contact_policy_reduces_friction_and_enables_six_propellers(self):
        body = ET.fromstring(
            "<body name='drone_base'>"
            "<geom name='frame_chassis_contact' friction='0.5 0.02 0.001'/>"
            "<geom name='landing_gear_left_skid_contact' friction='1 0.02 0.001'/>"
            "<geom name='landing_gear_right_skid_contact' friction='1 0.02 0.001'/>"
            + "".join(
                f"<body><geom name='prop{i}_geom' contype='2' conaffinity='4'/></body>"
                for i in range(1, 7)
            )
            + "</body>"
        )

        policy = drone_one.apply_eams_city_contact_policy(body)

        chassis = body.find(".//geom[@name='frame_chassis_contact']")
        self.assertEqual(chassis.get("friction"), drone_one.EAMS_CHASSIS_FRICTION)
        self.assertEqual(chassis.get("priority"), drone_one.EAMS_CONTACT_PRIORITY)
        for side in ("left", "right"):
            skid = body.find(
                f".//geom[@name='landing_gear_{side}_skid_contact']"
            )
            self.assertEqual(skid.get("friction"), drone_one.EAMS_SKID_FRICTION)
            self.assertEqual(skid.get("priority"), drone_one.EAMS_CONTACT_PRIORITY)
        for index in range(1, 7):
            propeller = body.find(f".//geom[@name='prop{index}_geom']")
            self.assertEqual(propeller.get("contype"), "1")
            self.assertEqual(propeller.get("conaffinity"), "1")
            self.assertEqual(propeller.get("condim"), "1")
            self.assertEqual(
                propeller.get("priority"),
                drone_one.EAMS_CONTACT_PRIORITY,
            )
        self.assertEqual(policy["propeller_collision_geoms"], 6)
        self.assertEqual(policy["contact_priority"], 1)
        landing = body.find(
            f".//geom[@name='{drone_one.EAMS_LANDING_COLLIDER_NAME}']"
        )
        self.assertIsNotNone(landing)
        self.assertEqual(landing.get("type"), "box")
        self.assertEqual(landing.get("pos"), "0 0 -0.44")
        self.assertEqual(landing.get("size"), "0.31 0.29 0.04")
        self.assertEqual(landing.get("contype"), "1")
        self.assertEqual(landing.get("conaffinity"), "1")
        self.assertEqual(landing.get("priority"), drone_one.EAMS_CONTACT_PRIORITY)

    def test_city_contact_policy_rejects_incomplete_propeller_set(self):
        body = ET.fromstring(
            "<body>"
            "<geom name='frame_chassis_contact'/>"
            "<geom name='landing_gear_left_skid_contact'/>"
            "<geom name='landing_gear_right_skid_contact'/>"
            "</body>"
        )
        with self.assertRaises(drone_one.base.RecipeError):
            drone_one.apply_eams_city_contact_policy(body)

    def test_drone_runtime_selects_validated_mjb_instead_of_source_xml(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime_mjb = Path(directory) / "eams-hexa-city.mjb"
            runtime_mjb.write_bytes(b"mjb")
            type_config = {
                "components": {
                    "droneDynamics": {
                        "mujoco": {"modelPath": "eams-hexa-city.xml"}
                    }
                }
            }

            drone_one.select_compiled_mujoco_model(type_config, runtime_mjb)

            self.assertEqual(
                type_config["components"]["droneDynamics"]["mujoco"]["modelPath"],
                str(runtime_mjb),
            )

    def test_eams_tuning_is_loaded_from_drone_pro_and_keeps_rpc_parameters(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base_path = root / "config/controller/param-api-mixer-mujoco.txt"
            tuned_path = root / drone_one.EAMS_TUNED_PARAMS_RELATIVE
            base_path.parent.mkdir(parents=True)
            tuned_path.parent.mkdir(parents=True)
            base_path.write_text(
                "PID_ALT_Kp 10\nTAKEOFF_ALT 3\n",
                encoding="utf-8",
            )
            tuned_path.write_text(
                "PID_ALT_Kp 11.0\nPID_ROLL_Kp 17.0\n",
                encoding="utf-8",
            )

            output = root / "runtime/controller-params.txt"
            drone_one.materialize_eams_controller_params(root, output)
            params = drone_one._parameter_values(output)

            self.assertEqual(params["PID_ALT_Kp"], "11.0")
            self.assertEqual(params["PID_ROLL_Kp"], "17.0")
            self.assertEqual(params["TAKEOFF_ALT"], "3")

    def test_rc_parameters_keep_tuning_and_enable_angle_control(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base_path = root / "tuning/vehicle/eams/config/controller-params.txt"
            tuned_path = root / drone_one.EAMS_TUNED_PARAMS_RELATIVE
            base_path.parent.mkdir(parents=True)
            tuned_path.parent.mkdir(parents=True)
            base_path.write_text(
                "ANGLE_CONTROL_ENABLE 0\n"
                "ANGLE_CONTROL_ENABLE 0.0\n"
                "ANGLE_RATE_CONTROL_ENABLE 1\n"
                "ALT_SPD_CONTROL_ENABLE 1\n"
                "PID_ROLL_Kp 10\n",
                encoding="utf-8",
            )
            tuned_path.write_text(
                "ANGLE_CONTROL_ENABLE 0\n"
                "PID_ROLL_Kp 17.0\n",
                encoding="utf-8",
            )

            output = root / "runtime/controller-params.txt"
            drone_one.materialize_eams_controller_params(
                root, output, rc_mode=True
            )
            params = drone_one._parameter_values(output)

            self.assertEqual(params["ANGLE_CONTROL_ENABLE"], "0")
            self.assertEqual(params["ANGLE_RATE_CONTROL_ENABLE"], "0")
            self.assertEqual(params["ALT_SPD_CONTROL_ENABLE"], "0")
            self.assertEqual(params["CTRLMODE_START_IN_HOVERING"], "0")
            self.assertEqual(
                params["CTRLMODE_TAKEOFF_TRIGGER_THROTTLE_VALUE"], "0.1"
            )
            self.assertEqual(
                params["CTRLMODE_LANDING_TRIGGER_THROTTLE_VALUE"], "0.3"
            )
            self.assertEqual(params["PID_ROLL_Kp"], "17.0")
            self.assertEqual(
                sum(
                    line.startswith("ANGLE_CONTROL_ENABLE ")
                    for line in output.read_text(encoding="utf-8").splitlines()
                ),
                1,
            )

    def test_runtime_spawn_converts_enu_pose_to_ned_fleet_pose(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fleet_path = root / "fleet.json"
            fleet_path.write_text(
                json.dumps(
                    {
                        "drones": [
                            {
                                "name": "Drone-1",
                                "position_meter": [5.0, -45.0, -2.884],
                                "angle_degree": [0.0, 0.0, 0.0],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            marker = {
                "fleet_config": str(fleet_path),
                "flight_plan": {
                    "spawn_points": [{"surface_height_m": 2.384}]
                },
            }

            drone_one.set_runtime_spawn(marker, {
                "east_m": 45.0,
                "north_m": 7.5,
                "up_m": 10.3,
                "yaw_deg": 0.0,
            })

            fleet = json.loads(fleet_path.read_text(encoding="utf-8"))
            self.assertEqual(
                fleet["drones"][0]["position_meter"], [7.5, 45.0, -10.3]
            )
            self.assertEqual(
                fleet["drones"][0]["angle_degree"], [0.0, 0.0, 90.0]
            )
            self.assertEqual(
                marker["flight_plan"]["runtime_spawn"]["frame"], "ENU"
            )

    def test_start_restores_urban_controller_params_to_recipe_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "urban-controller-params.txt"
            source.write_text(
                "PID_ROLL_RATE_Kp 2.7\nPID_PITCH_RATE_Kp 2.7\n",
                encoding="utf-8",
            )
            runtime = root / "recipe/rc/controller-params.txt"
            runtime.parent.mkdir(parents=True)
            runtime.write_text("PID_ROLL_RATE_Kp 3.0\n", encoding="utf-8")
            recipe = types.SimpleNamespace(
                control_mode="ps4-rc",
                controller_params=source,
            )
            paths = types.SimpleNamespace(recipe_root=root / "recipe")

            result = drone_one.refresh_runtime_controller_params(paths, recipe)

            self.assertEqual(result, runtime)
            self.assertEqual(runtime.read_bytes(), source.read_bytes())

    def test_runtime_recipe_accepts_pose_only_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("fleet.yaml", "city.json", "mission.json"):
                (root / name).write_text("{}", encoding="utf-8")
            recipe_path = root / "urban-drone-one.yaml"

            def write_recipe(*, east_m: float, city: str = "city.json") -> None:
                recipe_path.write_text(
                    f"""version: 1
id: test-drone
fleet_experiment:
  path: fleet.yaml
city_world:
  receipt: {city}
drone:
  profile: eams-nominal-9kg
  spawn_pose_enu:
    east_m: {east_m}
    north_m: 2.0
    up_m: 8.0
    yaw_deg: 0.0
  launch_area:
    mode: auto
    offset_m: [0.0, 0.0, 0.0]
    search_radius_m: 50.0
control:
  mode: ps4-rc
mission:
  path: mission.json
viewer:
  map_layout: bottom-left
  collider_overlay_default: false
""",
                    encoding="utf-8",
                )

            write_recipe(east_m=1.0)
            configured = drone_one.load_urban_recipe(recipe_path)
            write_recipe(east_m=9.0)

            runtime = drone_one.load_runtime_recipe(configured)

            self.assertEqual(runtime.spawn_pose_enu["east_m"], 9.0)

    def test_runtime_recipe_requires_configure_after_city_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("fleet.yaml", "city-a.json", "city-b.json", "mission.json"):
                (root / name).write_text("{}", encoding="utf-8")
            recipe_path = root / "urban-drone-one.yaml"
            template = """version: 1
id: test-drone
fleet_experiment:
  path: fleet.yaml
city_world:
  receipt: {city}
drone:
  profile: eams-nominal-9kg
  spawn_pose_enu:
    east_m: 1.0
    north_m: 2.0
    up_m: 8.0
    yaw_deg: 0.0
  launch_area:
    mode: auto
    offset_m: [0.0, 0.0, 0.0]
    search_radius_m: 50.0
control:
  mode: ps4-rc
mission:
  path: mission.json
viewer:
  map_layout: bottom-left
  collider_overlay_default: false
"""
            recipe_path.write_text(template.format(city="city-a.json"), encoding="utf-8")
            configured = drone_one.load_urban_recipe(recipe_path)
            recipe_path.write_text(template.format(city="city-b.json"), encoding="utf-8")

            with self.assertRaisesRegex(drone_one.base.RecipeError, "run configure"):
                drone_one.load_runtime_recipe(configured)

    def test_native_mujoco_viewer_is_opt_in_launcher_argument(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            launcher_path = root / "launcher.json"
            launcher_path.write_text(
                json.dumps(
                    {
                        "assets": [
                            {"name": "drone-service-1", "args": ["api.json"]},
                            {"name": "show-runner", "depends_on": []},
                            {
                                "name": "visual-state-publisher",
                                "depends_on": ["show-runner"],
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            paths = types.SimpleNamespace(
                recipe_config=root / "config",
                recipe_validation=root / "validation",
            )

            drone_one.patch_launcher(
                launcher_path,
                paths=paths,
                drone_root=root / "drone-core",
                mission_path=root / "mission.json",
                mujoco_viewer=True,
            )

            launcher = json.loads(launcher_path.read_text(encoding="utf-8"))
            self.assertEqual(
                launcher["runtime"], {"cleanup_mmap_on_start": True}
            )
            service = next(
                asset
                for asset in launcher["assets"]
                if asset["name"] == "drone-service-1"
            )
            self.assertIn("--mujoco-viewer", service["args"])
            self.assertEqual(service["readiness"], {
                "type": "hako_asset",
                "asset_name": "drone",
                "timeout_sec": drone_one.DRONE_SERVICE_READINESS_TIMEOUT_SEC,
            })

    def test_city_viewer_uses_eams_hexa_and_all_six_motor_channels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            embedded = (
                root / "web/map-viewer/thirdparty/hakoniwa-threejs-drone"
            )
            config = embedded / "config"
            models = embedded / "assets/models"
            local_models = embedded / "assets/local_models"
            config.mkdir(parents=True)
            models.mkdir(parents=True)
            local_models.mkdir(parents=True)
            (config / "drone_config-city-fleet.json").write_text(
                json.dumps(
                    {
                        "environments": [
                            {"name": "city-world", "model": "city-world.glb"}
                        ],
                        "droneTypesPath": "./drone_types-quadrotor_base.json",
                        "drones": [{"name": "Drone", "type": "quadrotor_base"}],
                    }
                ),
                encoding="utf-8",
            )
            (config / "viewer-config-fleets.json").write_text(
                json.dumps({"stateInput": {"fleets": {"roleMap": {}}}}),
                encoding="utf-8",
            )
            (config / "drone_types-hexa-eams.json").write_text(
                "{}", encoding="utf-8"
            )
            (models / "eams-hexa-frame.glb").write_bytes(b"glb")
            city_job = root / "city-job"
            receipt = city_job / "build/world/city-world-receipt.json"
            receipt.parent.mkdir(parents=True)
            receipt.write_text("{}", encoding="utf-8")
            collider = city_job / "viewer/city-world-colliders.glb"
            collider.parent.mkdir(parents=True)
            collider.write_bytes(b"collider")
            (root / "config").mkdir(parents=True)
            (root / "config/mujoco-city-fleet.json").write_text(
                json.dumps({"city_world": {"receipt": str(receipt)}}),
                encoding="utf-8",
            )

            drone_one.patch_eams_city_viewer(
                types.SimpleNamespace(recipe_root=root, recipe_config=root / "config")
            )

            scene = json.loads(
                (config / "drone_config-city-fleet.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(scene["droneTypesPath"], "./drone_types-hexa-eams.json")
            self.assertEqual(scene["drones"][0]["type"], "hexa_eams")
            self.assertFalse(any(
                environment["name"] == "city-world-colliders"
                for environment in scene["environments"]
            ))
            collider_scene = json.loads(
                (config / "drone_config-city-fleet-colliders.json").read_text(
                    encoding="utf-8"
                )
            )
            collider_environment = next(
                environment for environment in collider_scene["environments"]
                if environment["name"] == "city-world-colliders"
            )
            self.assertEqual(collider_environment["render"]["mode"], "wireframe")
            self.assertEqual(collider_environment["render"]["color"], "#22c55e")
            self.assertEqual(
                (local_models / "city-world-colliders.glb").read_bytes(),
                b"collider",
            )
            viewer = json.loads(
                (config / "viewer-config-fleets.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                viewer["stateInput"]["fleets"]["motorChannels"],
                [0, 1, 2, 3, 4, 5],
            )
            self.assertTrue(viewer["ui"]["enableAttachedCameras"])
            collider_viewer = json.loads(
                (config / "viewer-config-fleets-colliders.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                collider_viewer["three"]["sceneConfigPath"],
                "./drone_config-city-fleet-colliders.json",
            )

    def test_ps4_mode_replaces_automatic_mission_in_single_drone_launcher(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            launcher_path = root / "launcher.json"
            launcher_path.write_text(
                json.dumps(
                    {
                        "assets": [
                            {"name": "drone-service-1"},
                            {
                                "name": "show-runner",
                                "command": "/foundation/python",
                                "depends_on": ["drone-service-1"],
                            },
                            {
                                "name": "visual-state-publisher",
                                "depends_on": ["show-runner"],
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            paths = types.SimpleNamespace(recipe_config=root / "config")

            drone_one.patch_rc_launcher(
                launcher_path, paths=paths, drone_root=root / "drone-pro"
            )

            launcher = json.loads(launcher_path.read_text(encoding="utf-8"))
            self.assertEqual(
                launcher["runtime"], {"cleanup_mmap_on_start": True}
            )
            controller = next(
                asset
                for asset in launcher["assets"]
                if asset["name"] == "urban-drone-ps4-controller"
            )
            self.assertTrue(controller["args"][0].endswith("rc-custom.py"))
            self.assertEqual(controller["args"][-4:], ["--name", "Drone-1", "--index", "0"])
            service = next(
                asset
                for asset in launcher["assets"]
                if asset["name"] == "drone-service-1"
            )
            self.assertEqual(service["readiness"]["asset_name"], "drone")
            self.assertEqual(
                service["readiness"]["timeout_sec"],
                drone_one.DRONE_SERVICE_READINESS_TIMEOUT_SEC,
            )
            publisher = next(
                asset
                for asset in launcher["assets"]
                if asset["name"] == "visual-state-publisher"
            )
            self.assertEqual(publisher["depends_on"], ["drone-service-1"])


if __name__ == "__main__":
    unittest.main()
