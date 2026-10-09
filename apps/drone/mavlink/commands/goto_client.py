#!/usr/bin/env python3
"""Go to a position over MAVLink (same operation as hakoniwa-drone-core drone_api/external_rpc/commands/goto_client.py).

Position and yaw are in the ROS frame (x forward, y left, z up, yaw counter-clockwise, degrees).
"""
from __future__ import annotations

import argparse
import time

from _common import add_link_arguments, make_client
from mavlink_drone_client import print_response_elapsed

DEFAULT_TARGET_X = 1.0
DEFAULT_TARGET_Y = 0.0
DEFAULT_TARGET_Z = 0.5
DEFAULT_SPEED_M_S = 1.0
DEFAULT_YAW_DEG = 0.0
DEFAULT_TOLERANCE_M = 0.5
DEFAULT_TIMEOUT_SEC = 30.0


def main() -> int:
    parser = argparse.ArgumentParser(description="Go to a position over MAVLink")
    parser.add_argument("x", nargs="?", type=float, default=DEFAULT_TARGET_X)
    parser.add_argument("y", nargs="?", type=float, default=DEFAULT_TARGET_Y)
    parser.add_argument("z", nargs="?", type=float, default=DEFAULT_TARGET_Z)
    parser.add_argument("yaw", nargs="?", type=float, default=DEFAULT_YAW_DEG)
    parser.add_argument("--speed", dest="speed_m_s", type=float, default=DEFAULT_SPEED_M_S)
    parser.add_argument("--tolerance", dest="tolerance_m", type=float, default=DEFAULT_TOLERANCE_M)
    parser.add_argument("--timeout-sec", dest="timeout_sec", type=float, default=DEFAULT_TIMEOUT_SEC)
    add_link_arguments(parser)
    args = parser.parse_args()

    client = make_client(args)
    print(f"INFO: request goto drone={args.drone_name} target=({args.x}, {args.y}, {args.z}) yaw_deg={args.yaw}")
    start_time = time.time()
    res = client.goto(
        args.x, args.y, args.z, args.yaw,
        speed_m_s=args.speed_m_s, tolerance_m=args.tolerance_m, timeout_sec=args.timeout_sec,
    )
    print_response_elapsed("goto call", start_time)
    print(f"INFO: response ok={res.ok} message={res.message}")
    return 0 if res.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
