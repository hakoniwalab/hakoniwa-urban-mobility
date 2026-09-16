#!/usr/bin/env python3
"""Materialize and operate the Urban City World + Car Fleet recipe.

This wrapper composes component-owned artifacts.  It does not download PLATEAU
data, generate a vehicle model, or reimplement the generic Robot Runtime or
MuJoCo physics backend.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
BUSINESS_PACK = WORKSPACE / "hakoniwa-business-pack"
MBODY = WORKSPACE / "hakoniwa-mbody-registry"
MUJOCO_ROBOTS = WORKSPACE / "hakoniwa-mujoco-robots"
DEFAULT_CONFIG = ROOT / "recipes/multi-car-viewer.yaml"
FLEET_ASSET_NAME = "UrbanCarFleet"
FLEET_PDU_ROBOT = "UrbanFleet"
COMMAND_PDU = "ackermann_cmd"
CONTROL_MODES = {"external_python", "ps5"}


class RecipeError(RuntimeError):
    """A prerequisite or generated-artifact contract is invalid."""


def required(path: Path, label: str) -> Path:
    if not path.is_file():
        raise RecipeError(f"{label} not found: {path}")
    return path


def load_json(path: Path, label: str) -> dict:
    try:
        data = json.loads(required(path, label).read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise RecipeError(f"invalid {label}: {path}: {error}") from error
    if not isinstance(data, dict):
        raise RecipeError(f"{label} must contain a JSON object: {path}")
    return data


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def foundation_install() -> Path:
    return BUSINESS_PACK / "work/foundation/install"


def foundation_python() -> Path:
    return required(foundation_install() / "python/bin/python3", "Foundation Python")


def load_yaml(path: Path) -> dict:
    """Load YAML through the dependency-pinned Foundation Python environment."""
    required(path, "Urban Car Fleet configuration")
    result = subprocess.run(
        [
            str(foundation_python()),
            "-c",
            "import json,sys,yaml; "
            "print(json.dumps(yaml.safe_load(open(sys.argv[1], encoding='utf-8'))))",
            str(path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RecipeError(f"failed to load configuration {path}: {result.stderr.strip()}")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RecipeError(f"configuration loader returned invalid JSON for {path}") from error
    if not isinstance(value, dict):
        raise RecipeError(f"configuration must contain a YAML mapping: {path}")
    return value


def resolve_path(raw: str, label: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise RecipeError(f"{label} must be a non-empty path")
    path = Path(raw.strip()).expanduser()
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def resolve_config(config_path: Path) -> dict:
    config = load_yaml(config_path.resolve())
    try:
        recipe_id = str(config["id"])
        city = config["inputs"]["business_pack_city_receipt"]
        city_receipt = resolve_path(city["path"], "City World receipt path")
        vehicle_config = config["inputs"]["ackermann_vehicles"]
        type_inputs = vehicle_config["types"]
        vehicle_inputs = vehicle_config["vehicles"]
        realtime_sync_cycle_msec = int(config["inputs"]["ackermann_runtime"].get(
            "realtime_sync_cycle_msec", 2
        ))
        work = resolve_path(config["composition"]["output"]["directory"], "output directory")
    except (KeyError, TypeError, ValueError) as error:
        raise RecipeError(f"unsupported Urban Car Fleet configuration schema: {config_path}") from error
    if not isinstance(type_inputs, list) or not type_inputs:
        raise RecipeError("ackermann_vehicles.types must be a non-empty array")
    if not isinstance(vehicle_inputs, list) or not vehicle_inputs:
        raise RecipeError("ackermann_vehicles.vehicles must be a non-empty array")
    if realtime_sync_cycle_msec < 0:
        raise RecipeError("realtime_sync_cycle_msec must be non-negative")

    vehicle_types = {}
    for index, type_input in enumerate(type_inputs):
        try:
            type_name = str(type_input["type"]).strip()
            mjcf = resolve_path(type_input["mjcf"], f"vehicle type {type_name} MJCF")
            contract_path = resolve_path(
                type_input["contract"], f"vehicle type {type_name} contract"
            )
            contract = load_yaml(contract_path)
            validation = contract["validation"]
            interface = validation["interface"]
            geometry = validation["geometry"]
            resolved_type = {
                "type": type_name,
                "mjcf": required(mjcf, f"vehicle type {type_name} MJCF"),
                "contract": required(contract_path, f"vehicle type {type_name} contract"),
                "interface": {
                    "base_freejoint": str(interface["base_freejoint"]),
                    "joints": {
                        role: str(interface["joints"][role])
                        for role in ("steering_left", "steering_right", "drive_left", "drive_right")
                    },
                    "actuators": {
                        role: str(interface["actuators"][role])
                        for role in ("steering_left", "steering_right", "drive_left", "drive_right")
                    },
                },
                "geometry": {
                    "wheelbase_m": float(geometry["wheelbase_m"]),
                    "track_width_m": float(geometry["track_width_m"]),
                    "wheel_radius_m": float(geometry["wheel_radius_m"]),
                    "max_steering_angle_rad": float(geometry["max_center_steering_rad"]),
                    "max_wheel_angular_velocity_rad_s": float(
                        geometry["max_wheel_angular_velocity_rad_s"]
                    ),
                },
            }
        except (KeyError, TypeError, ValueError) as error:
            raise RecipeError(f"invalid vehicle type at index {index}") from error
        if not type_name or type_name in vehicle_types:
            raise RecipeError(f"vehicle type names must be non-empty and unique: {type_name!r}")
        numeric_geometry = resolved_type["geometry"].values()
        if any(not math.isfinite(value) or value <= 0.0 for value in numeric_geometry):
            raise RecipeError(f"vehicle type {type_name} has invalid Ackermann geometry")
        vehicle_types[type_name] = resolved_type

    vehicles = []
    names: set[str] = set()
    for index, vehicle_input in enumerate(vehicle_inputs, start=1):
        try:
            name = str(vehicle_input["name"]).strip()
            type_name = str(vehicle_input["type"]).strip()
            control_mode = str(vehicle_input.get("control_mode", "external_python")).strip()
            spawn = vehicle_input["spawn_pose_enu"]
            east = float(spawn["east_m"])
            north = float(spawn["north_m"])
            up = float(spawn["up_m"])
            yaw_deg = float(spawn["yaw_deg"])
        except (KeyError, TypeError, ValueError) as error:
            raise RecipeError(f"invalid vehicle entry at index {index - 1}") from error
        if not name or name in names:
            raise RecipeError(f"vehicle names must be non-empty and unique: {name!r}")
        if type_name not in vehicle_types:
            raise RecipeError(f"vehicle {name} references unknown type: {type_name!r}")
        if any(not math.isfinite(value) for value in (east, north, up, yaw_deg)):
            raise RecipeError(f"spawn values must be finite for vehicle {name}")
        if control_mode not in CONTROL_MODES:
            raise RecipeError(
                f"vehicle {name} control_mode must be external_python or ps5"
            )
        names.add(name)
        prefix = f"car_{index}_"
        vehicles.append({
            "name": name,
            "type": type_name,
            "type_definition": vehicle_types[type_name],
            "prefix": prefix,
            "control_mode": control_mode,
            "spawn_enu": {
                "frame": "city_origin_local_enu",
                "east_m": east,
                "north_m": north,
                "up_m": up,
                "yaw_deg": yaw_deg,
            },
            # City World MJCF is X=North, Y=-East, Z=Up. ENU yaw is positive
            # counter-clockwise from East; MuJoCo yaw is measured from +X.
            "spawn_mjcf": (
                north, -east, up, 0.0, 0.0, math.radians(yaw_deg - 90.0)
            ),
        })
    return {
        "raw": config,
        "path": config_path.resolve(),
        "recipe_id": recipe_id,
        "city_receipt": city_receipt,
        "work": work,
        "vehicles": vehicles,
        "vehicle_types": vehicle_types,
        "realtime_sync_cycle_msec": realtime_sync_cycle_msec,
    }


def paths() -> dict[str, Path]:
    return {
        "compose_tool": MBODY / "tools/compose_mujoco_world.py",
        "mujoco_compiler": BUSINESS_PACK / "tools/mujoco_model_compiler.py",
        "plant": ROOT / "build/bin/urban-car-hakoniwa-asset",
        "source_runtime": ROOT / "config/car/runtime.json",
        "ackermann_controller": ROOT / "config/car/controller/ackermann.json",
        "joint_state_output": ROOT / "config/car/state/joint-state.json",
        "multi_dof_state_template": ROOT / "config/car/state/multi-dof-state.template.json",
        "source_endpoint": ROOT / "config/car/endpoint.json",
        "source_cache": ROOT / "config/car/cache.json",
        "source_comm": ROOT / "config/car/comm.json",
        "external_sender": ROOT / "apps/car/ackermann_command.py",
        "scenario_executor": ROOT / "apps/car/scenario_executor.py",
        "command_client": ROOT / "apps/car/urban_car.py",
        "ps5_sender": ROOT / "apps/car/ps5_ackermann_sender.py",
        "ps5_mapping": ROOT / "config/car/ps5-controller-macos.json",
        "core_config": foundation_install().parent / "config/cpp_core_config.json",
    }


def mujoco_library() -> Path:
    version = required(MUJOCO_ROBOTS / "MUJOCO_VERSION.txt", "MuJoCo version").read_text(
        encoding="utf-8"
    ).strip()
    if not version:
        raise RecipeError("hakoniwa-mujoco-robots/MUJOCO_VERSION.txt is empty")
    roots = (
        ROOT / "build/_deps/mujoco_precompiled-src",
        MUJOCO_ROBOTS / "src/cmake-build/_deps/mujoco_precompiled-src",
    )
    for root in roots:
        candidates = (
            root / f"lib/mujoco.framework/Versions/A/libmujoco.{version}.dylib",
            root / f"lib/libmujoco.so.{version}",
            root / "lib/libmujoco.so",
            root / "bin/mujoco.dll",
        )
        for candidate in candidates:
            if candidate.is_file():
                return candidate.resolve()
    raise RecipeError(
        "MuJoCo shared library used by hakoniwa-mujoco-robots was not found under "
        f"{roots}; build the Urban Car application first"
    )


def city_inputs(city_receipt: Path) -> tuple[Path, Path, dict]:
    receipt = load_json(city_receipt, "City World receipt")
    try:
        mjcf = Path(receipt["mjcf"]["path"])
        glb = Path(receipt["glb"]["path"])
        coordinate_frame = receipt["coordinate_frame"]
        origin = coordinate_frame["origin"]
        latitude = float(origin["latitude"])
        longitude = float(origin["longitude"])
        altitude_offset = float(origin["altitude_offset_m"])
        half_extent = coordinate_frame["half_extent_m"]
        north_south = float(half_extent["north_south"])
        east_west = float(half_extent["east_west"])
        mjcf_frame = coordinate_frame["coordinate_systems"]["mjcf"]
    except (KeyError, TypeError, ValueError) as error:
        raise RecipeError(f"City World receipt has an unsupported schema: {city_receipt}") from error
    if any(
        not math.isfinite(value)
        for value in (latitude, longitude, altitude_offset, north_south, east_west)
    ) or north_south <= 0.0 or east_west <= 0.0:
        raise RecipeError(f"City World receipt has invalid origin or extent: {city_receipt}")
    if mjcf_frame != "X=North,Y=-East,Z=Up":
        raise RecipeError(f"unsupported City World MJCF coordinate system: {mjcf_frame}")
    return required(mjcf, "City World MJCF"), required(glb, "City World GLB"), receipt


def command(command: list[str]) -> None:
    print("+", subprocess.list2cmdline(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


MJCF_REFERENCE_ATTRIBUTES = {
    "joint", "joint1", "joint2", "body", "body1", "body2",
    "geom", "geom1", "geom2", "site", "site1", "site2",
    "tendon", "actuator",
}
MJCF_ASSET_REFERENCE_ATTRIBUTES = {"mesh", "material", "texture", "hfield"}
MJCF_FILE_BASE_ATTRIBUTES = {"mesh": "meshdir", "texture": "texturedir"}


def _named_objects(element: ET.Element) -> set[str]:
    return {
        item.get("name")
        for item in element.iter()
        if item.get("name") is not None
    }


def _namespace_mjcf(
    element: ET.Element,
    declaration_names: set[str],
    declaration_prefix: str,
    asset_names: set[str] | None = None,
    asset_prefix: str = "",
) -> None:
    asset_names = asset_names or set()
    for item in element.iter():
        name = item.get("name")
        if name in declaration_names:
            item.set("name", declaration_prefix + name)
        for attribute in MJCF_REFERENCE_ATTRIBUTES:
            reference = item.get(attribute)
            if reference in declaration_names:
                item.set(attribute, declaration_prefix + reference)
        for attribute in MJCF_ASSET_REFERENCE_ATTRIBUTES:
            reference = item.get(attribute)
            if reference in asset_names:
                item.set(attribute, asset_prefix + reference)


def _absolute_asset_files(
    asset: ET.Element,
    source_root: ET.Element,
    source_path: Path,
) -> None:
    compiler = source_root.find("compiler")
    for item in asset.iter():
        raw = item.get("file")
        if not raw:
            continue
        path = Path(raw)
        if not path.is_absolute():
            directory = None
            if compiler is not None:
                type_directory = compiler.get(MJCF_FILE_BASE_ATTRIBUTES.get(item.tag, ""))
                directory = type_directory or compiler.get("assetdir")
            base = source_path.parent if not directory else source_path.parent / directory
            path = (base / path).resolve()
        required(path, f"MJCF asset for vehicle type ({raw})")
        item.set("file", str(path))


def materialize_vehicle_fleet_model(
    output_xml: Path,
    vehicles: list[dict],
) -> None:
    first_path = vehicles[0]["type_definition"]["mjcf"]
    tree = ET.parse(first_path)
    root = tree.getroot()
    root.set("model", "urban_car_fleet")
    for section_name in ("asset", "worldbody", "actuator", "contact"):
        section = root.find(section_name)
        if section is not None:
            root.remove(section)
    asset_output = ET.SubElement(root, "asset")
    worldbody_output = ET.SubElement(root, "worldbody")
    actuator_output = ET.SubElement(root, "actuator")
    contact_output = ET.SubElement(root, "contact")

    type_assets: dict[str, tuple[set[str], str]] = {}
    used_types = []
    for vehicle in vehicles:
        type_name = vehicle["type"]
        if type_name not in used_types:
            used_types.append(type_name)
    for type_index, type_name in enumerate(used_types, start=1):
        definition = next(
            vehicle["type_definition"] for vehicle in vehicles
            if vehicle["type"] == type_name
        )
        source_path = definition["mjcf"]
        source_root = ET.parse(source_path).getroot()
        source_asset = source_root.find("asset")
        asset_names = _named_objects(source_asset) if source_asset is not None else set()
        asset_prefix = f"vehicle_type_{type_index}_"
        type_assets[type_name] = (asset_names, asset_prefix)
        if source_asset is not None:
            for template in list(source_asset):
                item = copy.deepcopy(template)
                _absolute_asset_files(item, source_root, source_path)
                _namespace_mjcf(
                    item, asset_names, asset_prefix, asset_names, asset_prefix
                )
                asset_output.append(item)

    # Lighting is a world presentation policy. Reuse it once from the first
    # selected model; per-vehicle ground planes are intentionally omitted.
    first_worldbody = ET.parse(first_path).getroot().find("worldbody")
    if first_worldbody is None:
        raise RecipeError(f"vehicle type MJCF has no worldbody: {first_path}")
    for child in list(first_worldbody):
        if child.tag not in {"body", "geom"}:
            worldbody_output.append(copy.deepcopy(child))

    for vehicle in vehicles:
        definition = vehicle["type_definition"]
        source_path = definition["mjcf"]
        source_root = ET.parse(source_path).getroot()
        source_worldbody = source_root.find("worldbody")
        source_actuator = source_root.find("actuator")
        source_contact = source_root.find("contact")
        if source_worldbody is None or source_actuator is None:
            raise RecipeError(
                f"vehicle type {vehicle['type']} requires worldbody and actuator sections"
            )
        bodies = [item for item in source_worldbody if item.tag == "body"]
        if len(bodies) != 1:
            raise RecipeError(
                f"vehicle type {vehicle['type']} must have one top-level body; found {len(bodies)}"
            )
        template_body = bodies[0]
        actuator_templates = list(source_actuator)
        names = _named_objects(template_body)
        for item in actuator_templates:
            names.update(_named_objects(item))
        freejoint = definition["interface"]["base_freejoint"]
        if freejoint not in names:
            raise RecipeError(
                f"vehicle type {vehicle['type']} is missing freejoint {freejoint}"
            )
        asset_names, asset_prefix = type_assets[vehicle["type"]]
        prefix = vehicle["prefix"]
        spawn = vehicle["spawn_mjcf"]
        body = copy.deepcopy(template_body)
        _namespace_mjcf(body, names, prefix, asset_names, asset_prefix)
        body.set("pos", " ".join(str(value) for value in spawn[:3]))
        body.set("euler", " ".join(str(value) for value in spawn[3:]))
        worldbody_output.append(body)

        for template in actuator_templates:
            item = copy.deepcopy(template)
            _namespace_mjcf(item, names, prefix, asset_names, asset_prefix)
            actuator_output.append(item)
        if source_contact is not None:
            for template in list(source_contact):
                item = copy.deepcopy(template)
                _namespace_mjcf(item, names, prefix, asset_names, asset_prefix)
                contact_output.append(item)

    ET.indent(tree, space="  ")
    tree.write(output_xml, encoding="utf-8", xml_declaration=True)


def _materialize_vehicle_configs(work: Path, vehicle: dict) -> tuple[list[dict], list[dict]]:
    source = paths()
    prefix = vehicle["prefix"]
    name = vehicle["name"]
    definition = vehicle["type_definition"]
    interface = definition["interface"]
    geometry = definition["geometry"]
    key = prefix.rstrip("_")
    actuator_sources = {
        "front_left_steer": ROOT / "config/car/actuator/front-left-steer.json",
        "front_right_steer": ROOT / "config/car/actuator/front-right-steer.json",
        "rear_left_wheel": ROOT / "config/car/actuator/rear-left-wheel.json",
        "rear_right_wheel": ROOT / "config/car/actuator/rear-right-wheel.json",
    }
    component_types = {
        "front_left_steer": "joint_position_actuator",
        "front_right_steer": "joint_position_actuator",
        "rear_left_wheel": "joint_velocity_actuator",
        "rear_right_wheel": "joint_velocity_actuator",
    }
    components = []
    joints = []
    actuator_ids = {}
    interface_roles = {
        "front_left_steer": "steering_left",
        "front_right_steer": "steering_right",
        "rear_left_wheel": "drive_left",
        "rear_right_wheel": "drive_right",
    }
    for logical_id, source_path in actuator_sources.items():
        component_id = prefix + logical_id
        actuator_ids[logical_id] = component_id
        config = load_json(source_path, f"{logical_id} actuator config")
        role = interface_roles[logical_id]
        config["spec"]["joint_name"] = prefix + interface["joints"][role]
        config["mjcf_binding"]["actuator_name"] = prefix + interface["actuators"][role]
        if role.startswith("steering_"):
            config["spec"]["limit"] = {
                "lower": -geometry["max_steering_angle_rad"],
                "upper": geometry["max_steering_angle_rad"],
            }
        else:
            config["spec"]["limit"] = {
                "velocity": geometry["max_wheel_angular_velocity_rad_s"]
            }
        config_path = work / f"{key}-{logical_id}.json"
        write_json(config_path, config)
        components.append({
            "id": component_id,
            "kind": "actuator",
            "type": component_types[logical_id],
            "config": str(config_path),
            "pdu_robot": name,
        })
        joints.append({
            "name": f"{name}/{logical_id}_joint",
            "mjcf_joint": config["spec"]["joint_name"],
        })

    controller = load_json(source["ackermann_controller"], "Ackermann controller config")
    controller["geometry"] = dict(geometry)
    for binding, logical_id in {
        "steering_left": "front_left_steer",
        "steering_right": "front_right_steer",
        "drive_left": "rear_left_wheel",
        "drive_right": "rear_right_wheel",
    }.items():
        controller["actuators"][binding] = actuator_ids[logical_id]
    controller_path = work / f"{key}-ackermann.json"
    write_json(controller_path, controller)
    components.append({
        "id": prefix + "ackermann",
        "kind": "controller",
        "type": "ackermann_controller",
        "config": str(controller_path),
        "pdu_robot": name,
    })
    return components, joints


def materialize_runtime(
    runtime_model: Path,
    work: Path,
    vehicles: list[dict],
) -> dict[str, Path]:
    source = paths()
    work.mkdir(parents=True, exist_ok=True)
    runtime = load_json(source["source_runtime"], "Robot Runtime config")
    runtime["actuators"] = {}

    command_pdu_types = [
        {"channel_id": 0, "pdu_size": 48, "name": COMMAND_PDU,
         "type": "ackermann_msgs/AckermannDrive"},
        {"channel_id": 1, "pdu_size": 32, "name": "front_left_steer_target",
         "type": "std_msgs/Float64"},
        {"channel_id": 2, "pdu_size": 32, "name": "front_right_steer_target",
         "type": "std_msgs/Float64"},
        {"channel_id": 3, "pdu_size": 32, "name": "rear_left_wheel_target",
         "type": "std_msgs/Float64"},
        {"channel_id": 4, "pdu_size": 32, "name": "rear_right_wheel_target",
         "type": "std_msgs/Float64"},
    ]
    state_pdu_types = [
        {"channel_id": 0, "pdu_size": 4096, "name": "joint_states",
         "type": "sensor_msgs/JointState"},
        {"channel_id": 1, "pdu_size": 4096, "name": "vehicle_states",
         "type": "sensor_msgs/MultiDOFJointState"},
    ]
    command_types_path = work / "urban-car-command-pdutypes.json"
    state_types_path = work / "urban-fleet-state-pdutypes.json"
    command_types_path.write_text(
        json.dumps(command_pdu_types, indent=2) + "\n", encoding="utf-8"
    )
    state_types_path.write_text(
        json.dumps(state_pdu_types, indent=2) + "\n", encoding="utf-8"
    )
    pdu_def = {
        "paths": [
            {"id": "urban-car-command", "path": str(command_types_path)},
            {"id": "urban-fleet-state", "path": str(state_types_path)},
        ],
        "robots": [
            *[
                {"name": vehicle["name"], "pdutypes_id": "urban-car-command"}
                for vehicle in vehicles
            ],
            {"name": FLEET_PDU_ROBOT, "pdutypes_id": "urban-fleet-state"},
        ],
    }
    pdu_def_path = work / "urban-car-pdudef.json"
    write_json(pdu_def_path, pdu_def)

    comm = load_json(source["source_comm"], "source Ackermann endpoint communication config")
    comm["name"] = "urban_car_fleet_shm"
    comm["io"]["robots"] = [
        *[{
            "name": vehicle["name"],
            "pdu": [
                {"name": item["name"], "notify_on_recv": item["name"] == COMMAND_PDU}
                for item in command_pdu_types
            ],
        } for vehicle in vehicles],
        {
            "name": FLEET_PDU_ROBOT,
            "pdu": [
                {"name": item["name"], "notify_on_recv": False}
                for item in state_pdu_types
            ],
        },
    ]
    comm_path = work / "urban-car-comm.json"
    write_json(comm_path, comm)

    endpoint = load_json(source["source_endpoint"], "source Ackermann endpoint config")
    endpoint["name"] = "urban_car_fleet_endpoint"
    endpoint["pdu_def_path"] = str(pdu_def_path)
    endpoint["cache"] = str(source["source_cache"].resolve())
    endpoint["comm"] = str(comm_path)
    endpoint_path = work / "urban-car-endpoint.json"
    write_json(endpoint_path, endpoint)

    components = []
    joint_bindings = []
    for vehicle in vehicles:
        vehicle_components, vehicle_joints = _materialize_vehicle_configs(work, vehicle)
        components.extend(vehicle_components)
        joint_bindings.extend(vehicle_joints)
        for component in vehicle_components:
            if component["kind"] == "actuator":
                runtime["actuators"][component["id"]] = {"command_timeout_sec": 0.2}

    runtime_path = work / "urban-car-runtime.json"
    write_json(runtime_path, runtime)

    joint_state = load_json(source["joint_state_output"], "JointState output config")
    joint_state["spec"]["joints"] = [
        {"name": item["name"]} for item in joint_bindings
    ]
    joint_state["mjcf_binding"] = {"joints": joint_bindings}
    joint_state_path = work / "urban-fleet-joint-state.json"
    write_json(joint_state_path, joint_state)

    multi_dof = load_json(source["multi_dof_state_template"], "MultiDOF state template")
    multi_dof["spec"]["bodies"] = [
        {"name": vehicle["name"]} for vehicle in vehicles
    ]
    multi_dof["mjcf_binding"]["bodies"] = [
        {
            "name": vehicle["name"],
            "mjcf_freejoint": (
                vehicle["prefix"]
                + vehicle["type_definition"]["interface"]["base_freejoint"]
            ),
        }
        for vehicle in vehicles
    ]
    multi_dof_path = work / "urban-fleet-multi-dof-state.json"
    write_json(multi_dof_path, multi_dof)

    components.extend([
        {"id": "urban_fleet_joint_states", "kind": "state_output", "type": "joint_state",
         "config": str(joint_state_path), "pdu_robot": FLEET_PDU_ROBOT},
        {"id": "urban_fleet_vehicle_states", "kind": "state_output",
         "type": "multi_dof_joint_state", "config": str(multi_dof_path),
         "pdu_robot": FLEET_PDU_ROBOT},
    ])
    manifest = {
        "schema_version": 1,
        "name": FLEET_ASSET_NAME,
        "description": "Urban-owned multi-vehicle Ackermann runtime",
        "model": str(runtime_model),
        "pdu_def": str(pdu_def_path),
        "endpoint": str(endpoint_path),
        "runtime_config": str(runtime_path),
        "components": components,
    }
    manifest_path = work / "urban-car-asset-manifest.json"
    write_json(manifest_path, manifest)
    return {
        "runtime": runtime_path,
        "pdu_def": pdu_def_path,
        "command_pdu_types": command_types_path,
        "state_pdu_types": state_types_path,
        "endpoint": endpoint_path,
        "comm": comm_path,
        "manifest": manifest_path,
    }


def launcher_supports_cleanup(python: Path) -> bool:
    probe = subprocess.run(
        [
            str(python),
            "-c",
            "from hakoniwa_pdu.apps.launcher.model import LauncherSpec; "
            "raise SystemExit(0 if 'runtime' in LauncherSpec.model_fields else 1)",
        ],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return probe.returncode == 0


def materialize_mjb(source_xml: Path, output_mjb: Path, receipt_path: Path) -> dict:
    source = paths()
    library = mujoco_library()
    if output_mjb.is_file() and receipt_path.is_file():
        receipt = load_json(receipt_path, "MuJoCo materialization receipt")
        expected = (
            receipt.get("source_xml_sha256") == sha256(source_xml)
            and receipt.get("output_mjb") == str(output_mjb.resolve())
            and receipt.get("output_mjb_sha256") == sha256(output_mjb)
            and receipt.get("mujoco_library") == str(library)
            and receipt.get("mujoco_library_sha256") == sha256(library)
            and receipt.get("reload_validation") == "passed"
        )
        if expected:
            print(f"Reusing validated MJB: {output_mjb}")
            return receipt

    command([
        str(foundation_python()), str(source["mujoco_compiler"]),
        "--xml", str(source_xml),
        "--mjb", str(output_mjb),
        "--mujoco-library", str(library),
        "--receipt", str(receipt_path),
    ])
    return load_json(receipt_path, "MuJoCo materialization receipt")


def materialize_launcher(
    runtime_files: dict[str, Path],
    work: Path,
    realtime_sync_cycle_msec: int,
    vehicles: list[dict],
) -> Path:
    source = paths()
    python = foundation_python()
    logs = work / "logs"
    runtime = work / "runtime"
    logs.mkdir(parents=True, exist_ok=True)
    runtime.mkdir(parents=True, exist_ok=True)
    install = foundation_install()
    assets = [
        {
            "name": "urban-car-fleet-plant",
            "activation_timing": "before_start",
            "command": str(source["plant"]),
            "args": [
                "--manifest", str(runtime_files["manifest"]),
                "--realtime-sync-cycle-msec", str(realtime_sync_cycle_msec),
            ],
            "delay_sec": 2,
            "readiness": {
                "type": "hako_asset",
                "asset_name": FLEET_ASSET_NAME,
                "timeout_sec": 120,
                "poll_interval_sec": 0.2,
                "command_timeout_sec": 2,
            },
        }
    ]
    for vehicle in vehicles:
        if vehicle["control_mode"] != "ps5":
            continue
        asset_key = vehicle["prefix"].rstrip("_").replace("_", "-")
        assets.append({
            "name": f"urban-{asset_key}-ps5-controller",
            "activation_timing": "after_start",
            "command": str(python),
            "args": [
                str(source["ps5_sender"]),
                "--pdu-def", str(runtime_files["pdu_def"]),
                "--rc-config", str(source["ps5_mapping"]),
                "--robot", vehicle["name"],
                "--pdu", COMMAND_PDU,
                "--max-speed", "3.5",
                "--max-steering-angle", "0.70",
                "--deadzone", "0.06",
            ],
            "depends_on": ["urban-car-fleet-plant"],
            "delay_sec": 1,
        })

    launcher = {
        "version": "0.1",
        "defaults": {
            "cwd": str(ROOT),
            "stdout": str(logs / "${asset}.out"),
            "stderr": str(logs / "${asset}.err"),
            "start_grace_sec": 2,
            "delay_sec": 1,
            "env": {
                "set": {
                    "HAKONIWA_CORE_ROOT": str(install),
                    "HAKONIWA_PDU_ENDPOINT_ROOT": str(install),
                    "HAKO_CONFIG_PATH": str(source["core_config"]),
                    "PYTHONUNBUFFERED": "1",
                },
                "prepend": {
                    "PATH": [str(python.parent), str(install / "bin")],
                    "DYLD_LIBRARY_PATH": [str(install / "lib")],
                },
            },
        },
        "assets": assets,
    }
    if not launcher_supports_cleanup(python):
        raise RecipeError(
            "Foundation Launcher does not support runtime.cleanup_mmap_on_start; "
            "upgrade hakoniwa-pdu before running this exclusive runtime Recipe"
        )
    launcher["runtime"] = {"cleanup_mmap_on_start": True}
    launcher_path = work / "launcher.json"
    write_json(launcher_path, launcher)
    return launcher_path


def doctor(resolved: dict) -> int:
    source = paths()
    checks: list[tuple[str, Path]] = [
        ("Urban Car Fleet configuration", resolved["path"]),
        ("City receipt", resolved["city_receipt"]),
    ]
    failed = False
    try:
        city_mjcf, city_glb, _ = city_inputs(resolved["city_receipt"])
        checks.extend([("City MJCF", city_mjcf), ("City GLB", city_glb)])
    except RecipeError as error:
        print(f"[NG] City receipt: {error}")
        failed = True
    checks.extend([
        ("MBody compose tool", source["compose_tool"]),
        ("MuJoCo MJB compiler", source["mujoco_compiler"]),
        ("Ackermann plant", source["plant"]),
        ("Ackermann PDU cache", source["source_cache"]),
        ("External Ackermann command sender", source["external_sender"]),
        ("Ackermann scenario executor", source["scenario_executor"]),
        ("External Ackermann Python client", source["command_client"]),
        ("Urban PS5 AckermannDrive sender", source["ps5_sender"]),
        ("Foundation Python", foundation_install() / "python/bin/python3"),
        ("Foundation Core config", source["core_config"]),
    ])
    for definition in resolved["vehicle_types"].values():
        checks.extend([
            (f"Vehicle type {definition['type']} MJCF", definition["mjcf"]),
            (f"Vehicle type {definition['type']} contract", definition["contract"]),
        ])
    try:
        checks.append(("Ackermann MuJoCo library", mujoco_library()))
    except RecipeError as error:
        print(f"[NG] Ackermann MuJoCo library: {error}")
        failed = True
    for label, path in checks:
        ok = path.is_file()
        print(f"[{'OK' if ok else 'NG'}] {label}: {path}")
        failed = failed or not ok
    return 1 if failed else 0


def configure(resolved: dict) -> int:
    source = paths()
    work = resolved["work"]
    vehicles = resolved["vehicles"]
    city_mjcf, city_glb, receipt = city_inputs(resolved["city_receipt"])
    for label, path in paths().items():
        if label != "plant":
            required(path, label)
    required(foundation_install() / "python/bin/python3", "Foundation Python")
    required(source["core_config"], "Foundation Core config")
    work.mkdir(parents=True, exist_ok=True)
    fleet_model = work / "urban-car-fleet.xml"
    materialize_vehicle_fleet_model(fleet_model, vehicles)
    output = work / "urban-cars-city.xml"
    command([
        str(foundation_python()), str(source["compose_tool"]),
        str(fleet_model), str(city_mjcf),
        "--output", str(output),
    ])

    mjb = work / "urban-cars-city.mjb"
    mjb_receipt = work / "mujoco-materialization.json"
    materialization = materialize_mjb(output, mjb, mjb_receipt)

    runtime_files = materialize_runtime(mjb, work, vehicles)
    launcher = materialize_launcher(
        runtime_files,
        work,
        resolved["realtime_sync_cycle_msec"],
        vehicles,
    )
    compose_receipt = {
        "schema_version": 1,
        "recipe": resolved["recipe_id"],
        "configuration": str(resolved["path"]),
        "city_receipt": str(resolved["city_receipt"]),
        "city_mjcf": {"path": str(city_mjcf), "sha256": sha256(city_mjcf)},
        "city_glb": {"path": str(city_glb), "sha256": sha256(city_glb)},
        "vehicle_types": [
            {
                "type": definition["type"],
                "mjcf": {"path": str(definition["mjcf"]), "sha256": sha256(definition["mjcf"])},
                "contract": {
                    "path": str(definition["contract"]),
                    "sha256": sha256(definition["contract"]),
                },
            }
            for definition in resolved["vehicle_types"].values()
        ],
        "output_mjcf": {"path": str(output), "sha256": sha256(output)},
        "runtime_mjb": {
            "path": str(mjb),
            "sha256": sha256(mjb),
            "mujoco_version": materialization["mujoco_version"],
            "mujoco_library": materialization["mujoco_library"],
            "reload_validation": materialization["reload_validation"],
            "materialization_receipt": str(mjb_receipt),
        },
        "vehicles": [
            {
                "name": vehicle["name"],
                "type": vehicle["type"],
                "mjcf_prefix": vehicle["prefix"],
                "control_mode": vehicle["control_mode"],
                "spawn_pose_city_enu": vehicle["spawn_enu"],
                "spawn_pose_mjcf": {
                    "frame": "X=North,Y=-East,Z=Up",
                    "position_m": list(vehicle["spawn_mjcf"][:3]),
                    "yaw_rad": vehicle["spawn_mjcf"][5],
                },
            }
            for vehicle in vehicles
        ],
        "coordinate_frame": receipt["coordinate_frame"],
        "runtime_manifest": str(runtime_files["manifest"]),
        "core_config": str(source["core_config"]),
        "runtime_ownership": "exclusive Foundation mmap; Launcher cleanup_mmap_on_start",
        "realtime_sync_cycle_msec": resolved["realtime_sync_cycle_msec"],
        "launcher": str(launcher),
    }
    write_json(work / "compose-receipt.json", compose_receipt)
    print(f"Composed MJCF : {output}")
    print(f"Runtime MJB   : {mjb}")
    print(f"Manifest      : {runtime_files['manifest']}")
    print(f"Launcher      : {launcher}")
    print("Vehicles      : " + ", ".join(
        f"{vehicle['name']}:{vehicle['type']}={vehicle['control_mode']}"
        for vehicle in vehicles
    ))
    if any(vehicle["control_mode"] == "external_python" for vehicle in vehicles):
        print("External control: run apps/car/ackermann_command.py with --robot NAME")
    print("Use 'start' for the configured runtime, or 'view' for Viewer-only inspection.")
    return 0


def launcher_path(work: Path) -> Path:
    return required(work / "launcher.json", "generated Launcher; run configure first")


def session_path(work: Path) -> Path:
    return work / "runtime/launcher-session.json"


def launch(operation: str, work: Path) -> int:
    python = foundation_python()
    if operation == "start":
        command([
            str(python), "-m", "hakoniwa_pdu.apps.launcher.hako_launcher",
            str(launcher_path(work)), "--background", str(session_path(work)),
        ])
    else:
        command([
            str(python), "-m", "hakoniwa_pdu.apps.launcher.hako_launcher_ctl",
            "status" if operation == "status" else "terminate", str(session_path(work)),
        ])
    return 0


def view(work: Path) -> int:
    manifest = required(
        work / "urban-car-asset-manifest.json",
        "generated manifest; run configure first",
    )
    command([str(paths()["plant"]), "--manifest", str(manifest), "--view-model"])
    return 0


def check_ps5(resolved: dict) -> int:
    source = paths()
    ps5_vehicles = [
        vehicle for vehicle in resolved["vehicles"]
        if vehicle["control_mode"] == "ps5"
    ]
    robot = ps5_vehicles[0]["name"] if ps5_vehicles else resolved["vehicles"][0]["name"]
    command([
        str(foundation_python()),
        str(required(source["ps5_sender"], "PS5 sender")),
        "--pdu-def", str(required(
            resolved["work"] / "urban-car-pdudef.json",
            "generated PDU definition",
        )),
        "--check-controller",
        "--rc-config", str(required(source["ps5_mapping"], "PS5 mapping")),
        "--robot", robot,
    ])
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Operate a configured Urban City World + Car Fleet recipe")
    result.add_argument(
        "command",
        choices=("doctor", "configure", "check-ps5", "view", "start", "status", "stop"),
    )
    result.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG,
        help=f"configuration YAML (default: {DEFAULT_CONFIG.relative_to(ROOT)})",
    )
    return result


def main() -> int:
    args = parser().parse_args()
    resolved = resolve_config(args.config)
    if args.command == "doctor":
        return doctor(resolved)
    if args.command == "configure":
        return configure(resolved)
    if args.command == "check-ps5":
        return check_ps5(resolved)
    if args.command == "view":
        return view(resolved["work"])
    return launch(args.command, resolved["work"])


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RecipeError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
