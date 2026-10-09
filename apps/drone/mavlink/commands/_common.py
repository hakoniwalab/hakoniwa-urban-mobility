"""Shared argument handling for the MAVLink command clients."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

MAVLINK_RPC_DIR = Path(__file__).resolve().parents[1]
if str(MAVLINK_RPC_DIR) not in sys.path:
    sys.path.insert(0, str(MAVLINK_RPC_DIR))

from mavlink_drone_client import DEFAULT_CONNECTION, MavlinkDroneClient  # noqa: E402

DEFAULT_DRONE_NAME = "Drone"


def add_link_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--connection",
        default=DEFAULT_CONNECTION,
        help=f"MAVLink connection (default: {DEFAULT_CONNECTION}, PX4 SITL onboard API port)",
    )
    parser.add_argument("--drone", dest="drone_name", default=DEFAULT_DRONE_NAME)
    parser.add_argument("--autopilot", choices=("auto", "px4", "ardupilot"), default="auto")


def make_client(args: argparse.Namespace) -> MavlinkDroneClient:
    return MavlinkDroneClient(connection=args.connection, drone_name=args.drone_name, autopilot=args.autopilot)
