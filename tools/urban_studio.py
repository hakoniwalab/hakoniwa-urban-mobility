#!/usr/bin/env python3
"""Urban Studio: the local browser UI for Urban Compositions (issue #5).

Serves web/ and a JSON API over tools/urban_assets.py and
tools/urban_simulation.py. Long commands (configure, start, ...) run as
child processes; their output and [HAKO_PROGRESS] events are exposed as jobs
the browser polls.

Run it from the Business Pack Workspace shell so the simulations it starts
inherit the Workspace environment:

  python tools/urban_studio.py [--port 28090] [--open-browser]   in this terminal (Ctrl+C stops it)
  python tools/urban_studio.py start [--port 28090] [--open-browser]   in the background
  python tools/urban_studio.py open           open the running Urban Studio in the browser
  python tools/urban_studio.py status | stop   stop: the Urban Studio on its port, however it was started

The port defaults to urban.manifest.yaml's urban-studio port
(HAKONIWA_URBAN_PORT_URBAN_STUDIO or the machine's overrides file change it).

API (all JSON):
  GET  /api/health                       this Urban Studio (app, pid, port): what start/status/stop check
  GET  /api/assets                       Asset catalog
  GET  /api/assets/<id>/preview          a vehicle's preview parts (GLB URL and pose in its frame)
  GET  /api/assets/<id>/preview/<n>      one preview GLB
  GET  /api/compositions                 saved and example Compositions
  GET  /api/compositions/<id>            one Composition
  PUT  /api/compositions/<id>            save a Composition; returns its plan
  GET  /api/compositions/<id>/plan       its route (tools/urban_simulation.py plan)
  POST /api/compositions/<id>/<command>  run plan|configure|start|stop|status
  GET  /api/compositions/<id>/viewer     the configured Viewer URL
  GET  /api/compositions/<id>/rtf        the latest real-time factor from the pacer log
  GET  /api/jobs/<job>?since=<line>      a command's state, output, and progress
  GET  /api/scenarios                    Car route scenarios (examples and saved)
  GET  /api/scenarios/<id>               one route scenario
  PUT  /api/scenarios/<id>               validate and save a route scenario
  DELETE /api/scenarios/<id>             delete a saved route scenario (not an example,
                                         not one a saved Composition uses)
  GET  /api/worlds/<id>                  a World's extent, map origin, and GLB URL
  GET  /api/worlds/<id>/glb              the World's display GLB
  GET  /api/worlds/<id>/footprints       a City World's building outlines (collision walls)
  POST /api/worlds/<id>/route-check      segments of route points blocked by those walls
  GET  /api/worlds/<id>/height?east=&north=  ground height (terrain, roofs, obstacles)
  POST /api/worlds/<id>/route-line       a Car route's loop with the height of the surface
                                         under it (the Route tab's 3D view)
  POST /api/worlds/<id>/flight-check     a Drone flight's point heights (from the height above
                                         the ground) and the legs that meet the World
  GET  /api/flights                      Drone flights (files with a drones: section)
  GET  /api/flights/<id>                 one flight
  PUT  /api/flights/<id>                 validate and save a flight
  DELETE /api/flights/<id>               delete a saved flight (not an example, not one a
                                         saved Composition uses)
  GET  /api/cities                       Environment Studio (running, its export folder), the
                                         City World jobs it wrote to Urban's folder (a ready,
                                         unregistered one starts its registration), the
                                         registered Cities
  POST /api/cities/environment-studio/start|stop
                                         start (configure first) or stop Environment Studio
                                         with Urban's export folder (urban_city_authoring.py)
  GET  /api/cache                        Urban cache (world-height, plain-world) and what
                                         prune-cache would remove
  POST /api/cache/prune                  run urban_assets.py prune-cache --apply
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import itertools
import json
import math
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlparse
import webbrowser

import urban_manifest


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
BUSINESS_PACK = urban_manifest.business_pack()  # $HAKONIWA_WORKSPACE_ROOT
WEB_ROOT = ROOT / "web"
USER_SCENARIOS = urban_manifest.work_dir() / "urban/scenarios"
EXAMPLE_SCENARIOS = ROOT / "recipes/scenarios"
SIMULATION = ROOT / "tools/urban_simulation.py"
COMMANDS = ("plan", "configure", "start", "stop", "status")
COMPOSITION_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
PROGRESS_MARKER = "[HAKO_PROGRESS] "
URBAN_ASSETS = ROOT / "tools/urban_assets.py"
CACHE_KEY = "cache:urban"

for _path in (ROOT / "tools", BUSINESS_PACK / "tools"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))


DEFAULT_PORT = urban_manifest.port("urban-studio")
APP_NAME = "urban-studio"
STATE_DIR = urban_manifest.path("studio.state")
# Set in a background Urban Studio's environment: start knows it answers and not another.
INSTANCE_ENV = "HAKONIWA_URBAN_STUDIO_INSTANCE"
START_TIMEOUT_SEC = 30.0
STOP_TIMEOUT_SEC = 10.0
USER_COMPOSITIONS = urban_manifest.path("compositions.user")
EXAMPLE_COMPOSITIONS = urban_manifest.path("compositions.repository")
# Cities are made by Environment Studio (PLATEAU, editing), which Urban Studio
# starts with Urban's export folder (tools/urban_city_authoring.py); Urban
# Studio registers the City World jobs that appear there.
CITY_AUTHORING = ROOT / "tools/urban_city_authoring.py"
STUDIO_CITY_JOBS = urban_manifest.path("assets.studio_city_jobs")
ENVIRONMENT_STUDIO_PORT = urban_manifest.port("environment-studio")


class StudioError(RuntimeError):
    def __init__(self, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST):
        super().__init__(message)
        self.status = status


# --- Assets and Compositions ------------------------------------------------------

def asset_catalog() -> list[dict]:
    import urban_assets

    assets = []
    for asset in urban_assets.catalog().values():
        data = asset.data
        entry = {
            "id": asset.id,
            "kind": asset.kind,
            "title": data.get("title", asset.id),
            "path": str(asset.path),
        }
        if asset.kind == "city":
            # A City whose receipt is gone (its City World job was deleted
            # elsewhere) stays listed but cannot be used as a World.
            entry["available"] = urban_assets.city_receipt_available(asset)
        if asset.kind == "vehicle":
            entry.update({
                "category": asset.category,
                "simulator": asset.simulator,
                "ground_clearance_m": data.get("spawn", {}).get("ground_clearance_m"),
                "controls": {
                    name: {
                        "scope": control.get("scope", "vehicle"),
                        "params": control.get("params", {}),
                    }
                    for name, control in asset.controls().items()
                },
                "interactions": sorted(data.get("interactions", {})),
                "fleet": data.get("fleet"),
                "preview": data.get("preview") is not None,
                "dimensions": data.get("dimensions"),
            })
        assets.append(entry)
    order = {"city": 0, "plain": 1, "vehicle": 2}
    return sorted(assets, key=lambda item: (order.get(item["kind"], 3), item["id"]))


def _check_id(composition_id: str) -> str:
    if not COMPOSITION_ID.match(composition_id):
        raise StudioError(f"a Composition id uses lowercase letters, digits, and '-': {composition_id!r}")
    return composition_id


def example_compositions() -> dict[str, Path]:
    """Example Compositions by id: this repository's, then other workspace
    repositories' (<repo>/assets/*.composition.yaml; urban.manifest.yaml)."""
    found = {path.stem: path for path in sorted(EXAMPLE_COMPOSITIONS.glob("*.yaml"))} if EXAMPLE_COMPOSITIONS.is_dir() else {}
    suffix = urban_manifest.value("compositions.suffix")
    pattern = urban_manifest.path("compositions.workspace").relative_to(WORKSPACE).as_posix()
    for path in sorted(WORKSPACE.glob(f"{pattern}/*{suffix}")):
        found.setdefault(path.name.removesuffix(suffix), path)
    return found


def example_dirs() -> list[Path]:
    return sorted({path.parent for path in example_compositions().values()} | {EXAMPLE_COMPOSITIONS})


def composition_path(composition_id: str) -> Path:
    """A saved Composition, else an example of that id."""
    _check_id(composition_id)
    path = USER_COMPOSITIONS / f"{composition_id}.yaml"
    if path.is_file():
        return path
    path = example_compositions().get(composition_id)
    if path is not None:
        return path
    raise StudioError(f"Composition {composition_id} not found", HTTPStatus.NOT_FOUND)


def missing_assets(data: dict, catalog: dict) -> list[str]:
    """What a Composition names that the workspace lacks: its World (or a City
    whose receipt is gone) and its vehicle and fleet Assets."""
    import urban_assets

    missing = []
    world = catalog.get(data.get("world"))
    if world is None or world.kind not in {"city", "plain"}:
        missing.append(f"World {data.get('world')}")
    elif world.kind == "city" and not urban_assets.city_receipt_available(world):
        missing.append(f"World {world.id} (its City World receipt)")
    for group in ("vehicles", "fleets"):
        for entry in data.get(group) or []:
            if isinstance(entry, dict) and entry.get("asset") not in catalog:
                missing.append(f"Asset {entry.get('asset')}")
    return list(dict.fromkeys(missing))


def _catalog_or_empty() -> dict:
    import urban_assets

    try:
        return urban_assets.catalog()
    except urban_assets.AssetError:
        return {}


def composition_summary(data: dict, catalog: dict) -> dict:
    """World and vehicle make-up of a Composition, for labels and the run summary.

    Unknown Asset ids are kept (their id is the title) so a broken Composition
    is still recognisable.
    """
    world_id = data.get("world")
    world = catalog.get(world_id)
    vehicles = []
    for entry in data.get("vehicles") or []:
        if not isinstance(entry, dict):
            continue
        asset = catalog.get(entry.get("asset"))
        vehicles.append({
            "name": entry.get("name"),
            "asset": entry.get("asset"),
            "title": asset.data.get("title", asset.id) if asset else entry.get("asset"),
            "control": entry.get("control"),
        })
    fleets = []
    for entry in data.get("fleets") or []:
        if not isinstance(entry, dict):
            continue
        asset = catalog.get(entry.get("asset"))
        fleets.append({
            "name": entry.get("name"),
            "asset": entry.get("asset"),
            "title": asset.data.get("title", asset.id) if asset else entry.get("asset"),
            "control": entry.get("control"),
            "count": entry.get("count"),
        })
    missing = missing_assets(data, catalog)
    return {
        "world": world_id,
        "world_title": world.data.get("title", world.id) if world else world_id,
        "world_kind": world.kind if world else None,
        "vehicle_list": vehicles,
        "fleets": fleets,
        "available": not missing,
        "missing": missing,
    }


def list_compositions() -> list[dict]:
    import yaml

    catalog = _catalog_or_empty()
    result = {}
    saved = sorted(USER_COMPOSITIONS.glob("*.yaml")) if USER_COMPOSITIONS.is_dir() else []
    entries = [(composition_id, path, False) for composition_id, path in example_compositions().items()]
    entries += [(path.stem, path, True) for path in saved]
    for composition_id, path, editable in entries:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        if not isinstance(data, dict):
            continue
        summary = composition_summary(data, catalog)
        if not editable and not summary["available"]:
            continue  # an example appears once the workspace has what it names
        # A saved Composition hides the example of the same id.
        result[composition_id] = {
            "id": composition_id,
            # Vehicles plus every drone of every fleet.
            "vehicles": len(data.get("vehicles") or []) + sum(
                int(item.get("count") or 0) for item in data.get("fleets") or [] if isinstance(item, dict)
            ),
            "editable": editable,
            "path": str(path),
            "updated_at": path.stat().st_mtime,
            **summary,
        }
    return sorted(result.values(), key=lambda item: item["id"])


def read_composition(composition_id: str) -> dict:
    import yaml

    path = composition_path(composition_id)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {"id": composition_id, "editable": path.parent == USER_COMPOSITIONS, "composition": data}


def _repo_reference(path: Path) -> str:
    """${repo:NAME}/... for a file inside a workspace repository, else the absolute path."""
    import urban_assets

    try:
        relative = path.relative_to(urban_assets.WORKSPACE.resolve())
    except ValueError:
        return path.as_posix()
    return f"${{repo:{relative.parts[0]}}}/{Path(*relative.parts[1:]).as_posix()}"


def relocate_path_params(composition: dict, catalog: dict, target_dir: Path = USER_COMPOSITIONS) -> dict:
    """Keep path params valid once the Composition is saved under target_dir.

    Relative paths resolve against the Composition file, so an example copied
    from recipes/compositions/ would point elsewhere. A relative path that
    only exists next to the examples becomes a ${repo:...} reference; a path
    that exists nowhere is rejected so the save fails instead of configure.
    """
    import urban_assets

    for group in ("vehicles", "fleets"):
        for entry in composition.get(group) or []:
            if not isinstance(entry, dict) or not isinstance(entry.get("params"), dict):
                continue
            asset = catalog.get(entry.get("asset"))
            declared = asset.controls().get(entry.get("control"), {}).get("params", {}) if asset else {}
            for name, definition in declared.items():
                value = entry["params"].get(name)
                if definition.get("type") != "path" or not isinstance(value, str) or not value:
                    continue
                if urban_assets.resolve_reference(value, target_dir).is_file():
                    continue
                examples = [urban_assets.resolve_reference(value, directory) for directory in example_dirs()]
                example = next((path for path in examples if path.is_file()), None)
                if not value.startswith("${") and not Path(value).is_absolute() and example is not None:
                    entry["params"][name] = _repo_reference(example)
                    continue
                raise StudioError(f"{entry.get('name')} の {name} のファイルが見つかりません: {value}")
    return composition


# --- Car route scenarios (Route tab) -----------------------------------------------------

ROUTE_SCHEMA_VERSIONS = (2, 3)


def _route_scenario_entry(path: Path, data: dict, editable: bool) -> dict:
    route = data.get("route") if isinstance(data.get("route"), dict) else {}
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    return {
        "id": path.stem,
        "name": data.get("name", path.stem),
        "editable": editable,
        "world": meta.get("world"),
        "points": len(route.get("points") or []),
        "vehicles": [item.get("name") for item in data.get("vehicles") or [] if isinstance(item, dict)],
        # The value a Composition's scenario param holds: valid from any save location.
        "reference": _repo_reference(path.resolve()),
        "path": str(path),
        "updated_at": path.stat().st_mtime,
    }


def _load_route_scenarios() -> dict[str, tuple[Path, dict, bool]]:
    import yaml

    found: dict[str, tuple[Path, dict, bool]] = {}
    for directory, editable in ((EXAMPLE_SCENARIOS, False), (USER_SCENARIOS, True)):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.yaml")):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError):
                continue
            # Only waypoint-loop route scenarios; timed command scenarios are not edited here.
            if isinstance(data, dict) and data.get("schema_version") in ROUTE_SCHEMA_VERSIONS \
                    and isinstance(data.get("route"), dict):
                found[path.stem] = (path, data, editable)  # a saved one hides the example
    return found


def route_clearance(asset_ids: list[str] | None = None) -> dict:
    """The wall clearance for the widest Car: of asset_ids, or of every Car Asset.

    Returns {"clearance_m", "vehicle"}; vehicle ({"asset", "title", "width_m"})
    is null when no such Car declares its dimensions.
    """
    import route_check
    import urban_assets

    cars = [
        asset for asset in urban_assets.catalog().values()
        if asset.kind == "vehicle" and asset.category == "car" and asset.data.get("dimensions")
        and (asset_ids is None or asset.id in asset_ids)
    ]
    if not cars:
        return {"clearance_m": route_check.DEFAULT_CLEARANCE_M, "vehicle": None}
    widest = max(cars, key=lambda asset: asset.data["dimensions"]["width_m"])
    width = float(widest.data["dimensions"]["width_m"])
    return {
        "clearance_m": round(route_check.clearance_for_width(width), 3),
        "vehicle": {"asset": widest.id, "title": widest.data.get("title", widest.id), "width_m": width},
    }


def route_conflicts_for(scenario: dict) -> list[dict]:
    """Segments of a route blocked by City World building walls (tools/route_check.py).

    A saved route does not know which Cars will follow it, so the widest Car
    Asset sets the clearance.
    """
    import route_check

    world_id = (scenario.get("meta") or {}).get("world")
    points = ((scenario.get("route") or {}).get("points")) or []
    if not world_id or len(points) < 3:
        return []
    try:
        buildings = world_footprints(world_id)["buildings"]
    except StudioError:
        return []
    return route_check.route_conflicts(points, buildings, route_clearance()["clearance_m"])


def check_route(world_id: str, body: object) -> dict:
    """Blocked segments of unsaved route points on a World (the live check while editing).

    body: {"points": [...], "assets": [Car Asset ids following the route]}; without
    assets the widest Car Asset sets the clearance.
    """
    import route_check

    points = body.get("points") if isinstance(body, dict) else None
    assets = body.get("assets") if isinstance(body, dict) else None
    if assets is not None and (not isinstance(assets, list) or not all(isinstance(item, str) for item in assets)):
        raise StudioError("assets must be a list of Asset ids")
    if not isinstance(points, list) or any(
        not isinstance(point, dict) or not all(isinstance(point.get(key), (int, float)) for key in ("east_m", "north_m"))
        for point in points
    ):
        raise StudioError("the request body must be {points: [{east_m, north_m}, ...]}")
    clearance = route_clearance(assets)
    # An unknown World is a 404; a plain World has no building walls.
    buildings = world_footprints(world_id)["buildings"]
    conflicts = route_check.route_conflicts(points, buildings, clearance["clearance_m"]) if len(points) >= 3 else []
    return {"conflicts": conflicts, **clearance}


def list_scenarios() -> list[dict]:
    return sorted(
        (_route_scenario_entry(path, data, editable) for path, data, editable in _load_route_scenarios().values()),
        key=lambda item: item["id"],
    )


def read_scenario(scenario_id: str) -> dict:
    _check_id(scenario_id)
    found = _load_route_scenarios().get(scenario_id)
    if found is None:
        raise StudioError(f"route scenario {scenario_id} not found", HTTPStatus.NOT_FOUND)
    path, data, editable = found
    return {**_route_scenario_entry(path, data, editable), "scenario": data, "conflicts": route_conflicts_for(data),
            **route_clearance()}


def _compositions_using(path: Path) -> list[str]:
    """Saved Compositions whose vehicle params point at path (a route scenario file)."""
    import urban_assets
    import yaml

    target = path.resolve()
    users = []
    for composition in sorted(USER_COMPOSITIONS.glob("*.yaml")) if USER_COMPOSITIONS.is_dir() else []:
        try:
            data = yaml.safe_load(composition.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        for vehicle in data.get("vehicles") or [] if isinstance(data, dict) else []:
            values = (vehicle.get("params") or {}).values() if isinstance(vehicle, dict) else []
            for value in values:
                try:
                    if isinstance(value, str) and urban_assets.resolve_reference(value, composition.parent) == target:
                        users.append(composition.stem)
                except urban_assets.AssetError:
                    continue
    return sorted(set(users))


def delete_scenario(scenario_id: str) -> dict:
    """Delete a saved route scenario; examples and routes still in use stay."""
    _check_id(scenario_id)
    found = _load_route_scenarios().get(scenario_id)
    if found is None:
        raise StudioError(f"route scenario {scenario_id} not found", HTTPStatus.NOT_FOUND)
    path, _, editable = found
    if not editable:
        raise StudioError(f"例のルート {scenario_id} は削除できません")
    users = _compositions_using(path)
    if users:
        raise StudioError(
            f"ルート {scenario_id} は Composition {', '.join(users)} が使っています。"
            "その車のルートを変えてから削除してください。",
            HTTPStatus.CONFLICT,
        )
    path.unlink()
    return {"deleted": scenario_id}


def save_scenario(scenario_id: str, scenario: dict) -> dict:
    """Validate a route scenario with the Car scenario executor and save it."""
    import yaml

    _check_id(scenario_id)
    if not isinstance(scenario, dict):
        raise StudioError("the request body must be a route scenario object")
    scenario = {**scenario, "schema_version": scenario.get("schema_version", 2)}
    USER_SCENARIOS.mkdir(parents=True, exist_ok=True)
    path = USER_SCENARIOS / f"{scenario_id}.yaml"
    staging = path.with_suffix(".partial.yaml")
    staging.write_text(yaml.safe_dump(scenario, sort_keys=False, allow_unicode=True), encoding="utf-8")
    sys.path.insert(0, str(ROOT / "apps/car"))
    try:
        from scenario_executor import ScenarioError, load_scenario

        try:
            load_scenario(staging)
        except ScenarioError as exc:
            staging.unlink()
            raise StudioError(f"ルートを保存できません: {exc}") from exc
    finally:
        sys.path.remove(str(ROOT / "apps/car"))
    staging.replace(path)
    # A blocked segment is reported, not refused: the user may still be editing.
    return {**_route_scenario_entry(path, scenario, True), "conflicts": route_conflicts_for(scenario),
            **route_clearance()}


# --- Drone flights (Flight tab) ----------------------------------------------------------
# A flight is the drones: section of a YAML file (apps/drone/drone_schedule.py):
# a file of its own, or a Car route scenario that also flies a Drone. Its
# meta: holds what only the editor uses: the World and the takeoff point
# (the Composition places the Drone; the editor checks the legs from there).


def _flight_entry(path: Path, data: dict, editable: bool) -> dict:
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    return {
        "id": path.stem,
        "name": data.get("name", path.stem),
        "editable": editable,
        "world": meta.get("world"),
        "drones": [item.get("name") for item in data.get("drones") or [] if isinstance(item, dict)],
        "route": isinstance(data.get("route"), dict),  # also a Car route scenario
        "takeoff": meta.get("takeoff") if isinstance(meta.get("takeoff"), dict) else None,
        "reference": _repo_reference(path.resolve()),
        "path": str(path),
        "updated_at": path.stat().st_mtime,
    }


def _load_flights() -> dict[str, tuple[Path, dict, bool]]:
    import yaml

    found: dict[str, tuple[Path, dict, bool]] = {}
    for directory, editable in ((EXAMPLE_SCENARIOS, False), (USER_SCENARIOS, True)):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.yaml")):
            if path.name.endswith(".partial.yaml"):
                continue
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError):
                continue
            if isinstance(data, dict) and isinstance(data.get("drones"), list) and data["drones"]:
                found[path.stem] = (path, data, editable)  # a saved one hides the example
    return found


def list_flights() -> list[dict]:
    return sorted(
        (_flight_entry(path, data, editable) for path, data, editable in _load_flights().values()),
        key=lambda item: item["id"],
    )


def read_flight(flight_id: str) -> dict:
    _check_id(flight_id)
    found = _load_flights().get(flight_id)
    if found is None:
        raise StudioError(f"flight {flight_id} not found", HTTPStatus.NOT_FOUND)
    path, data, editable = found
    entry = {**_flight_entry(path, data, editable), "flight": data, "used_by": _compositions_using(path)}
    # A flight written by hand names no World or takeoff point: those of a
    # Composition that flies it (its World, where it places the Drone).
    drones = set(entry["drones"])
    for composition_id in entry["used_by"]:
        try:
            composition = read_composition(composition_id)["composition"] or {}
        except (StudioError, OSError):
            continue
        entry.setdefault("suggested_world", composition.get("world"))
        for vehicle in composition.get("vehicles") or []:
            if isinstance(vehicle, dict) and vehicle.get("name") in drones and isinstance(vehicle.get("spawn"), dict):
                spawn = vehicle["spawn"]
                entry.setdefault("suggested_takeoff", {"east_m": spawn.get("east_m", 0), "north_m": spawn.get("north_m", 0)})
        if "suggested_takeoff" in entry:
            break
    return entry


def save_flight(flight_id: str, flight: dict) -> dict:
    """Validate every Drone schedule in a flight file (and its Car route, if any) and save it."""
    import yaml

    _check_id(flight_id)
    if not isinstance(flight, dict) or not isinstance(flight.get("drones"), list) or not flight["drones"]:
        raise StudioError("the request body must be a flight: {drones: [...], meta: {...}}")
    USER_SCENARIOS.mkdir(parents=True, exist_ok=True)
    path = USER_SCENARIOS / f"{flight_id}.yaml"
    staging = path.with_suffix(".partial.yaml")
    staging.write_text(yaml.safe_dump(flight, sort_keys=False, allow_unicode=True), encoding="utf-8")
    try:
        sys.path.insert(0, str(ROOT / "apps/drone"))
        try:
            from drone_schedule import ScheduleError, check_schedule

            for index, entry in enumerate(flight["drones"]):
                if not isinstance(entry, dict):
                    raise StudioError(f"drones[{index}] must be a mapping")
                check_schedule(entry, f"drones[{entry.get('name', index)}]")
        except ScheduleError as exc:
            raise StudioError(f"飛行計画を保存できません: {exc}") from exc
        finally:
            sys.path.remove(str(ROOT / "apps/drone"))
        if isinstance(flight.get("route"), dict):
            sys.path.insert(0, str(ROOT / "apps/car"))
            try:
                from scenario_executor import ScenarioError, load_scenario

                try:
                    load_scenario(staging)
                except ScenarioError as exc:
                    raise StudioError(f"飛行計画を保存できません（車のルート）: {exc}") from exc
            finally:
                sys.path.remove(str(ROOT / "apps/car"))
    except StudioError:
        staging.unlink()
        raise
    staging.replace(path)
    return _flight_entry(path, flight, True)


def delete_flight(flight_id: str) -> dict:
    """Delete a saved flight; examples and flights a saved Composition uses stay."""
    _check_id(flight_id)
    found = _load_flights().get(flight_id)
    if found is None:
        raise StudioError(f"flight {flight_id} not found", HTTPStatus.NOT_FOUND)
    path, _, editable = found
    if not editable:
        raise StudioError(f"例の飛行計画 {flight_id} は削除できません")
    users = _compositions_using(path)
    if users:
        raise StudioError(
            f"飛行計画 {flight_id} は Composition {', '.join(users)} が使っています。"
            "そのドローンの飛行計画を変えてから削除してください。",
            HTTPStatus.CONFLICT,
        )
    path.unlink()
    return {"deleted": flight_id}


def repair_saved_composition(path: Path) -> list[str]:
    """Before running a saved Composition, fix path params the same way a save does.

    A Composition saved before path params were checked may still hold an
    example-relative path; it is rewritten in place so the run just works.
    A path that exists nowhere stops the command with a clear message instead
    of a traceback from configure. Examples are never modified.
    """
    import copy

    import urban_assets
    import yaml

    if path.parent.resolve() != USER_COMPOSITIONS.resolve():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return []  # the simulation command reports an unreadable file
    if not isinstance(data, dict):
        return []
    repaired = copy.deepcopy(data)
    try:
        relocate_path_params(repaired, _catalog_or_empty(), path.parent)
    except urban_assets.AssetError as exc:
        raise StudioError(str(exc)) from exc
    except StudioError as exc:
        raise StudioError(f"{exc}。Compose で開いて、ファイルを指定し直してください。") from exc
    if repaired == data:
        return []
    path.write_text(yaml.safe_dump(repaired, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return [f"[studio] {path.name} のファイル参照を保存先に合わせて修正しました（例と同じファイルを ${{repo:...}} で参照）"]


def save_composition(composition_id: str, composition: dict) -> dict:
    """Validate and save a Composition under the user directory; return its plan."""
    import urban_simulation
    import yaml

    _check_id(composition_id)
    if not isinstance(composition, dict):
        raise StudioError("the request body must be a Composition object")
    composition = {**composition, "schema": "hakoniwa.composition/v1", "id": composition_id}
    import urban_assets

    try:
        relocate_path_params(composition, _catalog_or_empty())
    except urban_assets.AssetError as exc:  # a malformed reference such as ${runtime.*}
        raise StudioError(str(exc)) from exc
    USER_COMPOSITIONS.mkdir(parents=True, exist_ok=True)
    path = USER_COMPOSITIONS / f"{composition_id}.yaml"
    staging = path.with_suffix(".partial.yaml")
    staging.write_text(yaml.safe_dump(composition, sort_keys=False, allow_unicode=True), encoding="utf-8")
    try:
        selected = urban_simulation.plan(staging)
    except urban_simulation.SimulationError as exc:
        staging.unlink()
        raise StudioError(str(exc)) from exc
    staging.replace(path)
    # plan() read the staging file; report the saved path.
    return {**selected.to_json(), "path": str(path)}


# --- Worlds (placement view) ------------------------------------------------------------

_grounds: dict[str, tuple[object, threading.Lock, bool]] = {}
_grounds_lock = threading.Lock()


def world_receipt(world_id: str) -> tuple[object, Path]:
    """Return (World Asset, City World receipt); a plain World gets its City World job."""
    import urban_assets

    asset = urban_assets.catalog().get(world_id)
    if asset is None or asset.kind not in {"city", "plain"}:
        raise StudioError(f"World {world_id} not found", HTTPStatus.NOT_FOUND)
    if asset.kind == "city":
        if not urban_assets.city_receipt_available(asset):
            raise StudioError(
                f"City {world_id} の City World receipt がありません（ジョブが削除された可能性があります）",
                HTTPStatus.NOT_FOUND,
            )
        return asset, asset.resolve(asset.data["receipt"])
    import plain_world

    return asset, plain_world.materialize(asset.resolve(asset.data["world"]))


def world_info(world_id: str) -> dict:
    asset, receipt_path = world_receipt(world_id)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    frame = receipt["coordinate_frame"]
    info = {
        "id": world_id,
        "kind": asset.kind,
        "title": asset.data.get("title", world_id),
        "half_extent_m": frame["half_extent_m"],
        "glb": f"/api/worlds/{world_id}/glb",
        # A City whose receipt says kind plain (an environment made without a
        # map, such as one an authoring tool exported) has no place on a map.
        "map": asset.kind == "city" and receipt.get("kind") != "plain",
    }
    if info["map"]:
        info["origin"] = {key: frame["origin"][key] for key in ("latitude", "longitude")}
    return info


def _vehicle_asset(asset_id: str):
    import urban_assets

    asset = urban_assets.catalog().get(asset_id)
    if asset is None or asset.kind != "vehicle":
        raise StudioError(f"vehicle Asset {asset_id} not found", HTTPStatus.NOT_FOUND)
    return asset


def _preview_parts(asset_id: str) -> list[dict]:
    import asset_preview

    try:
        parts = asset_preview.preview_parts(_vehicle_asset(asset_id))
    except asset_preview.PreviewError as exc:
        raise StudioError(str(exc), HTTPStatus.NOT_FOUND) from exc
    if not parts:
        raise StudioError(f"Asset {asset_id} has no preview", HTTPStatus.NOT_FOUND)
    return parts


def asset_preview_info(asset_id: str) -> dict:
    """A vehicle Asset's preview parts in the vehicle frame (X forward, Y left, Z up)."""
    return {"parts": [
        {
            "url": f"/api/assets/{asset_id}/preview/{index}",
            "position": part["position"],
            "quaternion": part["quaternion"],
            "scale": part["scale"],
            "basis": part["basis"],
        }
        for index, part in enumerate(_preview_parts(asset_id))
    ]}


def asset_preview_model(asset_id: str, index: str) -> Path:
    """One preview GLB; only files the manifest's preview names are served."""
    parts = _preview_parts(asset_id)
    if not index.isdigit() or int(index) >= len(parts):
        raise StudioError(f"Asset {asset_id} has no preview part {index}", HTTPStatus.NOT_FOUND)
    return parts[int(index)]["path"]


def world_footprints(world_id: str) -> dict:
    """Building footprints of a City World in local ENU metres (the collision walls).

    City World builds buildings as walls standing on these outlines, so a Car
    route crossing one is blocked even where the map shows a driveway.
    """
    asset, receipt_path = world_receipt(world_id)
    if asset.kind != "city":
        return {"id": world_id, "buildings": []}
    lod1 = receipt_path.parent.parent / "city-world-lod1.json"
    if not lod1.is_file():
        return {"id": world_id, "buildings": []}
    data = json.loads(lod1.read_text(encoding="utf-8"))
    buildings = [
        {
            "id": item.get("id"),
            "vertices": item.get("vertices") or [],
            # Courtyards: open ground inside the outline, drawn as holes.
            "holes": [ring for ring in item.get("interior_rings") or [] if len(ring) >= 3],
            "height_m": item.get("zmax"),
        }
        for item in data.get("polygons") or []
        if isinstance(item, dict) and len(item.get("vertices") or []) >= 3
    ]
    return {"id": world_id, "buildings": buildings}


def world_glb(world_id: str) -> Path:
    _, receipt_path = world_receipt(world_id)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    path = Path(receipt["glb"]["path"])
    if not path.is_absolute():
        path = receipt_path.parent / path
    if not path.is_file():
        raise StudioError(f"World {world_id} has no GLB: {path}", HTTPStatus.NOT_FOUND)
    return path


def _world_ground(world_id: str) -> tuple[object, threading.Lock, bool]:
    """(ground(east, north), its lock, whether rooftops count); the World model loads once."""
    import urban_composition

    with _grounds_lock:
        entry = _grounds.get(world_id)
        if entry is None:
            _, receipt_path = world_receipt(world_id)
            try:
                import mujoco  # noqa: F401 - only to report whether rooftops count
                rooftops = True
            except ImportError:
                rooftops = False
            entry = (urban_composition.city_ground(receipt_path), threading.Lock(), rooftops)
            _grounds[world_id] = entry
    return entry


def check_flight(world_id: str, body: object) -> dict:
    """Heights and blocked legs of a Drone flight on a World (the flight editor).

    body: {"points": [{east_m, north_m, agl_m | up_m, stand?}]}, the flight's
    line in order (takeoff stand, waypoints, landing stand). agl_m is the
    height above the top of the World under the point (ground or roof).
    Returns each point's ground_m and up_m, and the legs that meet the World
    (tools/flight_check.py); checked is false when the World model cannot cast
    rays (no MuJoCo Python).
    """
    import flight_check

    points = body.get("points") if isinstance(body, dict) else None

    def number(value) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

    if not isinstance(points, list) or any(
        not isinstance(point, dict) or not number(point.get("east_m")) or not number(point.get("north_m"))
        or not (number(point.get("agl_m")) or number(point.get("up_m")))
        for point in points
    ):
        raise StudioError("the request body must be {points: [{east_m, north_m, agl_m or up_m, stand?}, ...]}")
    ground, lock, _ = _world_ground(world_id)
    resolved = []
    with lock:  # one MuJoCo query at a time per World model
        for point in points:
            try:
                ground_m = float(ground(point["east_m"], point["north_m"]))
            except Exception:  # noqa: BLE001 - e.g. outside the World
                ground_m = None
            if number(point.get("agl_m")):
                if ground_m is None:
                    raise StudioError(f"no ground at east={point['east_m']}, north={point['north_m']}")
                up_m = ground_m + float(point["agl_m"])
            else:
                up_m = float(point["up_m"])
            resolved.append({"ground_m": None if ground_m is None else round(ground_m, 3), "up_m": round(up_m, 3)})
        first_hit = getattr(ground, "first_hit", None)
        line = [{"east_m": point["east_m"], "north_m": point["north_m"], "up_m": done["up_m"],
                 "stand": bool(point.get("stand"))} for point, done in zip(points, resolved)]
        conflicts = flight_check.leg_conflicts(first_hit, line) if first_hit else []
    return {"points": resolved, "conflicts": conflicts, "checked": first_hit is not None, **flight_check.margins()}


def route_line_3d(world_id: str, body: object) -> dict:
    """A Car route's loop on a World with the height of the surface under it
    (tools/route_line.py), for the Route tab's 3D view.

    body: {"points": [{east_m, north_m}, ...]}. Returns {"points": [{east_m,
    north_m, up_m}], "corners": [the index of each route point in it]}.
    """
    import route_line

    points = body.get("points") if isinstance(body, dict) else None
    if not isinstance(points, list) or any(
        not isinstance(point, dict) or not all(
            isinstance(point.get(key), (int, float)) and not isinstance(point.get(key), bool) for key in ("east_m", "north_m"))
        for point in points
    ):
        raise StudioError("the request body must be {points: [{east_m, north_m}, ...]}")
    if len(points) < 2:
        return {"points": [], "corners": []}
    ground, lock, _ = _world_ground(world_id)
    with lock:  # one MuJoCo query at a time per World model
        try:
            line, corners = route_line.route_line([(point["east_m"], point["north_m"]) for point in points], ground,
                                                  closed=len(points) >= 3)
        except Exception as exc:  # noqa: BLE001 - e.g. a point outside the World
            raise StudioError(f"no ground under the route: {exc}") from exc
    return {"points": line, "corners": corners}


def world_height(world_id: str, east_m: float, north_m: float) -> dict:
    """Ground height (terrain, buildings, obstacles) under a point; the World model loads once."""
    ground, lock, rooftops = _world_ground(world_id)
    with lock:  # one MuJoCo query at a time per World model
        try:
            height = ground(east_m, north_m)
        except Exception as exc:  # noqa: BLE001 - e.g. outside the World
            raise StudioError(f"no ground at east={east_m}, north={north_m}: {exc}") from exc
    return {"ground_m": round(float(height), 4), "rooftops": rooftops}


def plan_json(composition_id: str) -> dict:
    import urban_simulation

    try:
        return urban_simulation.plan(composition_path(composition_id)).to_json()
    except urban_simulation.SimulationError as exc:
        raise StudioError(str(exc)) from exc


def viewer_serving(url: str | None) -> bool:
    """Whether the Viewer at a URL can be opened now: its HTTP server is
    listening (it runs with the simulation)."""
    if not url:
        return False
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    try:
        with socket.create_connection((parts.hostname or "127.0.0.1", parts.port or 80), timeout=0.3):
            return True
    except OSError:
        return False


def viewer(composition_id: str) -> dict:
    """The configured Viewer URLs, and whether they can be opened now (running)."""
    import urban_simulation

    try:
        selected = urban_simulation.plan(composition_path(composition_id))
    except urban_simulation.SimulationError as exc:
        raise StudioError(str(exc)) from exc
    url = urban_simulation.viewer_url(selected)
    return {"url": url, "collider_url": urban_simulation.collider_viewer_url(selected), "running": viewer_serving(url)}


def realtime_factor(composition_id: str) -> dict:
    """The pacer's latest report (wall, sim, rtf), or null before one exists."""
    import urban_realtime
    import urban_simulation

    try:
        selected = urban_simulation.plan(composition_path(composition_id))
    except urban_simulation.SimulationError as exc:
        raise StudioError(str(exc)) from exc
    log = urban_simulation.pacer_log(selected)
    report = urban_realtime.latest_rtf(log)
    if report is not None:
        report["age_sec"] = round(max(0.0, time.time() - report.pop("updated")), 1)
    return {"log": str(log), "report": report}


# --- Jobs -----------------------------------------------------------------------------

def parse_progress(line: str) -> dict | None:
    """Return a [HAKO_PROGRESS] event with a percent when current/total say so."""
    if not line.startswith(PROGRESS_MARKER):
        return None
    try:
        event = json.loads(line[len(PROGRESS_MARKER):])
    except json.JSONDecodeError:
        return None
    if not isinstance(event, dict):
        return None
    current, total = event.get("current"), event.get("total")
    if isinstance(current, (int, float)) and isinstance(total, (int, float)) and total > 0:
        event["percent"] = round(100.0 * current / total, 1)
    return event


@dataclass
class Job:
    id: str
    composition: str  # the Composition id, or "city:<task>" for City tasks
    command: str
    steps: list[list[str]]  # commands run in order; the first failure stops the job
    state: str = "running"
    exit_code: int | None = None
    lines: list[str] = field(default_factory=list)
    progress: dict | None = None
    started: float = field(default_factory=time.time)
    finished: float | None = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def snapshot(self, since: int = 0) -> dict:
        with self.lock:
            return {
                "id": self.id,
                "composition": self.composition,
                "command": self.command,
                "state": self.state,
                "exit_code": self.exit_code,
                "progress": self.progress,
                "line_count": len(self.lines),
                "lines": self.lines[since:],
                "started": self.started,
                "finished": self.finished,
            }


class JobRunner:
    """Run one command at a time per Composition and keep its output."""

    def __init__(self, python: str = sys.executable, simulation: Path = SIMULATION):
        self.python = python
        self.simulation = simulation
        self.jobs: dict[str, Job] = {}
        # City registration key -> receipt mtime_ns when that registration started.
        self.city_receipt_versions: dict[str, int] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def start(self, composition_id: str, command: str) -> Job:
        if command not in COMMANDS:
            raise StudioError(f"unknown command {command!r}; use one of {', '.join(COMMANDS)}")
        path = composition_path(composition_id)
        # stop / status must work even for a Composition that cannot be repaired.
        notes = repair_saved_composition(path) if command in {"plan", "configure", "start"} else []
        return self.launch(composition_id, command,
                           [[self.python, "-u", str(self.simulation), command, "--composition", str(path)]],
                           notes)

    def launch(self, key: str, command: str, steps: list[list[str]], notes: list[str] | None = None) -> Job:
        """Run steps as one job; one job at a time per key. notes open the job output."""
        with self._lock:
            running = self.running(key)
            if running is not None:
                raise StudioError(
                    f"{running.command} is still running for {key} (job {running.id})",
                    HTTPStatus.CONFLICT,
                )
            job = Job(id=str(next(self._ids)), composition=key, command=command, steps=steps,
                      lines=list(notes or []))
            self.jobs[job.id] = job
        threading.Thread(target=self._run, args=(job,), name=f"studio-job-{job.id}", daemon=True).start()
        return job

    def running(self, key: str) -> Job | None:
        return next((job for job in self.jobs.values() if job.composition == key and job.state == "running"), None)

    def latest(self, key: str) -> Job | None:
        return next((job for job in reversed(self.jobs.values()) if job.composition == key), None)

    def _run(self, job: Job) -> None:
        environment = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
        code = 0
        for arguments in job.steps:
            try:
                process = subprocess.Popen(
                    arguments, cwd=ROOT, env=environment, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                )
            except OSError as exc:
                with job.lock:
                    job.lines.append(f"error: {exc}")
                code = -1
                break
            assert process.stdout is not None
            for raw in process.stdout:
                line = raw.rstrip("\r\n")
                event = parse_progress(line)
                with job.lock:
                    if event is not None:
                        job.progress = event
                    else:
                        job.lines.append(line)
            code = process.wait()
            process.stdout.close()
            if code != 0:
                break
        with job.lock:
            job.exit_code = code
            job.state = "succeeded" if code == 0 else "failed"
            job.finished = time.time()

    def get(self, job_id: str) -> Job:
        job = self.jobs.get(job_id)
        if job is None:
            raise StudioError(f"job {job_id} not found", HTTPStatus.NOT_FOUND)
        return job


# --- Cities (made by Environment Studio) -------------------------------------------------

def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False


def _environment_studio() -> dict | None:
    """The Environment Studio answering on its port (its /api/health), or None."""
    from urllib.error import URLError
    from urllib.request import urlopen

    try:
        with urlopen(f"http://127.0.0.1:{ENVIRONMENT_STUDIO_PORT}/api/health", timeout=0.5) as response:
            data = json.loads(response.read())
    except (OSError, URLError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("app") == "environment-studio" else None


def studio_city_jobs() -> list[dict]:
    """The City World jobs Environment Studio wrote to Urban's export folder,
    newest first, and whether each is registered. The Studio moves a job in
    only when it is complete, so a job with its receipt is ready. A
    registered City whose receipt changed since (written again) counts as
    unregistered, so the new World is picked up."""
    import urban_assets

    registered = {
        Path(asset.data["receipt"]).resolve(): asset.data.get("version")
        for asset in urban_assets.catalog().values() if asset.kind == "city"
    }
    root = STUDIO_CITY_JOBS
    folders = sorted((path for path in root.glob("*") if path.is_dir() and not path.name.endswith(".partial")),
                     key=lambda path: path.stat().st_mtime, reverse=True) if root.is_dir() else []
    jobs = []
    for folder in folders:
        receipt = (folder / "build/world/city-world-receipt.json").resolve()
        ready = receipt.is_file()
        try:
            title = json.loads((folder / "job.json").read_text(encoding="utf-8")).get("title") or folder.name
        except (OSError, ValueError):
            title = folder.name
        jobs.append({
            "id": folder.name, "title": title, "ready": ready, "receipt": str(receipt),
            "registered": ready and registered.get(receipt) == receipt.stat().st_mtime_ns,
        })
    return jobs


def city_state(runner: "JobRunner") -> dict:
    """The City page: Environment Studio (running, where it writes), the jobs
    it wrote to Urban's export folder, and the registered Cities. Each ready,
    unregistered job starts its registration (urban_assets.py register-city,
    which checks the job first); Cities whose job was removed from the
    folder are unregistered first."""
    import urban_assets

    unregistered = urban_assets.prune_missing_cities(STUDIO_CITY_JOBS)
    jobs = studio_city_jobs()
    for job in jobs:
        key = f"city:{job['id']}"
        previous = runner.latest(key)
        version = Path(job["receipt"]).stat().st_mtime_ns if job["ready"] else None
        # A failed registration is shown, not retried. A succeeded one is
        # redone only for a new World: the receipt changed since (written
        # again, or removed and written anew) or it was just pruned.
        retry = (
            previous is not None
            and previous.state == "succeeded"
            and (job["id"] in unregistered or runner.city_receipt_versions.get(key) != version)
        )
        if job["ready"] and not job["registered"] and (previous is None or retry):
            previous = runner.launch(key, "register", [[
                runner.python, "-u", str(URBAN_ASSETS), "register-city", "--receipt", job["receipt"],
                "--title", job["title"],
            ]])
            runner.city_receipt_versions[key] = version
        job["registration"] = previous.snapshot() if previous else None
    cities = []
    for asset in urban_assets.catalog().values():
        if asset.kind != "city":
            continue
        receipt = asset.resolve(str(asset.data["receipt"]))
        cities.append({"id": asset.id, "title": asset.data.get("title") or asset.id,
                       "receipt": str(receipt), "available": receipt.is_file()})
    health = _environment_studio()
    lifecycle = runner.latest("city:environment-studio")
    return {
        "environment_studio": {
            "url": f"http://127.0.0.1:{ENVIRONMENT_STUDIO_PORT}/map.html",
            "running": health is not None,
            "export_dir": health.get("export_dir") if health else None,
            # Started by hand without Urban's folder: what it writes would not come here.
            "writes_here": bool(health) and health.get("export_dir") == str(STUDIO_CITY_JOBS.resolve()),
            "job": lifecycle.snapshot() if lifecycle else None,
        },
        "export_dir": str(STUDIO_CITY_JOBS),
        "jobs": jobs,
        "unregistered": unregistered,
        "cities": cities,
    }


def environment_studio(runner: "JobRunner", command: str) -> "Job":
    """Start (configure first) or stop Environment Studio with Urban's export folder."""
    if command not in {"start", "stop"}:
        raise StudioError(f"unknown Environment Studio command {command!r}", HTTPStatus.NOT_FOUND)
    return runner.launch("city:environment-studio", command, [[runner.python, "-u", str(CITY_AUTHORING), command]])


# --- Caches ---------------------------------------------------------------------------

def cache_state(runner: "JobRunner") -> dict:
    import urban_cache

    job = runner.latest(CACHE_KEY)
    return {
        "urban": urban_cache.plan(mujoco_version=urban_cache.current_mujoco_version()),
        "prune_job": job.snapshot() if job else None,
    }


def prune_cache(runner: "JobRunner") -> "Job":
    """Prune the Urban cache while no Studio command (configure, registration) runs."""
    busy = [job for job in runner.jobs.values() if job.state == "running" and job.composition != CACHE_KEY]
    if busy:
        raise StudioError(
            f"wait for {busy[0].command} of {busy[0].composition} to finish before pruning the cache",
            HTTPStatus.CONFLICT,
        )
    return runner.launch(CACHE_KEY, "prune", [[runner.python, "-u", str(URBAN_ASSETS), "prune-cache", "--apply"]])


# --- HTTP -----------------------------------------------------------------------------

class StudioHandler(SimpleHTTPRequestHandler):
    runner: JobRunner

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - http.server signature
        if not self.path.startswith(("/api/jobs/", "/api/health")):  # polled
            super().log_message(format, *args)

    def _json(self, value, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path, content_type: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(path.stat().st_size))
        self.end_headers()
        with path.open("rb") as source:
            while chunk := source.read(1 << 20):
                self.wfile.write(chunk)

    def _body(self) -> object:
        length = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(length) or b"null")
        except json.JSONDecodeError as exc:
            raise StudioError(f"invalid JSON body: {exc}") from exc

    def _shutdown(self) -> None:
        """Stop this Urban Studio, as Ctrl+C would (tools/urban_studio.py stop).

        JSON only: a page elsewhere cannot send it without the browser asking
        this server first (CORS preflight), which it does not allow."""
        if (self.headers.get("Content-Type") or "").split(";")[0].strip() != "application/json":
            raise StudioError("shutdown needs a JSON request", HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
        self._json({"stopping": True, "pid": os.getpid()})
        # shutdown() waits for serve_forever, so it cannot run on this request's thread.
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def _api(self, method: str) -> None:
        url = urlparse(self.path)
        parts = [part for part in url.path.split("/") if part][1:]  # drop "api"
        query = parse_qs(url.query)
        try:
            if method == "GET" and parts == ["health"]:
                return self._json({"app": APP_NAME, "pid": os.getpid(), "port": self.server.server_address[1],
                                   "instance": os.environ.get(INSTANCE_ENV)})
            if method == "POST" and parts == ["shutdown"]:
                return self._shutdown()
            if method == "GET" and parts == ["assets"]:
                return self._json(asset_catalog())
            if method == "GET" and len(parts) == 3 and parts[0] == "assets" and parts[2] == "preview":
                return self._json(asset_preview_info(parts[1]))
            if method == "GET" and len(parts) == 4 and parts[0] == "assets" and parts[2] == "preview":
                return self._file(asset_preview_model(parts[1], parts[3]), "model/gltf-binary")
            if method == "GET" and parts == ["compositions"]:
                return self._json(list_compositions())
            if method == "GET" and len(parts) == 2 and parts[0] == "worlds":
                return self._json(world_info(parts[1]))
            if method == "POST" and len(parts) == 3 and parts[0] == "worlds" and parts[2] == "route-check":
                return self._json(check_route(parts[1], self._body()))
            if method == "GET" and len(parts) == 3 and parts[0] == "worlds" and parts[2] == "footprints":
                return self._json(world_footprints(parts[1]))
            if method == "GET" and len(parts) == 3 and parts[0] == "worlds" and parts[2] == "glb":
                return self._file(world_glb(parts[1]), "model/gltf-binary")
            if method == "GET" and len(parts) == 3 and parts[0] == "worlds" and parts[2] == "height":
                try:
                    east = float(query["east"][0])
                    north = float(query["north"][0])
                except (KeyError, IndexError, ValueError) as exc:
                    raise StudioError("height needs numeric east and north") from exc
                return self._json(world_height(parts[1], east, north))
            if method == "GET" and parts == ["cities"]:
                return self._json(city_state(self.runner))
            if method == "POST" and len(parts) == 3 and parts[:2] == ["cities", "environment-studio"]:
                return self._json(environment_studio(self.runner, parts[2]).snapshot(), HTTPStatus.ACCEPTED)
            if method == "GET" and parts == ["cache"]:
                return self._json(cache_state(self.runner))
            if method == "POST" and parts == ["cache", "prune"]:
                return self._json(prune_cache(self.runner).snapshot(), HTTPStatus.ACCEPTED)
            if method == "POST" and len(parts) == 3 and parts[0] == "worlds" and parts[2] == "route-line":
                return self._json(route_line_3d(parts[1], self._body()))
            if method == "POST" and len(parts) == 3 and parts[0] == "worlds" and parts[2] == "flight-check":
                return self._json(check_flight(parts[1], self._body()))
            if method == "GET" and parts == ["flights"]:
                return self._json(list_flights())
            if len(parts) == 2 and parts[0] == "flights":
                if method == "GET":
                    return self._json(read_flight(parts[1]))
                if method == "PUT":
                    return self._json(save_flight(parts[1], self._body()))
                if method == "DELETE":
                    return self._json(delete_flight(parts[1]))
            if method == "GET" and parts == ["scenarios"]:
                return self._json(list_scenarios())
            if len(parts) == 2 and parts[0] == "scenarios":
                if method == "GET":
                    return self._json(read_scenario(parts[1]))
                if method == "PUT":
                    return self._json(save_scenario(parts[1], self._body()))
                if method == "DELETE":
                    return self._json(delete_scenario(parts[1]))
            if len(parts) == 2 and parts[0] == "compositions":
                if method == "GET":
                    return self._json(read_composition(parts[1]))
                if method == "PUT":
                    return self._json(save_composition(parts[1], self._body()))
            if len(parts) == 3 and parts[0] == "compositions":
                if method == "GET" and parts[2] == "viewer":
                    return self._json(viewer(parts[1]))
                if method == "GET" and parts[2] == "rtf":
                    return self._json(realtime_factor(parts[1]))
                if method == "GET" and parts[2] == "plan":
                    return self._json(plan_json(parts[1]))
                if method == "POST":
                    job = self.runner.start(parts[1], parts[2])
                    return self._json(job.snapshot(), HTTPStatus.ACCEPTED)
            if method == "GET" and len(parts) == 2 and parts[0] == "jobs":
                since = int(query.get("since", ["0"])[0])
                return self._json(self.runner.get(parts[1]).snapshot(since))
            raise StudioError(f"no API {method} {url.path}", HTTPStatus.NOT_FOUND)
        except StudioError as exc:
            return self._json({"error": str(exc)}, exc.status)
        except Exception as exc:  # noqa: BLE001 - report instead of dropping the connection
            return self._json({"error": f"{type(exc).__name__}: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_GET(self) -> None:  # noqa: N802 - http.server naming
        if self.path.startswith("/api/"):
            return self._api("GET")
        return super().do_GET()

    def do_PUT(self) -> None:  # noqa: N802
        return self._api("PUT")

    def do_POST(self) -> None:  # noqa: N802
        return self._api("POST")

    def do_DELETE(self) -> None:  # noqa: N802
        return self._api("DELETE")


def make_server(port: int = DEFAULT_PORT, runner: JobRunner | None = None) -> ThreadingHTTPServer:
    handler = type("BoundStudioHandler", (StudioHandler,), {"runner": runner or JobRunner()})
    return ThreadingHTTPServer(("127.0.0.1", port), handler)


# --- Lifecycle (start / status / stop) ---------------------------------------------------

def _health(port: int, timeout: float = 1.0) -> dict | None:
    """The Urban Studio answering on port, or None (nothing, or something else)."""
    from urllib.error import URLError
    from urllib.request import urlopen

    try:
        with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=timeout) as response:
            data = json.loads(response.read())
    except (OSError, URLError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("app") == APP_NAME else None


def _state_file(state_dir: Path) -> Path:
    return state_dir / "studio.json"


def _running(state_dir: Path) -> dict | None:
    """The recorded background Urban Studio, if it is still the one answering on its port."""
    try:
        state = json.loads(_state_file(state_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    health = _health(int(state.get("port", 0)))
    return state if health and health.get("pid") == state.get("pid") else None


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def command_hint(command: str) -> str:
    """How to issue a lifecycle command from where the person is (in the
    Workspace, usually hakoniwa-business-pack)."""
    script = Path(__file__).resolve()
    try:
        script = Path(os.path.relpath(script, Path.cwd()))
    except ValueError:  # another drive on Windows
        pass
    return f"python {script.as_posix()} {command}"


def _port_in_use(port: int) -> str:
    """Why port cannot be used, and what to do."""
    health = _health(port)
    if health:
        return (f"Urban Studio is already running: http://127.0.0.1:{port}/ (pid {health['pid']}). "
                f"Open it: {command_hint('open')}, or stop it first: {command_hint('stop')}")
    return (f"port {port} is in use by another program; stop it, or pass --port "
            "(or set HAKONIWA_URBAN_PORT_URBAN_STUDIO; see docs/urban-manifest.md)")


def serve(port: int, open_browser: bool) -> int:
    """Run Urban Studio in this terminal until Ctrl+C (or stop)."""
    if not _port_free(port):
        running = _health(port)
        if running and open_browser:
            # Asked for Urban Studio in the browser: that one is it.
            url = f"http://127.0.0.1:{port}/"
            print(f"Urban Studio is already running: {url} (pid {running['pid']}); opening it")
            webbrowser.open(url)
            return 0
        print(f"ERROR: {_port_in_use(port)}", file=sys.stderr)
        return 1
    server = make_server(port)
    url = f"http://127.0.0.1:{port}/"
    print(f"Urban Studio: {url}", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def start(port: int, open_browser: bool, state_dir: Path = STATE_DIR) -> int:
    """Run Urban Studio in the background (its pid, port, and log under state_dir)."""
    import secrets

    running = _running(state_dir)
    if running:
        print(f"Urban Studio is already running: {running['url']} (pid {running['pid']})")
        if open_browser:
            webbrowser.open(running["url"])
        return 0
    if not _port_free(port):
        print(f"ERROR: {_port_in_use(port)}", file=sys.stderr)
        return 1
    state_dir.mkdir(parents=True, exist_ok=True)
    log = state_dir / "studio.log"
    instance = secrets.token_hex(16)
    options: dict = {"cwd": ROOT, "stdin": subprocess.DEVNULL, "env": {**os.environ, INSTANCE_ENV: instance}}
    if os.name == "nt":
        options["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    with log.open("ab") as output:
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "serve", "--port", str(port)],
                                   stdout=output, stderr=subprocess.STDOUT, **options)
    deadline = time.monotonic() + START_TIMEOUT_SEC
    while True:
        health = _health(port, timeout=0.5)
        if health and health.get("instance") == instance:
            break
        if process.poll() is not None:
            print(f"ERROR: Urban Studio exited at start (exit {process.returncode}); see {log}", file=sys.stderr)
            return 1
        if time.monotonic() > deadline:
            process.kill()
            print(f"ERROR: Urban Studio did not answer within {START_TIMEOUT_SEC:.0f} s; see {log}", file=sys.stderr)
            return 1
        time.sleep(0.2)
    url = f"http://127.0.0.1:{port}/"
    state = {"app": APP_NAME, "pid": health["pid"], "port": port, "url": url, "log": str(log),
             "started": time.strftime("%Y-%m-%dT%H:%M:%S")}
    _state_file(state_dir).write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    print(f"Urban Studio started: {url} (pid {health['pid']})")
    print(f"  open it with: {command_hint('open')}")
    print(f"  stop it with: {command_hint('stop')}")
    if open_browser:
        webbrowser.open(url)
    return 0


def status(state_dir: Path = STATE_DIR, port: int = DEFAULT_PORT) -> int:
    running = _running(state_dir)
    if running:
        print(f"Urban Studio is running: {running['url']} (pid {running['pid']}, since {running['started']})")
        return 0
    health = _health(port)
    if health:
        print(f"Urban Studio is running in a terminal: http://127.0.0.1:{port}/ (pid {health['pid']}); "
              f"stop it with: {command_hint('stop')}")
        return 0
    print(f"Urban Studio is not running (start it with: {command_hint('start')})")
    return 1


def open_studio(state_dir: Path = STATE_DIR, port: int = DEFAULT_PORT) -> int:
    """Open the running Urban Studio in the browser: the background one, else the one on port."""
    running = _running(state_dir)
    url = running["url"] if running else f"http://127.0.0.1:{port}/" if _health(port) else None
    if not url:
        print(f"Urban Studio is not running (start it with: {command_hint('start --open-browser')})", file=sys.stderr)
        return 1
    print(f"Opening Urban Studio: {url}")
    webbrowser.open(url)
    return 0


def stop(state_dir: Path = STATE_DIR, port: int = DEFAULT_PORT) -> int:
    """Stop the Urban Studio, as Ctrl+C would: the background one this tool
    started, else the one answering on port (started in a terminal)."""
    import signal
    from urllib.error import URLError
    from urllib.request import Request, urlopen

    running = _running(state_dir)
    port = int(running["port"]) if running else port
    health = _health(port)
    if not health:
        _state_file(state_dir).unlink(missing_ok=True)
        print("Urban Studio is not running")
        return 0
    pid = int(health["pid"])
    try:
        urlopen(Request(f"http://127.0.0.1:{port}/api/shutdown", data=b"{}", method="POST",
                        headers={"Content-Type": "application/json"}), timeout=2).read()
    except (OSError, URLError):
        pass
    deadline = time.monotonic() + STOP_TIMEOUT_SEC
    while time.monotonic() < deadline and not _port_free(port):
        time.sleep(0.2)
    if not _port_free(port):
        if not running:
            print(f"ERROR: Urban Studio on port {port} (pid {pid}) did not stop; press Ctrl+C in its terminal",
                  file=sys.stderr)
            return 1
        # The background one did not stop by itself: end the process (on Windows SIGTERM terminates it).
        try:
            os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
        except OSError:
            pass
    _state_file(state_dir).unlink(missing_ok=True)
    print(f"Urban Studio stopped (pid {pid})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", nargs="?", default="serve", choices=("serve", "start", "status", "open", "stop"),
                        help="serve (default): in this terminal; start: in the background; status; "
                             "open: the running one in the browser; stop")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "start":
        return start(args.port, args.open_browser)
    if args.command == "status":
        return status(port=args.port)
    if args.command == "open":
        return open_studio(port=args.port)
    if args.command == "stop":
        return stop(port=args.port)
    return serve(args.port, args.open_browser)


if __name__ == "__main__":
    raise SystemExit(main())
