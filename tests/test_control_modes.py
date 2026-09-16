from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = ROOT / "tools/multi_car.py"
SPEC = importlib.util.spec_from_file_location("multi_car", TOOL_PATH)
assert SPEC is not None and SPEC.loader is not None
multi_car = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(multi_car)

CLIENT_PATH = ROOT / "apps/car/urban_car.py"
CLIENT_SPEC = importlib.util.spec_from_file_location("urban_car", CLIENT_PATH)
assert CLIENT_SPEC is not None and CLIENT_SPEC.loader is not None
urban_car = importlib.util.module_from_spec(CLIENT_SPEC)
CLIENT_SPEC.loader.exec_module(urban_car)


class FakeTransport:
    def __init__(self):
        self.sent = []
        self.connected = False

    def connect(self):
        self.connected = True

    def close(self):
        self.connected = False

    def channel_id(self, robot, pdu):
        return (
            0
            if robot in {"Car-1", "Car-2"} and pdu == "ackermann_cmd"
            else -1
        )

    def send(self, robot, pdu, payload):
        self.sent.append((robot, pdu, payload))
        return True

    def simulation_time_sec(self):
        return 1.0

    def read(self, robot, pdu):
        return bytearray(4096)


class ControlModeTest(unittest.TestCase):
    def test_checked_in_recipe_generates_ten_external_vehicles(self):
        resolved = multi_car.resolve_config(
            ROOT / "recipes/multi-car-viewer.yaml"
        )
        self.assertEqual(
            [(item["name"], item["type"], item["control_mode"])
             for item in resolved["vehicles"]],
            [
                (f"Car-{index}", "golf_cart", "external_python")
                for index in range(1, 11)
            ],
        )
        self.assertEqual(
            [item["prefix"] for item in resolved["vehicles"]],
            [f"car_{index}_" for index in range(1, 11)],
        )
        self.assertEqual(resolved["vehicle_generation"]["vehicle_count"], 10)
        self.assertAlmostEqual(
            resolved["vehicle_generation"]["max_route_offset_m"], 40.5
        )
        self.assertAlmostEqual(resolved["vehicles"][0]["spawn_enu"]["east_m"], 35.0)
        self.assertAlmostEqual(resolved["vehicles"][1]["spawn_enu"]["east_m"], 39.5)
        self.assertAlmostEqual(resolved["vehicles"][1]["spawn_enu"]["yaw_deg"], 180.0)

    def launcher(self, modes: list[str]) -> dict:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            source = {
                "plant": ROOT / "build/bin/urban-car-hakoniwa-asset",
                "core_config": ROOT / "core.json",
                "ps5_sender": ROOT / "apps/car/ps5_ackermann_sender.py",
                "ps5_mapping": ROOT / "config/car/ps5-controller-macos.json",
            }
            with (
                patch.object(multi_car, "paths", return_value=source),
                patch.object(
                    multi_car,
                    "foundation_python",
                    return_value=Path("/foundation/python/bin/python3"),
                ),
                patch.object(
                    multi_car,
                    "foundation_install",
                    return_value=Path("/foundation/install"),
                ),
                patch.object(
                    multi_car,
                    "launcher_supports_cleanup",
                    return_value=True,
                ),
            ):
                path = multi_car.materialize_launcher(
                    {
                        "manifest": work / "manifest.json",
                        "pdu_def": work / "pdudef.json",
                    },
                    work,
                    2,
                    [
                        {
                            "name": f"Car-{index}",
                            "prefix": f"car_{index}_",
                            "control_mode": mode,
                        }
                        for index, mode in enumerate(modes, start=1)
                    ],
                )
            return json.loads(path.read_text(encoding="utf-8"))

    def test_external_mode_launches_only_the_plant(self):
        launcher = self.launcher(["external_python", "external_python"])
        self.assertEqual(
            [asset["name"] for asset in launcher["assets"]],
            ["urban-car-fleet-plant"],
        )

    def test_mixed_mode_adds_only_the_selected_launcher_sender(self):
        launcher = self.launcher(["ps5", "external_python"])
        self.assertEqual(
            [asset["name"] for asset in launcher["assets"]],
            ["urban-car-fleet-plant", "urban-car-1-ps5-controller"],
        )
        self.assertEqual(
            launcher["assets"][1]["depends_on"], ["urban-car-fleet-plant"]
        )
        robot_index = launcher["assets"][1]["args"].index("--robot") + 1
        self.assertEqual(launcher["assets"][1]["args"][robot_index], "Car-1")

    def test_vehicle_instances_are_namespaced(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / "model.xml"
            model.write_text(
                "<mujoco><worldbody><body name=\"vehicle\">"
                "<freejoint name=\"base_freejoint\"/>"
                "<body name=\"wheel\"><joint name=\"wheel_joint\"/></body>"
                "</body></worldbody><actuator>"
                "<velocity name=\"wheel_motor\" joint=\"wheel_joint\"/>"
                "</actuator><contact><exclude body1=\"vehicle\" body2=\"wheel\"/>"
                "</contact></mujoco>",
                encoding="utf-8",
            )
            definition = {
                "mjcf": model,
                "interface": {"base_freejoint": "base_freejoint"},
            }
            vehicles = [
                {"type": "test", "type_definition": definition,
                 "prefix": "car_1_", "spawn_mjcf": (1, 2, 3, 0, 0, 0.1)},
                {"type": "test", "type_definition": definition,
                 "prefix": "car_2_", "spawn_mjcf": (4, 5, 6, 0, 0, 0.2)},
            ]
            fleet = Path(directory) / "fleet.xml"
            multi_car.materialize_vehicle_fleet_model(fleet, vehicles)
            text = fleet.read_text(encoding="utf-8")
            for prefix in ("car_1_", "car_2_"):
                self.assertIn(f'name="{prefix}vehicle"', text)
                self.assertIn(f'name="{prefix}base_freejoint"', text)
                self.assertIn(f'name="{prefix}wheel_motor"', text)
                self.assertIn(f'joint="{prefix}wheel_joint"', text)

    def test_runtime_materializes_independent_commands_and_fleet_state(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            vehicles = multi_car.resolve_config(
                ROOT / "recipes/multi-car-viewer.yaml"
            )["vehicles"]
            files = multi_car.materialize_runtime(
                work / "fleet.mjb", work, vehicles
            )
            manifest = json.loads(files["manifest"].read_text(encoding="utf-8"))
            pdu_def = json.loads(files["pdu_def"].read_text(encoding="utf-8"))
            self.assertEqual(
                [item["name"] for item in pdu_def["robots"]],
                [*[f"Car-{index}" for index in range(1, 11)], "UrbanFleet"],
            )
            self.assertEqual(
                [item["pdu_robot"] for item in manifest["components"]
                 if item["kind"] == "controller"],
                [f"Car-{index}" for index in range(1, 11)],
            )
            self.assertEqual(
                [item["pdu_robot"] for item in manifest["components"]
                 if item["kind"] == "state_output"],
                ["UrbanFleet", "UrbanFleet"],
            )
            state_pdu_types = json.loads(
                files["state_pdu_types"].read_text(encoding="utf-8")
            )
            self.assertEqual(
                [item["pdu_size"] for item in state_pdu_types],
                [16384, 16384],
            )


class AckermannClientTest(unittest.TestCase):
    def test_client_publishes_to_the_selected_robot_and_stops_cleanly(self):
        with tempfile.TemporaryDirectory() as directory:
            pdu_def = Path(directory) / "pdudef.json"
            pdu_def.write_text("{}\n", encoding="utf-8")
            transport = FakeTransport()
            encoded = []

            def encode(command):
                encoded.append((command.speed, command.steering_angle))
                return bytearray(48)

            with (
                patch.object(
                    urban_car,
                    "HakoniwaPollingTransport",
                    return_value=transport,
                ),
                patch.object(urban_car, "py_to_pdu_AckermannDrive", side_effect=encode),
            ):
                client = urban_car.AckermannClient(pdu_def, "Car-1").connect()
                client.send(1.25, 0.2)
                client.close(stop=True)

            self.assertEqual(encoded[0], (1.25, 0.2))
            self.assertEqual(encoded[-3:], [(0.0, 0.0)] * 3)
            self.assertEqual(len(transport.sent), 4)
            self.assertFalse(transport.connected)
            self.assertFalse(client.connected)

    def test_fleet_client_publishes_independent_commands_and_stops_all(self):
        with tempfile.TemporaryDirectory() as directory:
            pdu_def = Path(directory) / "pdudef.json"
            pdu_def.write_text("{}\n", encoding="utf-8")
            transport = FakeTransport()

            def encode(command):
                return bytearray(
                    f"{command.speed},{command.steering_angle}".encode("ascii")
                )

            with (
                patch.object(
                    urban_car,
                    "HakoniwaPollingTransport",
                    return_value=transport,
                ),
                patch.object(
                    urban_car,
                    "py_to_pdu_AckermannDrive",
                    side_effect=encode,
                ),
            ):
                client = urban_car.AckermannFleetClient(
                    pdu_def, ["Car-1", "Car-2"]
                ).connect()
                client.send("Car-1", 1.0, 0.1)
                client.send("Car-2", 0.8, -0.2)
                client.close(stop=True)

            sent_robots = [robot for robot, _pdu, _payload in transport.sent]
            self.assertEqual(sent_robots[:2], ["Car-1", "Car-2"])
            self.assertEqual(sent_robots[2:], ["Car-1", "Car-2"] * 3)
            self.assertFalse(client.connected)

    def test_fleet_client_retries_transient_receive_event_backpressure(self):
        with tempfile.TemporaryDirectory() as directory:
            pdu_def = Path(directory) / "pdudef.json"
            pdu_def.write_text("{}\n", encoding="utf-8")
            transport = FakeTransport()
            attempts = 0

            def send_with_backpressure(robot, pdu, payload):
                nonlocal attempts
                attempts += 1
                transport.sent.append((robot, pdu, payload))
                return attempts >= 3

            transport.send = send_with_backpressure
            with (
                patch.object(
                    urban_car,
                    "HakoniwaPollingTransport",
                    return_value=transport,
                ),
                patch.object(
                    urban_car,
                    "py_to_pdu_AckermannDrive",
                    return_value=bytearray(48),
                ),
            ):
                client = urban_car.AckermannFleetClient(
                    pdu_def,
                    ["Car-1"],
                    publish_timeout_sec=0.1,
                ).connect()
                client.send("Car-1", 1.0)
                client.close(stop=False)

            self.assertEqual(attempts, 3)

    def test_fleet_pose_converts_mujoco_frame_and_quaternion_to_enu(self):
        with tempfile.TemporaryDirectory() as directory:
            pdu_def = Path(directory) / "pdudef.json"
            pdu_def.write_text("{}\n", encoding="utf-8")
            transport = FakeTransport()
            transform = SimpleNamespace(
                translation=SimpleNamespace(x=-7.0, y=-35.0, z=4.6),
                rotation=SimpleNamespace(
                    x=0.0, y=0.0,
                    z=2.0 ** -0.5, w=2.0 ** -0.5,
                ),
            )
            state = SimpleNamespace(
                joint_names=["Car-1"],
                transforms=[transform],
            )
            with (
                patch.object(
                    urban_car,
                    "HakoniwaPollingTransport",
                    return_value=transport,
                ),
                patch.object(
                    urban_car,
                    "pdu_to_py_MultiDOFJointState",
                    return_value=state,
                ),
            ):
                client = urban_car.AckermannFleetClient(
                    pdu_def, ["Car-1"]
                ).connect()
                pose = client.vehicle_poses()["Car-1"]
                client.close(stop=False)

            self.assertAlmostEqual(pose.east_m, 35.0)
            self.assertAlmostEqual(pose.north_m, -7.0)
            self.assertAlmostEqual(pose.up_m, 4.6)
            self.assertAlmostEqual(abs(pose.yaw_rad), math.pi)


if __name__ == "__main__":
    unittest.main()
