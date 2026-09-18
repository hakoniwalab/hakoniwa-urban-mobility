import importlib.util
import json
from pathlib import Path
import tempfile
import types
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
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
        for side in ("left", "right"):
            skid = body.find(
                f".//geom[@name='landing_gear_{side}_skid_contact']"
            )
            self.assertEqual(skid.get("friction"), drone_one.EAMS_SKID_FRICTION)
        for index in range(1, 7):
            propeller = body.find(f".//geom[@name='prop{index}_geom']")
            self.assertEqual(propeller.get("contype"), "1")
            self.assertEqual(propeller.get("conaffinity"), "1")
            self.assertEqual(propeller.get("condim"), "1")
        self.assertEqual(policy["propeller_collision_geoms"], 6)

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

    def test_initial_drop_altitude_updates_ned_fleet_position(self):
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

            drone_one.set_initial_drop_altitude(marker, 7.0)

            fleet = json.loads(fleet_path.read_text(encoding="utf-8"))
            self.assertEqual(fleet["drones"][0]["position_meter"][2], -7.0)
            self.assertAlmostEqual(
                marker["flight_plan"]["initial_drop"]["drop_distance_m"], 4.616
            )

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
                mujoco_viewer=True,
            )

            launcher = json.loads(launcher_path.read_text(encoding="utf-8"))
            service = next(
                asset
                for asset in launcher["assets"]
                if asset["name"] == "drone-service-1"
            )
            self.assertIn("--mujoco-viewer", service["args"])


if __name__ == "__main__":
    unittest.main()
