#!/usr/bin/env python3
"""Control one Urban Car from an external Python process."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from urban_car import AckermannClient


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PDU_DEF = ROOT / "work/multi-car-viewer/urban-car-pdudef.json"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--pdu-def", type=Path, default=DEFAULT_PDU_DEF)
    result.add_argument("--robot", default="Car-1")
    result.add_argument("--pdu", default="ackermann_cmd")
    result.add_argument("--rate-hz", type=float, default=50.0)
    commands = result.add_subparsers(dest="command", required=True)

    drive = commands.add_parser("drive", help="publish a command for a fixed duration")
    drive.add_argument("--speed", type=float, required=True, help="linear speed in m/s")
    drive.add_argument(
        "--steering-deg", type=float, default=0.0,
        help="centre steering angle in degrees; positive turns left",
    )
    drive.add_argument("--duration", type=float, required=True, help="duration in seconds")
    commands.add_parser("stop", help="publish repeated zero commands")
    return result


def main() -> int:
    args = parser().parse_args()
    with AckermannClient(
        pdu_def=args.pdu_def,
        robot=args.robot,
        pdu=args.pdu,
        rate_hz=args.rate_hz,
    ) as client:
        if args.command == "drive":
            client.drive(
                speed_m_s=args.speed,
                steering_rad=math.radians(args.steering_deg),
                duration_sec=args.duration,
                stop_after=False,
            )
        # The context manager publishes repeated zero commands on either path.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
