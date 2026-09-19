#!/usr/bin/env python3
"""Validate a City-local formation route and build a static Three.js overlay."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.request import urlopen
import webbrowser


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
BUSINESS_PACK = WORKSPACE / "hakoniwa-business-pack"
ENVSIM = WORKSPACE / "hakoniwa-envsim"
DEFAULT_SCENARIO = ROOT / "recipes/scenarios/shizuoka-five-car-formation-loop.yaml"
DEFAULT_RECEIPT = (
    BUSINESS_PACK
    / "work/remote-operation/city-world-worker/jobs/"
    "shizuoka-22203-lat35.099-lon138.859/build/world/city-world-receipt.json"
)
DEFAULT_CITY_PYTHON = (
    BUSINESS_PACK
    / "work/recipes/plateau-citygml-mujoco-walls/python/bin/python3"
)


class RoutePreviewError(RuntimeError):
    pass


@dataclass(frozen=True)
class RoutePoint:
    east_m: float
    north_m: float


class ClosedRoute:
    def __init__(self, points: list[RoutePoint]):
        if len(points) < 3:
            raise RoutePreviewError("route requires at least three points")
        self.points = tuple(points)
        self.lengths: list[float] = []
        self.cumulative = [0.0]
        for start, end in zip(self.points, self.points[1:] + self.points[:1]):
            length = math.hypot(end.east_m - start.east_m, end.north_m - start.north_m)
            if length <= 1e-6:
                raise RoutePreviewError("route contains a zero-length segment")
            self.lengths.append(length)
            self.cumulative.append(self.cumulative[-1] + length)
        self.length = self.cumulative[-1]
        self.vertex_headings = tuple(
            math.atan2(
                self.points[(index + 1) % len(self.points)].north_m
                - self.points[(index - 1) % len(self.points)].north_m,
                self.points[(index + 1) % len(self.points)].east_m
                - self.points[(index - 1) % len(self.points)].east_m,
            )
            for index in range(len(self.points))
        )

    def _segment(self, distance_m: float) -> tuple[int, float]:
        wrapped = distance_m % self.length
        for index, length in enumerate(self.lengths):
            if wrapped <= self.cumulative[index] + length or index + 1 == len(self.lengths):
                return index, (wrapped - self.cumulative[index]) / length
        raise AssertionError("closed route segment not found")

    def sample(self, distance_m: float) -> RoutePoint:
        index, ratio = self._segment(distance_m)
        start = self.points[index]
        end = self.points[(index + 1) % len(self.points)]
        return RoutePoint(
            start.east_m + ratio * (end.east_m - start.east_m),
            start.north_m + ratio * (end.north_m - start.north_m),
        )

    def heading(self, distance_m: float) -> float:
        index, ratio = self._segment(distance_m)
        start = self.vertex_headings[index]
        end = self.vertex_headings[(index + 1) % len(self.points)]
        delta = math.atan2(math.sin(end - start), math.cos(end - start))
        return start + ratio * delta

    def formation_sample(
        self, distance_m: float, longitudinal_m: float, lateral_m: float
    ) -> tuple[RoutePoint, float]:
        selected = distance_m + longitudinal_m
        point = self.sample(selected)
        heading = self.heading(selected)
        return (
            RoutePoint(
                point.east_m - math.sin(heading) * lateral_m,
                point.north_m + math.cos(heading) * lateral_m,
            ),
            heading,
        )


def minimum_polyline_turn_radius(points: list[RoutePoint]) -> float:
    radii = []
    count = len(points)
    for index, center in enumerate(points):
        before = points[(index - 1) % count]
        after = points[(index + 1) % count]
        a = math.hypot(center.east_m - before.east_m, center.north_m - before.north_m)
        b = math.hypot(after.east_m - center.east_m, after.north_m - center.north_m)
        c = math.hypot(after.east_m - before.east_m, after.north_m - before.north_m)
        twice_area = abs(
            (center.east_m - before.east_m) * (after.north_m - before.north_m)
            - (center.north_m - before.north_m) * (after.east_m - before.east_m)
        )
        if twice_area > 1e-9:
            radii.append(a * b * c / (2.0 * twice_area))
    return min(radii) if radii else math.inf


def _absolute(path: Path) -> Path:
    return path if path.is_absolute() else (ROOT / path).resolve()


def _load_yaml(path: Path) -> dict:
    try:
        import yaml
    except ImportError as exc:
        raise RoutePreviewError(
            "PyYAML is unavailable; enter the Hakoniwa workspace environment first"
        ) from exc
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RoutePreviewError(f"failed to load scenario {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RoutePreviewError(f"scenario must contain an object: {path}")
    return value


def _workspace_url(path: Path) -> str:
    try:
        relative = path.resolve().relative_to(WORKSPACE.resolve())
    except ValueError as exc:
        raise RoutePreviewError(f"preview asset must be under {WORKSPACE}: {path}") from exc
    return "/" + relative.as_posix()


def configure(args: argparse.Namespace) -> int:
    scenario_path = _absolute(args.scenario)
    receipt_path = _absolute(args.receipt)
    city_python = _absolute(args.city_python)
    output = _absolute(args.output or Path("work/route-preview") / scenario_path.stem)
    for label, path in (
        ("scenario", scenario_path),
        ("City World receipt", receipt_path),
        ("City World Python", city_python),
    ):
        if not path.is_file():
            raise RoutePreviewError(f"{label} not found: {path}")
    scenario = _load_yaml(scenario_path)
    request = {
        "scenario_path": str(scenario_path),
        "scenario": scenario,
        "receipt": str(receipt_path),
        "envsim_root": str(ENVSIM.resolve()),
        "output": str(output),
        "workspace": str(WORKSPACE.resolve()),
    }
    with tempfile.TemporaryDirectory(prefix="urban-route-preview-") as directory:
        request_path = Path(directory) / "request.json"
        request_path.write_text(json.dumps(request), encoding="utf-8")
        result = subprocess.run(
            [str(city_python), str(Path(__file__).resolve()), "_worker", str(request_path)],
            cwd=ROOT,
            check=False,
        )
    if result.returncode:
        return result.returncode
    print(f"Validation: {output / 'route-validation.json'}")
    print(f"Overlay GLB: {output / 'route-preview.glb'}")
    print(f"Viewer URL : {(output / 'viewer-url.txt').read_text(encoding='utf-8').strip()}")
    return 0


def _finite(value: object, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise RoutePreviewError(f"{label} must be numeric") from exc
    if not math.isfinite(number):
        raise RoutePreviewError(f"{label} must be finite")
    return number


def _worker(request_path: Path) -> int:
    request = json.loads(request_path.read_text(encoding="utf-8"))
    scenario = request["scenario"]
    receipt_path = Path(request["receipt"])
    output = Path(request["output"])
    workspace = Path(request["workspace"])
    envsim_root = Path(request["envsim_root"])
    sys.path.insert(0, str(envsim_root / "src/city_pipeline"))

    import numpy as np
    from shapely.geometry import LineString, Polygon
    from shapely.ops import unary_union
    import trimesh

    from road_terrain_probe import extract_all_transport_surfaces, read_hfield
    from terrain_surface import TerrainSurface

    if scenario.get("schema_version") != 3:
        raise RoutePreviewError("route preview requires scenario schema_version 3")
    route_input = scenario.get("route")
    if not isinstance(route_input, dict) or route_input.get("closed") is not True:
        raise RoutePreviewError("route.closed must be true")
    raw_points = route_input.get("points")
    if not isinstance(raw_points, list):
        raise RoutePreviewError("route.points must be an array")
    points = [
        RoutePoint(
            _finite(item.get("east_m"), f"route.points[{index}].east_m"),
            _finite(item.get("north_m"), f"route.points[{index}].north_m"),
        )
        for index, item in enumerate(raw_points)
    ]
    route = ClosedRoute(points)
    surface_input = scenario.get("road_surface")
    if not isinstance(surface_input, dict):
        raise RoutePreviewError("road_surface must be an object")
    categories = surface_input.get("categories")
    if not isinstance(categories, list) or not categories:
        raise RoutePreviewError("road_surface.categories must be a non-empty array")
    length_m = _finite(surface_input.get("vehicle_length_m"), "vehicle_length_m")
    width_m = _finite(surface_input.get("vehicle_width_m"), "vehicle_width_m")
    safety_m = _finite(surface_input.get("safety_margin_m"), "safety_margin_m")
    required_radius = _finite(
        surface_input.get("minimum_turn_radius_m"), "minimum_turn_radius_m"
    )
    minimum_radius = minimum_polyline_turn_radius(points)
    if minimum_radius + 1e-6 < required_radius:
        raise RoutePreviewError(
            f"route minimum turn radius {minimum_radius:.3f} m is below "
            f"{required_radius:.3f} m"
        )

    vehicles_input = scenario.get("vehicles")
    if not isinstance(vehicles_input, list) or not vehicles_input:
        raise RoutePreviewError("vehicles must be a non-empty array")
    vehicles = []
    names = set()
    for index, item in enumerate(vehicles_input):
        if not isinstance(item, dict):
            raise RoutePreviewError(f"vehicles[{index}] must be an object")
        name = str(item.get("name", "")).strip()
        if not name or name in names:
            raise RoutePreviewError("vehicle names must be non-empty and unique")
        names.add(name)
        vehicles.append({
            "name": name,
            "longitudinal_m": _finite(item.get("route_offset_m", 0), "route_offset_m"),
            "lateral_m": _finite(item.get("lateral_offset_m", 0), "lateral_offset_m"),
        })

    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    frame = receipt["coordinate_frame"]
    origin = frame["origin"]
    half_extent = frame["half_extent_m"]
    expected_city = scenario.get("city_world", {})
    for key, actual in (
        ("origin_latitude", origin["latitude"]),
        ("origin_longitude", origin["longitude"]),
    ):
        if abs(_finite(expected_city.get(key), f"city_world.{key}") - float(actual)) > 1e-8:
            raise RoutePreviewError(f"scenario {key} does not match City World receipt")

    source = receipt_path.parents[1] / "source"
    source_paths, surfaces, lod_evidence = extract_all_transport_surfaces(
        source,
        float(origin["latitude"]),
        float(origin["longitude"]),
        float(half_extent["north_south"]),
        float(half_extent["east_west"]),
    )
    unknown = sorted(set(categories) - set(surfaces))
    if unknown:
        raise RoutePreviewError("unknown road surface categories: " + ", ".join(unknown))
    road = unary_union([
        polygon
        for category in categories
        for _, polygon in surfaces[category]
    ])

    sample_count = max(192, math.ceil(route.length / 0.25))
    half_swept_width = width_m / 2.0 + safety_m
    palette = (
        [34, 211, 238, 255], [245, 158, 11, 255], [249, 115, 22, 255],
        [132, 204, 22, 255], [34, 197, 94, 255],
    )
    planned = []
    preview_polygons: list[tuple[str, object, list[int]]] = []

    base_enu = [route.sample(route.length * index / sample_count) for index in range(sample_count)]
    base_xy = [(point.north_m, -point.east_m) for point in base_enu]
    base_line = LineString(base_xy + base_xy[:1])
    preview_polygons.append(("leader-route", base_line.buffer(0.10), [255, 255, 255, 255]))

    for index, vehicle in enumerate(vehicles):
        samples = [
            route.formation_sample(
                route.length * step / sample_count,
                vehicle["longitudinal_m"],
                vehicle["lateral_m"],
            )[0]
            for step in range(sample_count)
        ]
        xy = [(point.north_m, -point.east_m) for point in samples]
        line = LineString(xy + xy[:1])
        swept = line.buffer(half_swept_width)
        outside = swept.difference(road)
        start, yaw = route.formation_sample(
            0.0, vehicle["longitudinal_m"], vehicle["lateral_m"]
        )
        forward = (math.cos(yaw), math.sin(yaw))
        left = (-math.sin(yaw), math.cos(yaw))
        corners_enu = [
            (
                start.east_m + forward[0] * along + left[0] * across,
                start.north_m + forward[1] * along + left[1] * across,
            )
            for along, across in (
                (length_m / 2, width_m / 2),
                (-length_m / 2, width_m / 2),
                (-length_m / 2, -width_m / 2),
                (length_m / 2, -width_m / 2),
            )
        ]
        footprint = Polygon([(north, -east) for east, north in corners_enu])
        footprint_outside = footprint.difference(road).area
        if outside.area > 1e-4 or footprint_outside > 1e-4:
            raise RoutePreviewError(
                f"{vehicle['name']} leaves the selected road surface: "
                f"swept_outside={outside.area:.6f} m2, "
                f"initial_outside={footprint_outside:.6f} m2"
            )
        color = palette[index % len(palette)]
        preview_polygons.append((f"{vehicle['name']}-path", line.buffer(0.055), color))
        preview_polygons.append((f"{vehicle['name']}-initial", footprint, color))
        planned.append({
            **vehicle,
            "initial_pose_enu": {
                "east_m": start.east_m,
                "north_m": start.north_m,
                "yaw_deg": math.degrees(yaw),
            },
            "path_length_m": line.length,
            "swept_outside_road_m2": outside.area,
            "initial_footprint_outside_road_m2": footprint_outside,
            "minimum_swept_clearance_m": swept.distance(road.boundary),
        })

    terrain_receipt_path = Path(receipt["components"]["terrain_xml"]).with_name(
        "terrain-receipt.json"
    )
    terrain_receipt = json.loads(terrain_receipt_path.read_text(encoding="utf-8"))
    nrow, ncol, terrain_samples = read_hfield(Path(terrain_receipt["hfield"]["path"]))
    terrain = TerrainSurface.from_samples(
        terrain_samples,
        nrow,
        ncol,
        float(half_extent["north_south"]),
        float(half_extent["east_west"]),
    )
    altitude_offset = float(origin["altitude_offset_m"])
    scene = trimesh.Scene()
    for name, polygon, color in preview_polygons:
        draped = terrain.drape_polygon(polygon, vertical_offset_m=0.10)
        vertices = np.asarray([
            (-y, z - altitude_offset, -x) for x, y, z in draped.vertices
        ], dtype=float)
        faces = np.asarray(draped.faces, dtype=np.int64)
        if not len(vertices) or not len(faces):
            raise RoutePreviewError(f"preview geometry is empty: {name}")
        mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
        # Vertex colors avoid trimesh's optional SciPy dependency used when
        # converting face colors during GLB export.
        mesh.visual.vertex_colors = np.tile(
            np.asarray(color, dtype=np.uint8), (len(vertices), 1)
        )
        scene.add_geometry(mesh, node_name=name, geom_name=name)

    output.mkdir(parents=True, exist_ok=True)
    overlay_path = output / "route-preview.glb"
    scene.export(overlay_path)
    city_glb = Path(receipt["glb"]["path"])
    collider_glb = receipt_path.parents[2] / "viewer/city-world-colliders.glb"
    scene_config = {
        "version": "1.0",
        "format": "compact",
        "environments": [
            {"name": "city", "model": _path_url(city_glb, workspace)},
            {"name": "route-preview", "model": _path_url(overlay_path, workspace)},
            {
                "name": "city-world-colliders",
                "model": _path_url(collider_glb, workspace),
                "render": {
                    "mode": "wireframe", "color": "#22c55e", "opacity": 0.35,
                    "depthTest": True, "depthWrite": False,
                },
            },
        ],
        "main_camera": {
            "fov": 60, "near": 0.1, "far": 20000, "initialMode": "fixed",
            "position": [-45.0, -55.0, 55.0],
        },
        "vehicleTypesPath": "./vehicle-types.json",
        "vehicles": [],
    }
    (output / "scene-config.json").write_text(
        json.dumps(scene_config, indent=2) + "\n", encoding="utf-8"
    )
    (output / "vehicle-types.json").write_text("{}\n", encoding="utf-8")
    (output / "pdu.json").write_text("{}\n", encoding="utf-8")
    viewer_config = {
        "version": "1.0",
        "three": {
            "sceneConfigPath": _path_url(output / "scene-config.json", workspace),
            "initialCameraMode": "free",
        },
        "pdu": {
            "pduDefPath": _path_url(output / "pdu.json", workspace),
            "wsUri": "ws://127.0.0.1:8765",
            "wireVersion": "v2",
        },
        "ui": {"enableAttachedCameras": False, "enableMainCameraMouseControl": True},
        "stateInput": {"mode": "none"},
    }
    (output / "viewer-config.json").write_text(
        json.dumps(viewer_config, indent=2) + "\n", encoding="utf-8"
    )
    viewer_url = (
        "http://127.0.0.1:8000/hakoniwa-map-viewer/src/client/index.html"
        f"?threejsRoot=/hakoniwa-threejs-drone"
        f"&viewerConfigPath={_path_url(output / 'viewer-config.json', workspace)}"
        "&layout=three-main&autoConnect=false"
        f"&originLat={origin['latitude']}&originLon={origin['longitude']}"
    )
    (output / "viewer-url.txt").write_text(viewer_url + "\n", encoding="utf-8")
    validation = {
        "schema_version": 1,
        "scenario": str(Path(request["scenario_path"])),
        "city_world_receipt": str(receipt_path),
        "city_job_id": expected_city.get("job_id"),
        "road_surface_categories": categories,
        "transport_sources": [str(path) for path in source_paths],
        "lod_evidence": lod_evidence,
        "route": {
            "length_m": route.length,
            "point_count": len(points),
            "minimum_turn_radius_m": minimum_radius,
            "required_turn_radius_m": required_radius,
            "approval_status": route_input.get("approval_status"),
        },
        "formation": {
            "vehicle_count": len(planned),
            "vehicle_length_m": length_m,
            "vehicle_width_m": width_m,
            "safety_margin_m": safety_m,
            "vehicles": planned,
        },
        "automated_checks": {
            "receipt_origin_matches": True,
            "turn_radius_satisfied": True,
            "all_swept_footprints_inside_road_surface": True,
            "all_initial_footprints_inside_road_surface": True,
        },
        "manual_checks_required": [
            "approve the route in Three.js",
            "confirm route and initial footprints do not intersect building Collider wireframes",
        ],
        "overlay_glb": str(overlay_path),
    }
    (output / "route-validation.json").write_text(
        json.dumps(validation, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"OK: route={route.length:.2f} m, min_turn_radius={minimum_radius:.2f} m, "
        f"vehicles={len(planned)}, swept_road_check=passed"
    )
    return 0


def _path_url(path: Path, workspace: Path) -> str:
    try:
        return "/" + path.resolve().relative_to(workspace.resolve()).as_posix()
    except ValueError as exc:
        raise RoutePreviewError(f"path is outside Workspace HTTP root: {path}") from exc


def viewer_url(args: argparse.Namespace) -> str:
    scenario = _absolute(args.scenario)
    output = _absolute(args.output or Path("work/route-preview") / scenario.stem)
    path = output / "viewer-url.txt"
    if not path.is_file():
        raise RoutePreviewError(f"route preview is not configured: {path}")
    return path.read_text(encoding="utf-8").strip()


def open_viewer(args: argparse.Namespace) -> int:
    url = viewer_url(args)
    try:
        with urlopen(url, timeout=1.0) as response:
            if not 200 <= response.status < 400:
                raise RoutePreviewError(f"preview HTTP returned {response.status}: {url}")
    except OSError as exc:
        raise RoutePreviewError(
            "preview HTTP server is not running; start it with: "
            "python3 -m http.server 8000 --bind 127.0.0.1 --directory .."
        ) from exc
    print(f"Opening route preview: {url}")
    return 0 if webbrowser.open(url) else 1


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("command", choices=("configure", "url", "open-viewer", "_worker"))
    result.add_argument("request", nargs="?", type=Path, help=argparse.SUPPRESS)
    result.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO)
    result.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    result.add_argument("--city-python", type=Path, default=DEFAULT_CITY_PYTHON)
    result.add_argument("--output", type=Path)
    return result


def main() -> int:
    args = parser().parse_args()
    if args.command == "_worker":
        if args.request is None:
            raise RoutePreviewError("worker request is required")
        return _worker(args.request)
    if args.command == "configure":
        return configure(args)
    if args.command == "url":
        print(viewer_url(args))
        return 0
    return open_viewer(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, KeyError, ValueError, RoutePreviewError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
