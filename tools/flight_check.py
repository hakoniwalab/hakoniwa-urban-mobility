#!/usr/bin/env python3
"""Check a Drone flight's legs against the World (Urban Studio's flight editor).

A flight is a line of 3D points (Urban ENU metres): the takeoff stand, the
waypoints, the landing stand. Each leg is ray-cast on the World collision
geometry (tools/world_height.py first_hit) along its centre line and along
copies moved to each side, above and below, so a leg that passes a wall or a
roof edge by less than the Drone's size plus its tracking error is reported.
A stand point (on the ground) leaves out the copy below, which would only
meet the ground under it.
"""

from __future__ import annotations

import math
from typing import Callable, Sequence

# How far from a leg the World must stay clear: the EAMS Hexa is about 1 m
# across and overshoots a stop by up to a metre or two.
SIDE_M = 1.5
ABOVE_M = 1.0
BELOW_M = 1.5

FirstHit = Callable[[Sequence[float], Sequence[float]], "tuple[float, str] | None"]


def _offsets(start: Sequence[float], end: Sequence[float], stands: bool) -> list[tuple[str, tuple[float, float, float]]]:
    de, dn = end[0] - start[0], end[1] - start[1]
    flat = math.hypot(de, dn)
    # Sideways is across the leg; a vertical leg has no across, so east and north both count.
    if flat > 1.0e-6:
        sides = [("left", (-dn / flat * SIDE_M, de / flat * SIDE_M, 0.0)),
                 ("right", (dn / flat * SIDE_M, -de / flat * SIDE_M, 0.0))]
    else:
        sides = [(name, (east * SIDE_M, north * SIDE_M, 0.0))
                 for name, east, north in (("east", 1, 0), ("west", -1, 0), ("north", 0, 1), ("south", 0, -1))]
    result = [("centre", (0.0, 0.0, 0.0)), *sides, ("above", (0.0, 0.0, ABOVE_M))]
    if not stands:
        result.append(("below", (0.0, 0.0, -BELOW_M)))
    return result


def leg_conflicts(first_hit: FirstHit, points: Sequence[dict]) -> list[dict]:
    """Legs (1-based from/to point numbers) that meet the World.

    points: [{east_m, north_m, up_m, stand?}]; stand marks a point on the
    ground (takeoff, landing). Each blocked leg is reported once, at its
    nearest hit from the leg's start: {from, to, at: [e, n, u], geom, side}.
    """
    conflicts = []
    for index in range(len(points) - 1):
        a, b = points[index], points[index + 1]
        start = (float(a["east_m"]), float(a["north_m"]), float(a["up_m"]))
        end = (float(b["east_m"]), float(b["north_m"]), float(b["up_m"]))
        length = math.dist(start, end)
        if length < 1.0e-6:
            continue
        stands = bool(a.get("stand") or b.get("stand"))
        nearest = None
        for side, (oe, on, ou) in _offsets(start, end, stands):
            s = (start[0] + oe, start[1] + on, start[2] + ou)
            e = (end[0] + oe, end[1] + on, end[2] + ou)
            # Both ways: a ray that starts inside a mesh does not see its faces.
            for origin, target, forward in ((s, e, True), (e, s, False)):
                hit = first_hit(origin, target)
                if hit is None:
                    continue
                along = hit[0] if forward else length - hit[0]
                if nearest is None or along < nearest[0]:
                    nearest = (along, hit[1], side)
        if nearest is not None:
            t = nearest[0] / length
            at = [round(start[i] + (end[i] - start[i]) * t, 2) for i in range(3)]
            conflicts.append({"from": index + 1, "to": index + 2, "at": at, "geom": nearest[1], "side": nearest[2]})
    return conflicts


def margins() -> dict:
    return {"side_m": SIDE_M, "above_m": ABOVE_M, "below_m": BELOW_M}
