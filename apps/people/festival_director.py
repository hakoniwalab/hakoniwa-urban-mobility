#!/usr/bin/env python3
"""Run a scene of people and cars from outside: the festival director.

The loop an agent runs over the Hakoniwa People API and the Urban Car API,
driven by a scene file (recipes/people/scenes/*.yaml): staff wave at their
stalls, visitors and children go from stall to stall and stop for a while,
some people ride a car, and each car follows its route, slowing down and
stopping for a person ahead; a shuttle lets a visitor get on at a stop and
off at the next. Cars the scene does not drive (driven with a controller)
are watched too. Near misses (a moving car close to a person) and contacts
(the people plant's contact events) are danger events: printed
and written to logs/festival-director.jsonl of the Car Recipe, with Hakoniwa
time.

    python apps/people/festival_director.py recipes/people/scenes/sapporo-festival.yaml \\
        --people-pdu-def <work>/config/people/people-pdudef.json \\
        --car-pdu-def <work>/config/car/urban-car-pdudef.json --duration 120
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import math
import random
import sys
import time
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "car"))

from hakoniwa_people import PeopleClient  # noqa: E402
from urban_car import AckermannFleetClient  # noqa: E402

TICK_SEC = 0.1
ARRIVED_M = 0.5
WAYPOINT_M = 2.0
LOOKAHEAD_M = 3.0


def matches(name: str, patterns) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in ([patterns] if isinstance(patterns, str) else patterns))


class Director:
    def __init__(self, scene: dict, people: PeopleClient, cars: AckermannFleetClient, log: Path, seed: int):
        self.scene, self.people, self.cars = scene, people, cars
        self.rng = random.Random(seed)
        self.places = {name: tuple(point) for name, point in scene["places"].items()}
        self.log = open(log, "a", encoding="utf-8")
        self.roles = {}
        riders = {ride["person"] for ride in scene.get("rides", [])}
        for name in people.names():
            if name in riders:
                continue
            for role, spec in scene["roles"].items():
                if matches(name, spec["match"]):
                    self.roles[name] = role
                    break
        self.plans = {name: {"goal": None, "until": 0.0} for name in self.roles}
        self.routes = {name: {"index": 0, "until": 0.0, "rider": None, "boarding": None, "cap": 0.0}
                       for name in scene.get("vehicles", {})}
        self.near = set()  # (car, person) pairs already reported as near misses
        self.riding = {ride["person"] for ride in scene.get("rides", [])}  # people on a car now
        self.boarding = {}  # person -> the shuttle it walks to
        self.queues = {}  # stall place -> the people in its line, the head first
        self.seats = self.make_seats(scene)  # stools around the squares' tables
        self.seated = {}  # person -> its seat

    def record(self, kind: str, **event) -> None:
        event = {"time_sec": round(self.people.simulation_time(), 2), "event": kind, **event}
        self.log.write(json.dumps(event, ensure_ascii=False) + "\n")
        self.log.flush()
        print(json.dumps(event, ensure_ascii=False), flush=True)

    # --- People --------------------------------------------------------------------

    def start(self) -> None:
        # A clean start: whoever an earlier run left on a car or a seat stands up.
        rides = {ride["person"] for ride in self.scene.get("rides", [])}
        for name, entry in self.people.people().items():
            if entry["ride"] and name not in rides:
                self.people.get_off(name)
        self.people.stop_all()
        for ride in self.scene.get("rides", []):
            self.people.ride(ride["person"], ride["vehicle"], ride.get("seat", "driver"))
        self.record("scene", id=self.scene["id"], people=len(self.roles), rides=len(self.scene.get("rides", [])))

    def step_people(self, now: float, poses: dict, car_poses: dict) -> dict:
        velocities = {}
        for name, role in self.roles.items():
            if name in self.riding:
                continue
            if name in self.boarding and poses.get(name) and car_poses.get(self.boarding[name]):
                pose, car = poses[name], car_poses[self.boarding[name]]
                plan = self.plans[name]
                shuttle = self.scene["vehicles"][self.boarding[name]].get("shuttle", {})
                # Round the stalls by the shuttle's via points (in order), unless
                # already on the car's side of them.
                beyond = shuttle.get("via_unless_north_below")
                default = [] if beyond is not None and pose.north_m < beyond else list(shuttle.get("via", []))
                via = plan.setdefault("via", default)
                while via and math.hypot(via[0][0] - pose.east_m, via[0][1] - pose.north_m) < 1.0:
                    via.pop(0)
                tx, ty = via[0] if via else (car.east_m, car.north_m)
                dx, dy = tx - pose.east_m, ty - pose.north_m
                distance = math.hypot(dx, dy) or 1.0
                speed = min(1.3, max(0.4, distance))
                velocities[name] = (speed * dx / distance, speed * dy / distance)
                continue
            spec, plan = self.scene["roles"][role], self.plans[name]
            if role == "staff":
                if now >= plan["until"]:
                    waving = plan.get("waving", False)
                    self.people.set_animation(name, "auto" if waving else "wave")
                    plan["waving"] = not waving
                    low, high = spec.get("wave_every_sec", [6, 14])
                    plan["until"] = now + (self.rng.uniform(2, 4) if not waving else self.rng.uniform(low, high))
                continue
            pose = poses.get(name)
            if pose is None:
                continue
            velocities[name] = self.step_walker(now, name, spec, plan, pose)
        return self.keep_apart(velocities, poses)

    # --- Seats --------------------------------------------------------------------------

    @staticmethod
    def make_seats(scene: dict) -> list[dict]:
        """The stools around each table of each square, in the City World: the
        square's frame (x = -u, y; Environment Studio's open_space) turned by
        its yaw; each stool four times around its table."""
        seats = []
        for index, square in enumerate(scene.get("squares", [])):
            px, py, yaw = square["pose"]
            c, s = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
            world = lambda u, y: (px + c * -u - s * y, py + s * -u + c * y)
            for t, table in enumerate(scene.get("tables", [])):
                cu, cy = table["center"]
                centre = world(cu, cy)
                for k in range(4):
                    a = math.radians(45 + 90 * k)
                    r = table.get("stool_radius_m", 0.72)
                    seat = world(cu + r * math.cos(a), cy + r * math.sin(a))
                    out = (seat[0] - centre[0], seat[1] - centre[1])
                    norm = math.hypot(*out)
                    approach = (seat[0] + out[0] / norm * 0.45, seat[1] + out[1] / norm * 0.45)
                    seats.append({"pos": seat, "table": (index, t), "centre": centre, "approach": approach,
                                  "taken": None})
        return seats

    def take_seat(self, name: str, plan: dict) -> bool:
        free = [seat for seat in self.seats if seat["taken"] is None]
        if not free or self.rng.random() > self.scene.get("seating", {}).get("chance", 0.5):
            return False
        # Prefer a table where someone already sits (to talk), else any.
        busy = {seat["table"] for seat in self.seats if seat["taken"]}
        choice = [seat for seat in free if seat["table"] in busy] or free
        seat = self.rng.choice(choice)
        seat["taken"] = name
        plan.update(seat=seat, seat_phase="approach")
        return True

    def step_seated(self, now: float, name: str, plan: dict, pose) -> tuple:
        seat, phase = plan["seat"], plan["seat_phase"]
        if phase == "approach":
            gx, gy = seat["approach"]
            dx, dy = gx - pose.east_m, gy - pose.north_m
            distance = math.hypot(dx, dy)
            moved = plan.get("from")
            plan["from"] = (pose.east_m, pose.north_m)
            stuck = moved is not None and math.hypot(pose.east_m - moved[0], pose.north_m - moved[1]) < 0.005
            plan["stuck"] = plan.get("stuck", 0) + 1 if stuck else 0
            if distance < 0.35 or (plan["stuck"] > 15 and distance < 0.6):
                self.people.set_animation(name, "sit")  # no longer colliding: onto the stool
                plan.update(seat_phase="onto", until_seat=now + 4.0, stuck=0)
                return 0.0, 0.0
            if plan["stuck"] > 60:  # cannot get there: give the seat up
                seat["taken"] = None
                plan.update(seat=None, seat_phase=None, goal=None, stuck=0)
                return 0.0, 0.0
            speed = min(1.1, max(0.3, distance))
            return speed * dx / distance, speed * dy / distance
        if phase == "onto":
            dx, dy = seat["pos"][0] - pose.east_m, seat["pos"][1] - pose.north_m
            distance = math.hypot(dx, dy)
            if now < plan["until_seat"] and distance > 0.05:
                speed = min(1.0, distance * 2.0)
                return speed * dx / distance, speed * dy / distance
            low, high = self.scene.get("seating", {}).get("sit_sec", [20, 45])
            plan.update(seat_phase="seated", leave_at=now + self.rng.uniform(low, high), talking=False)
            return 0.0, 0.0
        # Seated: face the table, talk when someone else sits at it, then get up.
        if now >= plan["leave_at"]:
            seat["taken"] = None
            self.people.set_animation(name, "auto")
            plan.update(seat=None, seat_phase=None, goal=None, until=now + 0.5)
            return 0.0, 0.0
        company = any(other["taken"] and other["taken"] != name and other["table"] == seat["table"]
                      and self.plans.get(other["taken"], {}).get("seat_phase") == "seated"
                      for other in self.seats)
        if company != plan["talking"]:
            self.people.set_animation(name, "sit_talk" if company else "sit")
            plan["talking"] = company
        want = math.atan2(seat["centre"][1] - seat["pos"][1], seat["centre"][0] - seat["pos"][0])
        turn = math.atan2(math.sin(want - pose.yaw_rad), math.cos(want - pose.yaw_rad))
        return 0.0, 0.0, (2.5 * turn if abs(turn) > 0.08 else 0.0)

    # --- Queues and personal space -----------------------------------------------------

    def queue_spec(self, place: str) -> dict | None:
        for pattern, spec in self.scene.get("queues", {}).items():
            if fnmatch.fnmatch(place, pattern):
                return spec
        return None

    def choose_goal(self, name: str, plan: dict) -> None:
        for _ in range(8):
            goal = self.rng.choice([place for place in self.places if place != plan.get("last")])
            spec = self.queue_spec(goal)
            if spec is None:
                plan.update(goal=goal, queue=None)
                return
            line = self.queues.setdefault(goal, [])
            if len(line) < spec.get("max", 4):
                line.append(name)
                plan.update(goal=goal, queue=goal)
                return
        plan.update(goal=None, until=time.monotonic() + 2.0)

    def leave_queue(self, name: str, plan: dict) -> None:
        if plan.get("queue") and name in self.queues.get(plan["queue"], []):
            self.queues[plan["queue"]].remove(name)
        plan["queue"] = None

    def step_walker(self, now: float, name: str, spec: dict, plan: dict, pose) -> tuple:
        if plan.get("seat"):
            return self.step_seated(now, name, plan, pose)
        if now < plan["until"]:
            return 0.0, 0.0
        if plan.pop("staying", False):
            self.people.set_animation(name, "auto")
        if plan.pop("served", False):
            self.leave_queue(name, plan)
            plan.update(last=plan["goal"], goal=None)
            if self.take_seat(name, plan):  # bought something: eat it at a square's table
                return self.step_seated(now, name, plan, pose)
        if plan["goal"] is None:
            self.choose_goal(name, plan)
            if plan["goal"] is None:
                return 0.0, 0.0
        gx, gy = self.places[plan["goal"]]
        index = 0
        if plan.get("queue"):
            queue = self.queue_spec(plan["queue"])
            index = self.queues[plan["queue"]].index(name)
            ax, ay = queue["along"]
            gx, gy = gx + ax * queue.get("spacing_m", 0.85) * index, gy + ay * queue.get("spacing_m", 0.85) * index
        dx, dy = gx - pose.east_m, gy - pose.north_m
        distance = math.hypot(dx, dy)
        moved = plan.get("from")
        plan["from"] = (pose.east_m, pose.north_m)
        stuck = moved is not None and math.hypot(pose.east_m - moved[0], pose.north_m - moved[1]) < 0.005
        plan["stuck"] = plan.get("stuck", 0) + 1 if stuck and distance > ARRIVED_M else 0
        if plan.get("queue"):
            if distance < ARRIVED_M:
                plan["stuck"] = 0
                if index == 0:  # at the counter: served for a while, then off
                    low, high = self.queue_spec(plan["queue"]).get("serve_sec", [4, 9])
                    plan.update(until=now + self.rng.uniform(low, high), served=True, staying=True)
                    if self.rng.random() < spec.get("wave_chance", 0.0):
                        self.people.set_animation(name, "wave")
                return 0.0, 0.0  # waiting in line
            if plan["stuck"] > 100:  # could not get there: give up the line
                self.leave_queue(name, plan)
                plan.update(goal=None, stuck=0)
                return 0.0, 0.0
        elif distance < ARRIVED_M or plan["stuck"] > 25:
            low, high = spec.get("stay_sec", [3, 8])
            plan.update(until=now + self.rng.uniform(low, high), last=plan["goal"], goal=None, staying=True,
                        stuck=0)
            if self.rng.random() < spec.get("wave_chance", 0.0):
                self.people.set_animation(name, "wave")
            return 0.0, 0.0
        speed = min(spec.get("speed", 1.1), max(0.3, distance))
        return speed * dx / distance, speed * dy / distance

    def keep_apart(self, velocities: dict, poses: dict) -> dict:
        """Walkers step around each other: a push away from anyone closer than
        the personal space, and slower behind someone ahead."""
        space = self.scene.get("personal_space_m", 0.9)
        result = {}
        for name, velocity in velocities.items():
            vx, vy = velocity[0], velocity[1]
            pose = poses.get(name)
            if pose is None or (vx == 0.0 and vy == 0.0) or len(velocity) > 2 or self.plans.get(name, {}).get("seat"):
                result[name] = velocity
                continue
            speed = math.hypot(vx, vy)
            push_x = push_y = 0.0
            for other, opose in poses.items():
                if other == name or other in self.riding:
                    continue
                rx, ry = pose.east_m - opose.east_m, pose.north_m - opose.north_m
                gap = math.hypot(rx, ry)
                if 1e-6 < gap < space:
                    weight = (space - gap) / space
                    push_x, push_y = push_x + rx / gap * weight, push_y + ry / gap * weight
                    if (-rx * vx - ry * vy) / (gap * speed) > 0.7:  # right ahead
                        speed *= 0.6
            vx, vy = vx / math.hypot(vx, vy) * speed + 0.9 * push_x, vy / math.hypot(vx, vy) * speed + 0.9 * push_y
            norm = math.hypot(vx, vy)
            cap = 1.6
            result[name] = (vx * cap / norm, vy * cap / norm) if norm > cap else (vx, vy)
        return result

    # --- Cars ----------------------------------------------------------------------

    def watch(self, poses: dict, car_poses: dict) -> None:
        """Near misses of every car (also those driven with a controller)."""
        near_miss = self.scene.get("safety", {}).get("near_miss_m", 1.5)
        for name, pose in car_poses.items():
            for person, ppose in poses.items():
                if person in self.riding:
                    continue
                gap = math.hypot(ppose.east_m - pose.east_m, ppose.north_m - pose.north_m) - 1.0
                key = (name, person)
                if gap < near_miss and self.speeds.get(name, 0.0) > 0.3:
                    if key not in self.near:
                        self.near.add(key)
                        self.record("near_miss", vehicle=name, person=person, distance_m=round(gap, 2),
                                    speed_m_s=round(self.speeds.get(name, 0.0), 2))
                elif gap > near_miss + 2.0:
                    self.near.discard(key)

    def at_stop(self, now: float, name: str, spec: dict, state: dict, poses: dict, pose) -> None:
        """A shuttle stop: the rider gets off, a visitor nearby walks over to get on."""
        shuttle = spec.get("shuttle")
        if not shuttle:
            self.record("stop", vehicle=name, seconds=spec["stops"][state["index"]])
            return
        if state["rider"]:
            self.people.get_off(state["rider"])
            self.riding.discard(state["rider"])
            # Off for a walk: not straight back on (the next one gets the seat).
            self.plans[state["rider"]].update(goal=None, until=now + 1.0, no_ride_until=now + 60.0)
            self.record("get_off", vehicle=name, person=state["rider"])
            state["rider"] = None
        def free(person):  # walking about, or waiting behind the head of a line
            plan = self.plans[person]
            if now < plan.get("no_ride_until", 0.0):
                return False
            line = self.queues.get(plan.get("queue") or "", [])
            return not plan.get("seat") and (not plan.get("queue") or line.index(person) > 0)

        waiting = [(math.hypot(p.east_m - pose.east_m, p.north_m - pose.north_m), person)
                   for person, p in poses.items()
                   if self.roles.get(person) in ("visitor", "child") and person not in self.riding
                   and person not in self.boarding and free(person)]
        near = sorted(item for item in waiting if item[0] < shuttle.get("board_within_m", 15))
        self.record("shuttle_stop", vehicle=name, nearby=len(near))
        if near:
            self.leave_queue(near[0][1], self.plans[near[0][1]])
            state["boarding"] = near[0][1]
            self.boarding[near[0][1]] = name
            state["cap"] = now + 60.0
            self.record("boarding", vehicle=name, person=near[0][1], distance_m=round(near[0][0], 1))

    def step_cars(self, now: float, poses: dict, car_poses: dict) -> None:
        safety = self.scene.get("safety", {})
        for name, spec in self.scene.get("vehicles", {}).items():
            pose, state = car_poses.get(name), self.routes[name]
            if pose is None:
                continue
            route = spec["route"]
            boarding = state["boarding"]
            if boarding:
                person = poses.get(boarding)
                if person and math.hypot(person.east_m - pose.east_m, person.north_m - pose.north_m) < 2.0:
                    self.people.ride(boarding, name, spec["shuttle"].get("seat", "passenger"))
                    self.plans[boarding].pop("via", None)
                    self.riding.add(boarding)
                    self.boarding.pop(boarding, None)
                    state.update(rider=boarding, boarding=None)
                    self.record("ride", vehicle=name, person=boarding)
                elif now < state["cap"]:
                    state["until"] = max(state["until"], now + 0.5)  # wait for them
                else:
                    self.boarding.pop(boarding, None)
                    self.plans[boarding].pop("via", None)
                    state["boarding"] = None
            if now < state["until"]:
                self.cars.send(name, 0.0, 0.0)
                continue
            target = route[state["index"]]
            if math.hypot(target[0] - pose.east_m, target[1] - pose.north_m) < WAYPOINT_M:
                stop = spec.get("stops", {}).get(state["index"])
                if stop:
                    state["until"] = now + stop
                    self.at_stop(now, name, spec, state, poses, pose)
                state["index"] = (state["index"] + 1) % len(route)
                if stop:
                    self.cars.send(name, 0.0, 0.0)
                    continue
                target = route[state["index"]]
            # Pure pursuit towards a point LOOKAHEAD_M along the way to the target.
            dx, dy = target[0] - pose.east_m, target[1] - pose.north_m
            heading = math.atan2(dy, dx)
            alpha = math.atan2(math.sin(heading - pose.yaw_rad), math.cos(heading - pose.yaw_rad))
            steering = math.atan2(2 * spec.get("wheelbase_m", 1.5) * math.sin(alpha), LOOKAHEAD_M)
            steering = max(-spec.get("max_steering_rad", 0.6), min(spec.get("max_steering_rad", 0.6), steering))
            speed = spec.get("speed", 1.5) * max(0.3, math.cos(alpha))
            # People ahead: slow down, stop; a moving car close to someone is a near miss.
            nearest = math.inf
            c, s = math.cos(pose.yaw_rad), math.sin(pose.yaw_rad)
            for person, ppose in poses.items():
                if person in self.riding or person == state["boarding"]:
                    continue
                rx, ry = ppose.east_m - pose.east_m, ppose.north_m - pose.north_m
                ahead, side = rx * c + ry * s, -rx * s + ry * c
                if 0 < ahead and abs(side) < 1.6:
                    nearest = min(nearest, ahead - 1.1)
            if nearest < safety.get("stop_within_m", 2.5):
                speed = 0.0
            elif nearest < safety.get("slow_within_m", 6.0):
                speed = min(speed, 0.6)
            self.cars.send(name, speed, steering)

    def run(self, duration: float) -> None:
        self.start()
        self.speeds, previous = {}, {}
        end = time.monotonic() + duration
        while time.monotonic() < end:
            now = time.monotonic()
            poses = self.people.poses()
            car_poses = self.cars.vehicle_poses()
            for name, pose in car_poses.items():
                if name in previous:
                    self.speeds[name] = math.hypot(pose.east_m - previous[name].east_m,
                                                   pose.north_m - previous[name].north_m) / TICK_SEC
            previous = car_poses
            self.people.set_velocities(self.step_people(now, poses, car_poses))
            self.step_cars(now, poses, car_poses)
            self.watch(poses, car_poses)
            for event in self.people.contacts():
                if event["kind"] == "vehicle" and event["started"]:
                    self.record("contact", vehicle=event["other"], person=event["self"],
                                relative_speed_m_s=round(event["relative_speed"], 2), position=event["position"])
            time.sleep(TICK_SEC)

    def finish(self) -> None:
        self.cars.stop()
        for person in list(self.riding):
            self.people.get_off(person)
        self.people.stop_all()
        self.record("end")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scene", type=Path)
    parser.add_argument("--people-pdu-def", type=Path, required=True)
    parser.add_argument("--car-pdu-def", type=Path, required=True)
    parser.add_argument("--duration", type=float, default=120.0)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()
    scene = yaml.safe_load(args.scene.read_text(encoding="utf-8"))
    log = args.people_pdu_def.resolve().parents[2] / "logs/festival-director.jsonl"
    with PeopleClient(args.people_pdu_def) as people, AckermannFleetClient(
            args.car_pdu_def, list(scene.get("vehicles", {}))) as cars:
        director = Director(scene, people, cars, log, args.seed)
        try:
            director.run(args.duration)
        finally:
            director.finish()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
