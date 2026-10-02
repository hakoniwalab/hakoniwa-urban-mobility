#!/usr/bin/env python3
"""Ten people at once: the planner loop an AI agent would run.

Every tick it reads everyone's pose, decides each person's velocity (walk
towards a goal, stop there for a while, sometimes wave, then pick another)
and sends all the velocities together. The goals are places of the stall
street (recipes/people/people-crowd.yaml): stall fronts, the squares'
entrances, the street's ends.

    python apps/people/crowd_demo.py --pdu-def <work>/people/people-crowd/config/people-pdudef.json
"""

from __future__ import annotations

import argparse
import math
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from hakoniwa_people import PeopleClient  # noqa: E402

# Places in the stall street (east, north), City World ENU.
GOALS = {
    "orange stall": (-10.0, 3.2), "red stall": (-6.0, 3.2), "blue stall": (-2.0, 3.2),
    "lively square": (-7.5, -1.8), "simple square": (0.5, -1.8),
    "west end": (-13.5, 0.5), "east end": (13.0, 0.5), "open ground": (8.0, -3.0),
}
SPEED = {"child": 0.9}
ARRIVED_M = 0.4


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pdu-def", type=Path, required=True)
    parser.add_argument("--duration", type=float, default=60.0, help="seconds to run")
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()
    rng = random.Random(args.seed)
    with PeopleClient(args.pdu_def) as people:
        who = people.people()
        plans = {name: {"goal": rng.choice(list(GOALS)), "wait_until": 0.0, "look": entry["look"],
                        "stuck_since": None, "last": None} for name, entry in who.items()}
        staff = [name for name, plan in plans.items() if plan["look"] == "staff"]
        for name in staff:  # staff mind their stalls: they wave at passers-by
            plans.pop(name)
            people.set_animation(name, "wave")
        print("people:", {name: entry["look"] for name, entry in who.items()})
        end = time.monotonic() + args.duration
        try:
            while time.monotonic() < end:
                now = time.monotonic()
                poses = people.poses()
                velocities = {}
                for name, plan in plans.items():
                    pose = poses[name]
                    if now < plan["wait_until"]:
                        velocities[name] = (0.0, 0.0)
                        continue
                    if plan.pop("waiting", False):
                        people.set_animation(name, "auto")
                    gx, gy = GOALS[plan["goal"]]
                    dx, dy = gx - pose.east_m, gy - pose.north_m
                    distance = math.hypot(dx, dy)
                    moved = math.inf if plan["last"] is None else math.hypot(
                        pose.east_m - plan["last"][0], pose.north_m - plan["last"][1])
                    plan["last"] = (pose.east_m, pose.north_m)
                    plan["stuck_since"] = (plan["stuck_since"] or now) if moved < 0.01 else None
                    if distance < ARRIVED_M or (plan["stuck_since"] and now - plan["stuck_since"] > 2.0):
                        # There (or blocked): stay a little, sometimes wave, then go elsewhere.
                        plan["wait_until"] = now + rng.uniform(2.0, 6.0)
                        plan["goal"] = rng.choice([goal for goal in GOALS if goal != plan["goal"]])
                        plan["stuck_since"], plan["waiting"] = None, True
                        people.set_animation(name, "wave" if rng.random() < 0.3 else "auto")
                        velocities[name] = (0.0, 0.0)
                        continue
                    speed = min(SPEED.get(plan["look"], 1.2), max(0.4, distance))
                    velocities[name] = (speed * dx / distance, speed * dy / distance)
                people.set_velocities(velocities)
                time.sleep(0.1)
        finally:
            people.stop_all()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
