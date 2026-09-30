#!/usr/bin/env python3
"""Load Urban Compositions and adapt them to the current tool inputs.

This is migration step 1 of docs/asset-contract.md: a Composition is
translated into the input the existing tools already read. Adapted:
City + Car (tools/multi_car.py), City + one Drone (tools/drone_one.py), and
City + Car + one Drone (tools/urban_composer.py).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Callable

import yaml

import urban_assets
from urban_assets import Asset, AssetError
import urban_manifest


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
AREA_KEYS = {"east_m", "north_m"}
# tools/drone_fleet_city.py grid spacing bounds [m] (its SPAWN_CLEARANCE_RADIUS_M
# is the effective minimum) and the drones one Drone service process runs.
FLEET_SPACING_RANGE_M = (0.75, 5.0)
FLEET_DRONES_PER_PROCESS = 50
# A fleet Composition's WebBridge (viewer.web_bridge_port overrides it).
DEFAULT_FLEET_WEB_BRIDGE_PORT = urban_manifest.port("web-bridge-fleet")
DRONE_MIRROR_PARAMS = {
    "restitution_coefficient": 0.3,
    "relative_normal_speed_threshold_mps": 0.2,
    "cooldown_sec": 0.1,
}
PLAIN_WORLD_CACHE = urban_manifest.work_dir() / "urban/cache/plain-world"
FPV_GENERATOR_SRC = urban_assets.WORKSPACE / "hakoniwa-fpv-drone/src"
# Vehicles whose manifest names this generator run through tools/fpv.py.
FPV_TOOL = "tools/fpv.py"
DEFAULT_HTTP_PORT = urban_manifest.port("viewer-http")
DEFAULT_CAR_WEB_BRIDGE_PORT = urban_manifest.port("web-bridge-car")
# urban_composer.py serves the integrated viewer on the standard WebBridge port.
DEFAULT_INTEGRATED_WEB_BRIDGE_PORT = urban_manifest.port("web-bridge")


class CompositionError(RuntimeError):
    pass


@dataclass(frozen=True)
class Vehicle:
    name: str
    asset: Asset
    control: str
    params: dict
    spawn: dict
    # Composition replacement of the manifest control program (section 5.2).
    program: str | None = None
    args: tuple[str, ...] | None = None


@dataclass(frozen=True)
class Fleet:
    """Many drones of one Asset laid out automatically (asset-contract 5.8).

    The user places only the launch area centre; the builder lays the drones
    out on a grid with `spacing_m` around it and computes every height.
    """

    name: str
    asset: Asset
    control: str
    params: dict
    count: int
    spacing_m: float
    area: dict
    # Drone service processes; None lets the builder choose (FLEET_DRONES_PER_PROCESS).
    processes: int | None = None


@dataclass(frozen=True)
class Composition:
    id: str
    path: Path
    world: Asset
    vehicles: tuple[Vehicle, ...]
    interactions: tuple[dict, ...]
    viewer: dict
    fleets: tuple[Fleet, ...] = ()

    def simulators(self) -> set[str]:
        return {vehicle.asset.simulator for vehicle in (*self.vehicles, *self.fleets)}

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
    program = entry.get("program")
    args = entry.get("args")
    if (program is None) != (args is None):
        raise CompositionError(f"vehicle {name}: program and args replace the manifest control together")
    if program is not None:
        if not isinstance(program, str) or not program.strip():
            raise CompositionError(f"vehicle {name} program must be a non-empty path")
        if not isinstance(args, list) or not all(isinstance(arg, (str, int, float)) for arg in args):
            raise CompositionError(f"vehicle {name} args must be a list of scalars")
        args = tuple(str(arg) for arg in args)
    return Vehicle(
        name=name, asset=asset, control=control, params=params, spawn=spawn,
        program=program, args=args,
    )


def _fleet(entry: object, index: int, assets: dict[str, Asset]) -> Fleet:
    if not isinstance(entry, dict):
        raise CompositionError(f"fleets[{index}] must be a mapping")
    name = str(entry.get("name", "")).strip()
    if not name:
        raise CompositionError(f"fleets[{index}] has no name")
    unknown = set(entry) - {"name", "asset", "control", "params", "count", "spacing_m", "area", "processes"}
    if unknown:
        raise CompositionError(f"fleet {name} has unknown fields: {sorted(unknown)}")
    asset = assets.get(entry.get("asset"))
    if asset is None or asset.kind != "vehicle" or asset.data.get("fleet") is None:
        raise CompositionError(f"fleet {name} references no fleet-capable Drone Asset: {entry.get('asset')!r}")
    control = entry.get("control")
    if control not in asset.controls():
        raise CompositionError(
            f"fleet {name}: Asset {asset.id} offers controls {sorted(asset.controls())}, not {control!r}"
        )
    params = entry.get("params", {})
    declared = asset.controls()[control].get("params", {})
    if not isinstance(params, dict) or set(params) - set(declared):
        raise CompositionError(f"fleet {name} params must be a mapping of {sorted(declared)}")
    count = entry.get("count")
    max_count = asset.data["fleet"]["max_count"]
    if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= max_count:
        raise CompositionError(f"fleet {name} count must be an integer in [1, {max_count}]")
    spacing = _finite(entry.get("spacing_m", asset.data["fleet"]["default_spacing_m"]), f"fleet {name} spacing_m")
    low, high = FLEET_SPACING_RANGE_M
    if not low <= spacing <= high:
        raise CompositionError(f"fleet {name} spacing_m must be in [{low}, {high}]")
    area = entry.get("area")
    if not isinstance(area, dict) or set(area) != AREA_KEYS:
        raise CompositionError(
            f"fleet {name} area must have exactly {sorted(AREA_KEYS)}; the layout and heights are computed"
        )
    area = {key: _finite(area[key], f"fleet {name} area.{key}") for key in sorted(AREA_KEYS)}
    processes = entry.get("processes", "auto")
    if processes == "auto":
        processes = None
    elif not isinstance(processes, int) or isinstance(processes, bool) or not 1 <= processes <= count:
        raise CompositionError(f"fleet {name} processes must be auto or an integer in [1, count]")
    return Fleet(
        name=name, asset=asset, control=control, params=params, count=count, spacing_m=spacing,
        area=area, processes=processes,
    )


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
    if world is None:
        raise CompositionError(
            f"World Asset {data.get('world')!r} is not in the catalog; register a City with "
            "tools/urban_assets.py register-city --receipt <city-world-receipt.json>"
        )
    if world.kind not in {"city", "plain"}:
        raise CompositionError(f"Composition world is not a City or plain World Asset: {data.get('world')!r}")
    entries = data.get("vehicles", [])
    fleet_entries = data.get("fleets", [])
    if not isinstance(entries, list) or not isinstance(fleet_entries, list):
        raise CompositionError("Composition vehicles and fleets must be lists")
    if not entries and not fleet_entries:
        raise CompositionError("Composition needs at least one vehicle or fleet")
    vehicles = tuple(_vehicle(entry, index, assets) for index, entry in enumerate(entries))
    fleets = tuple(_fleet(entry, index, assets) for index, entry in enumerate(fleet_entries))
    names = [item.name for item in (*vehicles, *fleets)]
    if len(set(names)) != len(names):
        raise CompositionError(f"vehicle and fleet names must be unique: {names}")
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
    vehicles = _assign_route_cars(path, vehicles)
    return Composition(
        id=composition_id, path=path, world=world, vehicles=vehicles,
        interactions=interactions, viewer=viewer, fleets=fleets,
    )


ROUTE_CACHE = urban_manifest.work_dir() / "urban/cache/routes"
# Gap between a Car added to a route and the Car ahead of it.
ADDED_CAR_SPACING_M = 6.0


def _assign_route_cars(composition_path: Path, vehicles: tuple[Vehicle, ...]) -> tuple[Vehicle, ...]:
    """Make each route drive exactly the api Cars that selected it.

    The Car's route selection is authoritative: a route's vehicles list only
    provides offsets. A route used by a different set of Cars than it names is
    rewritten to a derived copy (content-addressed under ROUTE_CACHE) naming
    just those Cars, keeping their offsets and appending unnamed Cars 6 m
    apart. The Cars then point at the copy, so the Car configuration, spawns,
    and the scenario executor processes all agree. Route files are not modified.
    """
    groups: dict[Path, list[Vehicle]] = {}
    for vehicle in vehicles:
        if vehicle.control == "api" and "scenario" in vehicle.params:
            groups.setdefault(
                urban_assets.resolve_reference(str(vehicle.params["scenario"]), composition_path.parent), []
            ).append(vehicle)
    replacement: dict[str, str] = {}
    for route_path, cars in groups.items():
        try:
            data = yaml.safe_load(route_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue  # multi_car reports an unreadable route
        listed = data.get("vehicles") if isinstance(data, dict) else None
        if not isinstance(listed, list) or not all(isinstance(item, dict) for item in listed):
            continue  # a generated fleet or timed scenario is used as written
        chosen = [car.name for car in cars]
        if [item.get("name") for item in listed] == chosen:
            continue
        kept = [dict(item) for item in listed if item.get("name") in chosen]
        offsets = [float(item.get("route_offset_m", 0.0)) for item in kept]
        for name in chosen:
            if name in {item.get("name") for item in kept}:
                continue
            offset = min(offsets) - ADDED_CAR_SPACING_M if offsets else 0.0
            offsets.append(offset)
            kept.append({"name": name, "route_offset_m": offset})
        # The leading Car starts at the route start (offset 0).
        lead = max(float(item.get("route_offset_m", 0.0)) for item in kept)
        for item in kept:
            item["route_offset_m"] = float(item.get("route_offset_m", 0.0)) - lead
        derived = {**data, "vehicles": kept}
        text = yaml.safe_dump(derived, sort_keys=False, allow_unicode=True)
        digest = hashlib.sha256(f"{route_path}\n{text}".encode("utf-8")).hexdigest()[:16]
        target = ROUTE_CACHE / f"{route_path.stem}-{digest}.yaml"
        if not target.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            staging = target.with_suffix(".partial.yaml")
            staging.write_text(text, encoding="utf-8")
            staging.replace(target)
        for name in chosen:
            replacement[name] = str(target)
    if not replacement:
        return vehicles
    return tuple(
        dataclasses.replace(vehicle, params={**vehicle.params, "scenario": replacement[vehicle.name]})
        if vehicle.name in replacement else vehicle
        for vehicle in vehicles
    )


def city_receipt(composition: Composition) -> Path:
    """Return the City World receipt the builders read.

    A plain World is materialized as a City World job (tools/plain_world.py),
    so the City builders run on it unchanged.
    """
    if composition.world.kind == "plain":
        import plain_world

        try:
            return plain_world.materialize(plain_world_yaml(composition))
        except plain_world.PlainWorldError as exc:
            raise CompositionError(str(exc)) from exc
    return composition.world.resolve(composition.world.data["receipt"])


def city_ground(receipt: Path) -> Callable[[float, float], float]:
    """Return ground(east_m, north_m): the top of the City World collision geometry.

    Uses a downward MuJoCo ray (tools/world_height.py), so rooftops count.
    Without MuJoCo Python it falls back to the terrain hfield and says so.
    """
    import multi_car

    data = multi_car.load_json(receipt, "City World receipt")
    try:
        mjcf = Path(data["mjcf"]["path"])
    except (KeyError, TypeError) as exc:
        raise CompositionError(f"City World receipt has no MJCF: {receipt}") from exc
    if not mjcf.is_absolute():
        mjcf = receipt.parent / mjcf
    try:
        import world_height

        return world_height.ray_ground(mjcf)
    except ImportError:
        print(
            "WARNING: MuJoCo Python is not installed; spawn heights use the City terrain "
            "only, so rooftops are ignored. The managed Recipe configure installs it.",
            file=sys.stderr,
        )
        return lambda east_m, north_m: multi_car.terrain_height_mjcf(data, north_m, -east_m)


def plain_world_yaml(composition: Composition) -> Path:
    if composition.world.kind != "plain":
        raise CompositionError(f"World {composition.world.id} is not a plain World")
    return composition.world.resolve(composition.world.data["world"])


def plain_world_mjcf(world_yaml: Path, *, cache_dir: Path | None = None) -> Path:
    """Return the World-only MJCF (ground and obstacles) of a plain World YAML.

    The FPV generator writes it with the same geometry it merges into the FPV
    vehicle model; it is cached by the YAML and the generator source.
    """
    generator = FPV_GENERATOR_SRC / "fpv_drone_generator/generators/mujoco.py"
    digest = hashlib.sha256(world_yaml.read_bytes() + generator.read_bytes()).hexdigest()
    output = (cache_dir or PLAIN_WORLD_CACHE) / f"{digest}.xml"
    if not output.is_file():
        if str(FPV_GENERATOR_SRC) not in sys.path:
            sys.path.insert(0, str(FPV_GENERATOR_SRC))
        from fpv_drone_generator.generators.mujoco import generate_world_mujoco
        from fpv_drone_generator.world import load_world

        partial = output.with_suffix(".partial.xml")
        generate_world_mujoco(load_world(world_yaml), partial)
        partial.replace(output)
    return output


def plain_ground(world_yaml: Path) -> Callable[[float, float], float]:
    """Return ground(east_m, north_m) on a plain World: its ground or obstacle tops."""
    mjcf = plain_world_mjcf(world_yaml)
    try:
        import world_height

        return world_height.ray_ground(mjcf)
    except ImportError:
        print(
            "WARNING: MuJoCo Python is not installed; spawn heights use the flat ground "
            "only, so obstacle tops are ignored. The managed Recipe configure installs it.",
            file=sys.stderr,
        )
        return lambda east_m, north_m: 0.0


def _ground(composition: Composition, ground: Callable[[float, float], float] | None):
    if ground is not None:
        return ground
    if composition.world.kind == "plain":
        return plain_ground(plain_world_yaml(composition))
    return city_ground(city_receipt(composition))


def _surface(vehicle: Vehicle, ground: Callable[[float, float], float]) -> float:
    return round(ground(vehicle.spawn["east_m"], vehicle.spawn["north_m"]), 6)


def _spawn_up(vehicle: Vehicle, ground: Callable[[float, float], float]) -> float:
    return _surface(vehicle, ground) + float(vehicle.asset.data["spawn"]["ground_clearance_m"])


def _param_path(composition: Composition, value: object) -> Path:
    """A path param: ${repo:NAME}/..., absolute, or relative to the Composition file."""
    return urban_assets.resolve_reference(str(value), composition.path.parent)


def _require_simulators(composition: Composition, expected: set[str], adapter: str) -> None:
    if composition.simulators() != expected:
        raise CompositionError(
            f"the {adapter} adapter needs simulators {sorted(expected)}; "
            f"got {sorted(composition.simulators())}"
        )


# --- Car (tools/multi_car.py) -------------------------------------------------

def _route_start_spawn(path: Path, car_name: str) -> dict | None:
    """Where a route Car starts: the route start, set back by its offset, facing along the route.

    Returns None when the file is not a route scenario naming the Car (multi_car
    and _car_route_scenarios report those cases).
    """
    route_dir = str(urban_assets.ROOT / "apps/car")
    if route_dir not in sys.path:
        sys.path.insert(0, route_dir)
    from route_geometry import RouteGeometry, RoutePoint

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        points = tuple(
            RoutePoint(
                name=str(item.get("name", f"point-{index + 1}")),
                east_m=float(item["east_m"]),
                north_m=float(item["north_m"]),
                dwell_sec=float(item.get("dwell_sec", 0.0)),
            )
            for index, item in enumerate(data["route"]["points"])
        )
        geometry = RouteGeometry(points)
    except (OSError, yaml.YAMLError, KeyError, TypeError, ValueError):
        return None
    offset = next(
        (float(item.get("route_offset_m", 0.0)) for item in data.get("vehicles") or []
         if isinstance(item, dict) and item.get("name") == car_name),
        None,
    )
    if offset is None:
        return None
    east_m, north_m = geometry.sample(offset)
    return {
        "east_m": round(east_m, 3),
        "north_m": round(north_m, 3),
        "yaw_deg": round(math.degrees(geometry.heading_rad(offset)), 3),
    }


def _route_cars(composition: Composition, cars: list[Vehicle]) -> list[Vehicle]:
    """api Cars on a route start at the route (the placed spawn is not used)."""
    placed = []
    for car in cars:
        spawn = None
        if car.control == "api" and "scenario" in car.params:
            spawn = _route_start_spawn(_param_path(composition, car.params["scenario"]), car.name)
        placed.append(dataclasses.replace(car, spawn={**car.spawn, **spawn}) if spawn else car)
    return placed


def _car_route_scenarios(composition: Composition, cars: list[Vehicle]) -> list[dict]:
    """The route scenarios of the api Cars; each runs its own executor (asset-contract 4.3).

    load() already pointed every api Car at a route naming exactly the Cars
    that selected it (_assign_route_cars), so Cars may follow different routes.
    """
    # A Car whose api program is replaced (section 5.2) may run without a scenario.
    paths = {
        _param_path(composition, car.params["scenario"])
        for car in cars
        if car.control == "api" and "scenario" in car.params
    }
    return [{"path": str(path), "auto_start": True} for path in sorted(paths, key=str)]


def _car_inputs(
    composition: Composition,
    cars: list[Vehicle],
    web_bridge_port: int,
    ground: Callable[[float, float], float],
) -> dict:
    cars = _route_cars(composition, cars)
    types = []
    type_names: dict[str, str] = {}
    front_camera = None
    for car in cars:
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
                    # Absolute height: the World surface (rooftops included)
                    # plus the Asset clearance (section 5.4).
                    "up_m": _spawn_up(car, ground),
                },
            }
            for car in cars
        ],
    }
    route_scenarios = _car_route_scenarios(composition, cars)
    if len(route_scenarios) == 1:
        vehicles["route_scenario"] = route_scenarios[0]
    elif route_scenarios:
        vehicles["route_scenarios"] = route_scenarios
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
        # The Urban real-time pacer paces the plant (tools/urban_realtime.py), so
        # the plant does not sleep on its own.
        "ackermann_runtime": {"realtime_sync_cycle_msec": 0, "native_mujoco_viewer": False},
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


def to_car_config(
    composition: Composition,
    recipe_id: str,
    *,
    ground: Callable[[float, float], float] | None = None,
) -> dict:
    """Return the multi_car.py configuration for a City + Car Composition."""
    _require_simulators(composition, {CAR_SIMULATOR}, "Car")
    if composition.interactions:
        raise CompositionError("interactions need a Drone; this Composition has Cars only")
    inputs = _car_inputs(
        composition, list(composition.vehicles), DEFAULT_CAR_WEB_BRIDGE_PORT, _ground(composition, ground)
    )
    return _car_config(composition, recipe_id, inputs)


# --- Drone (tools/drone_one.py) -----------------------------------------------

def _drone_recipe(
    composition: Composition,
    drone: Vehicle,
    ground: Callable[[float, float], float],
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
    spawn = drone.spawn
    surface = _surface(drone, ground)
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
    return _drone_recipe(composition, composition.vehicles[0], _ground(composition, ground))


# --- Drone fleet (tools/drone_fleet.py) -------------------------------------------

def to_fleet_recipe(composition: Composition) -> dict:
    """Return the tools/drone_fleet.py recipe for a World + one Drone fleet Composition."""
    if composition.vehicles or len(composition.fleets) != 1:
        raise CompositionError("the fleet route runs exactly one fleet and no other vehicles (yet)")
    fleet = composition.fleets[0]
    profile = fleet.asset.data.get("source", {}).get("profile")
    if profile != "drone-core-quad":
        raise CompositionError(f"fleet {fleet.name}: tools/drone_fleet.py runs the drone-core-quad profile only")
    return {
        "version": 1,
        "id": composition.id,
        "fleet": fleet.name,
        "city_receipt": city_receipt(composition).as_posix(),
        "drone_count": fleet.count,
        "process_count": fleet.processes or math.ceil(fleet.count / FLEET_DRONES_PER_PROCESS),
        "spacing_m": fleet.spacing_m,
        # The builder lays the grid out around this centre and raycasts each
        # drone's ground (roofs included) itself.
        "area": dict(fleet.area),
        "ground_clearance_m": float(fleet.asset.data["spawn"]["ground_clearance_m"]),
        "control": fleet.control,
        "web_bridge_port": _port(
            composition.viewer.get("web_bridge_port", DEFAULT_FLEET_WEB_BRIDGE_PORT), "viewer.web_bridge_port",
        ),
    }


def _port(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 65535:
        raise CompositionError(f"{label} must be a port number: {value!r}")
    return value


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
    cars = composition.by_simulator(CAR_SIMULATOR)
    # One World model serves every vehicle's spawn height.
    ground = _ground(composition, ground)
    inputs = _car_inputs(composition, cars, DEFAULT_INTEGRATED_WEB_BRIDGE_PORT, ground)
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
    route_scenarios = inputs["ackermann_vehicles"].get("route_scenarios")
    if route_scenarios:
        scenarios["car"] = [item["path"] for item in route_scenarios]
    config["scenarios"] = scenarios
    return config, _drone_recipe(composition, drone, ground)


# --- FPV Drone on a plain World (tools/fpv.py) --------------------------------------

def is_fpv(vehicle: Vehicle) -> bool:
    generator = vehicle.asset.data.get("source", {}).get("generator")
    return isinstance(generator, dict) and generator.get("tool") == FPV_TOOL


def fpv_vehicle(composition: Composition) -> Vehicle:
    """Return the one FPV Drone of a World (City or plain) + FPV Drone Composition."""
    if len(composition.vehicles) != 1 or not is_fpv(composition.vehicles[0]):
        raise CompositionError("the FPV adapter runs exactly one FPV Drone (tools/fpv.py)")
    if composition.interactions:
        raise CompositionError("interactions need Cars; this Composition has one FPV Drone only")
    return composition.vehicles[0]


def to_fpv_spawn(
    composition: Composition,
    *,
    ground: Callable[[float, float], float] | None = None,
) -> dict:
    """Return the Drone Core droneDynamics pose (NED) for the FPV Drone spawn.

    The plain World's MuJoCo frame is X=North, Y=-East, Z=Up (section 6.2);
    Drone Core takes position_meter as [north, east, -up] and a NED yaw.
    """
    vehicle = fpv_vehicle(composition)
    up = _spawn_up(vehicle, _ground(composition, ground))
    spawn = vehicle.spawn
    yaw_ned = (90.0 - spawn["yaw_deg"] + 180.0) % 360.0 - 180.0
    return {
        "position_meter": [spawn["north_m"], spawn["east_m"], -up],
        "angle_degree": [0.0, 0.0, yaw_ned],
    }


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
