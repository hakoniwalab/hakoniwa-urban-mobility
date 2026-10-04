#!/usr/bin/env python3
"""A Car route as a line on the World: its loop with the height of the surface under it.

Used for the Viewer's planned routes (tools/urban_simulation.py route_paths)
and the Studio Route tab's 3D view. The loop is sampled every STEP_M; the
first point takes the top of the World under it (where a Car starts), every
next one looks down from LOOK_ABOVE_M above the previous one (World heights
ground_below), so a road under a bridge stays on the road. Without a ray
model (no MuJoCo) every point takes the top of the World.
"""

from __future__ import annotations

import math
from typing import Callable, Sequence

STEP_M = 1.0
CLEARANCE_M = 0.3   # the line is drawn this far above the surface
LOOK_ABOVE_M = 2.0


def route_line(corners: Sequence[tuple[float, float]], ground: Callable[[float, float], float],
               closed: bool = True) -> tuple[list[dict], list[int]]:
    """([{east_m, north_m, up_m}], the index in it of each corner)."""
    below = getattr(ground, "ground_below", None)
    legs = list(zip(corners, list(corners[1:]) + ([corners[0]] if closed else [])))
    samples, corner_index = [], []
    for (e0, n0), (e1, n1) in legs:
        corner_index.append(len(samples))
        steps = max(1, int(math.dist((e0, n0), (e1, n1)) // STEP_M))
        samples += [(e0 + (e1 - e0) * k / steps, n0 + (n1 - n0) * k / steps) for k in range(steps)]
    if not closed and corners:
        corner_index.append(len(samples))
        samples.append(tuple(corners[-1]))
    points, up = [], None
    for east, north in samples:
        if up is None or below is None:
            height = float(ground(east, north))
        else:
            found = below(east, north, up + LOOK_ABOVE_M)
            height = up if found is None else float(found)
        up = height
        points.append({"east_m": round(east, 2), "north_m": round(north, 2), "up_m": round(height + CLEARANCE_M, 2)})
    return points, corner_index


def section_values(corner_index: Sequence[int], values: Sequence[float | None], count: int) -> list[float | None]:
    """The value of the leg each of count samples is on: a route point's value
    is its leg's, from that point to the next one (as apps/car/scenario_executor.py
    TireFriction); None on a leg without one."""
    result = [None] * count
    for leg, (start, value) in enumerate(zip(corner_index, values)):
        end = corner_index[leg + 1] if leg + 1 < len(corner_index) else count
        result[start:end] = [value] * (end - start)
    return result
