"""Pure geometry helpers shared by Urban route configuration and execution."""

from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class RoutePoint:
    name: str
    east_m: float
    north_m: float
    dwell_sec: float = 0.0


@dataclass(frozen=True)
class RouteVehicleSpec:
    name: str
    offset_m: float
    lateral_offset_m: float = 0.0


class RouteGeometry:
    """Closed ENU polyline with projection and arc-length sampling."""

    def __init__(self, points: tuple[RoutePoint, ...]):
        if len(points) < 3:
            raise ValueError("route requires at least three points")
        self.points = points
        self.segment_lengths: list[float] = []
        self.cumulative = [0.0]
        for start, end in zip(points, points[1:] + points[:1]):
            length = math.hypot(
                end.east_m - start.east_m,
                end.north_m - start.north_m,
            )
            if length <= 1e-6:
                raise ValueError("route contains a zero-length segment")
            self.segment_lengths.append(length)
            self.cumulative.append(self.cumulative[-1] + length)
        self.length = self.cumulative[-1]

    def sample(self, distance_m: float) -> tuple[float, float]:
        wrapped = distance_m % self.length
        for index, length in enumerate(self.segment_lengths):
            start_s = self.cumulative[index]
            if wrapped <= start_s + length or index + 1 == len(self.segment_lengths):
                ratio = (wrapped - start_s) / length
                start = self.points[index]
                end = self.points[(index + 1) % len(self.points)]
                return (
                    start.east_m + ratio * (end.east_m - start.east_m),
                    start.north_m + ratio * (end.north_m - start.north_m),
                )
        raise AssertionError("route sample did not resolve a segment")

    def heading_rad(self, distance_m: float) -> float:
        wrapped = distance_m % self.length
        for index, length in enumerate(self.segment_lengths):
            if wrapped <= self.cumulative[index] + length:
                start = self.points[index]
                end = self.points[(index + 1) % len(self.points)]
                return math.atan2(
                    end.north_m - start.north_m,
                    end.east_m - start.east_m,
                )
        raise AssertionError("route heading did not resolve a segment")

    def smooth_heading_rad(self, distance_m: float) -> float:
        """Interpolate vertex tangents for a continuous formation path."""
        wrapped = distance_m % self.length
        for index, length in enumerate(self.segment_lengths):
            start_s = self.cumulative[index]
            if wrapped <= start_s + length or index + 1 == len(self.segment_lengths):
                ratio = (wrapped - start_s) / length
                before = self.points[(index - 1) % len(self.points)]
                after = self.points[(index + 1) % len(self.points)]
                next_after = self.points[(index + 2) % len(self.points)]
                start_heading = math.atan2(
                    after.north_m - before.north_m,
                    after.east_m - before.east_m,
                )
                end_heading = math.atan2(
                    next_after.north_m - self.points[index].north_m,
                    next_after.east_m - self.points[index].east_m,
                )
                delta = math.atan2(
                    math.sin(end_heading - start_heading),
                    math.cos(end_heading - start_heading),
                )
                return start_heading + ratio * delta
        raise AssertionError("route smooth heading did not resolve a segment")

    def formation_sample(
        self,
        distance_m: float,
        longitudinal_offset_m: float = 0.0,
        lateral_offset_m: float = 0.0,
    ) -> tuple[tuple[float, float], float]:
        """Sample a longitudinal/lateral formation target and its ENU yaw."""
        selected = distance_m + longitudinal_offset_m
        east_m, north_m = self.sample(selected)
        heading = self.smooth_heading_rad(selected)
        return (
            (
                east_m - math.sin(heading) * lateral_offset_m,
                north_m + math.cos(heading) * lateral_offset_m,
            ),
            heading,
        )

    def project(self, east_m: float, north_m: float) -> float:
        best_distance_sq = math.inf
        best_s = 0.0
        for index, length in enumerate(self.segment_lengths):
            start = self.points[index]
            end = self.points[(index + 1) % len(self.points)]
            dx = end.east_m - start.east_m
            dy = end.north_m - start.north_m
            ratio = max(0.0, min(1.0, (
                (east_m - start.east_m) * dx
                + (north_m - start.north_m) * dy
            ) / (length * length)))
            projected_east = start.east_m + ratio * dx
            projected_north = start.north_m + ratio * dy
            distance_sq = (
                (east_m - projected_east) ** 2
                + (north_m - projected_north) ** 2
            )
            if distance_sq < best_distance_sq:
                best_distance_sq = distance_sq
                best_s = self.cumulative[index] + ratio * length
        return best_s % self.length

    def signed_error(self, target_s: float, actual_s: float) -> float:
        error = (target_s - actual_s) % self.length
        if error > self.length / 2.0:
            error -= self.length
        return error


def expand_route_vehicles(value: object) -> tuple[RouteVehicleSpec, ...]:
    """Expand either explicit route vehicles or a generated fleet contract."""
    if isinstance(value, list):
        result = []
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                raise ValueError(f"vehicles[{index}] must be an object")
            name = str(item.get("name", "")).strip()
            try:
                offset_m = float(item.get("route_offset_m", 0.0))
                lateral_offset_m = float(item.get("lateral_offset_m", 0.0))
            except (TypeError, ValueError) as error:
                raise ValueError(
                    f"vehicles[{index}] route offsets must be numbers"
                ) from error
            if not math.isfinite(offset_m) or not math.isfinite(lateral_offset_m):
                raise ValueError(f"vehicles[{index}] route offsets must be finite")
            result.append(RouteVehicleSpec(name, offset_m, lateral_offset_m))
        return tuple(result)
    if not isinstance(value, dict) or not isinstance(value.get("generate"), dict):
        raise ValueError("vehicles must be an array or contain generate")
    generate = value["generate"]
    count = generate.get("count")
    if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
        raise ValueError("vehicles.generate.count must be a positive integer")
    prefix = str(generate.get("name_prefix", "")).strip()
    if not prefix:
        raise ValueError("vehicles.generate.name_prefix must not be empty")
    try:
        spacing_m = float(generate.get("route_spacing_m"))
    except (TypeError, ValueError) as error:
        raise ValueError("vehicles.generate.route_spacing_m must be a number") from error
    if not math.isfinite(spacing_m) or spacing_m <= 0.0:
        raise ValueError("vehicles.generate.route_spacing_m must be finite and positive")
    return tuple(
        RouteVehicleSpec(f"{prefix}{index}", -(index - 1) * spacing_m)
        for index in range(1, count + 1)
    )
