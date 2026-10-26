#!/usr/bin/env python3
"""箱庭人間 beside the Cars of a Composition.

The people are a kinematic layer over the Car world: their own MuJoCo world
(the same City World's colliders, so they bump into buildings and step onto
decks; not the cars) in apps/people/people_plant.py, a Hakoniwa asset that
joins the Car plant's Conductor. Both run in Hakoniwa time and the browser
viewer shows both (its state source reads every robot's state PDUs). An
agent keeps people and cars apart by reading both positions.

apply() is called with the Car route's Launcher (urban_simulation
apply_managed_runtime, at configure and at start) and is idempotent: it
writes config/people/, puts the people plant after the Car plant and adds
the people's state PDUs to the web bridge and the Three.js scene.
"""

from __future__ import annotations

import json
import math
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import multi_car
import people_sim
import urban_composition

PLANT_NAME = "people-plant"


def city_receipt(work: Path) -> Path:
    config = json.loads((work / "config/urban-composition.json").read_text(encoding="utf-8"))
    return Path(config["inputs"]["business_pack_city_receipt"]["path"])


def apply(work: Path, composition, launcher: dict, conductor: str) -> None:
    work = Path(work).resolve()  # the generated files name each other by absolute path
    people = urban_composition.people(composition)
    launcher["assets"] = [asset for asset in launcher.get("assets", []) if asset.get("name") != PLANT_NAME]
    config = work / "config/people"
    if not people:
        shutil.rmtree(config, ignore_errors=True)
        return
    config.mkdir(parents=True, exist_ok=True)
    city = people_sim.city_world(city_receipt(work))
    resolved = {"id": composition.id, "people": people, "ground_m": city["size_m"]}
    # Every car: its stand-in box (people bump into it) and its seats (riding).
    vehicles = {}
    for car in composition.by_simulator(urban_composition.CAR_SIMULATOR):
        entry = {"seats": car.asset.data.get("seats", {})}
        if car.asset.data.get("dimensions"):
            entry["box"] = people_sim.vehicle_box(car.asset.data)
        vehicles[car.name] = entry
    world = config / "people-world.xml"
    world.write_text(people_sim.world_xml(resolved, city, vehicles), encoding="utf-8")
    world_mjb = people_sim.compile_world(world)
    fleet_types = work / "config/car/urban-fleet-state-pdutypes.json"
    pdus = people_sim.pdu_files(resolved, config, (multi_car.FLEET_PDU_ROBOT, fleet_types))
    multi_car.write_json(config / "people-plant.json", {
        "vehicle_state_robot": multi_car.FLEET_PDU_ROBOT, "vehicles": vehicles,
        "asset_name": people_sim.PLANT_ASSET, "state_robot": people_sim.STATE_ROBOT, "world_xml": str(world),
        "world_mjb": str(world_mjb),
        "pdu_def": str(pdus["pdu_def"]), "delta_usec": 10000, "state_period_usec": 20000,
        # The Car plant owns the Conductor and the pacer keeps real time.
        "owns_conductor": False, "realtime": False, "people": people,
        "contact_log": str(work / "logs/people-contacts.jsonl"),
    })
    plant = {
        "name": PLANT_NAME, "activation_timing": "before_start",
        "command": str(multi_car.foundation_python()),
        "args": [str(people_sim.PLANT), str(config / "people-plant.json")],
        "depends_on": [conductor], "delay_sec": 1,
        "readiness": {"type": "hako_asset", "asset_name": people_sim.PLANT_ASSET, "timeout_sec": 60,
                      "poll_interval_sec": 0.2, "command_timeout_sec": multi_car.READINESS_PROBE_SEC},
    }
    assets = launcher["assets"]
    after = next((index for index, asset in enumerate(assets) if asset.get("name") == conductor), len(assets) - 1)
    assets.insert(after + 1, plant)
    add_to_viewer(work / "config", people, pdus["state_pdu_types"])


def apply_rpc(work: Path, composition, launcher: dict, conductor: str) -> list[str]:
    """Move Composition people into the native car runtime for PersonStep.

    ``apply`` has already materialized the PDU/viewer files.  This function
    removes its separate plant, merges the same prefixed person models into
    the first (and only) car world, and extends that runtime's endpoint with
    the people's observable PDUs.
    """
    work = Path(work).resolve()
    people = urban_composition.people(composition)
    launcher["assets"] = [
        asset for asset in launcher.get("assets", []) if asset.get("name") != PLANT_NAME
    ]
    if not people:
        return []

    plants = multi_car.load_plants(work)
    if len(plants) != 1:
        raise RuntimeError("PersonStep RPC currently requires one car plant process")
    plant = plants[0]
    root_dir = Path(plant["root"])
    xml_path = Path(plant["model_xml"])
    tree = ET.parse(xml_path)
    root = tree.getroot()
    worldbody = root.find("worldbody")
    actuator = root.find("actuator")
    if worldbody is None:
        raise RuntimeError(f"car world has no worldbody: {xml_path}")
    if actuator is None:
        actuator = ET.SubElement(root, "actuator")

    # Idempotence when the extension runs at both configure and start.
    for body in list(worldbody.findall("body")):
        if body.get("name", "").endswith("/person"):
            worldbody.remove(body)
    for element in list(actuator):
        if "/" in element.get("joint", ""):
            actuator.remove(element)

    for person in people:
        source = ET.parse(person["model"]).getroot()
        prefix = f"{person['name']}/"
        for element in source.iter():
            for key in ("name", "joint"):
                if key in element.attrib:
                    element.attrib[key] = prefix + element.attrib[key]
        # The person source declares degrees, while the car fleet's compiler
        # declares radians.  Convert only joint angle metadata; actuator
        # ctrlrange/forcerange are physical command limits, not XML angles.
        for joint in source.iter("joint"):
            if joint.get("type", "hinge") == "hinge" and "range" in joint.attrib:
                joint.set("range", " ".join(
                    f"{math.radians(float(value)):g}"
                    for value in joint.get("range", "").split()
                ))
        body = source.find("worldbody/body")
        if body is None:
            raise RuntimeError(f"person model has no body: {person['model']}")
        spawn = person["spawn"]
        body.set("pos", f"{spawn['north_m']:g} {-spawn['east_m']:g} 0")
        # The runtime interprets turn_joint qpos as the person's MuJoCo-world
        # yaw.  Keep ref at zero: a hinge's physical rotation is qpos - ref,
        # so encoding the spawn in ref makes riders face qpos - spawn.
        turn = next((joint for joint in body.iter("joint")
                     if joint.get("name") == prefix + "turn_joint"), None)
        if turn is None:
            raise RuntimeError(f"person model has no turn_joint: {person['model']}")
        turn.set("ref", "0")
        worldbody.append(body)
        source_actuator = source.find("actuator")
        for element in [] if source_actuator is None else list(source_actuator):
            actuator.append(element)
    ET.indent(tree, space="  ")
    tree.write(xml_path, encoding="utf-8", xml_declaration=True)

    manifest_path = root_dir / "urban-car-asset-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    runtime_path = Path(manifest["runtime_config"])
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    runtime["person_initial_poses"] = [
        {
            "name": person["name"],
            # Composition yaw is ENU (0=east); the car MuJoCo world is
            # (x=north, y=west), so its yaw is ENU yaw - pi/2.
            "yaw_rad": math.radians(float(person["spawn"]["yaw_deg"])) - math.pi / 2.0,
        }
        for person in people
    ]
    multi_car.write_json(runtime_path, runtime)
    model = Path(manifest["model"])
    receipt = work / "validation/mujoco-materialization.json"
    multi_car.materialize_mjb(xml_path, model, receipt)

    resolved = {"id": composition.id, "people": people, "ground_m": 0.0}
    people_config = work / "config/people"
    pdus = people_sim.pdu_files(resolved, people_config)
    car_pdu_path = Path(manifest["pdu_def"])
    car_pdu = json.loads(car_pdu_path.read_text(encoding="utf-8"))
    people_pdu = json.loads(pdus["pdu_def"].read_text(encoding="utf-8"))
    known_paths = {item["id"] for item in car_pdu["paths"]}
    known_robots = {item["name"] for item in car_pdu["robots"]}
    car_pdu["paths"].extend(item for item in people_pdu["paths"] if item["id"] not in known_paths)
    car_pdu["robots"].extend(item for item in people_pdu["robots"] if item["name"] not in known_robots)
    multi_car.write_json(car_pdu_path, car_pdu)

    endpoint = json.loads(Path(manifest["endpoint"]).read_text(encoding="utf-8"))
    comm_path = Path(endpoint["comm"])
    comm = json.loads(comm_path.read_text(encoding="utf-8"))
    names = {item["name"] for item in comm["io"]["robots"]}
    command_types = json.loads((people_config / "people-command-pdutypes.json").read_text(encoding="utf-8"))
    state_types = json.loads((people_config / "people-state-pdutypes.json").read_text(encoding="utf-8"))
    comm["io"]["robots"].extend({
        "name": person["name"],
        "pdu": [{"name": item["name"], "notify_on_recv": False} for item in command_types],
    } for person in people if person["name"] not in names)
    if people_sim.STATE_ROBOT not in names:
        comm["io"]["robots"].append({
            "name": people_sim.STATE_ROBOT,
            "pdu": [{"name": item["name"], "notify_on_recv": False} for item in state_types],
        })
    multi_car.write_json(comm_path, comm)
    return [person["name"] for person in people]


def add_to_viewer(config: Path, people: list[dict], state_types: Path) -> None:
    """The people's state PDUs through the web bridge, and the people in the scene."""
    bridge = config / "web-bridge"
    if not (bridge / "bridge/bridge.json").is_file():
        return  # no browser visualization
    robot = people_sim.STATE_ROBOT
    (bridge / "pdu/people-state-pdutypes.json").write_text(state_types.read_text(encoding="utf-8"), encoding="utf-8")
    command_types = state_types.parent / "people-command-pdutypes.json"
    (bridge / "pdu/people-command-pdutypes.json").write_text(
        command_types.read_text(encoding="utf-8"), encoding="utf-8")
    pdudef_path = bridge / "pdu/urban-visual-state.json"
    pdudef = json.loads(pdudef_path.read_text(encoding="utf-8"))
    pdudef["paths"] = [p for p in pdudef["paths"] if p["id"] not in {"people-state", "people-command"}] + [
        {"id": "people-state", "path": "people-state-pdutypes.json"},
        {"id": "people-command", "path": "people-command-pdutypes.json"}]
    person_names = {person["name"] for person in people}
    pdudef["robots"] = [r for r in pdudef["robots"] if r["name"] != robot and r["name"] not in person_names] + [
        {"name": robot, "pdutypes_id": "people-state"},
        *[{"name": name, "pdutypes_id": "people-command"} for name in sorted(person_names)],
    ]
    multi_car.write_json(pdudef_path, pdudef)
    comm_path = bridge / "comm/urban-state-shm-callback.json"
    comm = json.loads(comm_path.read_text(encoding="utf-8"))
    robots = comm["io"]["robots"]
    comm["io"]["robots"] = [r for r in robots if r["name"] != robot and r["name"] not in person_names] + [{"name": robot, "pdu": [
        {"name": "joint_states", "notify_on_recv": False},
        {"name": "vehicle_states", "notify_on_recv": False},
        {"name": "contact_events", "notify_on_recv": False}]},
        *[{"name": name, "pdu": [{"name": "contact", "notify_on_recv": False}]}
          for name in sorted(person_names)]]
    multi_car.write_json(comm_path, comm)
    bridge_path = bridge / "bridge/bridge.json"
    bridge_config = json.loads(bridge_path.read_text(encoding="utf-8"))
    for group in bridge_config["pduKeyGroups"].values():
        group[:] = [entry for entry in group
                    if entry["robot_name"] != robot and entry["robot_name"] not in person_names] + [
            {"id": f"{robot}.{pdu}", "robot_name": robot, "pdu_name": pdu}
            for pdu in ("joint_states", "vehicle_states", "contact_events")] + [
            {"id": f"{name}.contact", "robot_name": name, "pdu_name": "contact"}
            for name in sorted(person_names)]
    multi_car.write_json(bridge_path, bridge_config)
    threejs = config / "threejs"
    types_path = threejs / "vehicle-types.json"
    types = json.loads(types_path.read_text(encoding="utf-8"))
    for person in people:
        types[f"hakoniwa-person-{person['look']}"] = {"viewModelPath": multi_car.workspace_url(Path(person["view_model"]))}
    multi_car.write_json(types_path, types)
    names = {person["name"] for person in people}
    for scene_path in threejs.glob("scene-config*.json"):
        scene = json.loads(scene_path.read_text(encoding="utf-8"))
        scene["vehicles"] = [v for v in scene["vehicles"] if v["name"] not in names] + [
            {"name": person["name"], "type": f"hakoniwa-person-{person['look']}"} for person in people]
        multi_car.write_json(scene_path, scene)
