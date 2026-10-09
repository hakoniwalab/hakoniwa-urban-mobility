#!/usr/bin/env python3
"""Land over MAVLink (same operation as hakoniwa-drone-core drone_api/external_rpc/commands/land_client.py)."""
from __future__ import annotations

import argparse
import time

from _common import add_link_arguments, make_client
from mavlink_drone_client import print_response_elapsed


def main() -> int:
    parser = argparse.ArgumentParser(description="Land over MAVLink and wait until disarmed")
    parser.add_argument("--timeout-sec", type=float, default=0.0, help="0: wait up to 120 s")
    add_link_arguments(parser)
    args = parser.parse_args()

    client = make_client(args)
    print(f"INFO: request land drone={args.drone_name}")
    start_time = time.time()
    res = client.land(timeout_sec=args.timeout_sec)
    print_response_elapsed("land call", start_time)
    print(f"INFO: response ok={res.ok} message={res.message}")
    return 0 if res.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
