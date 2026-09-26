#!/usr/bin/env python3
"""Load Urban Compositions and adapt them to the current tool inputs.

This is migration step 1 of docs/asset-contract.md: a Composition is
translated into the input the existing tools already read. Adapted:
City + Car (tools/multi_car.py), City + one Drone (tools/drone_one.py), and
City + Car + one Drone (tools/urban_composer.py).
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Callable

import yaml

import urban_assets
from urban_assets import Asset, AssetError


COMPOSITION_SCHEMA = "hakoniwa.composition/v1"
SPAWN_KEYS = {"east_m", "north_m", "yaw_deg"}
CAR_SIMULATOR = "ackermann-mujoco"
DRONE_SIMULATOR = "drone-core"
# Current tool names for the contract controls.
CAR_CONTROL_MODES = {"rc": "ps5", "api": "external_python"}
DRONE_CONTROL_MODES = {"rc": "ps4-rc", "api": "fleet-rpc"}
# drone_one.py inputs the contract does not expose. The RC mode still
# requires a mission file, and the launch area only shapes the mission plan;
# the runtime spawn is always the Composition spawn.
DRONE_FLEET_EXPERIMENT = urban_assets.ROOT / "recipes/experiments/drone-one-fleet.yaml"
DRONE_DEFAULT_MISSION = urban_assets.ROOT / "config/drone/city-one-mission.json"
DRONE_LAUNCH_AREA = {"mode": "auto", "offset_m": [0.0, 0.0, 0.0], "search_radius_m": 100.0}
DRONE_MIRROR_PARAMS = {
    "restitution_coefficient": 0.3,
    "relative_normal_speed_threshold_mps": 0.2,
    "cooldown_sec": 0.1,
}
DEFAULT_HTTP_PORT = 8000
DEFAULT_CAR_WEB_BRIDGE_PORT = 18765
# urban_composer.py serves the integrated viewer on the standard WebBridge port.
DEFAULT_INTEGRATED_WEB_BRIDGE_PORT = 8765


class CompositionError(RuntimeError):
    pass


@dataclass(frozen=True)
class Vehicle:
    name: str
    asset: Asset
    control: str
    params: dict
    spawn: dict


@dataclass(frozen=True)
class Composition:
    id: str
    path: Path
    world: Asset
    vehicles: tuple[Vehicle, ...]
    interactions: tuple[dict, ...]
    viewer: dict

    def simulators(self) -> set[str]:
        return {vehicle.asset.simulator for vehicle in self.vehicles}

    def by_simulator(self, simulator: str) -> list[Vehicle]:
        return [vehicle for vehicle in self.vehicles if vehicle.asset.simulator == simulator]


def _finite(value: object, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise CompositionError(f"{label} must be a number: {value!r}") from exc
    if not math.isfinite(number):
        raise CompositionError(f"{label} must be finite: {value!r}")
    return number


def _vehicle(entry: object, index: int, assets: dict[str, Asset]) -> Vehicle:
    if not isinstance(entry, dict):
        raise CompositionError(f"vehicles[{index}] must be a mapping")
    name = str(entry.get("name", "")).strip()
    if not name:
        raise CompositionError(f"vehicles[{index}] has no name")
    asset_id = entry.get("asset")
    asset = assets.get(asset_id)
    if asset is None or asset.kind != "vehicle":
        raise CompositionError(f"vehicle {name} references unknown vehicle Asset: {asset_id!r}")
    control = entry.get("control")
    if control not in asset.controls():
        raise CompositionError(
            f"vehicle {name}: Asset {asset.id} offers controls "
            f"{sorted(asset.controls())}, not {control!r}"
        )
    params = entry.get("params", {})
    if not isinstance(params, dict):
        raise CompositionError(f"vehicle {name} params must be a mapping")
    declared = asset.controls()[control].get("params", {})
    unknown = set(params) - set(declared)
    if unknown:
        raise CompositionError(f"vehicle {name} has unknown {control} params: {sorted(unknown)}")
    for param, definition in declared.items():
        if definition.get("required") and param not in params:
            raise CompositionError(f"vehicle {name} {control} control requires param {param}")
    spawn = entry.get("spawn")
    if not isinstance(spawn, dict) or set(spawn) != SPAWN_KEYS:
        raise CompositionError(
            f"vehicle {name} spawn must have exactly {sorted(SPAWN_KEYS)}; "
            "the height is computed from the Asset ground clearance"
        )
    spawn = {key: _finite(spawn[key], f"vehicle {name} spawn.{key}") for key in sorted(SPAWN_KEYS)}
    return Vehicle(name=name, asset=asset, control=control, params=params, spawn=spawn)


def _interaction(entry: object, index: int, vehicles: dict[str, Vehicle]) -> dict:
    if not isinstance(entry, dict) or entry.get("type") != "drone-mirror":
        raise CompositionError(f"interactions[{index}] must be a drone-mirror mapping")
    drone = vehicles.get(entry.get("drone"))
    if drone is None or drone.asset.category != "drone":
        raise CompositionError(f"interactions[{index}] drone is not a Drone vehicle: {entry.get('drone')!r}")
    if "drone-mirror" not in drone.asset.data.get("interactions", {}):
        raise CompositionError(f"Asset {drone.asset.id} declares no drone-mirror interaction")
    unknown = set(entry) - {"type", "drone"} - set(DRONE_MIRROR_PARAMS)
    if unknown:
        raise CompositionError(f"interactions[{index}] has unknown fields: {sorted(unknown)}")
    params = {
        key: _finite(entry.get(key, default), f"interactions[{index}].{key}")
        for key, default in DRONE_MIRROR_PARAMS.items()
    }
    return {"type": "drone-mirror", "drone": drone.name, **params}


def load(path: Path, assets: dict[str, Asset] | None = None) -> Composition:
    path = path.expanduser().resolve()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise CompositionError(f"invalid Composition: {path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != COMPOSITION_SCHEMA:
        raise CompositionError(f"Composition schema must be {COMPOSITION_SCHEMA}: {path}")
    composition_id = str(data.get("id", "")).strip()
    if not composition_id:
        raise CompositionError(f"Composition has no id: {path}")
    try:
        assets = assets if assets is not None else urban_assets.catalog()
    except AssetError as exc:
        raise CompositionError(str(exc)) from exc
    world = assets.get(data.get("world"))
    if world is None or world.kind not in {"city", "plain"}:
        raise CompositionError(f"Composition world is not a City or plain World Asset: {data.get('world')!r}")
    entries = data.get("vehicles")
    if not isinstance(entries, list) or not entries:
        raise CompositionError("Composition needs at least one vehicle")
    vehicles = tuple(_vehicle(entry, index, assets) for index, entry in enumerate(entries))
    names = [vehicle.name for vehicle in vehicles]
    if len(set(names)) != len(names):
        raise CompositionError(f"vehicle names must be unique: {names}")
    interaction_entries = data.get("interactions", [])
    if not isinstance(interaction_entries, list):
        raise CompositionError("Composition interactions must be a list")
    by_name = {vehicle.name: vehicle for vehicle in vehicles}
    interactions = tuple(
        _interaction(entry, index, by_name) for index, entry in enumerate(interaction_entries)
    )
    viewer = data.get("viewer", {})
    if not isinstance(viewer, dict):
        raise CompositionError("Composition viewer must be a mapping")
    return Composition(
        id=composition_id, path=path, world=world, vehicles=vehicles,
        interactions=interactions, viewer=viewer,
    )


def city_receipt(composition: Composition) -> Path:
    if composition.world.kind != "city":
        raise CompositionError("plain Worlds are not adapted yet (asset-contract 7.1 step 3)")
    return composition.world.resolve(composition.world.data["receipt"])


def _param_path(composition: Composition, value: object) -> Path:
    path = Path(str(value)).expanduser()
    return (path if path.is_absolute() else composition.path.parent / path).resolve()


def _require_simulators(composition: Composition, expected: set[str], adapter: str) -> None:
    if composition.simulators() != expected:
        raise CompositionError(
            f"the {adapter} adapter needs simulators {sorted(expected)}; "
            f"got {sorted(composition.simulators())}"
        )


# --- Car (tools/multi_car.py) -------------------------------------------------

def _car_route_scenario(composition: Composition, cars: list[Vehicle]) -> dict | None:
    """One composition-scoped api program drives every api Car (asset-contract 4.3)."""
    scenarios = {_param_path(composition, car.params["scenario"]) for car in cars if car.control == "api"}
    if not scenarios:
        return None
    if len(scenarios) > 1:
        raise CompositionError(
            "api Cars must share one route scenario; got " + ", ".join(sorted(map(str, scenarios)))
        )
    return {"path": str(scenarios.pop()), "auto_start": True}


def _car_inputs(composition: Composition, cars: list[Vehicle], web_bridge_port: int) -> dict:
    types = []
    type_names: dict[str, str] = {}
    front_camera = None
    for car in cars:
        if car.control == "rc" and car.params:
            raise CompositionError(
                f"vehicle {car.name}: Car rc params are not adapted yet (asset-contract 7.1 step 3)"
            )
        asset = car.asset
        if asset.id in type_names:
            continue
        type_names[asset.id] = asset.id.replace("-", "_")
        model = asset.data["model"]
        types.append({
            "type": type_names[asset.id],
            "mjcf": str(asset.resolve(model["physics"])),
            "contract": str(asset.resolve(model["contract"])),
            "view_model": str(asset.resolve(model["visual"])),
        })
        camera = asset.data.get("viewer", {}).get("front_camera")
        if camera is not None:
            if front_camera is not None and camera != front_camera:
                # multi_car.py applies one front camera to every vehicle type.
                raise CompositionError("Car Assets with different front cameras are not adapted yet")
            front_camera = camera

    vehicles = {
        "types": types,
        "vehicles": [
            {
                "name": car.name,
                "type": type_names[car.asset.id],
                "control_mode": CAR_CONTROL_MODES[car.control],
                "spawn_pose_enu": {
                    "east_m": car.spawn["east_m"],
                    "north_m": car.spawn["north_m"],
                    "yaw_deg": car.spawn["yaw_deg"],
                    # multi_car.py adds the terrain height at configure time.
                    "ground_clearance_m": float(car.asset.data["spawn"]["ground_clearance_m"]),
                },
            }
            for car in cars
        ],
    }
    route_scenario = _car_route_scenario(composition, cars)
    if route_scenario is not None:
        vehicles["route_scenario"] = route_scenario
    visualization = {
        "enabled": True,
        "web_bridge_port": int(composition.viewer.get("web_bridge_port", web_bridge_port)),
        "http_port": int(composition.viewer.get("http_port", DEFAULT_HTTP_PORT)),
        "threejs_root": str((urban_assets.WORKSPACE / "hakoniwa-threejs-drone").resolve()),
    }
    if front_camera is not None:
        visualization["front_camera"] = front_camera
    return {
        "business_pack_city_receipt": {"path": str(city_receipt(composition))},
        "ackermann_vehicles": vehicles,
        "ackermann_runtime": {"realtime_sync_cycle_msec": 2, "native_mujoco_viewer": False},
        "browser_visualization": visualization,
    }


def _car_config(composition: Composition, recipe_id: str, inputs: dict) -> dict:
    return {
        "id": recipe_id,
        "title": f"Urban Composition {composition.id}",
        "recipe_version": 0.1,
        "composition_source": {"id": composition.id, "path": str(composition.path)},
        "inputs": inputs,
        "composition": {"output": {"recipe_id": recipe_id}},
    }


def to_car_config(composition: Composition, recipe_id: str) -> dict:
    """Return the multi_car.py configuration for a City + Car Composition."""
    _require_simulators(composition, {CAR_SIMULATOR}, "Car")
    if composition.interactions:
        raise CompositionError("interactions need a Drone; this Composition has Cars only")
    inputs = _car_inputs(composition, list(composition.vehicles), DEFAULT_CAR_WEB_BRIDGE_PORT)
    return _car_config(composition, recipe_id, inputs)


# --- Drone (tools/drone_one.py) -----------------------------------------------

def city_terrain_height(receipt: Path) -> Callable[[float, float], float]:
    """Return ground(east_m, north_m) from the City terrain hfield.

    This samples terrain only; buildings are added by the ray-based height in
    asset-contract 7.1 step 3.
    """
    import multi_car

    data = multi_car.load_json(receipt, "City World receipt")
    return lambda east_m, north_m: multi_car.terrain_height_mjcf(data, north_m, -east_m)


def _drone_recipe(
    composition: Composition,
    drone: Vehicle,
    ground: Callable[[float, float], float] | None,
) -> dict:
    receipt = city_receipt(composition)
    profile = drone.asset.data.get("source", {}).get("profile")
    if profile != "eams-nominal-9kg":
        raise CompositionError(
            f"vehicle {drone.name}: tools/drone_one.py runs the eams-nominal-9kg profile only"
        )
    mission = DRONE_DEFAULT_MISSION
    if drone.control == "api":
        mission = _param_path(composition, drone.params["mission"])
    ground = ground or city_terrain_height(receipt)
    spawn = drone.spawn
    surface = round(ground(spawn["east_m"], spawn["north_m"]), 6)
    clearance = float(drone.asset.data["spawn"]["ground_clearance_m"])
    return {
        "version": 1,
        "id": composition.id,
        "fleet_experiment": {"path": DRONE_FLEET_EXPERIMENT.as_posix()},
        "city_world": {"receipt": receipt.as_posix()},
        "drone": {
            "profile": profile,
            "spawn_pose_enu": {
                "east_m": spawn["east_m"],
                "north_m": spawn["north_m"],
                "up_m": surface + clearance,
                "yaw_deg": spawn["yaw_deg"],
            },
            "launch_area": DRONE_LAUNCH_AREA,
            # urban_composer.py reads the spawn surface under this name.
            "rooftop": {"surface_height_m": surface, "base_clearance_m": clearance},
        },
        "control": {"mode": DRONE_CONTROL_MODES[drone.control]},
        "mission": {"path": mission.as_posix()},
        "viewer": {"map_layout": "bottom-left", "collider_overlay_default": False},
    }


def to_drone_recipe(
    composition: Composition,
    *,
    ground: Callable[[float, float], float] | None = None,
) -> dict:
    """Return the tools/drone_one.py recipe for a City + one Drone Composition."""
    _require_simulators(composition, {DRONE_SIMULATOR}, "Drone")
    if len(composition.vehicles) != 1:
        raise CompositionError("the Drone adapter runs exactly one Drone (tools/drone_one.py)")
    if composition.interactions:
        raise CompositionError("interactions need Cars; this Composition has one Drone only")
    return _drone_recipe(composition, composition.vehicles[0], ground)


# --- Car + Drone (tools/urban_composer.py) --------------------------------------

def to_integrated(
    composition: Composition,
    recipe_id: str,
    drone_recipe_path: Path,
    *,
    ground: Callable[[float, float], float] | None = None,
) -> tuple[dict, dict]:
    """Return (urban_composer.py configuration, drone_one.py recipe).

    The configuration references the Drone recipe by drone_recipe_path, where
    the caller writes it with render_simple_yaml().
    """
    _require_simulators(composition, {CAR_SIMULATOR, DRONE_SIMULATOR}, "Car + Drone")
    drones = composition.by_simulator(DRONE_SIMULATOR)
    if len(drones) != 1:
        raise CompositionError("the Car + Drone adapter runs exactly one Drone (tools/urban_composer.py)")
    drone = drones[0]
    if drone.control != "rc":
        # urban_composer.py configures and merges the PS4 RC Drone only.
        raise CompositionError(
            f"vehicle {drone.name}: Drone api control with Cars is not adapted yet (asset-contract 7.1 step 3)"
        )
    cars = composition.by_simulator(CAR_SIMULATOR)
    inputs = _car_inputs(composition, cars, DEFAULT_INTEGRATED_WEB_BRIDGE_PORT)
    mirrors = []
    for interaction in composition.interactions:
        mirror = drone.asset.data["interactions"]["drone-mirror"]
        mirrors.append({
            "name": interaction["drone"],
            "pdu_types": str(drone.asset.resolve(mirror["pdu_types"])),
            "mjcf_model": str(drone.asset.resolve(mirror["mjcf_model"])),
            "mjcf_body": mirror["mjcf_body"],
            **{key: interaction[key] for key in DRONE_MIRROR_PARAMS},
        })
    if mirrors:
        inputs["drone_mirrors"] = mirrors
    config = _car_config(composition, recipe_id, inputs)
    scenarios = {"drone": str(drone_recipe_path)}
    route_scenario = inputs["ackermann_vehicles"].get("route_scenario")
    if route_scenario is not None:
        scenarios["car"] = route_scenario["path"]
    config["scenarios"] = scenarios
    return config, _drone_recipe(composition, drone, ground)


# --- Shared --------------------------------------------------------------------

def render_simple_yaml(data: dict, indent: int = 0) -> str:
    """Render the dependency-free YAML subset read by drone_one.py.

    That reader accepts nested mappings, scalars, and inline scalar lists.
    """
    lines = []
    for key, value in data.items():
        prefix = " " * indent + f"{key}:"
        if isinstance(value, dict):
            lines.append(prefix)
            lines.append(render_simple_yaml(value, indent + 2))
        elif isinstance(value, list):
            lines.append(f"{prefix} [{', '.join(json.dumps(item) for item in value)}]")
        elif isinstance(value, bool):
            lines.append(f"{prefix} {'true' if value else 'false'}")
        elif isinstance(value, (int, float)):
            lines.append(f"{prefix} {value}")
        else:
            lines.append(f"{prefix} {_simple_yaml_string(str(value))}")
    return "\n".join(lines)


def _simple_yaml_string(text: str) -> str:
    # The reader strips one pair of quotes without unescaping, so quote only
    # when the plain form would be read as another type or mis-split.
    if '"' in text or "\n" in text:
        raise CompositionError(f"value cannot be written to the simple YAML subset: {text!r}")
    plain_is_string = (
        text == text.strip() and text
        and text not in {"true", "false", "null", "~"}
        and not text.startswith(("[", "'"))
    )
    if plain_is_string:
        try:
            float(text)
        except ValueError:
            return text
    return f'"{text}"'


def placement_only_change(previous: dict, current: dict) -> bool:
    """True when two Car configs differ at most in vehicle spawn poses."""
    def without_spawn(config: dict) -> dict:
        stripped = json.loads(json.dumps(config))
        stripped.pop("composition_source", None)
        for vehicle in stripped["inputs"]["ackermann_vehicles"]["vehicles"]:
            vehicle.pop("spawn_pose_enu", None)
        return stripped

    return without_spawn(previous) == without_spawn(current)
