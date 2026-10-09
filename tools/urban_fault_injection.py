"""Let the browser inject Drone rotor faults and wind.

The Three.js Viewer's fault panel (hakoniwa-threejs-drone fault_panel.js)
writes the Drone's disturbance PDU over WebSocket. The WebBridge forwards it
into shared memory at once, where the Drone service reads the wind and one
thrust scale per rotor (d_user_custom[1]; 1.0 nominal, 0.0 failed).
"""

from __future__ import annotations

import json
from pathlib import Path

DISTURB_PDU = "disturb"
DISTURB_TYPES_ID = "drone-disturb"
DISTURB_TYPES_FILE = "drone-disturb-pdutypes.json"
INBOUND_CONNECTION = "conn_disturb_ws_to_shm"


class FaultInjectionError(RuntimeError):
    pass


def _load(path: Path, label: str):
    if not path.is_file():
        raise FaultInjectionError(f"{label} not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise FaultInjectionError(f"invalid {label}: {path}: {error}") from error


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def disturbance_target(pdudef_path: Path) -> tuple[str, dict]:
    """The Drone robot name and its disturbance PDU type from a one-Drone PDU definition."""
    names, disturb = disturbance_targets(pdudef_path)
    if len(names) != 1:
        raise FaultInjectionError("the Drone PDU definition must contain one Drone")
    return names[0], disturb


def disturbance_targets(pdudef_path: Path) -> tuple[list[str], dict]:
    """The Drone robot names and their (shared) disturbance PDU type from the Drone PDU definition."""
    pdudef = _load(pdudef_path, "Drone PDU definition")
    robots = pdudef.get("robots") or []
    if not robots:
        raise FaultInjectionError("the Drone PDU definition contains no Drone")
    if len({robot.get("pdutypes_id") for robot in robots}) != 1:
        raise FaultInjectionError("the Drones of the PDU definition must share their PDU types")
    robot = robots[0]
    types_path = next(
        (item["path"] for item in pdudef.get("paths", []) if item.get("id") == robot.get("pdutypes_id")),
        None,
    )
    if types_path is None:
        raise FaultInjectionError(f"the Drone PDU definition has no types for {robot.get('name')}")
    types = _load(pdudef_path.parent / types_path, "Drone PDU types")
    disturb = next((item for item in types if item.get("name") == DISTURB_PDU), None)
    if disturb is None:
        raise FaultInjectionError("the Drone PDU types have no disturbance PDU")
    return [item["name"] for item in robots], disturb


def rotor_count(drone_config_path: Path) -> int:
    config = _load(drone_config_path, "Drone config")
    rotors = config.get("components", {}).get("thruster", {}).get("rotorPositions")
    if not isinstance(rotors, list) or not rotors:
        raise FaultInjectionError(f"the Drone config has no rotor positions: {drone_config_path}")
    return len(rotors)


def add_to_pdudef(pdudef_path: Path, robot_name: str, disturb_type: dict) -> None:
    """Declare robot_name's disturbance PDU in a (browser or bridge) PDU definition."""
    _write(pdudef_path.parent / DISTURB_TYPES_FILE, [disturb_type])
    pdudef = _load(pdudef_path, "PDU definition")
    pdudef["paths"] = [item for item in pdudef.get("paths", []) if item.get("id") != DISTURB_TYPES_ID]
    pdudef["paths"].append({"id": DISTURB_TYPES_ID, "path": DISTURB_TYPES_FILE})
    pdudef["robots"] = [item for item in pdudef.get("robots", []) if item.get("name") != robot_name]
    pdudef["robots"].append({"name": robot_name, "pdutypes_id": DISTURB_TYPES_ID})
    _write(pdudef_path, pdudef)


def add_to_bridge(config_root: Path, robot_names: str | list[str], disturb_type: dict) -> None:
    """Add a WebSocket -> shared memory route for the robots' disturbance PDUs.

    config_root is a WebBridge config root (bridge/bridge.json and the
    endpoint, comm and PDU files it references).
    """
    names = [robot_names] if isinstance(robot_names, str) else list(robot_names)
    bridge_path = config_root / "bridge/bridge.json"
    bridge = _load(bridge_path, "WebBridge config")
    container_path = (bridge_path.parent / bridge["endpoints_config_path"]).resolve()
    container = _load(container_path, "WebBridge endpoints")
    if len(container) != 1:
        raise FaultInjectionError(f"the WebBridge must have one node: {container_path}")
    endpoint_ids = {}
    pdudefs = set()
    for endpoint in container[0]["endpoints"]:
        endpoint_path = container_path.parent / endpoint["config_path"]
        endpoint_config = _load(endpoint_path, "WebBridge endpoint")
        pdudefs.add((endpoint_path.parent / endpoint_config["pdu_def_path"]).resolve())
        comm_path = endpoint_path.parent / endpoint_config["comm"]
        comm = _load(comm_path, "WebBridge communication")
        endpoint_ids[comm["protocol"]] = endpoint["id"]
        if comm["protocol"] == "shm":
            robots = [item for item in comm["io"]["robots"] if item.get("name") not in names]
            robots.extend({"name": name, "pdu": [{"name": DISTURB_PDU, "notify_on_recv": False}]} for name in names)
            comm["io"]["robots"] = robots
            _write(comm_path, comm)
        # PDUs now flow both ways between shared memory and the browser.
        endpoint["direction"] = "inout"
    if set(endpoint_ids) != {"shm", "websocket"}:
        raise FaultInjectionError(f"the WebBridge needs one shm and one websocket endpoint: {container_path}")
    _write(container_path, container)
    for pdudef in sorted(pdudefs):
        for name in names:
            add_to_pdudef(pdudef, name, disturb_type)

    bridge["transferPolicies"]["immediate"] = {"type": "immediate"}
    bridge["pduKeyGroups"]["drone_disturb"] = [
        {"id": f"{name}.{DISTURB_PDU}", "robot_name": name, "pdu_name": DISTURB_PDU} for name in names
    ]
    connections = [item for item in bridge["connections"] if item.get("id") != INBOUND_CONNECTION]
    connections.append({
        "id": INBOUND_CONNECTION,
        "nodeId": container[0]["nodeId"],
        "source": {"endpointId": endpoint_ids["websocket"]},
        "destinations": [{"endpointId": endpoint_ids["shm"]}],
        "transferPdus": [{"pduKeyGroupId": "drone_disturb", "policyId": "immediate"}],
    })
    bridge["connections"] = connections
    _write(bridge_path, bridge)


def add_to_viewer(viewer_path: Path, robot_name: str, rotors: int) -> None:
    """Show the Viewer's fault panel for robot_name with one slider per rotor."""
    viewer = _load(viewer_path, "Viewer config")
    viewer["faultInjection"] = {"robotName": robot_name, "rotorCount": rotors}
    _write(viewer_path, viewer)
