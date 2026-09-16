#!/usr/bin/env python3
"""Publish a DualSense command as ackermann_msgs/AckermannDrive."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT.parent
PDU_REGISTRY = WORKSPACE / "hakoniwa-pdu-registry"
sys.path.insert(0, str(PDU_REGISTRY))

import hakopy  # noqa: E402
import pygame  # noqa: E402
from hakoniwa_pdu.impl.shm_communication_service import ShmCommunicationService  # noqa: E402
from hakoniwa_pdu.pdu_manager import PduManager  # noqa: E402
from pdu.python.ackermann_msgs.pdu_conv_AckermannDrive import (  # noqa: E402
    py_to_pdu_AckermannDrive,
)
from pdu.python.ackermann_msgs.pdu_pytype_AckermannDrive import AckermannDrive  # noqa: E402


TARGET_NAMES = ("Wireless Controller", "DualSense")
AXIS_COUNT = 6


class StickMonitor:
    """Apply the small axis mapping/curve contract used by the Urban recipe."""

    def __init__(self, path: str):
        with Path(path).open(encoding="utf-8") as stream:
            config = json.load(stream)
        stick = config["stick"]
        self.features = {
            stick["Left"]["LR"]["index"]: (0, stick["Left"]["LR"]),
            stick["Left"]["UD"]["index"]: (1 if config["mode"] == 2 else 3, stick["Left"]["UD"]),
            stick["Right"]["LR"]["index"]: (2, stick["Right"]["LR"]),
            stick["Right"]["UD"]["index"]: (3 if config["mode"] == 2 else 1, stick["Right"]["UD"]),
        }
        self.history = {index: [] for index in range(AXIS_COUNT)}

    def operation_index(self, axis: int) -> int | None:
        item = self.features.get(axis)
        return None if item is None else item[0]

    def value(self, axis: int, raw: float) -> float:
        operation, feature = self.features[axis]
        value = raw
        if feature.get("average", False):
            history = self.history[operation]
            history.append(value)
            del history[:-5]
            value = sum(history) / len(history)
        conversion = feature.get("conversion")
        if conversion:
            value = (
                conversion["paramA"] * value**3
                + conversion["paramB"] * value**2
                + conversion.get("paramC", 0.0) * value
            )
        if feature.get("valueInverse", False):
            value = -value
        return max(-1.0, min(1.0, value))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdu-def", required=True)
    parser.add_argument("--rc-config", required=True)
    parser.add_argument("--robot", required=True)
    parser.add_argument("--pdu", default="ackermann_cmd")
    parser.add_argument("--rate-hz", type=float, default=50.0)
    parser.add_argument("--max-speed", type=float, default=3.5)
    parser.add_argument("--max-steering-angle", type=float, default=0.70)
    parser.add_argument("--deadzone", type=float, default=0.06)
    parser.add_argument("--check-controller", action="store_true")
    return parser.parse_args()


def select_joystick():
    for index in range(pygame.joystick.get_count()):
        joystick = pygame.joystick.Joystick(index)
        joystick.init()
        name = joystick.get_name()
        print(f"[INFO] Detected Joystick {index}: {name}")
        if any(candidate.lower() in name.lower() for candidate in TARGET_NAMES):
            print(f"[INFO] Selected Joystick {index}: {name}")
            return joystick
    return None


def apply_deadzone(value: float, deadzone: float) -> float:
    clamped = max(-1.0, min(1.0, value))
    if abs(clamped) <= deadzone:
        return 0.0
    return math.copysign((abs(clamped) - deadzone) / (1.0 - deadzone), clamped)


def main() -> int:
    args = parse_args()
    if args.rate_hz <= 0 or args.max_speed <= 0 or args.max_steering_angle <= 0:
        raise ValueError("rate and command limits must be positive")
    if not 0 <= args.deadzone < 1:
        raise ValueError("deadzone must be in [0, 1)")

    pygame.init()
    pygame.joystick.init()
    joystick = select_joystick()
    if joystick is None:
        print("[ERROR] PS5/DualSense controller was not found.")
        return 1
    print(f"[INFO] Axis count: {joystick.get_numaxes()}")
    print(f"[INFO] Button count: {joystick.get_numbuttons()}")
    if args.check_controller:
        pygame.joystick.quit()
        pygame.quit()
        return 0

    monitor = StickMonitor(args.rc_config)
    axes = [0.0] * AXIS_COUNT
    manager = PduManager()
    manager.initialize(
        config_path=str(Path(args.pdu_def).resolve()),
        comm_service=ShmCommunicationService(),
    )
    manager.start_service_nowait()
    if hakopy.init_for_external() is False:
        print("[ERROR] hakopy.init_for_external() failed")
        return 1

    period = 1.0 / args.rate_hz
    print("[INFO] AckermannDrive: left stick steering; right stick throttle/reverse")
    try:
        while True:
            manager.run_nowait()
            pygame.event.pump()
            for event in pygame.event.get():
                if hasattr(event, "instance_id") and event.instance_id != joystick.get_instance_id():
                    continue
                if event.type == pygame.JOYAXISMOTION and event.axis < joystick.get_numaxes():
                    output_index = monitor.operation_index(event.axis)
                    if output_index is not None and output_index < len(axes):
                        axes[output_index] = monitor.value(event.axis, event.value)

            command = AckermannDrive()
            command.steering_angle = (
                -apply_deadzone(axes[0], args.deadzone) * args.max_steering_angle
            )
            command.speed = -apply_deadzone(axes[3], args.deadzone) * args.max_speed
            manager.flush_pdu_raw_data_nowait(
                args.robot, args.pdu, py_to_pdu_AckermannDrive(command)
            )
            time.sleep(period)
    except KeyboardInterrupt:
        print("[INFO] AckermannDrive sender stopped.")
    finally:
        pygame.joystick.quit()
        pygame.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
