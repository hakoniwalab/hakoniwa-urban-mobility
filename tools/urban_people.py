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
import shutil
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
    fleet_types = work / "config/car/urban-fleet-state-pdutypes.json"
    pdus = people_sim.pdu_files(resolved, config, (multi_car.FLEET_PDU_ROBOT, fleet_types))
    multi_car.write_json(config / "people-plant.json", {
        "vehicle_state_robot": multi_car.FLEET_PDU_ROBOT, "vehicles": vehicles,
        "asset_name": people_sim.PLANT_ASSET, "state_robot": people_sim.STATE_ROBOT, "world_xml": str(world),
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


def add_to_viewer(config: Path, people: list[dict], state_types: Path) -> None:
    """The people's state PDUs through the web bridge, and the people in the scene."""
    bridge = config / "web-bridge"
    if not (bridge / "bridge/bridge.json").is_file():
        return  # no browser visualization
    robot = people_sim.STATE_ROBOT
    (bridge / "pdu/people-state-pdutypes.json").write_text(state_types.read_text(encoding="utf-8"), encoding="utf-8")
    pdudef_path = bridge / "pdu/urban-visual-state.json"
    pdudef = json.loads(pdudef_path.read_text(encoding="utf-8"))
    pdudef["paths"] = [p for p in pdudef["paths"] if p["id"] != "people-state"] + [
        {"id": "people-state", "path": "people-state-pdutypes.json"}]
    pdudef["robots"] = [r for r in pdudef["robots"] if r["name"] != robot] + [
        {"name": robot, "pdutypes_id": "people-state"}]
    multi_car.write_json(pdudef_path, pdudef)
    comm_path = bridge / "comm/urban-state-shm-callback.json"
    comm = json.loads(comm_path.read_text(encoding="utf-8"))
    robots = comm["io"]["robots"]
    comm["io"]["robots"] = [r for r in robots if r["name"] != robot] + [{"name": robot, "pdu": [
        {"name": "joint_states", "notify_on_recv": False}, {"name": "vehicle_states", "notify_on_recv": False}]}]
    multi_car.write_json(comm_path, comm)
    bridge_path = bridge / "bridge/bridge.json"
    bridge_config = json.loads(bridge_path.read_text(encoding="utf-8"))
    for group in bridge_config["pduKeyGroups"].values():
        group[:] = [entry for entry in group if entry["robot_name"] != robot] + [
            {"id": f"{robot}.{pdu}", "robot_name": robot, "pdu_name": pdu} for pdu in ("joint_states", "vehicle_states")]
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
