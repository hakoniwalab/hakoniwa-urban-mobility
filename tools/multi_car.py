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
import struct
import subprocess
import sys
from urllib.parse import urlencode
import webbrowser
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
BUSINESS_PACK = WORKSPACE / "hakoniwa-business-pack"
sys.path.insert(0, str(ROOT / "apps/car"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(BUSINESS_PACK / "tools"))

from route_geometry import RouteGeometry, RoutePoint, expand_route_vehicles  # noqa: E402
import urban_lifecycle  # noqa: E402
from workdir import foundation_install as resolve_foundation_install  # noqa: E402
from workdir import recipe_root as resolve_recipe_root  # noqa: E402

MBODY = WORKSPACE / "hakoniwa-mbody-registry"
MUJOCO_ROBOTS = WORKSPACE / "hakoniwa-mujoco-robots"
MAP_VIEWER = WORKSPACE / "hakoniwa-map-viewer"
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


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def workspace_url(path: Path) -> str:
    """Return an HTTP path for a file served from the workspace root."""
    try:
        relative = path.resolve().relative_to(WORKSPACE.resolve())
    except ValueError as error:
        raise RecipeError(f"browser asset is outside the workspace: {path}") from error
    return "/" + relative.as_posix()


def map_viewer_url(resolved: dict, viewer_config: Path) -> str:
    receipt = load_json(resolved["city_receipt"], "City World receipt")
    try:
        origin = receipt["coordinate_frame"]["origin"]
        latitude = float(origin["latitude"])
        longitude = float(origin["longitude"])
    except (KeyError, TypeError, ValueError) as error:
        raise RecipeError("City World receipt has no valid map origin") from error
    query = urlencode({
        "threejsRoot": workspace_url(resolved["visualization"]["threejs_root"]),
        "viewerConfigPath": workspace_url(viewer_config),
        "layout": "three-main",
        "originLat": latitude,
        "originLon": longitude,
        "autoConnect": "true",
    })
    return (
        f"http://127.0.0.1:{resolved['visualization']['http_port']}"
        + workspace_url(MAP_VIEWER / "src/client/index.html")
        + "?"
        + query
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def foundation_install() -> Path:
    """Resolve the active Foundation prefix through the Business Pack contract."""
    return resolve_foundation_install(BUSINESS_PACK)


def recipe_workspace(recipe_id: str) -> Path:
    """Resolve a Recipe output root through the Business Pack workdir contract."""
    return resolve_recipe_root(BUSINESS_PACK, recipe_id)


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


def expand_config_vehicle_inputs(
    vehicle_inputs: object,
) -> tuple[list[dict], dict | None]:
    if isinstance(vehicle_inputs, list):
        if not vehicle_inputs:
            raise RecipeError("ackermann_vehicles.vehicles must not be empty")
        return vehicle_inputs, None
    if not isinstance(vehicle_inputs, dict):
        raise RecipeError(
            "ackermann_vehicles.vehicles must be an array or generated_from_route"
        )
    generated = vehicle_inputs.get("generated_from_route")
    if not isinstance(generated, dict):
        raise RecipeError("ackermann_vehicles.vehicles.generated_from_route is required")
    try:
        scenario_path = resolve_path(
            generated["scenario"], "generated vehicle route scenario"
        )
        scenario = load_yaml(scenario_path)
        if scenario.get("schema_version") not in (2, 3):
            raise RecipeError(
                "generated vehicle route scenario must use schema_version 2 or 3"
            )
        route = scenario["route"]
        if route.get("closed") is not True:
            raise RecipeError("generated vehicle route must be closed")
        points = tuple(
            RoutePoint(
                name=str(item.get("name", f"point-{index + 1}")),
                east_m=float(item["east_m"]),
                north_m=float(item["north_m"]),
                dwell_sec=float(item.get("dwell_sec", 0.0)),
            )
            for index, item in enumerate(route["points"])
        )
        geometry = RouteGeometry(points)
        fleet = expand_route_vehicles(scenario.get("vehicles"))
        type_name = str(generated["type"]).strip()
        control_mode = str(
            generated.get("control_mode", "external_python")
        ).strip()
        has_absolute_height = "up_m" in generated
        has_ground_clearance = "ground_clearance_m" in generated
        if has_absolute_height == has_ground_clearance:
            raise RecipeError(
                "generated vehicle requires exactly one of up_m or ground_clearance_m"
            )
        height_key = "up_m" if has_absolute_height else "ground_clearance_m"
        height_value = float(generated[height_key])
    except (KeyError, TypeError, ValueError) as error:
        raise RecipeError(f"invalid generated vehicle route: {error}") from error
    if not fleet:
        raise RecipeError("generated vehicle route fleet must not be empty")
    if not type_name:
        raise RecipeError("generated vehicle type must not be empty")
    if not math.isfinite(height_value):
        raise RecipeError(f"generated vehicle {height_key} must be finite")
    max_offset = max(-vehicle.offset_m for vehicle in fleet)
    if max_offset >= geometry.length / 2.0:
        raise RecipeError(
            "generated fleet offsets must be less than half the route length"
        )
    result = []
    for vehicle in fleet:
        if scenario.get("schema_version") == 2:
            east_m, north_m = geometry.sample(vehicle.offset_m)
            yaw_rad = geometry.heading_rad(vehicle.offset_m)
        else:
            (east_m, north_m), yaw_rad = geometry.formation_sample(
                0.0, vehicle.offset_m, vehicle.lateral_offset_m
            )
        yaw_deg = math.degrees(yaw_rad)
        result.append({
            "name": vehicle.name,
            "type": type_name,
            "control_mode": control_mode,
            "spawn_pose_enu": {
                "frame": "city_origin_local_enu",
                "east_m": east_m,
                "north_m": north_m,
                height_key: height_value,
                "yaw_deg": yaw_deg,
            },
        })
    return result, {
        "scenario": scenario_path,
        "route_length_m": geometry.length,
        "vehicle_count": len(result),
        "max_route_offset_m": max_offset,
        "auto_start_scenario": bool(generated.get("auto_start_scenario", False)),
    }


def resolve_config(config_path: Path) -> dict:
    config = load_yaml(config_path.resolve())
    try:
        recipe_id = str(config["id"])
        city = config["inputs"]["business_pack_city_receipt"]
        city_receipt = resolve_path(city["path"], "City World receipt path")
        vehicle_config = config["inputs"]["ackermann_vehicles"]
        type_inputs = vehicle_config["types"]
        vehicle_inputs, vehicle_generation = expand_config_vehicle_inputs(
            vehicle_config["vehicles"]
        )
        runtime_input = config["inputs"]["ackermann_runtime"]
        realtime_sync_cycle_msec = int(runtime_input.get(
            "realtime_sync_cycle_msec", 2
        ))
        native_mujoco_viewer = runtime_input.get("native_mujoco_viewer", True)
        visualization = config["inputs"].get("browser_visualization", {})
        visualization_enabled = bool(visualization.get("enabled", False))
        front_camera = visualization.get("front_camera")
        web_bridge_port = int(visualization.get("web_bridge_port", 8765))
        http_port = int(visualization.get("http_port", 8000))
        threejs_root = resolve_path(
            visualization.get("threejs_root", "../hakoniwa-threejs-drone"),
            "Three.js root",
        )
        output_recipe_id = str(
            config["composition"]["output"].get("recipe_id", recipe_id)
        ).strip()
        mirror_inputs = config["inputs"].get("drone_mirrors", [])
    except (KeyError, TypeError, ValueError) as error:
        raise RecipeError(f"unsupported Urban Car Fleet configuration schema: {config_path}") from error
    if not isinstance(type_inputs, list) or not type_inputs:
        raise RecipeError("ackermann_vehicles.types must be a non-empty array")
    if not output_recipe_id or output_recipe_id != recipe_id:
        raise RecipeError(
            "composition.output.recipe_id must match the top-level recipe id"
        )
    work = recipe_workspace(output_recipe_id)
    if not isinstance(mirror_inputs, list):
        raise RecipeError("drone_mirrors must be an array")
    if realtime_sync_cycle_msec < 0:
        raise RecipeError("realtime_sync_cycle_msec must be non-negative")
    if not isinstance(native_mujoco_viewer, bool):
        raise RecipeError("native_mujoco_viewer must be boolean")
    if not (1 <= web_bridge_port <= 65535 and 1 <= http_port <= 65535):
        raise RecipeError("browser visualization ports must be within 1..65535")
    if front_camera is not None and not isinstance(front_camera, dict):
        raise RecipeError("browser_visualization.front_camera must be a mapping")

    vehicle_types = {}
    for index, type_input in enumerate(type_inputs):
        try:
            type_name = str(type_input["type"]).strip()
            mjcf = resolve_path(type_input["mjcf"], f"vehicle type {type_name} MJCF")
            contract_path = resolve_path(
                type_input["contract"], f"vehicle type {type_name} contract"
            )
            contract = load_yaml(contract_path)
            view_model_path = resolve_path(
                type_input["view_model"], f"vehicle type {type_name} view model"
            )
            view_model = load_json(view_model_path, f"vehicle type {type_name} view model")
            if view_model.get("format") != "hako_viewer_model":
                raise RecipeError(f"vehicle type {type_name} has an invalid view model")
            validation = contract["validation"]
            interface = validation["interface"]
            geometry = validation["geometry"]
            resolved_type = {
                "type": type_name,
                "mjcf": required(mjcf, f"vehicle type {type_name} MJCF"),
                "contract": required(contract_path, f"vehicle type {type_name} contract"),
                "view_model": required(
                    view_model_path, f"vehicle type {type_name} view model"
                ),
                "view_model_data": view_model,
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
            has_absolute_height = "up_m" in spawn
            has_ground_clearance = "ground_clearance_m" in spawn
            if has_absolute_height == has_ground_clearance:
                raise RecipeError(
                    "spawn pose requires exactly one of up_m or ground_clearance_m"
                )
            height_key = "up_m" if has_absolute_height else "ground_clearance_m"
            height_value = float(spawn[height_key])
            yaw_deg = float(spawn["yaw_deg"])
        except (KeyError, TypeError, ValueError) as error:
            raise RecipeError(f"invalid vehicle entry at index {index - 1}") from error
        if not name or name in names:
            raise RecipeError(f"vehicle names must be non-empty and unique: {name!r}")
        if type_name not in vehicle_types:
            raise RecipeError(f"vehicle {name} references unknown type: {type_name!r}")
        if any(not math.isfinite(value) for value in (
            east, north, height_value, yaw_deg
        )):
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
                height_key: height_value,
                "yaw_deg": yaw_deg,
            },
            "spawn_height": {
                "mode": "absolute" if has_absolute_height else "terrain_relative",
                "value_m": height_value,
            },
            # City World MJCF is X=North, Y=-East, Z=Up. ENU yaw is positive
            # counter-clockwise from East; MuJoCo yaw is measured from +X.
            "spawn_mjcf": (
                north, -east, height_value, 0.0, 0.0,
                math.radians(yaw_deg - 90.0)
            ),
        })

    mirrors = []
    mirror_names: set[str] = set()
    for index, mirror_input in enumerate(mirror_inputs, start=1):
        try:
            name = str(mirror_input["name"]).strip()
            pdu_types = resolve_path(
                mirror_input["pdu_types"], f"Drone Mirror {name} PDU types"
            )
            model_value = mirror_input.get("mjcf_model")
            model = (
                resolve_path(model_value, f"Drone Mirror {name} MJCF model")
                if model_value is not None
                else None
            )
            model_body = str(mirror_input.get("mjcf_body", "drone_base")).strip()
            initial_position = tuple(
                float(value) for value in mirror_input.get(
                    "initial_position_mjcf", [0.0, 0.0, 7.0]
                )
            )
            restitution = float(mirror_input.get("restitution_coefficient", 0.3))
            speed_threshold = float(
                mirror_input.get("relative_normal_speed_threshold_mps", 0.2)
            )
            cooldown_sec = float(mirror_input.get("cooldown_sec", 0.1))
        except (KeyError, TypeError, ValueError) as error:
            raise RecipeError(f"invalid Drone Mirror at index {index - 1}") from error
        if not name or name in mirror_names:
            raise RecipeError(f"Drone Mirror names must be non-empty and unique: {name!r}")
        if model is not None and not model_body:
            raise RecipeError(f"Drone Mirror {name} mjcf_body must be non-empty")
        if len(initial_position) != 3 or any(
            not math.isfinite(value) for value in initial_position
        ):
            raise RecipeError(f"Drone Mirror {name} initial_position_mjcf is invalid")
        if not 0.0 <= restitution <= 1.0:
            raise RecipeError(f"Drone Mirror {name} restitution must be within 0..1")
        if speed_threshold < 0.0 or cooldown_sec <= 0.0:
            raise RecipeError(f"Drone Mirror {name} collision policy is invalid")
        mirror_names.add(name)
        mirrors.append({
            "name": name,
            "prefix": f"mirror_drone_{index}_",
            "pdu_types": required(pdu_types, f"Drone Mirror {name} PDU types"),
            "mjcf_model": (
                required(model, f"Drone Mirror {name} MJCF model")
                if model is not None
                else None
            ),
            "mjcf_body": model_body,
            "initial_position_mjcf": initial_position,
            "restitution_coefficient": restitution,
            "relative_normal_speed_threshold_mps": speed_threshold,
            "cooldown_sec": cooldown_sec,
        })
    return {
        "raw": config,
        "path": config_path.resolve(),
        "recipe_id": recipe_id,
        "city_receipt": city_receipt,
        "work": work,
        "vehicles": vehicles,
        "mirrors": mirrors,
        "vehicle_types": vehicle_types,
        "vehicle_generation": vehicle_generation,
        "realtime_sync_cycle_msec": realtime_sync_cycle_msec,
        "native_mujoco_viewer": native_mujoco_viewer,
        "visualization": {
            "enabled": visualization_enabled,
            "web_bridge_port": web_bridge_port,
            "http_port": http_port,
            "threejs_root": threejs_root,
            "front_camera": front_camera,
        },
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
        "web_bridge": foundation_install() / "bin/hakoniwa-pdu-web-bridge",
        "http_server": ROOT / "tools/workspace_http_server.py",
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
        glb_frame = coordinate_frame["coordinate_systems"]["glb"]
    except (KeyError, TypeError, ValueError) as error:
        raise RecipeError(f"City World receipt has an unsupported schema: {city_receipt}") from error
    if any(
        not math.isfinite(value)
        for value in (latitude, longitude, altitude_offset, north_south, east_west)
    ) or north_south <= 0.0 or east_west <= 0.0:
        raise RecipeError(f"City World receipt has invalid origin or extent: {city_receipt}")
    if mjcf_frame != "X=North,Y=-East,Z=Up":
        raise RecipeError(f"unsupported City World MJCF coordinate system: {mjcf_frame}")
    if glb_frame != "X=East,Y=Up,Z=-North":
        raise RecipeError(f"unsupported City World GLB coordinate system: {glb_frame}")
    return required(mjcf, "City World MJCF"), required(glb, "City World GLB"), receipt


def terrain_height_mjcf(receipt: dict, north_m: float, minus_east_m: float) -> float:
    """Evaluate the exact MuJoCo hfield triangle surface at one local XY point."""
    try:
        coordinate_frame = receipt["coordinate_frame"]
        half_extent = coordinate_frame["half_extent_m"]
        north_south = float(half_extent["north_south"])
        east_west = float(half_extent["east_west"])
        altitude_offset = float(coordinate_frame["origin"]["altitude_offset_m"])
        terrain_xml = Path(receipt["components"]["terrain_xml"])
        terrain_receipt = load_json(
            terrain_xml.with_name("terrain-receipt.json"), "terrain receipt"
        )
        hfield_path = Path(terrain_receipt["hfield"]["path"])
    except (KeyError, TypeError, ValueError) as error:
        raise RecipeError("City World receipt has no usable terrain hfield") from error

    data = required(hfield_path, "terrain hfield").read_bytes()
    if len(data) < 8:
        raise RecipeError(f"terrain hfield is truncated: {hfield_path}")
    nrow, ncol = struct.unpack("<ii", data[:8])
    expected = 8 + 4 * nrow * ncol
    if nrow < 2 or ncol < 2 or len(data) != expected:
        raise RecipeError(f"terrain hfield has an invalid shape: {hfield_path}")
    samples = struct.unpack(f"<{nrow * ncol}f", data[8:])

    column = (north_m + north_south) * (ncol - 1) / (2.0 * north_south)
    row = (minus_east_m + east_west) * (nrow - 1) / (2.0 * east_west)
    column = min(max(column, 0.0), ncol - 1.0)
    row = min(max(row, 0.0), nrow - 1.0)
    col_index = min(int(column), ncol - 2)
    row_index = min(int(row), nrow - 2)
    dc = column - col_index
    dr = row - row_index
    at = lambda r, c: samples[r * ncol + c]
    bottom_left = at(row_index, col_index)
    top_right = at(row_index + 1, col_index + 1)
    if dr >= dc:
        top_left = at(row_index + 1, col_index)
        absolute_height = (
            bottom_left
            + dr * (top_left - bottom_left)
            + dc * (top_right - top_left)
        )
    else:
        bottom_right = at(row_index, col_index + 1)
        absolute_height = (
            bottom_left
            + dc * (bottom_right - bottom_left)
            + dr * (top_right - bottom_right)
        )
    return absolute_height - altitude_offset


def initial_body_poses(resolved: dict, receipt: dict | None = None) -> list[dict]:
    if receipt is None:
        _, _, receipt = city_inputs(resolved["city_receipt"])
    poses = []
    for vehicle in resolved["vehicles"]:
        north, minus_east, _, roll, pitch, yaw = vehicle["spawn_mjcf"]
        height = vehicle["spawn_height"]
        if height["mode"] == "terrain_relative":
            surface = terrain_height_mjcf(receipt, north, minus_east)
            up = surface + height["value_m"]
        else:
            surface = None
            up = height["value_m"]
        poses.append({
            "name": vehicle["name"],
            "mjcf_freejoint": (
                vehicle["prefix"]
                + vehicle["type_definition"]["interface"]["base_freejoint"]
            ),
            "position_m": [north, minus_east, up],
            "orientation_rpy_rad": [roll, pitch, yaw],
            "terrain_height_m": surface,
        })
    return poses


def refresh_runtime_initial_body_poses(resolved: dict) -> list[dict]:
    runtime_path = required(
        resolved["work"] / "config/car/urban-car-runtime.json",
        "generated Runtime configuration; run configure first",
    )
    runtime = load_json(runtime_path, "Runtime configuration")
    poses = initial_body_poses(resolved)
    runtime["initial_body_poses"] = [
        {
            "mjcf_freejoint": pose["mjcf_freejoint"],
            "position_m": pose["position_m"],
            "orientation_rpy_rad": pose["orientation_rpy_rad"],
        }
        for pose in poses
    ]
    write_json(runtime_path, runtime)
    for pose in poses:
        terrain = pose["terrain_height_m"]
        detail = "absolute"
        if terrain is not None:
            detail = f"terrain={terrain:.3f}m"
        print(
            f"Initial pose  : {pose['name']} z={pose['position_m'][2]:.3f}m "
            f"({detail})"
        )
    return poses


def city_collider_glb(city_receipt: Path) -> Path:
    world_dir = city_receipt.resolve().parent
    if world_dir.name != "world" or world_dir.parent.name != "build":
        raise RecipeError(
            f"City World receipt is outside a Worker job: {city_receipt}"
        )
    return required(
        world_dir.parent.parent / "viewer/city-world-colliders.glb",
        "City World Collider GLB",
    )


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
    mirrors: list[dict] | None = None,
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
        body = copy.deepcopy(template_body)
        _namespace_mjcf(body, names, prefix, asset_names, asset_prefix)
        # Vehicle spawn poses are runtime state, not model structure. Keeping
        # them out of MJCF lets the expensive City World MJB remain reusable.
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

    # Import an explicitly selected Drone body when the experiment provides
    # one. The Mirror owns only this rigid proxy; propulsion and control remain
    # exclusively in the external Drone asset.
    for mirror in mirrors or []:
        prefix = mirror["prefix"]
        mirror_model = mirror.get("mjcf_model")
        if mirror_model is not None:
            source_tree = ET.parse(mirror_model)
            source_root = source_tree.getroot()
            source_worldbody = source_root.find("worldbody")
            if source_worldbody is None:
                raise RecipeError(f"Drone Mirror MJCF has no worldbody: {mirror_model}")
            template_body = next(
                (
                    item for item in list(source_worldbody)
                    if item.tag == "body" and item.get("name") == mirror["mjcf_body"]
                ),
                None,
            )
            if template_body is None:
                raise RecipeError(
                    f"Drone Mirror MJCF has no body {mirror['mjcf_body']}: {mirror_model}"
                )
            source_asset = source_root.find("asset")
            asset_names = (
                _named_objects(source_asset) if source_asset is not None else set()
            )
            asset_prefix = prefix + "asset_"
            if source_asset is not None:
                for template in list(source_asset):
                    item = copy.deepcopy(template)
                    _absolute_asset_files(item, source_root, mirror_model)
                    _namespace_mjcf(
                        item, asset_names, asset_prefix, asset_names, asset_prefix
                    )
                    asset_output.append(item)
            names = _named_objects(template_body)
            body = copy.deepcopy(template_body)
            _namespace_mjcf(body, names, prefix, asset_names, asset_prefix)
            body.set("name", prefix + "body")
            body.set(
                "pos",
                " ".join(str(value) for value in mirror["initial_position_mjcf"]),
            )
            freejoint = body.find("freejoint")
            if freejoint is None:
                raise RecipeError(f"Drone Mirror body has no freejoint: {mirror_model}")
            freejoint.set("name", prefix + "freejoint")
            worldbody_output.append(body)
            continue

        # Backward-compatible standard Quad proxy.
        body = ET.SubElement(
            worldbody_output,
            "body",
            {
                "name": prefix + "body",
                "pos": " ".join(str(value) for value in mirror["initial_position_mjcf"]),
            },
        )
        ET.SubElement(body, "freejoint", {"name": prefix + "freejoint"})
        common = {
            "friction": "0.5 0.5 0.5",
            "contype": "2",
            "conaffinity": "1",
        }
        ET.SubElement(body, "geom", {
            **common, "name": prefix + "base", "type": "box",
            "size": "0.07 0.07 0.015", "density": "810",
            "rgba": "0.5 0.5 0.5 1",
        })
        arm_specs = (
            (1, 0.13, -0.13, 0.05, -0.05, math.pi / 4.0, "0.3 0.7 0.9 1"),
            (2, -0.13, 0.13, -0.05, 0.05, math.pi / 4.0, "0.4 0.9 0.5 1"),
            (3, 0.13, 0.13, 0.05, 0.05, -math.pi / 4.0, "1 0.6 0.7 1"),
            (4, -0.13, -0.13, -0.05, -0.05, -math.pi / 4.0, "1 0.4 0 1"),
        )
        for index, arm_x, arm_y, prop_x, prop_y, arm_yaw, prop_color in arm_specs:
            arm = ET.SubElement(body, "body", {
                "name": prefix + f"arm{index}",
                "pos": f"{arm_x} {arm_y} 0",
            })
            ET.SubElement(arm, "geom", {
                **common, "name": prefix + f"arm_geom{index}",
                "type": "cylinder", "size": "0.008 0.077",
                "euler": f"{math.pi / 2.0} {arm_yaw} 0",
                "density": "500", "rgba": "0 0 0 1",
            })
            propeller = ET.SubElement(arm, "body", {
                "name": prefix + f"propeller{index}",
                "pos": f"{prop_x} {prop_y} 0.02",
            })
            ET.SubElement(propeller, "geom", {
                **common, "name": prefix + f"propeller_geom{index}",
                "type": "cylinder", "size": "0.076 0.0025",
                "density": "200", "rgba": prop_color,
            })
            motor = ET.SubElement(body, "body", {
                "name": prefix + f"motor{index}",
                "pos": f"{arm_x + prop_x} {arm_y + prop_y} 0.02",
            })
            ET.SubElement(motor, "geom", {
                **common, "name": prefix + f"motor_geom{index}",
                "type": "box", "size": "0.007 0.007 0.007",
                "density": "500", "rgba": "0 0 0 1",
            })

        leg_specs = (
            (1, -0.1, 0.1, math.radians(20.0)),
            (2, 0.1, 0.1, math.radians(20.0)),
            (3, -0.1, -0.1, math.radians(-20.0)),
            (4, 0.1, -0.1, math.radians(-20.0)),
        )
        for index, x, y, roll in leg_specs:
            leg = ET.SubElement(body, "body", {
                "name": prefix + f"leg{index}", "pos": f"{x} {y} -0.10",
            })
            ET.SubElement(leg, "geom", {
                **common, "name": prefix + f"leg_geom{index}",
                "type": "cylinder", "size": "0.008 0.077",
                "euler": f"{roll} 0 0", "density": "500",
                "rgba": "0 0 0 1",
            })
        for index, y in enumerate((-0.13, 0.13), start=1):
            skid = ET.SubElement(body, "body", {
                "name": prefix + f"skid{index}", "pos": f"0 {y} -0.18",
            })
            ET.SubElement(skid, "geom", {
                **common, "name": prefix + f"skid_geom{index}",
                "type": "cylinder", "size": "0.008 0.13",
                "euler": f"0 {math.pi / 2.0} 0", "density": "500",
                "rgba": "1 0.6 0.7 1",
            })

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
    mirrors: list[dict] | None = None,
) -> dict[str, Path]:
    source = paths()
    mirrors = mirrors or []
    work.mkdir(parents=True, exist_ok=True)
    runtime = load_json(source["source_runtime"], "Robot Runtime config")
    runtime["actuators"] = {}

    # JointState carries the actuator-backed steering and driven-wheel joints.
    # MultiDOFJointState also carries one name, transform, twist, and wrench per
    # vehicle. Keep a power-of-two buffer with conservative room for both
    # generated variable-length payloads.
    estimated_state_bytes = 1024 + 768 * len(vehicles)
    state_pdu_size = max(4096, 1 << (estimated_state_bytes - 1).bit_length())

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
        {"channel_id": 0, "pdu_size": state_pdu_size, "name": "joint_states",
         "type": "sensor_msgs/JointState"},
        {"channel_id": 1, "pdu_size": state_pdu_size, "name": "vehicle_states",
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
            *[
                {"id": f"urban-drone-mirror-{index}", "path": str(mirror["pdu_types"])}
                for index, mirror in enumerate(mirrors, start=1)
            ],
        ],
        "robots": [
            *[
                {"name": vehicle["name"], "pdutypes_id": "urban-car-command"}
                for vehicle in vehicles
            ],
            {"name": FLEET_PDU_ROBOT, "pdutypes_id": "urban-fleet-state"},
            *[
                {"name": mirror["name"], "pdutypes_id": f"urban-drone-mirror-{index}"}
                for index, mirror in enumerate(mirrors, start=1)
            ],
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
        *[{
            "name": mirror["name"],
            "pdu": [
                {"name": "pos", "notify_on_recv": False},
                {"name": "velocity", "notify_on_recv": False},
                {"name": "impulse", "notify_on_recv": False},
            ],
        } for mirror in mirrors],
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

    for mirror in mirrors:
        key = mirror["prefix"].rstrip("_")
        mirror_component_id = key + "_controller"
        mirror_config = {
            "$schema": "https://hakoniwa.dev/schemas/mirror-body-controller.schema.json",
            "schema_version": 1,
            "spec": {"mirror_id": mirror["name"]},
            "input": {
                "pose": {
                    "pdu_name": "pos",
                    "message_type": "geometry_msgs/Twist",
                },
                "velocity": {
                    "pdu_name": "velocity",
                    "message_type": "geometry_msgs/Twist",
                    "frame": "body",
                },
            },
            "mjcf_binding": {
                "freejoint": mirror["prefix"] + "freejoint",
                "contact_bodies": [
                    {
                        "body_id": vehicle["name"],
                        "mjcf_freejoint": (
                            vehicle["prefix"]
                            + vehicle["type_definition"]["interface"]["base_freejoint"]
                        ),
                    }
                    for vehicle in vehicles
                ],
            },
        }
        mirror_config_path = work / f"{key}-controller.json"
        write_json(mirror_config_path, mirror_config)
        impulse_config = {
            "$schema": "https://hakoniwa.dev/schemas/impulse-collision-output.schema.json",
            "schema_version": 1,
            "spec": {"mirror_component": mirror_component_id},
            "pdu_config": {
                "pdu_name": "impulse",
                "message_type": "hako_msgs/ImpulseCollision",
            },
            "policy": {
                "restitution_coefficient": mirror["restitution_coefficient"],
                "relative_normal_speed_threshold_mps": (
                    mirror["relative_normal_speed_threshold_mps"]
                ),
                "cooldown_sec": mirror["cooldown_sec"],
            },
        }
        impulse_config_path = work / f"{key}-impulse.json"
        write_json(impulse_config_path, impulse_config)
        components.extend([
            {
                "id": mirror_component_id,
                "kind": "controller",
                "type": "mirror_body",
                "config": str(mirror_config_path),
                "pdu_robot": mirror["name"],
            },
            {
                "id": key + "_impulse",
                "kind": "state_output",
                "type": "impulse_collision",
                "config": str(impulse_config_path),
                "pdu_robot": mirror["name"],
            },
        ])

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


def materialize_browser_visualization(
    runtime_files: dict[str, Path],
    work: Path,
    city_glb: Path,
    vehicles: list[dict],
    visualization: dict,
    collider_glb: Path | None = None,
) -> dict[str, Path | str]:
    """Materialize a read-only state bridge and compact Three.js configs."""
    bridge_root = work / "web-bridge"
    browser_root = work / "threejs"
    state_types = json.loads(runtime_files["state_pdu_types"].read_text(encoding="utf-8"))

    state_types_path = bridge_root / "pdu/urban-fleet-state-pdutypes.json"
    state_types_path.parent.mkdir(parents=True, exist_ok=True)
    state_types_path.write_text(json.dumps(state_types, indent=2) + "\n", encoding="utf-8")
    browser_pdu_def = {
        "paths": [{
            "id": "urban-fleet-state",
            "path": "urban-fleet-state-pdutypes.json",
        }],
        "robots": [{
            "name": FLEET_PDU_ROBOT,
            "pdutypes_id": "urban-fleet-state",
        }],
    }
    browser_pdu_def_path = bridge_root / "pdu/urban-visual-state.json"
    write_json(browser_pdu_def_path, browser_pdu_def)
    write_json(bridge_root / "cache/latest.json", {
        "type": "buffer",
        "name": "urban_vehicle_state_latest_buffer",
        "store": {"mode": "latest"},
    })
    write_json(bridge_root / "comm/urban-state-shm-callback.json", {
        "protocol": "shm",
        "impl_type": "callback",
        "name": "urban_vehicle_state_shm_callback",
        "direction": "inout",
        "io": {"robots": [{
            "name": FLEET_PDU_ROBOT,
            "pdu": [
                {"name": "joint_states", "notify_on_recv": False},
                {"name": "vehicle_states", "notify_on_recv": False},
            ],
        }]},
    })
    write_json(bridge_root / "comm/urban-state-websocket-server.json", {
        "protocol": "websocket",
        "name": "urban_vehicle_state_websocket_server",
        "direction": "inout",
        "role": "server",
        "comm_raw_version": "v2",
        "local": {"port": visualization["web_bridge_port"]},
        "options": {
            "connect_timeout_ms": 5000,
            "read_timeout_ms": 5000,
            "write_timeout_ms": 5000,
            "ping_interval_sec": 30,
            "handshake_timeout_ms": 5000,
        },
    })
    for endpoint_name, comm_name in (
        ("urban-state-shm", "urban-state-shm-callback.json"),
        ("urban-state-ws", "urban-state-websocket-server.json"),
    ):
        write_json(bridge_root / f"endpoint/{endpoint_name}.json", {
            "name": f"{endpoint_name}-ep",
            "pdu_def_path": "../pdu/urban-visual-state.json",
            "cache": "../cache/latest.json",
            "comm": f"../comm/{comm_name}",
        })
    write_json(bridge_root / "endpoint/endpoint_container.json", [{
        "nodeId": "urban_vehicle_viewer_node1",
        "endpoints": [
            {
                "id": "bridge-shm-ep",
                "mode": "local",
                "config_path": "urban-state-shm.json",
                "direction": "in",
            },
            {
                "id": "bridge-ws-ep",
                "mode": "local",
                "config_path": "urban-state-ws.json",
                "direction": "out",
            },
        ],
    }])
    bridge_config_path = bridge_root / "bridge/bridge.json"
    write_json(bridge_config_path, {
        "version": "2.0.0",
        "transferPolicies": {
            "ticker_20ms": {"type": "ticker", "intervalMs": 20},
        },
        "nodes": [{"id": "urban_vehicle_viewer_node1"}],
        "endpoints_config_path": "../endpoint/endpoint_container.json",
        "wireLinks": [],
        "pduKeyGroups": {
            "urban_vehicle_state": [
                {
                    "id": f"{FLEET_PDU_ROBOT}.joint_states",
                    "robot_name": FLEET_PDU_ROBOT,
                    "pdu_name": "joint_states",
                },
                {
                    "id": f"{FLEET_PDU_ROBOT}.vehicle_states",
                    "robot_name": FLEET_PDU_ROBOT,
                    "pdu_name": "vehicle_states",
                },
            ],
        },
        "connections": [{
            "id": "conn_urban_state_shm_to_ws",
            "nodeId": "urban_vehicle_viewer_node1",
            "source": {"endpointId": "bridge-shm-ep"},
            "destinations": [{"endpointId": "bridge-ws-ep"}],
            "transferPdus": [{
                "pduKeyGroupId": "urban_vehicle_state",
                "policyId": "ticker_20ms",
            }],
        }],
    })

    type_definitions = {
        vehicle["type"]: vehicle["type_definition"] for vehicle in vehicles
    }
    vehicle_types_path = browser_root / "vehicle-types.json"
    write_json(vehicle_types_path, {
        type_name: {
            "viewModelPath": workspace_url(definition["view_model"]),
        }
        for type_name, definition in sorted(type_definitions.items())
    })
    scene_config_path = browser_root / "scene-config.json"
    scene_config = {
        "version": "1.0",
        "format": "compact",
        "environments": [{
            "name": "city",
            "model": workspace_url(city_glb),
        }],
        "main_camera": {
            "fov": 60,
            "near": 0.1,
            "far": 20000,
            "initialMode": "fixed",
            "position": [-12.0, -12.0, 8.0],
            "target": vehicles[0]["name"],
        },
        "vehicleTypesPath": "./vehicle-types.json",
        "vehicles": [
            {
                "name": vehicle["name"],
                "type": vehicle["type"],
                **(
                    {"frontCamera": visualization["front_camera"]}
                    if visualization.get("front_camera") is not None
                    else {}
                ),
            }
            for vehicle in vehicles
        ],
    }
    write_json(scene_config_path, scene_config)
    viewer_config_path = browser_root / "viewer-config.json"
    viewer_config = {
        "version": "1.0",
        "three": {
            "sceneConfigPath": workspace_url(scene_config_path),
            "initialCameraMode": "free",
        },
        "pdu": {
            "pduDefPath": workspace_url(browser_pdu_def_path),
            "wsUri": f"ws://127.0.0.1:{visualization['web_bridge_port']}",
            "wireVersion": "v2",
        },
        "ui": {
            "enableAttachedCameras": visualization.get("front_camera") is not None,
            "enableMainCameraMouseControl": True,
        },
        "stateInput": {
            "mode": "none",
            "vehicles": {"roleMap": {
                "vehicle_states": "sensor_msgs/MultiDOFJointState",
                "joint_states": "sensor_msgs/JointState",
            }},
        },
    }
    write_json(viewer_config_path, viewer_config)
    viewer_url = (
        f"http://127.0.0.1:{visualization['http_port']}"
        + workspace_url(visualization["threejs_root"] / "index.html")
        + "?viewerConfigPath="
        + workspace_url(viewer_config_path)
    )
    result = {
        "bridge_root": bridge_root,
        "bridge_config": bridge_config_path,
        "pdu_def": browser_pdu_def_path,
        "vehicle_types": vehicle_types_path,
        "scene_config": scene_config_path,
        "viewer_config": viewer_config_path,
        "viewer_url": viewer_url,
    }
    if collider_glb is not None:
        collider_scene = copy.deepcopy(scene_config)
        collider_scene["environments"].append({
            "name": "city-world-colliders",
            "model": workspace_url(collider_glb),
            "render": {
                "mode": "wireframe",
                "color": "#22c55e",
                "opacity": 0.72,
                "depthTest": True,
                "depthWrite": False,
            },
        })
        collider_scene_path = browser_root / "scene-config-colliders.json"
        write_json(collider_scene_path, collider_scene)
        collider_viewer = copy.deepcopy(viewer_config)
        collider_viewer["three"]["sceneConfigPath"] = workspace_url(
            collider_scene_path
        )
        collider_viewer_path = browser_root / "viewer-config-colliders.json"
        write_json(collider_viewer_path, collider_viewer)
        collider_url = (
            f"http://127.0.0.1:{visualization['http_port']}"
            + workspace_url(visualization["threejs_root"] / "index.html")
            + "?viewerConfigPath="
            + workspace_url(collider_viewer_path)
        )
        result.update({
            "collider_scene_config": collider_scene_path,
            "collider_viewer_config": collider_viewer_path,
            "collider_viewer_url": collider_url,
        })
    return result


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
    browser_files: dict[str, Path | str] | None = None,
    native_mujoco_viewer: bool = True,
    vehicle_generation: dict | None = None,
) -> Path:
    source = paths()
    python = foundation_python()
    logs = work / "logs"
    runtime = work / "runtime"
    logs.mkdir(parents=True, exist_ok=True)
    runtime.mkdir(parents=True, exist_ok=True)
    install = foundation_install()
    plant_args = [
        "--manifest", str(runtime_files["manifest"]),
        "--realtime-sync-cycle-msec", str(realtime_sync_cycle_msec),
    ]
    if not native_mujoco_viewer:
        plant_args.append("--no-viewer")
    assets = [
        {
            "name": "urban-car-fleet-plant",
            "activation_timing": "before_start",
            "command": str(source["plant"]),
            "args": plant_args,
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
    # All generated vehicles share one route scenario. The opt-in keeps older
    # external_python recipes manual while allowing exactly one executor per demo.
    if (
        vehicle_generation is not None
        and vehicle_generation.get("auto_start_scenario") is True
    ):
        if any(vehicle["control_mode"] != "external_python" for vehicle in vehicles):
            raise RecipeError(
                "auto-started route scenarios require external_python for every vehicle"
            )
        assets.append({
            "name": "urban-car-scenario-executor",
            "activation_timing": "after_start",
            "command": str(python),
            "args": [
                str(source["scenario_executor"]),
                str(vehicle_generation["scenario"]),
                "--pdu-def", str(runtime_files["pdu_def"]),
            ],
            "depends_on": ["urban-car-fleet-plant"],
            "delay_sec": 1,
        })
    if browser_files is not None:
        assets.extend([
            {
                "name": "urban-vehicle-web-bridge",
                "activation_timing": "before_start",
                "command": str(source["web_bridge"]),
                "args": [
                    "--config-root", str(browser_files["bridge_root"]),
                    "--node-name", "urban_vehicle_viewer_node1",
                    "--delta-time-step-usec", "20000",
                    "--enable-ondemand",
                ],
                "depends_on": ["urban-car-fleet-plant"],
                "delay_sec": 1,
            },
            {
                "name": "urban-vehicle-http-server",
                "activation_timing": "after_start",
                "command": str(python),
                "args": [
                    str(source["http_server"]),
                    "--port", str(browser_files["http_port"]),
                    "--bind", "127.0.0.1",
                    "--directory", str(WORKSPACE),
                ],
                "cwd": str(WORKSPACE),
                "depends_on": ["urban-vehicle-web-bridge"],
                "delay_sec": 1,
            },
        ])

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
    launcher_path = work / "config/launcher.json"
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
    if resolved["visualization"]["enabled"]:
        checks.extend([
            ("Hakoniwa PDU WebBridge", source["web_bridge"]),
            ("Three.js viewer", resolved["visualization"]["threejs_root"] / "index.html"),
            ("Map Viewer", MAP_VIEWER / "src/client/index.html"),
        ])
    for definition in resolved["vehicle_types"].values():
        checks.extend([
            (f"Vehicle type {definition['type']} MJCF", definition["mjcf"]),
            (f"Vehicle type {definition['type']} contract", definition["contract"]),
            (f"Vehicle type {definition['type']} view model", definition["view_model"]),
        ])
    for mirror in resolved["mirrors"]:
        checks.append((f"Drone Mirror {mirror['name']} PDU types", mirror["pdu_types"]))
        if mirror["mjcf_model"] is not None:
            checks.append((f"Drone Mirror {mirror['name']} MJCF", mirror["mjcf_model"]))
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
    config_root = work / "config/car"
    vehicles = resolved["vehicles"]
    mirrors = resolved["mirrors"]
    city_mjcf, city_glb, receipt = city_inputs(resolved["city_receipt"])
    for label, path in paths().items():
        if label != "plant":
            required(path, label)
    required(foundation_install() / "python/bin/python3", "Foundation Python")
    required(source["core_config"], "Foundation Core config")
    config_root.mkdir(parents=True, exist_ok=True)
    fleet_model = config_root / "urban-car-fleet.xml"
    materialize_vehicle_fleet_model(fleet_model, vehicles, mirrors)
    output = config_root / "urban-cars-city.xml"
    command([
        str(foundation_python()), str(source["compose_tool"]),
        str(fleet_model), str(city_mjcf),
        "--output", str(output),
    ])

    mjb = config_root / "urban-cars-city.mjb"
    mjb_receipt = work / "validation/mujoco-materialization.json"
    materialization = materialize_mjb(output, mjb, mjb_receipt)

    runtime_files = materialize_runtime(mjb, config_root, vehicles, mirrors)
    configured_initial_poses = refresh_runtime_initial_body_poses(resolved)
    initial_pose_by_name = {
        pose["name"]: pose for pose in configured_initial_poses
    }
    browser_files = None
    if resolved["visualization"]["enabled"]:
        browser_files = materialize_browser_visualization(
            runtime_files,
            work / "config",
            city_glb,
            vehicles,
            resolved["visualization"],
            city_collider_glb(resolved["city_receipt"]),
        )
        browser_files["http_port"] = resolved["visualization"]["http_port"]
        browser_files["viewer_url"] = map_viewer_url(
            resolved, browser_files["viewer_config"]
        )
        browser_files["collider_viewer_url"] = map_viewer_url(
            resolved, browser_files["collider_viewer_config"]
        )
    launcher = materialize_launcher(
        runtime_files,
        work,
        resolved["realtime_sync_cycle_msec"],
        vehicles,
        browser_files,
        resolved["native_mujoco_viewer"],
        resolved["vehicle_generation"],
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
                "view_model": {
                    "path": str(definition["view_model"]),
                    "sha256": sha256(definition["view_model"]),
                },
            }
            for definition in resolved["vehicle_types"].values()
        ],
        "vehicle_generation": (
            None if resolved["vehicle_generation"] is None else {
                **resolved["vehicle_generation"],
                "scenario": str(resolved["vehicle_generation"]["scenario"]),
            }
        ),
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
                    "position_m": initial_pose_by_name[vehicle["name"]]["position_m"],
                    "yaw_rad": initial_pose_by_name[vehicle["name"]][
                        "orientation_rpy_rad"
                    ][2],
                    "terrain_height_m": initial_pose_by_name[vehicle["name"]][
                        "terrain_height_m"
                    ],
                },
            }
            for vehicle in vehicles
        ],
        "drone_mirrors": [
            {
                "name": mirror["name"],
                "mjcf_freejoint": mirror["prefix"] + "freejoint",
                "pdu_types": {
                    "path": str(mirror["pdu_types"]),
                    "sha256": sha256(mirror["pdu_types"]),
                },
                "pdu_contract": ["pos", "velocity", "impulse"],
            }
            for mirror in mirrors
        ],
        "coordinate_frame": receipt["coordinate_frame"],
        "runtime_manifest": str(runtime_files["manifest"]),
        "core_config": str(source["core_config"]),
        "runtime_ownership": "exclusive Foundation mmap; Launcher cleanup_mmap_on_start",
        "realtime_sync_cycle_msec": resolved["realtime_sync_cycle_msec"],
        "native_mujoco_viewer": resolved["native_mujoco_viewer"],
        "launcher": str(launcher),
        "browser_visualization": (
            None if browser_files is None else {
                "viewer_url": browser_files["viewer_url"],
                "viewer_config": str(browser_files["viewer_config"]),
                "scene_config": str(browser_files["scene_config"]),
                "vehicle_types": str(browser_files["vehicle_types"]),
                "bridge_config": str(browser_files["bridge_config"]),
            }
        ),
    }
    write_json(work / "validation/compose-receipt.json", compose_receipt)
    print(f"Composed MJCF : {output}")
    print(f"Runtime MJB   : {mjb}")
    print(f"Manifest      : {runtime_files['manifest']}")
    print(f"Launcher      : {launcher}")
    if browser_files is not None:
        print(f"Three.js      : {browser_files['viewer_url']}")
    print("Vehicles      : " + ", ".join(
        f"{vehicle['name']}:{vehicle['type']}={vehicle['control_mode']}"
        for vehicle in vehicles
    ))
    if mirrors:
        print("Drone Mirrors: " + ", ".join(mirror["name"] for mirror in mirrors))
    if (
        resolved["vehicle_generation"] is not None
        and resolved["vehicle_generation"].get("auto_start_scenario") is True
    ):
        print(
            "External control: one scenario executor starts with the Launcher: "
            + str(resolved["vehicle_generation"]["scenario"])
        )
    elif any(vehicle["control_mode"] == "external_python" for vehicle in vehicles):
        print("External control: run apps/car/ackermann_command.py with --robot NAME")
    print("Use 'start' for the configured runtime, or 'view' for Viewer-only inspection.")
    return 0


def launcher_path(work: Path) -> Path:
    return required(
        work / "config/launcher.json",
        "generated Launcher; run configure first",
    )


def session_path(work: Path) -> Path:
    return work / "runtime/launcher-session.json"


def lifecycle_spec(resolved: dict, *, viewer_url: str | None = None):
    visualization = resolved["visualization"]
    default_viewer = resolved["work"] / "config/threejs/viewer-config.json"
    return urban_lifecycle.LifecycleSpec(
        recipe_id=resolved["recipe_id"],
        recipe_root=resolved["work"],
        launcher=resolved["work"] / "config/launcher.json",
        session=session_path(resolved["work"]),
        viewer_url=viewer_url or map_viewer_url(resolved, default_viewer),
        websocket_port=visualization["web_bridge_port"],
        ports=(visualization["http_port"], visualization["web_bridge_port"], 54111),
    )


def launch(operation: str, resolved: dict) -> int:
    work = resolved["work"]
    python = foundation_python()
    if operation == "start":
        urban_lifecycle.preflight_start(lifecycle_spec(resolved))
        refresh_runtime_initial_body_poses(resolved)
        command([
            str(python), "-m", "hakoniwa_pdu.apps.launcher.hako_launcher",
            str(launcher_path(work)), "--background", str(session_path(work)),
        ])
    else:
        urban_lifecycle.read_session(lifecycle_spec(resolved))
        command([
            str(python), "-m", "hakoniwa_pdu.apps.launcher.hako_launcher_ctl",
            "status" if operation == "status" else "terminate", str(session_path(work)),
        ])
        if operation == "stop":
            urban_lifecycle.verify_stopped(lifecycle_spec(resolved))
    return 0


def view(work: Path) -> int:
    manifest = required(
        work / "config/car/urban-car-asset-manifest.json",
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
            resolved["work"] / "config/car/urban-car-pdudef.json",
            "generated PDU definition",
        )),
        "--check-controller",
        "--rc-config", str(required(source["ps5_mapping"], "PS5 mapping")),
        "--robot", robot,
    ])
    return 0


def open_viewer(resolved: dict, *, show_colliders: bool = False) -> int:
    if not resolved["visualization"]["enabled"]:
        raise RecipeError("browser visualization is disabled in this recipe")
    config_name = (
        "viewer-config-colliders.json" if show_colliders else "viewer-config.json"
    )
    viewer_config = required(
        resolved["work"] / "config/threejs" / config_name,
        "generated Three.js viewer config; run configure first",
    )
    url = map_viewer_url(resolved, viewer_config)
    urban_lifecycle.require_viewer_ready(
        lifecycle_spec(resolved, viewer_url=url)
    )
    print(f"Opening Three.js: {url}")
    return 0 if webbrowser.open(url) else 1


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Operate a configured Urban City World + Car Fleet recipe")
    result.add_argument(
        "command",
        choices=(
            "doctor", "configure", "check-ps5", "view", "open-viewer",
            "start", "status", "stop",
        ),
    )
    result.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG,
        help=f"configuration YAML (default: {DEFAULT_CONFIG.relative_to(ROOT)})",
    )
    result.add_argument(
        "--colliders",
        action="store_true",
        help="show green City World Collider wireframes with open-viewer",
    )
    return result


def main() -> int:
    args = parser().parse_args()
    if args.colliders and args.command != "open-viewer":
        parser().error("--colliders is available only with open-viewer")
    resolved = resolve_config(args.config)
    if args.command == "doctor":
        return doctor(resolved)
    if args.command == "configure":
        return configure(resolved)
    if args.command == "check-ps5":
        return check_ps5(resolved)
    if args.command == "view":
        return view(resolved["work"])
    if args.command == "open-viewer":
        return open_viewer(resolved, show_colliders=args.colliders)
    return launch(args.command, resolved)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RecipeError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
