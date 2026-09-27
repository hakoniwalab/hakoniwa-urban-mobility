import json
from pathlib import Path
import sys
import tempfile
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import drone_one  # noqa: E402
import urban_fault_injection  # noqa: E402

DISTURB = {"channel_id": 3, "pdu_size": 256, "name": "disturb", "type": "hako_msgs/Disturbance"}


class FaultInjectionFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, relative, data):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def read(self, relative):
        return json.loads((self.root / relative).read_text(encoding="utf-8"))

    def drone_pdudef(self, relative="pdudef/drone-pdudef-current.json"):
        self.write(Path(relative).parent / "drone-pdutypes.json", [
            {"channel_id": 0, "pdu_size": 72, "name": "pos", "type": "geometry_msgs/Twist"},
            DISTURB,
        ])
        return self.write(relative, {
            "paths": [{"id": "drone_type", "path": "drone-pdutypes.json"}],
            "robots": [{"name": "Drone-1", "pdutypes_id": "drone_type"}],
        })

    def bridge(self, root="bridge", node="viewer_node1"):
        """A WebBridge config root laid out like multi_car and web_bridge_fleets write it."""
        self.write(f"{root}/pdu/visual.json", {
            "paths": [{"id": "visual", "path": "visual-pdutypes.json"}],
            "robots": [{"name": "DroneVisualStatePublisher", "pdutypes_id": "visual"}],
        })
        self.write(f"{root}/comm/state-shm.json", {"protocol": "shm", "io": {"robots": [
            {"name": "DroneVisualStatePublisher", "pdu": [{"name": "drone_visual_state_array_0"}]},
        ]}})
        self.write(f"{root}/comm/state-ws.json", {"protocol": "websocket", "local": {"port": 8765}})
        for name, comm in (("state-shm", "state-shm.json"), ("state-ws", "state-ws.json")):
            self.write(f"{root}/endpoint/{name}.json", {
                "pdu_def_path": "../pdu/visual.json", "comm": f"../comm/{comm}",
            })
        self.write(f"{root}/endpoint/endpoint_container.json", [{
            "nodeId": node,
            "endpoints": [
                {"id": "bridge-shm-ep", "config_path": "state-shm.json", "direction": "in"},
                {"id": "bridge-ws-ep", "config_path": "state-ws.json", "direction": "out"},
            ],
        }])
        self.write(f"{root}/bridge/bridge.json", {
            "transferPolicies": {"ticker_20ms": {"type": "ticker", "intervalMs": 20}},
            "endpoints_config_path": "../endpoint/endpoint_container.json",
            "pduKeyGroups": {"visual_state": []},
            "connections": [{"id": "conn_visual_state_shm_to_ws", "nodeId": node}],
        })
        return self.root / root

    def assert_bridge_routes_disturb(self, root="bridge", node="viewer_node1"):
        self.assertEqual(self.read(f"{root}/pdu/drone-disturb-pdutypes.json"), [DISTURB])
        pdudef = self.read(f"{root}/pdu/visual.json")
        self.assertEqual(pdudef["robots"][-1], {"name": "Drone-1", "pdutypes_id": "drone-disturb"})
        self.assertEqual(len(pdudef["robots"]), 2)
        shm = self.read(f"{root}/comm/state-shm.json")
        self.assertEqual(shm["io"]["robots"][-1],
                         {"name": "Drone-1", "pdu": [{"name": "disturb", "notify_on_recv": False}]})
        endpoints = self.read(f"{root}/endpoint/endpoint_container.json")[0]["endpoints"]
        self.assertEqual({item["direction"] for item in endpoints}, {"inout"})
        bridge = self.read(f"{root}/bridge/bridge.json")
        self.assertEqual(bridge["transferPolicies"]["immediate"], {"type": "immediate"})
        self.assertEqual(bridge["pduKeyGroups"]["drone_disturb"][0]["id"], "Drone-1.disturb")
        self.assertEqual([item["id"] for item in bridge["connections"]],
                         ["conn_visual_state_shm_to_ws", "conn_disturb_ws_to_shm"])
        inbound = bridge["connections"][1]
        self.assertEqual(inbound["source"], {"endpointId": "bridge-ws-ep"})
        self.assertEqual(inbound["destinations"], [{"endpointId": "bridge-shm-ep"}])
        self.assertEqual(inbound["nodeId"], node)


class FaultInjectionTest(FaultInjectionFixture):
    def test_the_drone_target_comes_from_the_drone_pdu_definition(self):
        name, disturb = urban_fault_injection.disturbance_target(self.drone_pdudef())
        self.assertEqual((name, disturb), ("Drone-1", DISTURB))

    def test_the_urban_hexa_has_six_rotors(self):
        self.assertEqual(
            urban_fault_injection.rotor_count(drone_one.URBAN_HEXA_ROOT / "drone_config_0.json"), 6
        )

    def test_the_browser_can_write_the_drone_disturbance(self):
        root = self.bridge()
        for _ in range(2):  # re-running configure must not duplicate entries
            urban_fault_injection.add_to_bridge(root, "Drone-1", DISTURB)
        self.assert_bridge_routes_disturb()

    def test_the_viewer_shows_one_slider_per_rotor(self):
        viewer = self.write("viewer-config.json", {"version": "1.0", "pdu": {"pduDefPath": "./p.json"}})
        urban_fault_injection.add_to_viewer(viewer, "Drone-1", 6)
        self.assertEqual(self.read("viewer-config.json")["faultInjection"],
                         {"robotName": "Drone-1", "rotorCount": 6})


class DroneOneFaultInjectionTest(FaultInjectionFixture):
    def test_the_recipe_gets_its_own_bridge_with_the_disturb_route(self):
        shared = self.bridge("install/web_bridge_fleets", node="web_bridge_fleets_node1")
        recipe_root = self.root / "recipe"
        paths = types.SimpleNamespace(recipe_root=recipe_root, recipe_config=recipe_root / "config")
        self.drone_pdudef("recipe/config/pdudef/drone-pdudef-current.json")
        viewer_dir = "recipe/web/map-viewer/thirdparty/hakoniwa-threejs-drone/config"
        self.write(f"{viewer_dir}/viewer-config-fleets.json",
                   {"version": "1.0", "pdu": {"pduDefPath": "./pdudef-fleets.json"}})
        self.write(f"{viewer_dir}/pdudef-fleets.json", {"paths": [], "robots": [
            {"name": "DroneVisualStatePublisher", "pdutypes_id": "visual"},
        ]})
        launcher = self.write("recipe/config/launcher.json", {"assets": [
            {"name": "drone-service-1", "args": []},
            {"name": "web-bridge-fleets", "args": [
                "--config-root", str(shared), "--node-name", "web_bridge_fleets_node1",
            ]},
        ]})

        for _ in range(2):
            drone_one.apply_fault_injection(launcher, paths)

        args = self.read("recipe/config/launcher.json")["assets"][1]["args"]
        self.assertEqual(Path(args[1]), paths.recipe_config / drone_one.FAULT_BRIDGE_DIR)
        self.assert_bridge_routes_disturb("recipe/config/web-bridge-fleets", node="web_bridge_fleets_node1")
        # The Foundation's shared config stays untouched.
        self.assertEqual(len(self.read("install/web_bridge_fleets/bridge/bridge.json")["connections"]), 1)
        self.assertEqual(self.read(f"{viewer_dir}/viewer-config-fleets.json")["faultInjection"],
                         {"robotName": "Drone-1", "rotorCount": 6})
        self.assertEqual(self.read(f"{viewer_dir}/pdudef-fleets.json")["robots"][-1],
                         {"name": "Drone-1", "pdutypes_id": "drone-disturb"})

    def test_a_launcher_without_a_web_bridge_is_left_alone(self):
        launcher = self.write("launcher.json", {"assets": [{"name": "drone-service-1", "args": []}]})
        paths = types.SimpleNamespace(recipe_root=self.root, recipe_config=self.root / "config")
        drone_one.apply_fault_injection(launcher, paths)
        self.assertEqual(self.read("launcher.json"), {"assets": [{"name": "drone-service-1", "args": []}]})


if __name__ == "__main__":
    unittest.main()
