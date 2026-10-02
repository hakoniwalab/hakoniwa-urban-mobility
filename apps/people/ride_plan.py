#!/usr/bin/env python3
"""People ride cars as a car route scenario says: the people side of it.

A car route scenario (apps/car/scenario_executor.py, schema_version 2) drives
a car round its route and stops it at its stops (dwell_sec). Its ``people:``
section, which the executor ignores, says who rides it:

    people:
      vehicle: Cart-1
      riders:
        - name: Person-1
          wait: [-2.0, 5.0]                        # walk here first (east, north), optional
          board: {stop: station, seat: passenger}  # a stop of the route, a seat of the car
          alight: {stop: destination, then: [12.0, 30.0]}  # get off there, walk on, optional

This program moves those people through the Hakoniwa People API: each waits,
and when the car stands at its boarding stop, walks to the door of its seat
(round the back of the car if the seat is on the far side) and gets on; when
the car stands at its alighting stop it gets off and walks on. It reads the
car's pose and never commands the car, so the car keeps the executor's
simulation-time schedule; nothing here is random, and every event is written
with Hakoniwa time (stdout, and --log as JSON lines).

    python apps/people/ride_plan.py recipes/scenarios/hakoniwa-cart-station-pickup.yaml \\
        --people-pdu-def <work>/config/people/people-pdudef.json \\
        --car-pdu-def <work>/config/car/urban-car-pdudef.json

The Composition starts it as the people's ``ride`` control (one process for
everyone in the same scenario).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "car"))

from hakoniwa_people import PeopleClient  # noqa: E402
from urban_car import AckermannFleetClient  # noqa: E402

TICK_SEC = 0.1
WALK_M_S = 1.1
ARRIVED_M = 0.3          # at a walking target
DOOR_M = 0.95            # a seat's door: this far out from the seat's side
BEHIND_M = 2.4           # going round the car: this far behind its centre
AT_STOP_M = 2.5          # the car is at a stop within this distance of it
STOPPED_M_S = 0.05       # and stands when slower than this
STANDING_SEC = 0.5       # for this long


class PlanError(ValueError):
    pass


@dataclass
class Rider:
    name: str
    board_stop: str
    seat: str
    alight_stop: str | None
    wait: tuple[float, float] | None = None
    then: tuple[float, float] | None = None
    state: str = "waiting"        # waiting -> boarding -> riding -> walking -> done
    path: list = field(default_factory=list)  # walking targets in the car's frame (boarding) or the world


@dataclass(frozen=True)
class Plan:
    vehicle: str
    stops: dict[str, tuple[float, float]]
    riders: tuple[Rider, ...]


def _point(value, label: str) -> tuple[float, float]:
    if not (isinstance(value, (list, tuple)) and len(value) == 2):
        raise PlanError(f"{label} must be [east, north]")
    east, north = (float(item) for item in value)
    if not (math.isfinite(east) and math.isfinite(north)):
        raise PlanError(f"{label} must be finite")
    return east, north


def load_plan(scenario: dict) -> Plan:
    """The ``people:`` section of a car route scenario, checked against its route."""
    section = scenario.get("people")
    if not isinstance(section, dict):
        raise PlanError("the scenario has no people: section")
    vehicle = str(section.get("vehicle", "")).strip()
    names = {str(item.get("name", "")) for item in scenario.get("vehicles", []) if isinstance(item, dict)}
    if vehicle not in names:
        raise PlanError(f"people.vehicle {vehicle!r} is not one of the scenario's vehicles {sorted(names)}")
    stops = {}
    for point in scenario.get("route", {}).get("points", []):
        if isinstance(point, dict) and float(point.get("dwell_sec", 0.0)) > 0.0:
            stops[str(point["name"])] = _point([point["east_m"], point["north_m"]], f"stop {point['name']}")
    riders = []
    seats_taken: set[str] = set()
    for index, item in enumerate(section.get("riders") or []):
        label = f"people.riders[{index}]"
        name = str(item.get("name", "")).strip()
        board = item.get("board") or {}
        alight = item.get("alight") or {}
        if not name or any(rider.name == name for rider in riders):
            raise PlanError(f"{label}: names must be non-empty and unique")
        if board.get("stop") not in stops:
            raise PlanError(f"{label}: board.stop must be a stop of the route (a point with dwell_sec): {sorted(stops)}")
        seat = str(board.get("seat", "")).strip()
        if not seat or seat in seats_taken:
            raise PlanError(f"{label}: board.seat must be given and not taken by another rider")
        seats_taken.add(seat)
        if alight and alight.get("stop") not in stops:
            raise PlanError(f"{label}: alight.stop must be a stop of the route: {sorted(stops)}")
        riders.append(Rider(
            name=name, board_stop=board["stop"], seat=seat, alight_stop=alight.get("stop"),
            wait=_point(item["wait"], f"{label}.wait") if "wait" in item else None,
            then=_point(alight["then"], f"{label}.alight.then") if "then" in alight else None,
        ))
    if not riders:
        raise PlanError("people.riders must not be empty")
    return Plan(vehicle, stops, tuple(riders))


def to_world(pose, local: tuple[float, float]) -> tuple[float, float]:
    """A point in the car's frame (x forward, y left) in City World ENU."""
    c, s = math.cos(pose.yaw_rad), math.sin(pose.yaw_rad)
    return pose.east_m + c * local[0] - s * local[1], pose.north_m + s * local[0] + c * local[1]


def to_local(pose, world: tuple[float, float]) -> tuple[float, float]:
    c, s = math.cos(pose.yaw_rad), math.sin(pose.yaw_rad)
    de, dn = world[0] - pose.east_m, world[1] - pose.north_m
    return c * de + s * dn, -s * de + c * dn


def around(start_local: tuple[float, float], end_local: tuple[float, float], width: float) -> list[tuple[float, float]]:
    """Walking targets in the car's frame from one point to another, round the
    back of the car when they are on its two sides (`width`: how far out to keep)."""
    if start_local[1] * end_local[1] >= 0:
        return [end_local]
    here = 1.0 if start_local[1] >= 0 else -1.0
    return [(-BEHIND_M, here * width), (-BEHIND_M, -here * width), end_local]


def boarding_path(person_local: tuple[float, float], seat: tuple[float, float]) -> list[tuple[float, float]]:
    """Walking targets in the car's frame to the seat's door."""
    side = 1.0 if seat[1] >= 0 else -1.0
    door = (seat[0], side * (abs(seat[1]) + DOOR_M))
    return around(person_local, door, abs(seat[1]) + DOOR_M)


class RidePlan:
    def __init__(self, plan: Plan, seats: dict, people: PeopleClient, cars: AckermannFleetClient, log: Path | None):
        missing = sorted({rider.seat for rider in plan.riders} - set(seats))
        if missing:
            raise PlanError(f"{plan.vehicle} has no seats {missing} (its seats: {sorted(seats)})")
        self.plan, self.seats, self.people, self.cars = plan, seats, people, cars
        self.log = log.open("a", encoding="utf-8") if log else None
        self.last_pose = None       # (time, east, north) of the car
        self.standing_since = None  # when the car started standing
        self.at_stop = None         # the stop the car stands at, or None

    def record(self, kind: str, **event) -> None:
        event = {"time_sec": round(self.people.simulation_time(), 2), "event": kind, **event}
        line = json.dumps(event, ensure_ascii=False)
        print(line, flush=True)
        if self.log:
            self.log.write(line + "\n")
            self.log.flush()

    def watch_car(self, now: float, pose) -> None:
        """Which stop (if any) the car stands at now."""
        speed = 0.0
        if self.last_pose is not None and now > self.last_pose[0]:
            speed = math.hypot(pose.east_m - self.last_pose[1], pose.north_m - self.last_pose[2]) / (now - self.last_pose[0])
        self.last_pose = (now, pose.east_m, pose.north_m)
        if speed > STOPPED_M_S:
            self.standing_since = None
            if self.at_stop is not None:
                self.record("departed", vehicle=self.plan.vehicle, stop=self.at_stop)
            self.at_stop = None
            return
        if self.standing_since is None:
            self.standing_since = now
        if self.at_stop is None and now - self.standing_since >= STANDING_SEC:
            for name, point in self.plan.stops.items():
                if math.hypot(pose.east_m - point[0], pose.north_m - point[1]) <= AT_STOP_M:
                    self.at_stop = name
                    self.record("arrived", vehicle=self.plan.vehicle, stop=name)

    def walk(self, person_pose, target) -> tuple[float, float] | None:
        """A velocity towards target, or None when there."""
        de, dn = target[0] - person_pose.east_m, target[1] - person_pose.north_m
        distance = math.hypot(de, dn)
        if distance <= ARRIVED_M:
            return None
        speed = min(WALK_M_S, max(0.3, distance * 1.5))
        return speed * de / distance, speed * dn / distance

    def step(self, now: float) -> bool:
        """One tick; True when every rider is done."""
        car = self.cars.vehicle_poses().get(self.plan.vehicle)
        poses = self.people.poses()
        if car is None:
            return False
        self.watch_car(now, car)
        velocities = {}
        for rider in self.plan.riders:
            pose = poses.get(rider.name)
            if pose is None:
                continue
            seat = tuple(self.seats[rider.seat][:2])
            if rider.state == "waiting":
                if self.at_stop == rider.board_stop:
                    rider.state, rider.path = "boarding", boarding_path(to_local(car, (pose.east_m, pose.north_m)), seat)
                    self.record("boarding", person=rider.name, vehicle=self.plan.vehicle, seat=rider.seat)
                elif rider.wait is not None:
                    velocities[rider.name] = self.walk(pose, rider.wait) or (0.0, 0.0)
            if rider.state == "boarding":
                while rider.path:
                    velocity = self.walk(pose, to_world(car, rider.path[0]))
                    if velocity is not None:
                        velocities[rider.name] = velocity
                        break
                    rider.path.pop(0)
                if not rider.path:
                    velocities[rider.name] = (0.0, 0.0)
                    self.people.ride(rider.name, self.plan.vehicle, rider.seat)
                    rider.state = "riding"
                    self.record("ride", person=rider.name, vehicle=self.plan.vehicle, seat=rider.seat)
            elif rider.state == "riding":
                if rider.alight_stop is not None and self.at_stop == rider.alight_stop:
                    self.people.get_off(rider.name)
                    rider.state = "walking" if rider.then else "done"
                    if rider.then:
                        # Off beside the seat; round the back of the standing car if going the other way
                        side = 1.0 if seat[1] >= 0 else -1.0
                        door = (seat[0], side * (abs(seat[1]) + DOOR_M))
                        rider.path = [to_world(car, point) for point in
                                      around(door, to_local(car, rider.then), abs(seat[1]) + DOOR_M)[:-1]] + [rider.then]
                    self.record("alight", person=rider.name, vehicle=self.plan.vehicle, stop=rider.alight_stop)
            elif rider.state == "walking":
                velocity = None
                while rider.path:
                    velocity = self.walk(pose, rider.path[0])
                    if velocity is not None:
                        break
                    rider.path.pop(0)
                if velocity is None:
                    velocities[rider.name] = (0.0, 0.0)
                    rider.state = "done"
                    self.record("reached", person=rider.name, east_m=round(pose.east_m, 2), north_m=round(pose.north_m, 2))
                else:
                    velocities[rider.name] = velocity
        if velocities:
            self.people.set_velocities(velocities)
        return all(rider.state == "done" or (rider.state == "riding" and rider.alight_stop is None)
                   for rider in self.plan.riders)

    def run(self, duration: float) -> None:
        start = self.people.simulation_time()
        self.record("plan", vehicle=self.plan.vehicle, riders=[rider.name for rider in self.plan.riders])
        while True:
            now = self.people.simulation_time()
            if self.step(now):
                self.record("finished", vehicle=self.plan.vehicle)
                return
            if duration and now - start > duration:
                self.record("timeout", states={rider.name: rider.state for rider in self.plan.riders})
                return
            time.sleep(TICK_SEC)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    result.add_argument("scenario", type=Path, help="a car route scenario with a people: section")
    result.add_argument("--people-pdu-def", type=Path, required=True)
    result.add_argument("--car-pdu-def", type=Path, required=True)
    result.add_argument("--log", type=Path, help="also write the events here (JSON lines)")
    result.add_argument("--duration", type=float, default=0.0, help="give up after this many simulation seconds (0: never)")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        plan = load_plan(yaml.safe_load(args.scenario.read_text(encoding="utf-8")) or {})
    except (OSError, yaml.YAMLError, PlanError, KeyError, TypeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    plant = json.loads((args.people_pdu_def.parent / "people-plant.json").read_text(encoding="utf-8"))
    seats = plant.get("vehicles", {}).get(plan.vehicle, {}).get("seats", {})
    if args.log:
        args.log.parent.mkdir(parents=True, exist_ok=True)
    cars = AckermannFleetClient(args.car_pdu_def, [plan.vehicle]).connect()
    try:
        with PeopleClient(args.people_pdu_def) as people:
            RidePlan(plan, seats, people, cars, args.log).run(args.duration)
    except PlanError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    finally:
        cars.close(stop=False)  # the executor drives the car; never stop it from here
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
