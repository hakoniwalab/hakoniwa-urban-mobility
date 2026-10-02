#!/usr/bin/env python3
"""Move 箱庭人間 from outside: the Hakoniwa People API and its command line.

An external program (a script, a planner, an AI agent) attaches to a running
people simulation (tools/people_sim.py) and, per person by name,

    people = PeopleClient(pdu_def)
    with people:
        people.set_velocity("Person-1", east=1.0, north=0.0)   # m/s, City World ENU
        people.set_animation("Person-1", "wave")              # auto | walk | idle | wave | sit
        people.poses()["Person-1"]                            # east, north, yaw (ENU), up
        people.set_velocities({"Person-2": (0.0, 0.5), "Person-3": (-0.3, 0.2)})
        people.people()                                       # who is here: look, pose, animation
        people.stop_all()                                     # stand still, animation back to auto
        people.walk_to("Person-1", east=4.0, north=2.0)       # a blocking demo helper
        people.ride("Person-1", "Car-1", "driver")            # sit on a car's seat and go with it
        people.get_off("Person-1")

The commands stay until the next one (a person keeps walking until told to
stop). The command line does the same:

    python apps/people/hakoniwa_people.py --pdu-def <pdudef> poses
    python apps/people/hakoniwa_people.py --pdu-def <pdudef> velocity Person-1 --east 1 --duration 3
    python apps/people/hakoniwa_people.py --pdu-def <pdudef> walk-to Person-1 --east 4 --north 2
    python apps/people/hakoniwa_people.py --pdu-def <pdudef> animate Person-1 wave
    python apps/people/hakoniwa_people.py --pdu-def <pdudef> stop Person-1
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "car"))

from urban_car import HakoniwaPollingTransport  # noqa: E402

PLANT_ASSET = "HakoniwaPeople"
STATE_ROBOT = "UrbanPeople"
ANIMATIONS = ("auto", "walk", "idle", "wave", "sit")


class PersonPose(NamedTuple):
    east_m: float
    north_m: float
    yaw_rad: float  # ENU: 0 faces east, counter-clockwise positive
    up_m: float = 0.0  # the feet's height (the ground, a deck)


class PeopleError(RuntimeError):
    pass


class PeopleClient:
    def __init__(self, pdu_def: str | Path, plant_asset: str = PLANT_ASSET, state_robot: str = STATE_ROBOT):
        self.pdu_def = Path(pdu_def).expanduser().resolve()
        if not self.pdu_def.is_file():
            raise PeopleError(f"PDU definition not found: {self.pdu_def}")
        self.state_robot = state_robot
        self._transport = HakoniwaPollingTransport(self.pdu_def, plant_asset)

    def __enter__(self) -> "PeopleClient":
        self._transport.connect()
        return self

    def __exit__(self, *_exc) -> None:
        self._transport.close()

    # --- Low level --------------------------------------------------------------------

    def set_velocity(self, name: str, east: float = 0.0, north: float = 0.0, yaw_rate: float = 0.0) -> None:
        """Walk at (east, north) m/s; a person faces where it walks. yaw_rate
        (rad/s, left positive) turns it while it stands."""
        from hakoniwa_pdu.pdu_msgs.geometry_msgs.pdu_conv_Twist import py_to_pdu_Twist
        from hakoniwa_pdu.pdu_msgs.geometry_msgs.pdu_pytype_Twist import Twist

        values = (float(east), float(north), float(yaw_rate))
        if not all(math.isfinite(value) for value in values):
            raise PeopleError("velocity must be finite")
        twist = Twist()
        twist.linear.x, twist.linear.y, twist.angular.z = values
        if not self._transport.send(name, "cmd_vel", py_to_pdu_Twist(twist)):
            raise PeopleError(f"cannot command {name}: is it in the simulation?")

    def set_animation(self, name: str, animation: str) -> None:
        from hakoniwa_pdu.pdu_msgs.std_msgs.pdu_conv_String import py_to_pdu_String
        from hakoniwa_pdu.pdu_msgs.std_msgs.pdu_pytype_String import String

        if animation not in ANIMATIONS:
            raise PeopleError(f"animation must be one of {', '.join(ANIMATIONS)}")
        message = String()
        message.data = animation
        if not self._transport.send(name, "animation", py_to_pdu_String(message)):
            raise PeopleError(f"cannot animate {name}: is it in the simulation?")

    def stop(self, name: str, reset_animation: bool = True) -> None:
        """Stand still; by default also back to the `auto` animation (a forced
        `walk` would otherwise keep walking on the spot)."""
        self.set_velocity(name, 0.0, 0.0, 0.0)
        if reset_animation:
            self.set_animation(name, "auto")

    def ride(self, name: str, vehicle: str, seat: str = "driver") -> None:
        """Sit on a car's seat (its Asset's seats: driver, passenger) and go with it."""
        self._send_text(name, "ride", f"{vehicle}/{seat}")

    def get_off(self, name: str) -> None:
        """Get off beside the seat, back on the ground."""
        self._send_text(name, "ride", "")

    def _send_text(self, name: str, pdu: str, text: str) -> None:
        from hakoniwa_pdu.pdu_msgs.std_msgs.pdu_conv_String import py_to_pdu_String
        from hakoniwa_pdu.pdu_msgs.std_msgs.pdu_pytype_String import String

        message = String()
        message.data = text
        if not self._transport.send(name, pdu, py_to_pdu_String(message)):
            raise PeopleError(f"cannot send {pdu} to {name}: is it in the simulation?")

    def set_velocities(self, velocities: dict[str, tuple[float, float] | tuple[float, float, float]]) -> None:
        """Several people at once: {name: (east, north[, yaw_rate])}."""
        for name, velocity in velocities.items():
            self.set_velocity(name, *velocity)

    def stop_all(self, reset_animation: bool = True) -> None:
        for name in self.names():
            self.stop(name, reset_animation)

    # --- Discovery -------------------------------------------------------------------

    def names(self) -> list[str]:
        return [person["name"] for person in self._plant_people()]

    def people(self) -> dict[str, dict]:
        """Who is here: {name: {"look", "pose", "animation", "ride"}} (the look
        as configured: visitor, staff, passerby, child; ride "<vehicle>/<seat>"
        or empty)."""
        poses = self.poses()
        result = {}
        for person in self._plant_people():
            name = person["name"]
            result[name] = {"look": person.get("look"), "pose": poses.get(name), "animation": self.animation(name),
                            "ride": self._read_text(name, "ride")}
        return result

    def animation(self, name: str) -> str:
        """The animation last commanded (auto until one is sent)."""
        from hakoniwa_pdu.pdu_msgs.std_msgs.pdu_conv_String import pdu_to_py_String

        raw = self._transport.read(name, "animation")
        try:
            text = str(pdu_to_py_String(raw).data).strip() if raw else ""
        except (IndexError, TypeError, ValueError, UnicodeDecodeError):
            text = ""
        return text if text in ANIMATIONS else "auto"

    def _read_text(self, name: str, pdu: str) -> str:
        from hakoniwa_pdu.pdu_msgs.std_msgs.pdu_conv_String import pdu_to_py_String

        raw = self._transport.read(name, pdu)
        try:
            return str(pdu_to_py_String(raw).data).strip() if raw else ""
        except (IndexError, TypeError, ValueError, UnicodeDecodeError):
            return ""

    def _plant_people(self) -> list[dict]:
        """The people of the plant config written next to the PDU definition."""
        if not hasattr(self, "_people_config"):
            config = self.pdu_def.parent / "people-plant.json"
            try:
                self._people_config = json.loads(config.read_text(encoding="utf-8"))["people"]
            except (OSError, KeyError, ValueError) as error:
                raise PeopleError(f"cannot read the people's config: {config}") from error
        return self._people_config

    def poses(self) -> dict[str, PersonPose]:
        from hakoniwa_pdu.pdu_msgs.sensor_msgs.pdu_conv_MultiDOFJointState import pdu_to_py_MultiDOFJointState

        raw = self._transport.read(self.state_robot, "vehicle_states")
        if raw is None:
            raise PeopleError("cannot read the people's states")
        state = pdu_to_py_MultiDOFJointState(raw)
        result = {}
        for name, transform in zip(state.joint_names, state.transforms):
            q, p = transform.rotation, transform.translation
            yaw_mjcf = math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y ** 2 + q.z ** 2))
            result[name] = PersonPose(-float(p.y), float(p.x), math.atan2(math.cos(yaw_mjcf), -math.sin(yaw_mjcf)),
                                      float(p.z))
        return result

    def simulation_time(self) -> float:
        return self._transport.simulation_time_sec()

    # --- A helper on top --------------------------------------------------------------

    def walk_to(self, name: str, east: float, north: float, speed: float = 1.2, tolerance: float = 0.15,
                timeout_sec: float = 60.0) -> PersonPose:
        """Walk straight to (east, north), slowing at the end; returns where it
        stopped. A demo helper: it blocks until then, one person at a time. To
        move many people, loop over poses() and set_velocities() instead."""
        deadline = time.monotonic() + timeout_sec
        try:
            while time.monotonic() < deadline:
                pose = self.poses().get(name)
                if pose is None:
                    raise PeopleError(f"{name} is not in the simulation")
                de, dn = east - pose.east_m, north - pose.north_m
                distance = math.hypot(de, dn)
                if distance <= tolerance:
                    return pose
                v = min(speed, max(0.3, distance * 1.5))
                self.set_velocity(name, v * de / distance, v * dn / distance)
                time.sleep(0.05)
            raise PeopleError(f"{name} did not reach ({east}, {north}) in {timeout_sec} s")
        finally:
            self.stop(name)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    result.add_argument("--pdu-def", type=Path, required=True, help="the people simulation's PDU definition")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("poses", help="print every person's pose (JSON)")
    commands.add_parser("people", help="print who is here: look, pose, animation (JSON)")
    commands.add_parser("stop-all", help="stop everyone (animation back to auto)")
    velocity = commands.add_parser("velocity", help="walk at a velocity (and stop after --duration)")
    velocity.add_argument("name")
    velocity.add_argument("--east", type=float, default=0.0)
    velocity.add_argument("--north", type=float, default=0.0)
    velocity.add_argument("--yaw-rate", type=float, default=0.0)
    velocity.add_argument("--duration", type=float, help="seconds; then stop (default: keep walking)")
    walk = commands.add_parser("walk-to", help="walk straight to a point")
    walk.add_argument("name")
    walk.add_argument("--east", type=float, required=True)
    walk.add_argument("--north", type=float, required=True)
    walk.add_argument("--speed", type=float, default=1.2)
    animate = commands.add_parser("animate", help="set an animation")
    animate.add_argument("name")
    animate.add_argument("animation", choices=ANIMATIONS)
    ride = commands.add_parser("ride", help="sit on a car's seat (with cars in a Composition)")
    ride.add_argument("name")
    ride.add_argument("vehicle")
    ride.add_argument("--seat", default="driver")
    get_off = commands.add_parser("get-off", help="get off a car")
    get_off.add_argument("name")
    stop = commands.add_parser("stop", help="stop walking")
    stop.add_argument("name")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        with PeopleClient(args.pdu_def) as people:
            if args.command == "poses":
                print(json.dumps({name: pose._asdict() for name, pose in people.poses().items()}, indent=2))
            elif args.command == "people":
                print(json.dumps({name: {**entry, "pose": entry["pose"]._asdict() if entry["pose"] else None}
                                  for name, entry in people.people().items()}, indent=2))
            elif args.command == "stop-all":
                people.stop_all()
            elif args.command == "velocity":
                people.set_velocity(args.name, args.east, args.north, args.yaw_rate)
                if args.duration is not None:
                    time.sleep(args.duration)
                    people.stop(args.name)
            elif args.command == "walk-to":
                pose = people.walk_to(args.name, args.east, args.north, args.speed)
                print(json.dumps(pose._asdict()))
            elif args.command == "ride":
                people.ride(args.name, args.vehicle, args.seat)
            elif args.command == "get-off":
                people.get_off(args.name)
            elif args.command == "animate":
                people.set_animation(args.name, args.animation)
            else:
                people.stop(args.name)
    except PeopleError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
