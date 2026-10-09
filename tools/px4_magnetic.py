#!/usr/bin/env python3
"""The magnetic field PX4 expects at a position, from PX4's own World Magnetic Model table.

The PX4 EKF compares the measured field with its WMM lookup at the GPS position
(src/lib/world_magnetic_model). A simulated vehicle must therefore report the
field of that same table at its home, or PX4 has no consistent heading. This
reads geo_magnetic_tables.hpp from the PX4-Autopilot checkout and applies the
same bilinear interpolation as geo_mag_declination.cpp.

    python tools/px4_magnetic.py --px4-dir ../PX4-Autopilot 35.099 138.859
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re

TABLES = Path("src/lib/world_magnetic_model/geo_magnetic_tables.hpp")
SAMPLING_RES = 10.0
MIN_LAT, MAX_LAT = -90.0, 90.0
MIN_LON, MAX_LON = -180.0, 180.0


def _constant(text: str, name: str) -> float:
    match = re.search(rf"{name}\s*=\s*([-0-9.eE]+)f?;", text)
    if not match:
        raise ValueError(f"{name} not found in {TABLES}")
    return float(match.group(1))


def _table(text: str, name: str) -> list[list[int]]:
    match = re.search(rf"{name}\[\d+\]\[\d+\]\s*\{{(.*?)\n\}};", text, re.S)
    if not match:
        raise ValueError(f"{name} not found in {TABLES}")
    rows = re.findall(r"\{([^{}]*)\}", match.group(1))
    return [[int(value) for value in row.split(",") if value.strip()] for row in rows]


def load_tables(px4_dir: Path) -> dict:
    text = (px4_dir / TABLES).read_text(encoding="utf-8")
    return {
        "declination": (_table(text, "declination_table"), _constant(text, "WMM_DECLINATION_SCALE_TO_DEGREES")),
        "inclination": (_table(text, "inclination_table"), _constant(text, "WMM_INCLINATION_SCALE_TO_DEGREES")),
        "intensity": (_table(text, "totalintensity_table"), _constant(text, "WMM_TOTALINTENSITY_SCALE_TO_NANOTESLA")),
    }


def _index(value: float, low: float, high: float) -> tuple[int, float]:
    value = min(max(value, low), high - SAMPLING_RES)
    return int((value - low) / SAMPLING_RES), value


def _lookup(table: list[list[int]], lat: float, lon: float) -> float:
    lat = min(max(lat, MIN_LAT), MAX_LAT)
    if lon > MAX_LON:
        lon -= 360.0
    if lon < MIN_LON:
        lon += 360.0
    lat_index, min_lat = _index(math.floor(lat / SAMPLING_RES) * SAMPLING_RES, MIN_LAT, MAX_LAT)
    lon_index, min_lon = _index(math.floor(lon / SAMPLING_RES) * SAMPLING_RES, MIN_LON, MAX_LON)
    sw = table[lat_index][lon_index]
    se = table[lat_index][lon_index + 1]
    ne = table[lat_index + 1][lon_index + 1]
    nw = table[lat_index + 1][lon_index]
    lat_scale = min(max((lat - min_lat) / SAMPLING_RES, 0.0), 1.0)
    lon_scale = min(max((lon - min_lon) / SAMPLING_RES, 0.0), 1.0)
    low = lon_scale * (se - sw) + sw
    high = lon_scale * (ne - nw) + nw
    return lat_scale * (high - low) + low


def magnetic_field(px4_dir: Path, latitude_deg: float, longitude_deg: float) -> dict:
    """The drone_config simulation.location.magneticField for a position."""
    tables = load_tables(px4_dir)
    values = {name: _lookup(table, latitude_deg, longitude_deg) * scale for name, (table, scale) in tables.items()}
    return {
        "intensity_nT": round(values["intensity"], 1),
        "declination_deg": round(values["declination"], 3),
        "inclination_deg": round(values["inclination"], 3),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--px4-dir", type=Path, required=True)
    parser.add_argument("latitude", type=float)
    parser.add_argument("longitude", type=float)
    args = parser.parse_args(argv)
    print(json.dumps(magnetic_field(args.px4_dir, args.latitude, args.longitude)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
