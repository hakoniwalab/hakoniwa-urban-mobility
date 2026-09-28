"""Check a Car route against City World building outlines (the collision walls).

City World builds every building as walls standing on its outline, so a route
segment that enters an outline, or passes closer to a wall than half a Car's
width, will block the Car even where the map shows a driveway. Courtyards
(interior rings) are open ground.
"""

from __future__ import annotations

import math

# Room kept on each side of a Car, beyond half its width, in metres.
SIDE_MARGIN_M = 0.2
# Used when no Car declares its dimensions: about half the Golf Cart width
# (1.22 m) plus the margin.
DEFAULT_CLEARANCE_M = 0.8
SAMPLE_STEP_M = 0.5


def _inside(point: tuple[float, float], ring: list[list[float]]) -> bool:
    x, y = point
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def _segment_distance(point: tuple[float, float], a: list[float], b: list[float]) -> float:
    (px, py), (ax, ay), (bx, by) = point, a, b
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    t = 0.0 if length_sq == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _wall_distance(point: tuple[float, float], building: dict) -> float:
    rings = [building["vertices"], *building.get("holes", [])]
    return min(
        _segment_distance(point, a, b)
        for ring in rings for a, b in zip(ring, ring[1:] + ring[:1])
    )


def _in_building(point: tuple[float, float], building: dict) -> bool:
    return _inside(point, building["vertices"]) and not any(
        _inside(point, hole) for hole in building.get("holes", [])
    )


def clearance_for_width(width_m: float) -> float:
    """How far a route must stay from a wall for a Car of this width."""
    return width_m / 2.0 + SIDE_MARGIN_M


def route_conflicts(
    points: list[dict], buildings: list[dict], clearance_m: float = DEFAULT_CLEARANCE_M,
) -> list[dict]:
    """One entry per blocked segment of the closed route: which points, which building, why.

    points: route points with east_m / north_m (the loop closes last -> first).
    buildings: {"id", "vertices", "holes"} outlines in the same local ENU frame.
    """
    conflicts = []
    count = len(points)
    for index in range(count if count >= 3 else 0):
        start, end = points[index], points[(index + 1) % count]
        ax, ay = float(start["east_m"]), float(start["north_m"])
        bx, by = float(end["east_m"]), float(end["north_m"])
        length = math.hypot(bx - ax, by - ay)
        samples = max(1, math.ceil(length / SAMPLE_STEP_M))
        hit = None
        for step in range(samples + 1):
            ratio = step / samples
            point = (ax + ratio * (bx - ax), ay + ratio * (by - ay))
            for building in buildings:
                if _in_building(point, building):
                    hit = (building["id"], "inside", point)
                    break
                if _wall_distance(point, building) < clearance_m:
                    hit = (building["id"], "near_wall", point)
                    break
            if hit:
                break
        if hit:
            conflicts.append({
                "from": index + 1,
                "to": (index + 1) % count + 1,
                "building": hit[0],
                "reason": hit[1],
                "at": [round(hit[2][0], 2), round(hit[2][1], 2)],
            })
    return conflicts
