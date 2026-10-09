#!/usr/bin/env python3
"""Get the vehicle state over MAVLink (same output as hakoniwa-drone-core drone_api/external_rpc/commands/get_state_client.py)."""
from __future__ import annotations

import argparse
import math

from _common import add_link_arguments, make_client


def quaternion_to_euler_deg(w: float, x: float, y: float, z: float) -> tuple[float, float, float]:
    roll = math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
    pitch = math.asin(max(-1.0, min(1.0, 2 * (w * y - z * x))))
    yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
    return math.degrees(roll), math.degrees(pitch), math.degrees(yaw)


def main() -> int:
    parser = argparse.ArgumentParser(description="Get the vehicle state over MAVLink")
    add_link_arguments(parser)
    args = parser.parse_args()

    client = make_client(args)
    print(f"INFO: request get-state drone={args.drone_name}")
    res = client.get_state()
    pos = res.current_pose.position
    q = res.current_pose.orientation
    roll_deg, pitch_deg, yaw_deg = quaternion_to_euler_deg(q.w, q.x, q.y, q.z)
    print(
        "INFO: response "
        f"ok={res.ok} is_ready={res.is_ready} mode={res.mode} message={res.message} "
        f"position=({pos.x:.3f}, {pos.y:.3f}, {pos.z:.3f}) "
        f"angle_deg=({roll_deg:.1f}, {pitch_deg:.1f}, {yaw_deg:.1f})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
