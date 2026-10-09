#!/usr/bin/env python3
"""Take off over MAVLink (same operation as hakoniwa-drone-core drone_api/external_rpc/commands/takeoff_client.py)."""
from __future__ import annotations

import argparse
import time

from _common import add_link_arguments, make_client
from mavlink_drone_client import print_response_elapsed

DEFAULT_ALT_M = 0.5


def main() -> int:
    parser = argparse.ArgumentParser(description="Arm and take off over MAVLink")
    parser.add_argument("alt_m", nargs="?", type=float, default=DEFAULT_ALT_M, help="altitude above the local origin (m)")
    parser.add_argument("--timeout-sec", type=float, default=60.0)
    add_link_arguments(parser)
    args = parser.parse_args()

    client = make_client(args)
    print(f"INFO: request takeoff drone={args.drone_name} alt_m={args.alt_m}")
    start_time = time.time()
    res = client.takeoff(args.alt_m, timeout_sec=args.timeout_sec)
    print_response_elapsed("takeoff call", start_time)
    print(f"INFO: response ok={res.ok} message={res.message}")
    return 0 if res.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
