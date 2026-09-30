#!/usr/bin/env python3
"""Build one shared MuJoCo City World containing a fleet of drones.

Urban Mobility owns placing Drone Core drones in a City World: the fleet MJCF
(Drone Core's multi-drone generator and templates) merged with the City
collision World, per-process MJBs, the automatic spawn layout, and the fleet
configuration the Drone service reads. It materializes into the generic
Business Pack ``drone-fleet-single-host`` workspace.

Show-specific planning (formations, LED, the show runner) stays in
hakoniwa-drone-show. This module started as a copy of that repository's
tools/recipe/drone_fleet_mujoco_city.py (a4b349e), so Urban does not import
drone-show tools.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib.util
import json
import math
from concurrent.futures import ThreadPoolExecutor
import os
import shutil
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


ROOT = Path(__file__).absolute().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import mujoco_c_layout  # noqa: E402
import urban_manifest  # noqa: E402

BUSINESS_PACK_ROOT = urban_manifest.business_pack()  # $HAKONIWA_WORKSPACE_ROOT
BUSINESS_PACK_TOOLS = BUSINESS_PACK_ROOT / "tools"
BUSINESS_PACK_RECIPE_TOOLS = BUSINESS_PACK_TOOLS / "recipe"
for search_path in (BUSINESS_PACK_RECIPE_TOOLS, BUSINESS_PACK_TOOLS):
    if str(search_path) not in sys.path:
        sys.path.insert(0, str(search_path))

from mujoco_model_compiler import (  # type: ignore[import-not-found]
    MujocoCompileError,
    compile_mujoco_xml,
    find_mujoco_library,
)


def _load_business_pack_city_drone():
    script = BUSINESS_PACK_RECIPE_TOOLS / "drone_shibuya_gamepad.py"
    spec = importlib.util.spec_from_file_location(
        "business_pack_drone_shibuya_gamepad", script
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Business Pack City helper: {script}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


city_drone = _load_business_pack_city_drone()


DEFAULT_DRONE_ROOT = ROOT.parent / "hakoniwa-drone-core"
DEFAULT_OUTPUT = (
    urban_manifest.work_dir()
    / "recipes"
    / "drone-fleet-single-host"
    / "config"
    / "drone"
    / "mujoco-city-fleet"
)
# Compiled process models, keyed by their MJCF and the MuJoCo library: a
# configure that produces the same model reuses the MJB instead of spending
# minutes on the City again.
MJB_CACHE_DIR = urban_manifest.work_dir() / "cache" / "mujoco-fleet-mjb"
# Process models compile concurrently (MuJoCo releases the GIL); each City
# compile needs gigabytes, so only a few at a time.
MAX_PARALLEL_COMPILES = 4
DRONE_COLLISION_MASK = {"contype": "2", "conaffinity": "1"}
MODEL_SIZE = {"nstack": "40000000", "nconmax": "500000"}
DRONE_BODY_PATTERN = re.compile(r"d[1-9][0-9]*_b_drone_base")
LANDING_GEAR_CLEARANCE_M = 0.50
SPAWN_CLEARANCE_RADIUS_M = 0.75
SPAWN_DEFAULT_SEPARATION_M = 1.0
SPAWN_MIN_SEPARATION_M = 0.0
SPAWN_MAX_SEPARATION_M = 5.0
SURFACE_MATCH_TOLERANCE_M = 0.15
# A takeoff footprint must be nearly level.  Larger local height differences
# leave some landing gear unsupported and can flip the vehicle as it settles.
MAX_SPAWN_SLOPE_DELTA_M = 0.005
SPAWN_AREA_MARGIN_M = 3.0
SPAWN_AREA_MAX_HEIGHT_DELTA_M = 0.05
SPAWN_AREA_SEARCH_RADIUS_M = 100.0
SPAWN_AREA_SEARCH_STEP_M = 5.0
SPAWN_AREA_SAMPLE_STEP_M = 1.0
ALTITUDE_MODES = {"route-clearance", "city-max-clearance"}


class FleetMujocoError(RuntimeError):
    pass


def _disable_runtime_csv_logging(type_config: dict[str, Any]) -> None:
    """Disable per-drone CSV logging in generated Drone Show configurations."""
    simulation = type_config.get("simulation")
    if not isinstance(simulation, dict):
        raise FleetMujocoError("MuJoCo Drone type config has no simulation object")
    logging = simulation.get("logging")
    if logging is None:
        logging = {}
        simulation["logging"] = logging
    if not isinstance(logging, dict):
        raise FleetMujocoError(
            "MuJoCo Drone type config simulation.logging must be an object"
        )
    logging["mode"] = "none"


def _validate_spawn_spacing(spawn_spacing_m: float) -> float:
    value = float(spawn_spacing_m)
    if (
        not math.isfinite(value)
        or value <= SPAWN_MIN_SEPARATION_M
        or value > SPAWN_MAX_SEPARATION_M
    ):
        raise FleetMujocoError(
            "spawn_spacing_m must be a finite value in "
            f"({SPAWN_MIN_SEPARATION_M}, {SPAWN_MAX_SEPARATION_M}]"
        )
    return value


def _city_position_to_fleet_ned(
    x_m: float, y_m: float, altitude_m: float
) -> list[float]:
    """Convert City/MuJoCo local XYZ to Drone Core's NED position contract."""
    return [float(x_m), -float(y_m), -float(altitude_m)]


def _resolve_launch_area(
    launch_area: dict[str, Any] | None,
) -> tuple[str, tuple[float, float, float], float]:
    value = (
        {"mode": "auto", "offset_m": [0.0, 0.0, 0.0]}
        if launch_area is None
        else launch_area
    )
    if not isinstance(value, dict) or value.get("mode") not in {"auto", "manual"}:
        raise FleetMujocoError("launch_area.mode must be auto or manual")
    offset = value.get("offset_m", [0.0, 0.0, 0.0])
    if not isinstance(offset, (list, tuple)) or len(offset) != 3:
        raise FleetMujocoError("launch_area.offset_m must contain [x, y, z]")
    try:
        resolved = tuple(float(component) for component in offset)
    except (TypeError, ValueError) as exc:
        raise FleetMujocoError("launch_area.offset_m must be numeric") from exc
    if any(not math.isfinite(component) for component in resolved):
        raise FleetMujocoError("launch_area.offset_m must be finite")
    if resolved[2] < 0.0 or resolved[2] > 100.0:
        raise FleetMujocoError("launch_area offset z must be in [0, 100]")
    if value["mode"] == "auto" and resolved != (0.0, 0.0, 0.0):
        raise FleetMujocoError("auto launch_area cannot have a non-zero offset")
    search_radius_m = float(
        value.get(
            "search_radius_m",
            SPAWN_AREA_SEARCH_RADIUS_M if value["mode"] == "auto" else 0.0,
        )
    )
    if not math.isfinite(search_radius_m) or not 0.0 <= search_radius_m <= 500.0:
        raise FleetMujocoError("launch_area.search_radius_m must be in [0, 500]")
    if value["mode"] == "manual" and search_radius_m != 0.0:
        raise FleetMujocoError("manual launch_area cannot have a search radius")
    return str(value["mode"]), resolved, search_radius_m


def _city_visual_max_height(city_receipt_path: Path) -> tuple[float, Path]:
    """Return the highest visual-building Z in the City World's local frame.

    GLB uses Y-up while the Physics World uses Z-up, so the GLB receipt's
    maximum Y is the corresponding local altitude.
    """
    try:
        city_receipt = json.loads(city_receipt_path.read_text(encoding="utf-8"))
        buildings_xml = Path(city_receipt["components"]["buildings_xml"]).resolve()
        glb_receipt_path = buildings_xml.with_name("buildings-glb-receipt.json")
        glb_receipt = json.loads(glb_receipt_path.read_text(encoding="utf-8"))
        maximum = glb_receipt["bounds"]["max"]
        height_m = float(maximum[1])
    except (OSError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise FleetMujocoError(
            "city-max-clearance requires a valid buildings-glb-receipt.json: "
            f"{exc}"
        ) from exc
    if not math.isfinite(height_m):
        raise FleetMujocoError("City visual maximum building height is not finite")
    return height_m, glb_receipt_path


# Rays start this far above the World's highest geom (as tools/world_height.py).
RAY_START_MARGIN_M = 1.0


class _MujocoRayScene:
    """Query the highest collision surface without depending on mujoco-python."""

    def __init__(self, xml_path: Path, library_path: Path):
        self.xml_path = xml_path.resolve()
        self.library_path = library_path.resolve()
        self.library = ctypes.CDLL(str(self.library_path))
        self.model: int | None = None
        self.data: int | None = None
        self.ray_origin_z = 0.0

    def __enter__(self) -> "_MujocoRayScene":
        lib = self.library
        lib.mj_loadXML.argtypes = [
            ctypes.c_char_p,
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_int,
        ]
        lib.mj_loadXML.restype = ctypes.c_void_p
        lib.mj_makeData.argtypes = [ctypes.c_void_p]
        lib.mj_makeData.restype = ctypes.c_void_p
        lib.mj_forward.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        # MuJoCo 3.13 (the Drone Core runtime) adds a trailing normal[3]
        # output. Passing NULL for it is required there: without it the
        # callee writes through an undefined pointer (the process dies on
        # Windows). Older MuJoCo ignores the extra argument.
        lib.mj_ray.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_double),
            ctypes.POINTER(ctypes.c_double),
            ctypes.c_void_p,
            ctypes.c_ubyte,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_int),
            ctypes.c_void_p,
        ]
        lib.mj_ray.restype = ctypes.c_double
        lib.mj_deleteData.argtypes = [ctypes.c_void_p]
        lib.mj_deleteModel.argtypes = [ctypes.c_void_p]
        error = ctypes.create_string_buffer(4096)
        self.model = lib.mj_loadXML(
            os.fsencode(self.xml_path), None, error, len(error)
        )
        if not self.model:
            detail = error.value.decode("utf-8", errors="replace")
            raise FleetMujocoError(
                f"MuJoCo could not load ray-query model {self.xml_path}: {detail}"
            )
        self.data = lib.mj_makeData(self.model)
        if not self.data:
            lib.mj_deleteModel(self.model)
            self.model = None
            raise FleetMujocoError(
                f"MuJoCo could not allocate ray-query data for {self.xml_path}"
            )
        lib.mj_forward(self.model, self.data)
        try:
            self.ray_origin_z = self._highest_point() + RAY_START_MARGIN_M
        except mujoco_c_layout.MujocoLayoutError as exc:
            self.__exit__()
            raise FleetMujocoError(
                f"cannot read the World's geoms from the MuJoCo library: {exc}"
            ) from exc
        return self

    def _highest_point(self) -> float:
        """The top of the highest geom: each geom's local bounding box
        (geom_aabb: centre, half sizes) turned into the world by its pose. A
        plane's bounding box is unbounded in its own plane; its height is its
        position. Rays start just above it: MuJoCo 3.13's ray-mesh test misses
        a thin mesh (a 2 cm road slab) from a few hundred metres away."""
        layout = mujoco_c_layout.Layout(
            self.library,
            self.library_path,
            model_until="geom_aabb",
            data_until="geom_xmat",
        )
        model = layout.model.from_address(self.model)
        data = layout.data.from_address(self.data)
        count = model.ngeom
        if count == 0:
            return 0.0
        geom_type = mujoco_c_layout.array(model, "geom_type", count, ctypes.c_int)
        aabb = mujoco_c_layout.array(model, "geom_aabb", count * 6)
        xpos = mujoco_c_layout.array(data, "geom_xpos", count * 3)
        xmat = mujoco_c_layout.array(data, "geom_xmat", count * 9)
        plane = layout.constants["mjGEOM_PLANE"]
        highest = -math.inf
        for geom in range(count):
            top = xpos[3 * geom + 2]
            if geom_type[geom] != plane:
                # Row 2 of the rotation gives the world z of a local vector.
                row = xmat[9 * geom + 6:9 * geom + 9]
                box = aabb[6 * geom:6 * geom + 6]
                top += sum(row[i] * box[i] + abs(row[i]) * box[3 + i] for i in range(3))
            highest = max(highest, top)
        return highest

    def __exit__(self, *_args: object) -> None:
        if self.data:
            self.library.mj_deleteData(self.data)
            self.data = None
        if self.model:
            self.library.mj_deleteModel(self.model)
            self.model = None

    def height(self, x_m: float, y_m: float) -> float:
        if not self.model or not self.data:
            raise FleetMujocoError("MuJoCo ray-query scene is not open")
        ray_origin_z = self.ray_origin_z
        point = (ctypes.c_double * 3)(x_m, y_m, ray_origin_z)
        direction = (ctypes.c_double * 3)(0.0, 0.0, -1.0)
        geom_id = ctypes.c_int(-1)
        distance = self.library.mj_ray(
            self.model,
            self.data,
            point,
            direction,
            None,
            1,
            -1,
            ctypes.byref(geom_id),
            None,
        )
        if distance < 0.0 or geom_id.value < 0:
            raise FleetMujocoError(
                f"no collision surface below local point ({x_m:.3f}, {y_m:.3f})"
            )
        return ray_origin_z - float(distance)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _candidate_spawn_centers(
    half_extent_m: dict[str, Any], *, spacing_m: float = SPAWN_DEFAULT_SEPARATION_M,
    center_m: tuple[float, float] = (0.0, 0.0),
    max_radius_m: float = 40.0,
) -> list[tuple[float, float]]:
    spacing_m = max(float(spacing_m), SPAWN_CLEARANCE_RADIUS_M)
    north_south = float(half_extent_m.get("north_south", 0.0))
    east_west = float(half_extent_m.get("east_west", 0.0))
    limit_x = max(0.0, north_south - SPAWN_CLEARANCE_RADIUS_M)
    limit_y = max(0.0, east_west - SPAWN_CLEARANCE_RADIUS_M)
    center_x, center_y = center_m
    available_x = limit_x - abs(center_x)
    available_y = limit_y - abs(center_y)
    if available_x < 0.0 or available_y < 0.0:
        return []
    max_ring = int(min(max(available_x, available_y), max_radius_m) // spacing_m)
    candidates: list[tuple[float, float]] = [(center_x, center_y)]
    for ring in range(1, max_ring + 1):
        radius = ring * spacing_m
        ring_points: list[tuple[float, float]] = [
            (radius, 0.0),
            (-radius, 0.0),
            (0.0, radius),
            (0.0, -radius),
        ]
        for offset in range(1, ring):
            value = offset * spacing_m
            other = radius - value
            ring_points.extend(
                (
                    (value, other),
                    (-value, other),
                    (value, -other),
                    (-value, -other),
                )
            )
        for x_offset_m, y_offset_m in ring_points:
            x_m = center_x + x_offset_m
            y_m = center_y + y_offset_m
            if abs(x_m) <= limit_x and abs(y_m) <= limit_y:
                candidates.append((x_m, y_m))
    return candidates


def _clearance_probe_points(x_m: float, y_m: float) -> list[tuple[float, float]]:
    diagonal = SPAWN_CLEARANCE_RADIUS_M / math.sqrt(2.0)
    offsets = (
        (0.0, 0.0),
        (SPAWN_CLEARANCE_RADIUS_M, 0.0),
        (-SPAWN_CLEARANCE_RADIUS_M, 0.0),
        (0.0, SPAWN_CLEARANCE_RADIUS_M),
        (0.0, -SPAWN_CLEARANCE_RADIUS_M),
        (diagonal, diagonal),
        (-diagonal, diagonal),
        (diagonal, -diagonal),
        (-diagonal, -diagonal),
    )
    return [(x_m + dx, y_m + dy) for dx, dy in offsets]


def _grid_spawn_offsets(
    drone_count: int, spawn_spacing_m: float
) -> list[tuple[float, float]]:
    if drone_count < 1:
        return []
    # A zero user spacing means the most compact physical layout, not that all
    # bodies occupy the same coordinates.
    spacing_m = max(spawn_spacing_m, SPAWN_CLEARANCE_RADIUS_M)
    columns = math.ceil(math.sqrt(drone_count))
    rows = math.ceil(drone_count / columns)
    width_m = (columns - 1) * spacing_m
    depth_m = (rows - 1) * spacing_m
    offsets: list[tuple[float, float]] = []
    for row in range(rows):
        for column in range(columns):
            if len(offsets) == drone_count:
                return offsets
            offsets.append(
                (
                    column * spacing_m - width_m / 2.0,
                    row * spacing_m - depth_m / 2.0,
                )
            )
    return offsets


def _area_sample_coordinates(
    placements: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    min_x = min(x for x, _ in placements) - SPAWN_AREA_MARGIN_M
    max_x = max(x for x, _ in placements) + SPAWN_AREA_MARGIN_M
    min_y = min(y for _, y in placements) - SPAWN_AREA_MARGIN_M
    max_y = max(y for _, y in placements) + SPAWN_AREA_MARGIN_M
    columns = math.ceil((max_x - min_x) / SPAWN_AREA_SAMPLE_STEP_M)
    rows = math.ceil((max_y - min_y) / SPAWN_AREA_SAMPLE_STEP_M)
    return [
        (
            min_x + column * (max_x - min_x) / max(columns, 1),
            min_y + row * (max_y - min_y) / max(rows, 1),
        )
        for row in range(rows + 1)
        for column in range(columns + 1)
    ]


def _select_safe_spawn_points(
    *,
    drone_count: int,
    half_extent_m: dict[str, Any],
    terrain_height: Any,
    city_height: Any,
    spawn_spacing_m: float = SPAWN_DEFAULT_SEPARATION_M,
    center_m: tuple[float, float] = (0.0, 0.0),
    search_nearby: bool = True,
    search_radius_m: float = SPAWN_AREA_SEARCH_RADIUS_M,
) -> list[dict[str, float]]:
    spawn_spacing_m = _validate_spawn_spacing(spawn_spacing_m)
    offsets = _grid_spawn_offsets(drone_count, spawn_spacing_m)
    north_south = float(half_extent_m.get("north_south", 0.0))
    east_west = float(half_extent_m.get("east_west", 0.0))
    if search_nearby:
        area_centers = _candidate_spawn_centers(
            half_extent_m,
            spacing_m=SPAWN_AREA_SEARCH_STEP_M,
            center_m=center_m,
            max_radius_m=search_radius_m,
        )
    else:
        area_centers = [center_m]

    for area_center_x, area_center_y in area_centers:
        if math.hypot(
            area_center_x - center_m[0], area_center_y - center_m[1]
        ) > search_radius_m:
            continue
        placements = [
            (area_center_x + dx, area_center_y + dy) for dx, dy in offsets
        ]
        samples = _area_sample_coordinates(placements)
        if any(
            abs(x_m) > north_south or abs(y_m) > east_west
            for x_m, y_m in samples
        ):
            continue
        terrain = [float(terrain_height(x, y)) for x, y in samples]
        city = [float(city_height(x, y)) for x, y in samples]
        if any(
            city_z - terrain_z > SURFACE_MATCH_TOLERANCE_M
            for terrain_z, city_z in zip(terrain, city)
        ):
            continue
        if max(terrain) - min(terrain) > SPAWN_AREA_MAX_HEIGHT_DELTA_M:
            continue

        selected: list[dict[str, float]] = []
        area_is_safe = True
        for x_m, y_m in placements:
            probes = _clearance_probe_points(x_m, y_m)
            local_terrain = [float(terrain_height(x, y)) for x, y in probes]
            local_city = [float(city_height(x, y)) for x, y in probes]
            if (
                max(local_terrain) - min(local_terrain)
                > MAX_SPAWN_SLOPE_DELTA_M
                or any(
                    city_z - terrain_z > SURFACE_MATCH_TOLERANCE_M
                    for terrain_z, city_z in zip(local_terrain, local_city)
                )
            ):
                area_is_safe = False
                break
            selected.append(
                {
                    "x_m": x_m,
                    "y_m": y_m,
                    "terrain_height_m": local_terrain[0],
                    "surface_height_m": max(local_city),
                }
            )
        if area_is_safe:
            return selected

    raise FleetMujocoError(
        f"could not fit a level launch grid for {drone_count} drones near "
        f"({center_m[0]:.3f}, {center_m[1]:.3f}) with "
        f"{SPAWN_AREA_MARGIN_M:g} m safety margins; use manual "
        "launch_area.offset_m to select a more open area"
    )


def _manual_spawn_points(
    *,
    drone_count: int,
    half_extent_m: dict[str, Any],
    terrain_height: Any,
    city_height: Any,
    spawn_spacing_m: float,
    center_m: tuple[float, float],
) -> list[dict[str, float]]:
    offsets = _grid_spawn_offsets(
        drone_count, _validate_spawn_spacing(spawn_spacing_m)
    )
    placements = [(center_m[0] + dx, center_m[1] + dy) for dx, dy in offsets]
    north_south = float(half_extent_m.get("north_south", 0.0))
    east_west = float(half_extent_m.get("east_west", 0.0))
    if any(
        abs(x_m) > north_south or abs(y_m) > east_west
        for x_m, y_m in placements
    ):
        raise FleetMujocoError(
            "manual launch grid extends beyond the City World bounds"
        )
    selected: list[dict[str, float]] = []
    for x_m, y_m in placements:
        terrain_z = float(terrain_height(x_m, y_m))
        city_z = float(city_height(x_m, y_m))
        selected.append(
            {
                "x_m": x_m,
                "y_m": y_m,
                "terrain_height_m": terrain_z,
                "surface_height_m": max(terrain_z, city_z),
            }
        )
    return selected


def _formation_targets(
    show: dict[str, Any], *, show_path: Path | None = None
) -> list[tuple[float, float]]:
    options = show.get("options", {})
    center = options.get("center", [0.0, 0.0, 0.0])
    scale = float(options.get("scale", 1.0))
    targets: list[tuple[float, float]] = []
    formations = show.get("formations", {})
    if isinstance(formations, dict):
        values = list(formations.values())
    else:
        values = []
    if show_path is not None:
        for entry in show.get("formation_files", []):
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                continue
            formation_path = (show_path.parent / entry["path"]).resolve()
            if not formation_path.is_file():
                raise FleetMujocoError(
                    f"formation file referenced by City fleet is missing: {formation_path}"
                )
            values.append(json.loads(formation_path.read_text(encoding="utf-8")))
    for formation in values:
        if not isinstance(formation, dict):
            continue
        for point in formation.get("points", []):
            if isinstance(point, list) and len(point) >= 2:
                targets.append(
                    (
                        float(center[0]) + scale * float(point[0]),
                        float(center[1]) + scale * float(point[1]),
                    )
                )
    return targets


def _point_span(points: list[list[float]], axis: int) -> float:
    values = [float(point[axis]) for point in points if len(point) > axis]
    return max(values) - min(values) if values else 0.0


def _sample_closed_outline(
    vertices: list[tuple[float, float]], count: int
) -> list[list[float]]:
    """Sample a closed 2D outline at approximately equal arc-length."""
    if count < 1 or len(vertices) < 3:
        raise FleetMujocoError("closed outline requires >= 3 vertices and count >= 1")
    segments: list[tuple[tuple[float, float], tuple[float, float], float]] = []
    perimeter = 0.0
    for index, start in enumerate(vertices):
        end = vertices[(index + 1) % len(vertices)]
        length = math.hypot(end[0] - start[0], end[1] - start[1])
        if length <= 0.0:
            continue
        segments.append((start, end, length))
        perimeter += length
    if perimeter <= 0.0:
        raise FleetMujocoError("closed outline has zero perimeter")
    points: list[list[float]] = []
    segment_index = 0
    traversed = 0.0
    for point_index in range(count):
        distance = perimeter * point_index / count
        while (
            segment_index + 1 < len(segments)
            and traversed + segments[segment_index][2] < distance
        ):
            traversed += segments[segment_index][2]
            segment_index += 1
        start, end, length = segments[segment_index]
        ratio = min(1.0, max(0.0, (distance - traversed) / length))
        points.append(
            [
                start[0] + (end[0] - start[0]) * ratio,
                start[1] + (end[1] - start[1]) * ratio,
                0.0,
            ]
        )
    return points


def _ellipse_vertices(
    center_x: float,
    center_y: float,
    radius_x: float,
    radius_y: float,
    *,
    samples: int = 180,
) -> list[tuple[float, float]]:
    return [
        (
            center_x + radius_x * math.cos(math.tau * index / samples),
            center_y + radius_y * math.sin(math.tau * index / samples),
        )
        for index in range(samples)
    ]


def _sample_picture_components(
    components: list[tuple[list[tuple[float, float]], float]], count: int
) -> list[list[float]]:
    """Distribute drones across independent outlines while preserving total count."""
    if count < len(components) * 3:
        raise FleetMujocoError("picture formation has too few drones")
    total_weight = sum(weight for _, weight in components)
    allocations = [
        max(3, int(count * weight / total_weight)) for _, weight in components
    ]
    while sum(allocations) < count:
        index = max(
            range(len(components)),
            key=lambda item: count * components[item][1] / total_weight - allocations[item],
        )
        allocations[index] += 1
    while sum(allocations) > count:
        index = max(range(len(components)), key=lambda item: allocations[item])
        allocations[index] -= 1
    points: list[list[float]] = []
    for (vertices, _), allocation in zip(components, allocations):
        points.extend(_sample_closed_outline(vertices, allocation))
    return points


def _face_features() -> list[tuple[list[tuple[float, float]], float]]:
    return [
        (_ellipse_vertices(-0.27, -0.05, 0.065, 0.105), 0.07),
        (_ellipse_vertices(0.27, -0.05, 0.065, 0.105), 0.07),
        ([(-0.14, -0.30), (0.0, -0.39), (0.14, -0.30)], 0.08),
    ]


def _chiikawa_picture(count: int) -> list[list[float]]:
    return _sample_picture_components(
        [
            (_ellipse_vertices(0.0, -0.08, 0.82, 0.70), 0.60),
            (_ellipse_vertices(-0.61, 0.48, 0.25, 0.27), 0.16),
            (_ellipse_vertices(0.61, 0.48, 0.25, 0.27), 0.16),
            *_face_features(),
        ],
        count,
    )


def _hachiware_picture(count: int) -> list[list[float]]:
    head = [
        (-0.82, 0.12),
        (-0.78, 0.68),
        (-0.39, 0.47),
        (0.0, 0.68),
        (0.39, 0.47),
        (0.78, 0.68),
        (0.82, 0.12),
        (0.70, -0.51),
        (0.0, -0.76),
        (-0.70, -0.51),
    ]
    forehead_patch = [
        (-0.58, 0.30),
        (-0.30, 0.10),
        (0.0, 0.34),
        (0.30, 0.10),
        (0.58, 0.30),
    ]
    return _sample_picture_components(
        [(head, 0.72), (forehead_patch, 0.16), *_face_features()], count
    )


def _usagi_picture(count: int) -> list[list[float]]:
    return _sample_picture_components(
        [
            (_ellipse_vertices(0.0, -0.24, 0.80, 0.62), 0.58),
            (_ellipse_vertices(-0.34, 0.62, 0.20, 0.58), 0.19),
            (_ellipse_vertices(0.34, 0.62, 0.20, 0.58), 0.19),
            *_face_features(),
        ],
        count,
    )


def _materialize_three_phase_city_show(
    show: dict[str, Any],
    *,
    show_path: Path,
    drone_count: int,
) -> None:
    """Replace the word with three simple character-face formations."""
    source_timeline = show.get("timeline")
    if not isinstance(source_timeline, list) or not source_timeline:
        raise FleetMujocoError("generated HAKONIWA timeline is missing")
    source_step = source_timeline[0]
    if not isinstance(source_step, dict):
        raise FleetMujocoError("generated HAKONIWA timeline step is invalid")
    try:
        transition_sec = float(source_step["duration_sec"])
        hold_sec = float(source_step.get("hold_sec", 0.0))
    except (KeyError, TypeError, ValueError) as exc:
        raise FleetMujocoError("generated HAKONIWA timing is invalid") from exc
    if (
        not math.isfinite(transition_sec)
        or not math.isfinite(hold_sec)
        or transition_sec <= 0.0
        or hold_sec < 0.0
    ):
        raise FleetMujocoError("generated HAKONIWA timing is out of range")
    entries = show.get("formation_files")
    if not isinstance(entries, list) or not entries:
        raise FleetMujocoError("generated HAKONIWA formation is missing")
    word_path = (show_path.parent / entries[0]["path"]).resolve()
    word = json.loads(word_path.read_text(encoding="utf-8"))
    word_points = word.get("points")
    if not isinstance(word_points, list) or len(word_points) != drone_count:
        raise FleetMujocoError("generated HAKONIWA point count is invalid")
    word_long_span = max(_point_span(word_points, 0), _point_span(word_points, 1))
    word_short_span = min(_point_span(word_points, 0), _point_span(word_points, 1))

    target_span = max(word_short_span * 2.0, word_long_span * 0.55)
    if drone_count == 1:
        # A one-vehicle Fleet is a useful City integration checkpoint even
        # though it cannot draw a multi-outline character. Preserve the one
        # generated point for each compatibility phase; the caller may replace
        # the Show runner with direct Fleet RPC control.
        specifications = tuple(
            (formation_id, [[0.0, 0.0, 0.0]], "single-drone-city-checkpoint")
            for formation_id in ("CHIIKAWA", "HACHIWARE", "USAGI")
        )
    else:
        specifications = (
            ("CHIIKAWA", _chiikawa_picture(drone_count), "generated-chiikawa-face"),
            ("HACHIWARE", _hachiware_picture(drone_count), "generated-hachiware-face"),
            ("USAGI", _usagi_picture(drone_count), "generated-usagi-face"),
        )
    generated_entries = []
    for formation_id, sampled, source_description in specifications:
        source_span = max(_point_span(sampled, 0), _point_span(sampled, 1))
        if source_span <= 0.0 and drone_count != 1:
            raise FleetMujocoError(f"formation template has zero span: {formation_id}")
        scale = target_span / source_span if source_span > 0.0 else 1.0
        for point in sampled:
            point[0] *= scale
            point[1] *= scale
            point[2] *= scale
        output_name = f"formation-{formation_id}.json"
        output_path = show_path.parent / "formations" / output_name
        output_path.write_text(
            json.dumps(
                {
                    "id": formation_id,
                    "points": sampled,
                    "derived_from": source_description,
                    "resampling": "equal-arc-length-per-component",
                    "source_point_count": drone_count,
                    "target_point_count": drone_count,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        generated_entries.append(
            {"id": formation_id, "path": f"formations/{output_name}"}
        )

    show["formation_files"] = generated_entries
    show["timeline"] = [
        {
            "formation": formation_id,
            "duration_sec": transition_sec,
            "hold_sec": hold_sec,
        }
        for formation_id in ("CHIIKAWA", "HACHIWARE", "USAGI")
    ]
    show.setdefault("meta", {})["city_show_phases"] = 3
    show_path.write_text(json.dumps(show, indent=2) + "\n", encoding="utf-8")


def _rotate_formation_files(
    show: dict[str, Any],
    *,
    show_path: Path,
    rotation_deg: float,
    tilt_deg: float = 0.0,
) -> None:
    for entry in show.get("formation_files", []):
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            continue
        formation_path = (show_path.parent / entry["path"]).resolve()
        payload = json.loads(formation_path.read_text(encoding="utf-8"))
        previous_rotation_deg = float(
            payload.get("rotation_deg_clockwise", 0.0)
        )
        previous_tilt_deg = float(payload.get("audience_tilt_deg", 0.0))
        if math.isclose(previous_rotation_deg, rotation_deg) and math.isclose(
            previous_tilt_deg, tilt_deg
        ):
            continue
        if not math.isclose(previous_tilt_deg, 0.0):
            raise FleetMujocoError(
                "cannot change an already materialized audience tilt; rerun configure"
            )
        delta_deg = rotation_deg - previous_rotation_deg
        radians = math.radians(delta_deg)
        cosine = math.cos(radians)
        sine = math.sin(radians)
        points = payload.get("points")
        if not isinstance(points, list):
            continue
        for point in points:
            if not isinstance(point, list) or len(point) < 2:
                continue
            x_m, y_m = float(point[0]), float(point[1])
            point[0] = cosine * x_m + sine * y_m
            point[1] = -sine * x_m + cosine * y_m
        if not math.isclose(tilt_deg, 0.0):
            tilt_radians = math.radians(tilt_deg)
            tilt_cosine = math.cos(tilt_radians)
            tilt_sine = math.sin(tilt_radians)
            pivot_x = min(float(point[0]) for point in points if len(point) >= 3)
            for point in points:
                if not isinstance(point, list) or len(point) < 3:
                    continue
                offset_x = float(point[0]) - pivot_x
                source_z = float(point[2])
                point[0] = pivot_x + tilt_cosine * offset_x - tilt_sine * source_z
                point[2] = tilt_sine * offset_x + tilt_cosine * source_z
        payload["rotation_deg_clockwise"] = rotation_deg
        payload["audience_tilt_deg"] = tilt_deg
        payload["audience_tilt_axis"] = "ROS-Y after map rotation"
        formation_path.write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )


def _path_points(
    start: tuple[float, float], end: tuple[float, float], spacing_m: float = 0.5
) -> list[tuple[float, float]]:
    distance = math.dist(start, end)
    divisions = max(1, math.ceil(distance / spacing_m))
    return [
        (
            start[0] + (end[0] - start[0]) * index / divisions,
            start[1] + (end[1] - start[1]) * index / divisions,
        )
        for index in range(divisions + 1)
    ]


def _formation_clearance_points(
    targets: list[tuple[float, float]],
) -> set[tuple[float, float]]:
    points: set[tuple[float, float]] = set()
    for x_m, y_m in targets:
        points.update(_clearance_probe_points(x_m, y_m))
    return points


def _load_generator(drone_root: Path):
    script = drone_root / "tools" / "gen_mujoco_multidrone_xml.py"
    if not script.is_file():
        raise FleetMujocoError(f"multi-drone MuJoCo generator not found: {script}")
    spec = importlib.util.spec_from_file_location(
        "hakoniwa_drone_pro_mujoco_fleet_generator", script
    )
    if spec is None or spec.loader is None:
        raise FleetMujocoError(f"cannot load multi-drone generator: {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _generate_base_fleet_xml(
    drone_root: Path, drone_count: int, destination: Path
) -> None:
    generator = _load_generator(drone_root)
    template_root = drone_root / "config" / "drone" / "fleets" / "types"
    scene = template_root / "mujoco-scene.xml.template"
    drone = template_root / "mujoco-drone.xml.template"
    if not scene.is_file() or not drone.is_file():
        raise FleetMujocoError(
            f"MuJoCo fleet templates are incomplete under {template_root}"
        )
    xml = generator.generate_xml(
        scene.read_text(encoding="utf-8"),
        drone.read_text(encoding="utf-8").strip(),
        drone_count,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(xml, encoding="utf-8")


def _remove_demo_landmarks(root: ET.Element) -> list[str]:
    worldbody = root.find("worldbody")
    if worldbody is None:
        raise FleetMujocoError("generated fleet MJCF has no worldbody")
    removed: list[str] = []
    for body in list(worldbody.findall("body")):
        name = body.get("name", "")
        if not DRONE_BODY_PATTERN.fullmatch(name):
            worldbody.remove(body)
            removed.append(name or "<unnamed>")
    return removed


def _prepare_base_fleet_xml(
    path: Path,
    expected_count: int,
    *,
    drone_ids: list[int] | None = None,
) -> dict[str, Any]:
    tree = ET.parse(path)
    root = tree.getroot()
    removed_landmarks = _remove_demo_landmarks(root)
    size = root.find("size")
    if size is None:
        size = ET.Element("size")
        root.insert(1, size)
    size.attrib.update(MODEL_SIZE)
    drone_default = root.find("./default/default[@class='drone']/geom")
    if drone_default is None:
        raise FleetMujocoError("generated fleet MJCF has no drone geom default")
    drone_default.attrib.update(DRONE_COLLISION_MASK)
    selected_ids = drone_ids or list(range(1, expected_count + 1))
    selected_names = {f"d{index}_b_drone_base" for index in selected_ids}
    worldbody = root.find("worldbody")
    assert worldbody is not None
    for body in list(worldbody.findall("body")):
        name = body.get("name", "")
        if DRONE_BODY_PATTERN.fullmatch(name) and name not in selected_names:
            worldbody.remove(body)
    names = [
        body.get("name", "")
        for body in root.findall("./worldbody/body")
        if DRONE_BODY_PATTERN.fullmatch(body.get("name", ""))
    ]
    expected = [f"d{index}_b_drone_base" for index in selected_ids]
    if names != expected:
        raise FleetMujocoError(
            f"generated drone body contract mismatch: expected={expected}, actual={names}"
        )
    ET.indent(tree, space="  ")
    tree.write(path, encoding="utf-8", xml_declaration=True)
    return {"drone_body_names": names, "removed_demo_landmarks": removed_landmarks}


def build_shared_model(
    *,
    drone_root: Path,
    city_world_path: Path,
    drone_count: int,
    output_dir: Path,
    drone_ids: list[int] | None = None,
) -> dict[str, Any]:
    if not 1 <= drone_count <= 200:
        raise FleetMujocoError("drone_count must be in [1, 200]")
    drone_root = drone_root.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        city_world = city_drone._resolve_city_world(city_world_path)
    except city_drone.RecipeError as exc:
        raise FleetMujocoError(str(exc)) from exc
    city_receipt = json.loads(city_world.receipt_path.read_text(encoding="utf-8"))
    terrain_xml_value = city_receipt.get("components", {}).get("terrain_xml")
    terrain_xml = Path(str(terrain_xml_value)).resolve()
    if not terrain_xml.is_file():
        raise FleetMujocoError(
            f"City World receipt does not provide a usable terrain XML: {terrain_xml}"
        )

    base_xml = output_dir / "fleet-base.xml"
    shared_xml = output_dir / "city-fleet.xml"
    shared_mjb = output_dir / "city-fleet.mjb"
    _generate_base_fleet_xml(drone_root, drone_count, base_xml)
    fleet = _prepare_base_fleet_xml(
        base_xml, drone_count, drone_ids=drone_ids
    )
    try:
        composition = city_drone._compose_drone_and_city_mjcf(
            base_xml, city_world.mjcf_path, shared_xml
        )
        compiled = _compile_cached(
            shared_xml, shared_mjb, find_mujoco_library(drone_root)
        )
    except (city_drone.RecipeError, MujocoCompileError, ET.ParseError) as exc:
        raise FleetMujocoError(str(exc)) from exc

    receipt: dict[str, Any] = {
        "schema_version": 1,
        "component": "drone-fleet-mujoco-city",
        "scope": "non-ICRA drone-fleet-single-host",
        "drone_count": drone_count,
        "local_drone_count": len(fleet["drone_body_names"]),
        "drone_ids": drone_ids or list(range(1, drone_count + 1)),
        "process_count_contract": 1,
        "city_world": {
            "receipt": str(city_world.receipt_path),
            "mjcf": str(city_world.mjcf_path),
            "mjcf_sha256": _sha256(city_world.mjcf_path),
            "terrain_mjcf": str(terrain_xml),
            "terrain_mjcf_sha256": _sha256(terrain_xml),
            "glb": str(city_world.glb_path),
            "origin": city_world.origin,
            "half_extent_m": city_world.half_extent_m,
        },
        "fleet": fleet,
        "model_size": MODEL_SIZE,
        "collision_contract": {
            "city": {"contype": "1", "conaffinity": "0"},
            "drone": DRONE_COLLISION_MASK,
            "city_to_drone": "enabled",
            "drone_to_drone": "disabled",
        },
        "composition": composition,
        "compiled_model": compiled,
    }
    receipt_path = output_dir / "receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt


def _partition_drone_ids(drone_count: int, process_count: int) -> list[list[int]]:
    if process_count < 1 or process_count > drone_count:
        raise FleetMujocoError("process_count must be in [1, drone_count]")
    base, remainder = divmod(drone_count, process_count)
    counts = [base] * process_count
    for index in range(process_count - remainder, process_count):
        counts[index] += 1
    result: list[list[int]] = []
    next_id = 1
    for count in counts:
        result.append(list(range(next_id, next_id + count)))
        next_id += count
    return result


def _compile_cached(xml_path: Path, mjb_path: Path, library_path: Path) -> dict[str, Any]:
    """compile_mujoco_xml, reusing an MJB compiled earlier from the same inputs."""
    digest = hashlib.sha256(xml_path.read_bytes())
    digest.update(Path(library_path).read_bytes())
    key = digest.hexdigest()
    cached_mjb = MJB_CACHE_DIR / f"{key}.mjb"
    cached_receipt = MJB_CACHE_DIR / f"{key}.json"
    if cached_mjb.is_file() and cached_receipt.is_file():
        shutil.copyfile(cached_mjb, mjb_path)
        compiled = json.loads(cached_receipt.read_text(encoding="utf-8"))
        compiled["output_mjb"] = str(mjb_path)
        compiled["cache"] = {"hit": True, "key": key}
        print(f"MuJoCo compile: {xml_path.name} reused from cache ({cached_mjb})", flush=True)
        return compiled
    compiled = compile_mujoco_xml(xml_path, mjb_path, library_path)
    MJB_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    partial = cached_mjb.with_suffix(f".{os.getpid()}.partial")
    shutil.copyfile(mjb_path, partial)
    partial.replace(cached_mjb)
    cached_receipt.write_text(json.dumps(compiled, indent=2) + "\n", encoding="utf-8")
    return {**compiled, "cache": {"hit": False, "key": key}}


def build_process_models(
    *,
    drone_root: Path,
    city_world_path: Path,
    drone_count: int,
    process_count: int,
    output_dir: Path,
) -> dict[str, Any]:
    partitions = _partition_drone_ids(drone_count, process_count)

    def build(indexed: tuple[int, list[int]]) -> dict[str, Any]:
        process_index, drone_ids = indexed
        return build_shared_model(
            drone_root=drone_root,
            city_world_path=city_world_path,
            drone_count=drone_count,
            output_dir=output_dir / f"process-{process_index:02d}",
            drone_ids=drone_ids,
        )

    with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL_COMPILES, len(partitions))) as pool:
        process_models = list(pool.map(build, enumerate(partitions, start=1)))
    aggregate = {
        "schema_version": 1,
        "component": "drone-fleet-mujoco-city-process-models",
        "scope": "non-ICRA drone-fleet-single-host",
        "drone_count": drone_count,
        "process_count": process_count,
        "city_world": process_models[0]["city_world"],
        "process_models": process_models,
    }
    receipt_path = output_dir / "receipt.json"
    receipt_path.write_text(
        json.dumps(aggregate, indent=2) + "\n", encoding="utf-8"
    )
    return aggregate


def build_flat_shared_model(
    *,
    drone_root: Path,
    drone_count: int,
    output_dir: Path,
    ground_height_m: float,
    origin: dict[str, float],
    drone_ids: list[int] | None = None,
) -> dict[str, Any]:
    """Build a fleet-only MuJoCo model with one flat ground plane."""
    if not 1 <= drone_count <= 200:
        raise FleetMujocoError("drone_count must be in [1, 200]")
    if not math.isfinite(ground_height_m):
        raise FleetMujocoError("ground_height_m must be finite")
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    model_xml = output_dir / "flat-fleet.xml"
    model_mjb = output_dir / "flat-fleet.mjb"
    _generate_base_fleet_xml(drone_root, drone_count, model_xml)
    fleet = _prepare_base_fleet_xml(
        model_xml, drone_count, drone_ids=drone_ids
    )
    tree = ET.parse(model_xml)
    ground = tree.find("./worldbody/geom[@name='ground']")
    if ground is None:
        raise FleetMujocoError("generated fleet MJCF has no ground plane")
    ground.set("pos", f"0 0 {ground_height_m:.12g}")
    ground.set("size", "1000 1000 .01")
    ET.indent(tree, space="  ")
    tree.write(model_xml, encoding="utf-8", xml_declaration=True)
    try:
        compiled = compile_mujoco_xml(
            model_xml, model_mjb, find_mujoco_library(drone_root)
        )
    except (MujocoCompileError, ET.ParseError) as exc:
        raise FleetMujocoError(str(exc)) from exc
    receipt = {
        "schema_version": 1,
        "component": "drone-fleet-mujoco-flat",
        "scope": "non-ICRA drone-fleet-single-host",
        "drone_count": drone_count,
        "local_drone_count": len(fleet["drone_body_names"]),
        "drone_ids": drone_ids or list(range(1, drone_count + 1)),
        "process_count_contract": 1,
        "environment": {
            "mode": "flat",
            "ground_height_m": ground_height_m,
            "origin": origin,
        },
        "fleet": fleet,
        "model_size": MODEL_SIZE,
        "collision_contract": {
            "ground": {"contype": "1", "conaffinity": "1"},
            "drone": DRONE_COLLISION_MASK,
            "ground_to_drone": "enabled",
            "drone_to_drone": "disabled",
        },
        "compiled_model": compiled,
    }
    (output_dir / "receipt.json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )
    return receipt


def build_flat_process_models(
    *,
    drone_root: Path,
    drone_count: int,
    process_count: int,
    output_dir: Path,
    ground_height_m: float,
    origin: dict[str, float],
) -> dict[str, Any]:
    process_models = [
        build_flat_shared_model(
            drone_root=drone_root,
            drone_count=drone_count,
            output_dir=output_dir / f"process-{process_index:02d}",
            ground_height_m=ground_height_m,
            origin=origin,
            drone_ids=drone_ids,
        )
        for process_index, drone_ids in enumerate(
            _partition_drone_ids(drone_count, process_count), start=1
        )
    ]
    aggregate = {
        "schema_version": 1,
        "component": "drone-fleet-mujoco-flat-process-models",
        "scope": "non-ICRA drone-fleet-single-host",
        "drone_count": drone_count,
        "process_count": process_count,
        "environment": process_models[0]["environment"],
        "process_models": process_models,
    }
    (output_dir / "receipt.json").write_text(
        json.dumps(aggregate, indent=2) + "\n", encoding="utf-8"
    )
    return aggregate


def materialize_fleet_config(
    *,
    drone_root: Path,
    recipe_config: Path,
    model_receipt: dict[str, Any],
    spawn_altitude_m: float = LANDING_GEAR_CLEARANCE_M,
    spawn_spacing_m: float = SPAWN_DEFAULT_SEPARATION_M,
    altitude_mode: str = "route-clearance",
    above_city_clearance_m: float = 10.0,
    formation_rotation_deg: float = 90.0,
    formation_tilt_deg: float = 15.0,
    launch_area: dict[str, Any] | None = None,
    character_show: bool = False,
) -> dict[str, Any]:
    """Replace a configured non-ICRA fleet with its MuJoCo equivalent.

    The generated scenario (the Business Pack HAKONIWA word) is kept unless
    character_show selects the three character-face formations. Those are
    Drone Show content carried over from hakoniwa-drone-show; show content
    belongs to the show plugin and is removed from this module later.
    """
    if not math.isfinite(formation_rotation_deg):
        raise FleetMujocoError("formation_rotation_deg must be finite")
    if not math.isfinite(formation_tilt_deg) or not -85.0 <= formation_tilt_deg <= 85.0:
        raise FleetMujocoError("formation_tilt_deg must be in [-85, 85]")
    if not 0.18 <= spawn_altitude_m <= 2.0:
        raise FleetMujocoError(
            "spawn_altitude_m is the body-origin clearance above terrain and "
            "must be between 0.18 and 2.0"
        )
    spawn_spacing_m = _validate_spawn_spacing(spawn_spacing_m)
    launch_mode, launch_offset_m, launch_search_radius_m = _resolve_launch_area(
        launch_area
    )
    if altitude_mode not in ALTITUDE_MODES:
        raise FleetMujocoError(
            f"altitude_mode must be one of {sorted(ALTITUDE_MODES)}"
        )
    if not 1.0 <= above_city_clearance_m <= 100.0:
        raise FleetMujocoError("above_city_clearance_m must be in [1.0, 100.0]")
    drone_root = drone_root.expanduser().resolve()
    recipe_config = recipe_config.expanduser().resolve()
    drone_count = model_receipt.get("drone_count")
    if not isinstance(drone_count, int) or drone_count < 1:
        raise FleetMujocoError("shared model receipt has an invalid drone_count")
    process_count = int(model_receipt.get("process_count", 1))
    process_models = model_receipt.get("process_models")
    if not isinstance(process_models, list):
        process_models = [model_receipt]
    if len(process_models) != process_count:
        raise FleetMujocoError("process model count does not match process_count")
    compiled = process_models[0].get("compiled_model")
    city = model_receipt.get("city_world")
    if not isinstance(compiled, dict) or not isinstance(city, dict):
        raise FleetMujocoError("shared model receipt is incomplete")
    model_path = Path(str(compiled.get("output_mjb", ""))).resolve()
    if not model_path.is_file():
        raise FleetMujocoError(f"compiled shared MJB not found: {model_path}")

    type_source = (
        drone_root / "config" / "drone" / "fleets" / "types" / "api-mujoco.json"
    )
    if not type_source.is_file():
        raise FleetMujocoError(f"MuJoCo Drone type config not found: {type_source}")
    type_relative = Path("config/drone/fleets/types/api-mujoco-city.json")
    type_output = recipe_config / type_relative.relative_to("config")
    type_output.parent.mkdir(parents=True, exist_ok=True)
    type_config = json.loads(type_source.read_text(encoding="utf-8"))
    type_config["name"] = "api-mujoco-city"
    _disable_runtime_csv_logging(type_config)
    dynamics = type_config["components"]["droneDynamics"]
    dynamics["enable_disturbance"] = True
    dynamics["mujoco"]["modelPath"] = str(model_path)
    origin = city.get("origin")
    if not isinstance(origin, dict):
        raise FleetMujocoError("City World origin is missing from the model receipt")
    location = type_config["simulation"]["location"]
    location["latitude"] = origin["latitude"]
    location["longitude"] = origin["longitude"]
    location["altitude"] = origin["altitude_offset_m"]
    type_output.write_text(json.dumps(type_config, indent=2) + "\n", encoding="utf-8")

    fleet = recipe_config / "drone" / "fleets" / "api-current.json"
    service = (
        recipe_config
        / "drone"
        / "fleets"
        / "services"
        / "api-current-service.json"
    )
    pdudef = recipe_config / "pdudef" / "drone-pdudef-current.json"
    generator = drone_root / "tools" / "gen_fleet_scale_config.py"
    command = [
        sys.executable,
        str(generator),
        "--drone-count",
        str(drone_count),
        "--fleet-path",
        str(fleet),
        "--pdudef-path",
        str(pdudef),
        "--service-config-path",
        "config/drone/fleets/services/api-current-service.json",
        "--service-out-path",
        str(service),
        "--type-name",
        "api-mujoco-city",
        "--type-config-path",
        str(type_relative),
        "--enable-mujoco-overrides",
        "--layout",
        "packed-rings",
        "--center-z",
        str(-spawn_altitude_m),
    ]
    result = subprocess.run(command, cwd=recipe_config.parent, check=False)
    if result.returncode != 0:
        raise FleetMujocoError(
            f"MuJoCo fleet config generation failed with rc={result.returncode}"
        )

    terrain_xml = Path(str(city.get("terrain_mjcf", ""))).resolve()
    city_xml = Path(str(city.get("mjcf", ""))).resolve()
    half_extent = city.get("half_extent_m")
    if not terrain_xml.is_file() or not city_xml.is_file():
        raise FleetMujocoError("City World ray-query MJCF paths are incomplete")
    if not isinstance(half_extent, dict):
        raise FleetMujocoError("City World half_extent_m is missing")
    show_path = recipe_config / "scenario" / "show.json"
    if not show_path.is_file():
        raise FleetMujocoError(f"generated show configuration not found: {show_path}")
    show = json.loads(show_path.read_text(encoding="utf-8"))
    if character_show:
        _materialize_three_phase_city_show(
            show,
            show_path=show_path,
            drone_count=drone_count,
        )
    _rotate_formation_files(
        show,
        show_path=show_path,
        rotation_deg=formation_rotation_deg,
        tilt_deg=formation_tilt_deg,
    )
    options = show.get("options")
    if not isinstance(options, dict):
        raise FleetMujocoError("generated show options are missing")
    requested_agl_m = float(options.get("base_alt", 0.0))
    if requested_agl_m < 0.5:
        raise FleetMujocoError("scenario altitude must be at least 0.5 m AGL")

    library_path = find_mujoco_library(drone_root)
    with _MujocoRayScene(terrain_xml, library_path) as terrain_scene, _MujocoRayScene(
        city_xml, library_path
    ) as city_scene:
        spawn_args = {
            "drone_count": drone_count,
            "half_extent_m": half_extent,
            "terrain_height": terrain_scene.height,
            "city_height": city_scene.height,
            "spawn_spacing_m": spawn_spacing_m,
            "center_m": (launch_offset_m[0], launch_offset_m[1]),
        }
        if launch_mode == "manual":
            spawn_points = _manual_spawn_points(**spawn_args)
        else:
            spawn_points = _select_safe_spawn_points(
                **spawn_args,
                search_nearby=True,
                search_radius_m=launch_search_radius_m,
            )
        targets = _formation_targets(show, show_path=show_path)
        if drone_count == 1:
            # The single-Drone City checkpoint is controlled directly through
            # Fleet RPC. Its clearance route starts at the resolved launch
            # point rather than at the compatibility Show point at city origin.
            targets = [(item["x_m"], item["y_m"]) for item in spawn_points]
        elif not targets:
            targets = [(item["x_m"], item["y_m"]) for item in spawn_points]
        route_points = _formation_clearance_points(targets)
        print(
            "[mujoco-city] checking launch and formation clearance: "
            f"{len(route_points)} unique samples"
        )
        route_surface_height_m = max(
            max(point["surface_height_m"] for point in spawn_points),
            max(city_scene.height(x_m, y_m) for x_m, y_m in route_points),
        )

    city_visual_max_height_m: float | None = None
    city_visual_receipt: Path | None = None
    if altitude_mode == "city-max-clearance":
        city_receipt_path = Path(str(city.get("receipt", ""))).resolve()
        city_visual_max_height_m, city_visual_receipt = _city_visual_max_height(
            city_receipt_path
        )
        altitude_reference_m = max(
            route_surface_height_m, city_visual_max_height_m
        )
        requested_clearance_m = float(above_city_clearance_m)
    else:
        altitude_reference_m = route_surface_height_m
        requested_clearance_m = requested_agl_m
    flight_altitude_m = altitude_reference_m + requested_clearance_m
    fleet_config = json.loads(fleet.read_text(encoding="utf-8"))
    drones = fleet_config.get("drones")
    if not isinstance(drones, list) or len(drones) != drone_count:
        raise FleetMujocoError("generated fleet drone count changed unexpectedly")
    for drone, spawn in zip(drones, spawn_points):
        local_z_m = (
            spawn["surface_height_m"] + spawn_altitude_m + launch_offset_m[2]
        )
        drone["position_meter"] = _city_position_to_fleet_ned(
            spawn["x_m"], spawn["y_m"], local_z_m
        )
        spawn["body_origin_height_m"] = local_z_m
    fleet.write_text(json.dumps(fleet_config, indent=2) + "\n", encoding="utf-8")
    options["base_alt"] = flight_altitude_m
    show_path.write_text(json.dumps(show, indent=2) + "\n", encoding="utf-8")

    process_type_configs: list[str] = []
    if process_count > 1:
        splitter = drone_root / "tools" / "gen_fleet_split_config.py"
        split_command = [
            sys.executable,
            str(splitter),
            "--fleet-in",
            str(fleet),
            "--service-in",
            str(service),
            "--fleet-out-template",
            str(recipe_config / "drone" / "fleets" / "api-current-part{part}.json"),
            "--service-out-template",
            str(
                recipe_config
                / "drone"
                / "fleets"
                / "services"
                / "api-current-service-part{part}.json"
            ),
            "--shared-service-config-path",
            "config/drone/fleets/services/api-current-service.json",
            "--parts",
            str(process_count),
        ]
        result = subprocess.run(split_command, cwd=recipe_config.parent, check=False)
        if result.returncode != 0:
            raise FleetMujocoError(
                f"MuJoCo fleet partition generation failed with rc={result.returncode}"
            )
        for process_index, process_model in enumerate(process_models, start=1):
            process_compiled = process_model.get("compiled_model")
            if not isinstance(process_compiled, dict):
                raise FleetMujocoError(
                    f"process {process_index} compiled model receipt is missing"
                )
            process_model_path = Path(
                str(process_compiled.get("output_mjb", ""))
            ).resolve()
            if not process_model_path.is_file():
                raise FleetMujocoError(
                    f"process {process_index} MJB not found: {process_model_path}"
                )
            process_type_relative = Path(
                f"config/drone/fleets/types/api-mujoco-city-part{process_index}.json"
            )
            process_type_output = recipe_config / process_type_relative.relative_to(
                "config"
            )
            process_type = json.loads(json.dumps(type_config))
            process_type["components"]["droneDynamics"]["mujoco"][
                "modelPath"
            ] = str(process_model_path)
            process_type_output.write_text(
                json.dumps(process_type, indent=2) + "\n", encoding="utf-8"
            )
            partition_path = (
                recipe_config
                / "drone"
                / "fleets"
                / f"api-current-part{process_index}.json"
            )
            partition = json.loads(partition_path.read_text(encoding="utf-8"))
            partition["types"]["api-mujoco-city"] = str(process_type_relative)
            partition_path.write_text(
                json.dumps(partition, indent=2) + "\n", encoding="utf-8"
            )
            process_type_configs.append(str(process_type_output))

    flight_plan = {
        "altitude_mode": altitude_mode,
        "altitude_contract": (
            "clearance above highest visual building in the generated city"
            if altitude_mode == "city-max-clearance"
            else "requested AGL above launch and formation areas"
        ),
        "requested_agl_m": requested_agl_m,
        "requested_clearance_m": requested_clearance_m,
        "route_maximum_surface_height_m": route_surface_height_m,
        "city_visual_maximum_height_m": city_visual_max_height_m,
        "city_visual_bounds_receipt": (
            str(city_visual_receipt) if city_visual_receipt is not None else None
        ),
        "altitude_reference_height_m": altitude_reference_m,
        "resolved_flight_altitude_m": flight_altitude_m,
        "spawn_body_clearance_m": spawn_altitude_m,
        "launch_area": {
            "mode": launch_mode,
            "validation": (
                "trusted-manual" if launch_mode == "manual" else "level-grid"
            ),
            "requested_offset_m": list(launch_offset_m),
            "search_center_m": [launch_offset_m[0], launch_offset_m[1]],
            "search_radius_m": launch_search_radius_m,
            "resolved_center_m": [
                sum(point["x_m"] for point in spawn_points) / len(spawn_points),
                sum(point["y_m"] for point in spawn_points) / len(spawn_points),
                sum(point["body_origin_height_m"] for point in spawn_points)
                / len(spawn_points),
            ],
        },
        "spawn_clearance_radius_m": SPAWN_CLEARANCE_RADIUS_M,
        "spawn_maximum_footprint_height_delta_m": MAX_SPAWN_SLOPE_DELTA_M,
        "spawn_area_safety_margin_m": SPAWN_AREA_MARGIN_M,
        "spawn_area_maximum_height_delta_m": SPAWN_AREA_MAX_HEIGHT_DELTA_M,
        "spawn_layout": "rectangular-grid",
        "spawn_minimum_separation_m": spawn_spacing_m,
        "spawn_resolved_separation_m": max(
            spawn_spacing_m, SPAWN_CLEARANCE_RADIUS_M
        ),
        "spawn_points": spawn_points,
        "formation_targets": [list(point) for point in targets],
        "formation_rotation_deg_clockwise": formation_rotation_deg,
        "formation_audience_tilt_deg": formation_tilt_deg,
        "show_phases": (
            ["CHIIKAWA", "HACHIWARE", "USAGI"] if character_show
            else [str(step.get("formation", step.get("id", "HAKONIWA")))
                  for step in show.get("timeline", []) if isinstance(step, dict)] or ["HAKONIWA"]
        ),
    }
    marker = {
        "schema_version": 1,
        "backend": "mujoco-city",
        "scope": "non-ICRA drone-fleet-single-host",
        "drone_root": str(drone_root),
        "drone_count": drone_count,
        "process_count": process_count,
        "flight_plan": flight_plan,
        "fleet_config": str(fleet),
        "type_config": str(type_output),
        "shared_model_receipt": str(
            Path(str(compiled["output_mjb"])).parent / "receipt.json"
        ),
        "shared_mjb": str(model_path),
        "process_models": [
            {
                "process_index": index,
                "drone_ids": process_model.get("drone_ids", []),
                "mjb": str(process_model["compiled_model"]["output_mjb"]),
                "receipt": str(
                    Path(process_model["compiled_model"]["output_mjb"]).parent
                    / "receipt.json"
                ),
            }
            for index, process_model in enumerate(process_models, start=1)
        ],
        "process_type_configs": process_type_configs,
        "city_world": city,
    }
    marker_path = recipe_config / "mujoco-city-fleet.json"
    marker_path.write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")
    return marker


def configure_single_host_fleet(
    *,
    drone_root: Path,
    city_world_path: Path,
    drone_count: int,
    recipe_config: Path,
    spawn_altitude_m: float = LANDING_GEAR_CLEARANCE_M,
    spawn_spacing_m: float = SPAWN_DEFAULT_SEPARATION_M,
    altitude_mode: str = "route-clearance",
    above_city_clearance_m: float = 10.0,
    process_count: int = 1,
    formation_rotation_deg: float = 90.0,
    formation_tilt_deg: float = 15.0,
    launch_area: dict[str, Any] | None = None,
    character_show: bool = False,
) -> dict[str, Any]:
    spawn_spacing_m = _validate_spawn_spacing(spawn_spacing_m)
    if process_count < 1 or process_count > drone_count:
        raise FleetMujocoError("process_count must be in [1, drone_count]")
    model = build_process_models(
        drone_root=drone_root,
        city_world_path=city_world_path,
        drone_count=drone_count,
        process_count=process_count,
        output_dir=recipe_config / "drone" / "mujoco-city-fleet",
    )
    return materialize_fleet_config(
        drone_root=drone_root,
        recipe_config=recipe_config,
        model_receipt=model,
        spawn_altitude_m=spawn_altitude_m,
        spawn_spacing_m=spawn_spacing_m,
        altitude_mode=altitude_mode,
        above_city_clearance_m=above_city_clearance_m,
        formation_rotation_deg=formation_rotation_deg,
        formation_tilt_deg=formation_tilt_deg,
        launch_area=launch_area,
        character_show=character_show,
    )


def materialize_flat_fleet_config(
    *,
    drone_root: Path,
    recipe_config: Path,
    model_receipt: dict[str, Any],
    spawn_altitude_m: float,
    spawn_spacing_m: float,
    flight_altitude_agl_m: float,
    formation_rotation_deg: float,
    formation_tilt_deg: float,
    launch_area: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Replace the generic fleet with a shared flat-ground MuJoCo fleet."""
    if not 0.18 <= spawn_altitude_m <= 2.0:
        raise FleetMujocoError(
            "spawn_altitude_m must be between 0.18 and 2.0"
        )
    spawn_spacing_m = _validate_spawn_spacing(spawn_spacing_m)
    launch_mode, launch_offset_m, launch_search_radius_m = _resolve_launch_area(
        launch_area
    )
    if not math.isfinite(flight_altitude_agl_m) or flight_altitude_agl_m < 0.5:
        raise FleetMujocoError("flat flight altitude must be at least 0.5 m AGL")
    if not math.isfinite(formation_rotation_deg):
        raise FleetMujocoError("formation_rotation_deg must be finite")
    if not math.isfinite(formation_tilt_deg) or not -85.0 <= formation_tilt_deg <= 85.0:
        raise FleetMujocoError("formation_tilt_deg must be in [-85, 85]")

    drone_root = drone_root.expanduser().resolve()
    recipe_config = recipe_config.expanduser().resolve()
    drone_count = int(model_receipt["drone_count"])
    process_count = int(model_receipt.get("process_count", 1))
    process_models = model_receipt.get("process_models")
    environment = model_receipt.get("environment")
    if not isinstance(process_models, list) or len(process_models) != process_count:
        raise FleetMujocoError("flat process model count is invalid")
    if not isinstance(environment, dict):
        raise FleetMujocoError("flat environment receipt is missing")
    origin = environment.get("origin")
    if not isinstance(origin, dict):
        raise FleetMujocoError("flat environment origin is missing")
    ground_height_m = float(environment["ground_height_m"])
    first_compiled = process_models[0].get("compiled_model")
    if not isinstance(first_compiled, dict):
        raise FleetMujocoError("flat compiled model receipt is missing")
    model_path = Path(str(first_compiled.get("output_mjb", ""))).resolve()
    if not model_path.is_file():
        raise FleetMujocoError(f"compiled flat MJB not found: {model_path}")

    source_type = (
        drone_root / "config" / "drone" / "fleets" / "types" / "api-mujoco.json"
    )
    if not source_type.is_file():
        raise FleetMujocoError(f"MuJoCo Drone type config not found: {source_type}")
    type_relative = Path("config/drone/fleets/types/api-mujoco-flat.json")
    type_output = recipe_config / type_relative.relative_to("config")
    type_output.parent.mkdir(parents=True, exist_ok=True)
    type_config = json.loads(source_type.read_text(encoding="utf-8"))
    type_config["name"] = "api-mujoco-flat"
    _disable_runtime_csv_logging(type_config)
    dynamics = type_config["components"]["droneDynamics"]
    dynamics["enable_disturbance"] = True
    dynamics["mujoco"]["modelPath"] = str(
        model_path
    )
    location = type_config["simulation"]["location"]
    location["latitude"] = float(origin["latitude"])
    location["longitude"] = float(origin["longitude"])
    location["altitude"] = float(origin["altitude_offset_m"])
    type_output.write_text(
        json.dumps(type_config, indent=2) + "\n", encoding="utf-8"
    )

    fleet = recipe_config / "drone" / "fleets" / "api-current.json"
    service = recipe_config / "drone" / "fleets" / "services" / "api-current-service.json"
    pdudef = recipe_config / "pdudef" / "drone-pdudef-current.json"
    generator = drone_root / "tools" / "gen_fleet_scale_config.py"
    body_origin_height_m = ground_height_m + spawn_altitude_m + launch_offset_m[2]
    command = [
        sys.executable,
        str(generator),
        "--drone-count",
        str(drone_count),
        "--fleet-path",
        str(fleet),
        "--pdudef-path",
        str(pdudef),
        "--service-config-path",
        "config/drone/fleets/services/api-current-service.json",
        "--service-out-path",
        str(service),
        "--type-name",
        "api-mujoco-flat",
        "--type-config-path",
        str(type_relative),
        "--enable-mujoco-overrides",
        "--layout",
        "packed-rings",
        "--center-z",
        str(-body_origin_height_m),
    ]
    if subprocess.run(command, cwd=recipe_config.parent, check=False).returncode != 0:
        raise FleetMujocoError("flat MuJoCo fleet config generation failed")

    candidates = _candidate_spawn_centers(
        {"north_south": 40.0, "east_west": 40.0},
        spacing_m=spawn_spacing_m,
        center_m=(launch_offset_m[0], launch_offset_m[1]),
    )
    if len(candidates) < drone_count:
        raise FleetMujocoError("flat ground cannot allocate all launch points")
    fleet_config = json.loads(fleet.read_text(encoding="utf-8"))
    drones = fleet_config.get("drones")
    if not isinstance(drones, list) or len(drones) != drone_count:
        raise FleetMujocoError("generated flat fleet drone count is invalid")
    spawn_points = []
    for drone, (x_m, y_m) in zip(drones, candidates):
        drone["position_meter"] = _city_position_to_fleet_ned(
            x_m, y_m, body_origin_height_m
        )
        spawn_points.append(
            {
                "x_m": x_m,
                "y_m": y_m,
                "ground_height_m": ground_height_m,
                "body_origin_height_m": body_origin_height_m,
            }
        )
    fleet.write_text(json.dumps(fleet_config, indent=2) + "\n", encoding="utf-8")

    process_type_configs: list[str] = []
    if process_count > 1:
        splitter = drone_root / "tools" / "gen_fleet_split_config.py"
        split_command = [
            sys.executable,
            str(splitter),
            "--fleet-in",
            str(fleet),
            "--service-in",
            str(service),
            "--fleet-out-template",
            str(recipe_config / "drone" / "fleets" / "api-current-part{part}.json"),
            "--service-out-template",
            str(recipe_config / "drone" / "fleets" / "services" / "api-current-service-part{part}.json"),
            "--shared-service-config-path",
            "config/drone/fleets/services/api-current-service.json",
            "--parts",
            str(process_count),
        ]
        if subprocess.run(split_command, cwd=recipe_config.parent, check=False).returncode != 0:
            raise FleetMujocoError("flat MuJoCo fleet partition generation failed")
        for process_index, process_model in enumerate(process_models, start=1):
            compiled = process_model.get("compiled_model")
            if not isinstance(compiled, dict):
                raise FleetMujocoError(
                    f"flat process {process_index} compiled model is missing"
                )
            process_model_path = Path(str(compiled.get("output_mjb", ""))).resolve()
            if not process_model_path.is_file():
                raise FleetMujocoError(
                    f"flat process {process_index} MJB not found: {process_model_path}"
                )
            process_type_relative = Path(
                f"config/drone/fleets/types/api-mujoco-flat-part{process_index}.json"
            )
            process_type_output = recipe_config / process_type_relative.relative_to(
                "config"
            )
            process_type = json.loads(json.dumps(type_config))
            process_type["components"]["droneDynamics"]["mujoco"]["modelPath"] = str(
                process_model_path
            )
            process_type_output.write_text(
                json.dumps(process_type, indent=2) + "\n", encoding="utf-8"
            )
            partition_path = recipe_config / "drone" / "fleets" / f"api-current-part{process_index}.json"
            partition = json.loads(partition_path.read_text(encoding="utf-8"))
            partition["types"]["api-mujoco-flat"] = str(process_type_relative)
            partition_path.write_text(
                json.dumps(partition, indent=2) + "\n", encoding="utf-8"
            )
            process_type_configs.append(str(process_type_output))

    flight_altitude_m = ground_height_m + flight_altitude_agl_m
    flight_plan = {
        "altitude_mode": "flat-ground-agl",
        "altitude_contract": "scenario altitude above configured flat ground",
        "requested_agl_m": flight_altitude_agl_m,
        "requested_clearance_m": flight_altitude_agl_m,
        "altitude_reference_height_m": ground_height_m,
        "resolved_flight_altitude_m": flight_altitude_m,
        "spawn_body_clearance_m": spawn_altitude_m,
        "launch_area": {
            "mode": launch_mode,
            "requested_offset_m": list(launch_offset_m),
            "search_center_m": [launch_offset_m[0], launch_offset_m[1]],
            "search_radius_m": launch_search_radius_m,
            "resolved_center_m": [
                sum(point["x_m"] for point in spawn_points) / len(spawn_points),
                sum(point["y_m"] for point in spawn_points) / len(spawn_points),
                body_origin_height_m,
            ],
        },
        "spawn_minimum_separation_m": spawn_spacing_m,
        "spawn_points": spawn_points,
        "formation_targets": [],
        "formation_rotation_deg_clockwise": formation_rotation_deg,
        "formation_audience_tilt_deg": formation_tilt_deg,
    }
    marker = {
        "schema_version": 1,
        "backend": "mujoco-flat",
        "scope": "non-ICRA drone-fleet-single-host",
        "drone_root": str(drone_root),
        "drone_count": drone_count,
        "process_count": process_count,
        "flight_plan": flight_plan,
        "fleet_config": str(fleet),
        "type_config": str(type_output),
        "shared_model_receipt": str(model_path.parent / "receipt.json"),
        "shared_mjb": str(model_path),
        "process_models": [
            {
                "process_index": index,
                "drone_ids": process_model.get("drone_ids", []),
                "mjb": str(process_model["compiled_model"]["output_mjb"]),
                "receipt": str(Path(process_model["compiled_model"]["output_mjb"]).parent / "receipt.json"),
            }
            for index, process_model in enumerate(process_models, start=1)
        ],
        "process_type_configs": process_type_configs,
        "environment": environment,
    }
    return marker


def configure_single_host_flat_fleet(
    *,
    drone_root: Path,
    drone_count: int,
    recipe_config: Path,
    origin: dict[str, float],
    ground_height_m: float,
    flight_altitude_agl_m: float,
    spawn_altitude_m: float = LANDING_GEAR_CLEARANCE_M,
    spawn_spacing_m: float = SPAWN_DEFAULT_SEPARATION_M,
    process_count: int = 1,
    formation_rotation_deg: float = 90.0,
    formation_tilt_deg: float = 15.0,
    launch_area: dict[str, Any] | None = None,
) -> dict[str, Any]:
    model = build_flat_process_models(
        drone_root=drone_root,
        drone_count=drone_count,
        process_count=process_count,
        output_dir=recipe_config / "drone" / "mujoco-flat-fleet",
        ground_height_m=ground_height_m,
        origin=origin,
    )
    return materialize_flat_fleet_config(
        drone_root=drone_root,
        recipe_config=recipe_config,
        model_receipt=model,
        spawn_altitude_m=spawn_altitude_m,
        spawn_spacing_m=spawn_spacing_m,
        flight_altitude_agl_m=flight_altitude_agl_m,
        formation_rotation_deg=formation_rotation_deg,
        formation_tilt_deg=formation_tilt_deg,
        launch_area=launch_area,
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("command", choices=["build"])
    result.add_argument("--city-world", type=Path, required=True)
    result.add_argument("--drone-count", type=int, default=2)
    result.add_argument("--drone-root", type=Path, default=DEFAULT_DRONE_ROOT)
    result.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        receipt = build_shared_model(
            drone_root=args.drone_root,
            city_world_path=args.city_world,
            drone_count=args.drone_count,
            output_dir=args.output_dir,
        )
    except FleetMujocoError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"Shared MuJoCo XML: {receipt['composition']['generated_xml']}")
    print(f"Shared MuJoCo MJB: {receipt['compiled_model']['output_mjb']}")
    print(f"Receipt          : {args.output_dir.expanduser().resolve() / 'receipt.json'}")
    print(f"Drones           : {receipt['drone_count']}")
    print("Collision        : city<->drone enabled; drone<->drone disabled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
