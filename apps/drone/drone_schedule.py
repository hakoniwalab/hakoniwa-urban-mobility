#!/usr/bin/env python3
"""Fly a Drone by a schedule on Hakoniwa time (the Drone's `schedule` control).

The schedule is the `drones:` section of a YAML file: a car route scenario's
(so one file holds the Cars' route, the people's rides and the Drone's
flight) or a file of its own. Times are simulation time: like the Car
scenario executor this program reads the Hakoniwa clock, so every run flies
the same.

    drones:
      - name: Drone-1
        start_delay_sec: 5.0      # on the ground first (settled after spawn)
        speed_m_s: 3.0            # default for every leg
        tolerance_m: 0.5
        # hop_m: 3.0              # optional: fly each leg in hops of at most this
        loop_count: forever       # or a number of rounds of the waypoints
        takeoff: {rise_m: 15.0, hold_sec: 3.0}
        waypoints:
          - {name: over-bridge, east_m: -40.0, north_m: -70.0, up_m: 265.0,
             yaw_deg: 200.0, speed_m_s: 4.0, hold_sec: 20.0}
          - {name: back, east_m: 82.0, north_m: -5.0, rise_m: 15.0, hold_sec: 5.0,
             # optional, on the leg from this point to the next (apps/drone/flight_events.py):
             wind: {towards_deg: 90.0, speed_m_s: 8.0},     # blows north while the Drone is in the zone
             fault: {rotors: [0], scale: 0.0},             # rotor 0 stops once the Drone enters it
             zone_width_m: 4.0, zone_height_m: 4.0}        # this leg's zone (else the drone's)
        zone_width_m: 2.0         # the zones round the legs: width across, height round the line
        zone_height_m: 2.0
        land: true                # after the last round: back over the takeoff point, land
        # or land elsewhere, approached rise_m (default takeoff.rise_m) above it;
        # up_m is the Drone's height when down there (as the spawn's):
        # land: {east_m: 89.25, north_m: -3.82, up_m: 248.93, rise_m: 10.0}

Positions are the Urban World's: east_m / north_m from its origin, and the
height either up_m (the World's height, as the spawn's) or rise_m (above the
takeoff point). yaw_deg is the Urban convention (east 0, counter-clockwise);
left out, the Drone faces where it flies. Drone Core's RPC frame is ROS
(x north, y west, z up; yaw 0 north), so a point is (north, -east, up).

    python apps/drone/drone_schedule.py --check --schedule <file> --drone Drone-1 \\
        --spawn 82,-5,247.28
prints the flight without flying.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
import time
import traceback
from typing import Any, Iterator

import yaml

import flight_events


class ScheduleError(RuntimeError):
    pass


# --- The schedule ------------------------------------------------------------------

DRONE_KEYS = {"name", "start_delay_sec", "speed_m_s", "tolerance_m", "hop_m", "loop_count", "takeoff", "waypoints", "land",
              "zone_width_m", "zone_height_m", "contact_fault"}
LAND_KEYS = {"east_m", "north_m", "up_m", "rise_m"}
WAYPOINT_KEYS = {"name", "east_m", "north_m", "up_m", "rise_m", "yaw_deg", "speed_m_s", "hold_sec",
                 "wind", "fault", "zone_width_m", "zone_height_m"}


def _number(value: Any, where: str, *, positive: bool = False, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ScheduleError(f"{where} must be a number")
    if positive and value <= 0:
        raise ScheduleError(f"{where} must be positive")
    if minimum is not None and value < minimum:
        raise ScheduleError(f"{where} must be at least {minimum}")
    return float(value)


def load_schedule(path: Path, drone: str) -> dict:
    """The named Drone's entry of a file's drones: section, checked."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ScheduleError(f"cannot read {path}: {exc}") from exc
    drones = data.get("drones")
    if not isinstance(drones, list) or not drones:
        raise ScheduleError(f"{path} has no drones: section")
    entries = [entry for entry in drones if isinstance(entry, dict) and entry.get("name", drone) == drone]
    if len(entries) != 1:
        names = [entry.get("name") for entry in drones if isinstance(entry, dict)]
        raise ScheduleError(f"{path}: no single drones: entry for {drone} (entries: {names})")
    return check_schedule(entries[0], f"{path.name} drones[{drone}]")


def check_schedule(entry: dict, where: str) -> dict:
    unknown = set(entry) - DRONE_KEYS
    if unknown:
        raise ScheduleError(f"{where}: unknown keys {sorted(unknown)} (known: {sorted(DRONE_KEYS)})")
    schedule = {
        "start_delay_sec": _number(entry.get("start_delay_sec", 5.0), f"{where}.start_delay_sec", minimum=0.0),
        "speed_m_s": _number(entry.get("speed_m_s", 2.0), f"{where}.speed_m_s", positive=True),
        "tolerance_m": _number(entry.get("tolerance_m", 0.5), f"{where}.tolerance_m", positive=True),
        # Optional: fly each leg in hops of at most this (a vehicle whose position
        # loop does not hold long gotos). Off by default: the EAMS Hexa flies
        # long legs with its API tuning (config/drone/hexa/controller-api-tuning.txt).
        "hop_m": (_number(entry["hop_m"], f"{where}.hop_m", positive=True) if "hop_m" in entry else None),
        # The wind / fault zones' size round each leg (apps/drone/flight_events.py).
        "zone_width_m": _number(entry.get("zone_width_m", flight_events.DEFAULT_ZONE_WIDTH_M),
                                f"{where}.zone_width_m", positive=True),
        "zone_height_m": _number(entry.get("zone_height_m", flight_events.DEFAULT_ZONE_HEIGHT_M),
                                 f"{where}.zone_height_m", positive=True),
    }
    # Optional: when the Drone touches something in flight (its status PDU's
    # collided_counts goes up), the rotor on the wall side stops (scale 0) or
    # loses thrust, and stays so (flight_events.wall_side_rotor).
    contact = entry.get("contact_fault")
    if contact is not None:
        if not isinstance(contact, dict) or set(contact) - {"scale"}:
            raise ScheduleError(f"{where}.contact_fault takes scale (0 stopped .. 1)")
        scale = _number(contact.get("scale", 0.0), f"{where}.contact_fault.scale", minimum=0.0)
        if scale > 1.0:
            raise ScheduleError(f"{where}.contact_fault.scale must be at most 1")
        schedule["contact_fault"] = {"scale": scale}
    loops = entry.get("loop_count", 1)
    if loops == "forever":
        schedule["loop_count"] = None
    elif isinstance(loops, int) and not isinstance(loops, bool) and loops >= 1:
        schedule["loop_count"] = loops
    else:
        raise ScheduleError(f"{where}.loop_count must be a positive whole number or forever")
    takeoff = entry.get("takeoff", {})
    if not isinstance(takeoff, dict) or set(takeoff) - {"rise_m", "hold_sec"}:
        raise ScheduleError(f"{where}.takeoff takes rise_m and hold_sec")
    schedule["takeoff"] = {
        "rise_m": _number(takeoff.get("rise_m", 10.0), f"{where}.takeoff.rise_m", positive=True),
        "hold_sec": _number(takeoff.get("hold_sec", 2.0), f"{where}.takeoff.hold_sec", minimum=0.0),
    }
    waypoints = entry.get("waypoints")
    if not isinstance(waypoints, list) or not waypoints:
        raise ScheduleError(f"{where}.waypoints must list at least one point")
    schedule["waypoints"] = []
    for index, point in enumerate(waypoints):
        at = f"{where}.waypoints[{index}]"
        if not isinstance(point, dict):
            raise ScheduleError(f"{at} must be a mapping")
        unknown = set(point) - WAYPOINT_KEYS
        if unknown:
            raise ScheduleError(f"{at}: unknown keys {sorted(unknown)} (known: {sorted(WAYPOINT_KEYS)})")
        if ("up_m" in point) == ("rise_m" in point):
            raise ScheduleError(f"{at} needs one height: up_m (the World's) or rise_m (above the takeoff point)")
        checked = {
            "name": str(point.get("name", f"point-{index + 1}")),
            "east_m": _number(point.get("east_m"), f"{at}.east_m"),
            "north_m": _number(point.get("north_m"), f"{at}.north_m"),
            "speed_m_s": _number(point.get("speed_m_s", schedule["speed_m_s"]), f"{at}.speed_m_s", positive=True),
            "hold_sec": _number(point.get("hold_sec", 0.0), f"{at}.hold_sec", minimum=0.0),
        }
        if "up_m" in point:
            checked["up_m"] = _number(point["up_m"], f"{at}.up_m")
        else:
            checked["rise_m"] = _number(point["rise_m"], f"{at}.rise_m")
        if "yaw_deg" in point:
            checked["yaw_deg"] = _number(point["yaw_deg"], f"{at}.yaw_deg")
        for key in ("zone_width_m", "zone_height_m"):
            if key in point:
                checked[key] = _number(point[key], f"{at}.{key}", positive=True)
        if "wind" in point:
            wind = point["wind"]
            if not isinstance(wind, dict) or set(wind) != {"towards_deg", "speed_m_s"}:
                raise ScheduleError(f"{at}.wind takes towards_deg and speed_m_s")
            checked["wind"] = flight_events.Wind(_number(wind["towards_deg"], f"{at}.wind.towards_deg"),
                                                 _number(wind["speed_m_s"], f"{at}.wind.speed_m_s", minimum=0.0))
        if "fault" in point:
            fault = point["fault"]
            rotors = fault.get("rotors") if isinstance(fault, dict) else None
            if (not isinstance(fault, dict) or set(fault) - {"rotors", "scale"} or not isinstance(rotors, list) or not rotors
                    or not all(isinstance(rotor, int) and not isinstance(rotor, bool) and rotor >= 0 for rotor in rotors)):
                raise ScheduleError(f"{at}.fault takes rotors (numbers from 0) and optionally scale (0 stopped .. 1)")
            scale = _number(fault.get("scale", 0.0), f"{at}.fault.scale", minimum=0.0)
            if scale > 1.0:
                raise ScheduleError(f"{at}.fault.scale must be at most 1")
            checked["fault"] = flight_events.Fault(tuple(sorted(set(rotors))), scale)
        schedule["waypoints"].append(checked)
    land = entry.get("land", True)
    if isinstance(land, dict):
        # Somewhere other than the takeoff point: approached rise_m above it.
        if set(land) - LAND_KEYS or not {"east_m", "north_m", "up_m"} <= set(land):
            raise ScheduleError(f"{where}.land takes east_m, north_m, up_m and optionally rise_m")
        land = {
            "east_m": _number(land["east_m"], f"{where}.land.east_m"),
            "north_m": _number(land["north_m"], f"{where}.land.north_m"),
            "up_m": _number(land["up_m"], f"{where}.land.up_m"),
            "rise_m": _number(land.get("rise_m", schedule["takeoff"]["rise_m"]), f"{where}.land.rise_m",
                              positive=True),
        }
    elif not isinstance(land, bool):
        raise ScheduleError(f"{where}.land must be true, false or a place to land")
    if land and schedule["loop_count"] is None:
        raise ScheduleError(f"{where}: a schedule that loops forever never lands (set land: false)")
    schedule["land"] = land
    return schedule


# --- Frames ------------------------------------------------------------------------

def to_ros(east_m: float, north_m: float, up_m: float) -> tuple[float, float, float]:
    """Urban ENU -> Drone Core's RPC frame (ROS: x north, y west, z up)."""
    return (north_m, -east_m, up_m)


def ros_yaw(urban_yaw_deg: float) -> float:
    """Urban yaw (east 0, counter-clockwise) -> ROS yaw (north 0, counter-clockwise), in -180..180."""
    return (urban_yaw_deg - 90.0 + 180.0) % 360.0 - 180.0


def heading(from_en: tuple[float, float], to_en: tuple[float, float], fallback: float) -> float:
    """The Urban yaw from one point towards another (fallback when they coincide)."""
    de, dn = to_en[0] - from_en[0], to_en[1] - from_en[1]
    if math.hypot(de, dn) < 0.5:
        return fallback
    return math.degrees(math.atan2(dn, de))


# --- The flight as steps -----------------------------------------------------------

def steps(schedule: dict, spawn_enu: tuple[float, float, float], spawn_yaw_deg: float = 0.0) -> Iterator[dict]:
    """The flight, step by step (endless for loop_count forever):
    {"op": "wait"|"set_ready"|"takeoff"|"goto"|"land", ...}, positions in Urban ENU."""
    east0, north0, up0 = spawn_enu
    cruise = up0 + schedule["takeoff"]["rise_m"]
    yield {"op": "wait", "sec": schedule["start_delay_sec"], "label": "on the ground"}
    yield {"op": "set_ready"}
    yield {"op": "takeoff", "up_m": cruise}
    if schedule["takeoff"]["hold_sec"] > 0:
        yield {"op": "wait", "sec": schedule["takeoff"]["hold_sec"], "label": "after takeoff"}
    here, here_up, yaw = (east0, north0), cruise, spawn_yaw_deg
    round_number = 0
    while schedule["loop_count"] is None or round_number < schedule["loop_count"]:
        round_number += 1
        for point in schedule["waypoints"]:
            up = point["up_m"] if "up_m" in point else up0 + point["rise_m"]
            target = (point["east_m"], point["north_m"])
            yaw = point.get("yaw_deg", heading(here, target, yaw))
            yield from _hops({"op": "goto", "name": point["name"], "round": round_number, "east_m": target[0],
                              "north_m": target[1], "up_m": up, "yaw_deg": yaw, "speed_m_s": point["speed_m_s"],
                              "tolerance_m": schedule["tolerance_m"]}, (*here, here_up), schedule["hop_m"])
            here, here_up = target, up
            if point["hold_sec"] > 0:
                yield {"op": "wait", "sec": point["hold_sec"], "label": f"at {point['name']}"}
    if schedule["land"]:
        name, (east1, north1, up1, over) = "over-takeoff-point", (east0, north0, up0, cruise)
        if isinstance(schedule["land"], dict):
            place = schedule["land"]
            name = "over-landing-point"
            east1, north1, up1 = place["east_m"], place["north_m"], place["up_m"]
            over = up1 + place["rise_m"]
        yaw = heading(here, (east1, north1), yaw)
        yield from _hops({"op": "goto", "name": name, "round": round_number, "east_m": east1,
                          "north_m": north1, "up_m": over, "yaw_deg": yaw, "speed_m_s": schedule["speed_m_s"],
                          "tolerance_m": schedule["tolerance_m"]}, (*here, here_up), schedule["hop_m"])
        yield {"op": "land", "up_m": up1}


def flight_path(schedule: dict, spawn_enu: tuple[float, float, float], spawn_yaw_deg: float = 0.0) -> list[dict]:
    """The flight's line for a viewer, from the same steps the Drone flies.

    [{east_m, north_m, up_m, kind, label, ...}] in order: the takeoff stand
    and the climb (kind "takeoff", label "T"), the waypoints of the first
    round (kind "waypoint", labels "1", "2", ...; hops are left out), the
    first one again for another round ("again": true), then the landing: over
    it and down (kind "land", label "L"; "T" when it lands where it took off).
    Stands on a surface carry "stand": true.
    """
    east0, north0, up0 = spawn_enu
    line = [{"east_m": east0, "north_m": north0, "up_m": up0, "kind": "takeoff", "label": "T", "stand": True}]
    last_round = 0
    for step in steps(schedule, spawn_enu, spawn_yaw_deg):
        if step["op"] == "takeoff":
            line.append({"east_m": east0, "north_m": north0, "up_m": step["up_m"], "kind": "takeoff", "label": "T"})
            continue
        if step["op"] == "land":
            over = line[-1]
            line.append({"east_m": over["east_m"], "north_m": over["north_m"], "up_m": step["up_m"],
                         "kind": over["kind"], "label": over["label"], "stand": True})
            break
        if step["op"] != "goto" or ("hop" in step and step["hop"].split("/")[0] != step["hop"].split("/")[1]):
            continue
        point = {"east_m": step["east_m"], "north_m": step["north_m"], "up_m": step["up_m"]}
        if step["name"] in ("over-takeoff-point", "over-landing-point"):
            at_takeoff = step["name"] == "over-takeoff-point"
            line.append({**point, "kind": "takeoff" if at_takeoff else "land", "label": "T" if at_takeoff else "L"})
            continue
        if step["round"] > 1:
            if last_round == 1:  # back to the first waypoint for another round; one round is drawn
                first = next(item for item in line if item["kind"] == "waypoint")
                line.append({**first, "again": True})
            last_round = step["round"]
            if schedule["loop_count"] is None:
                break  # never lands
            continue
        last_round = 1
        number = sum(1 for item in line if item["kind"] == "waypoint") + 1
        line.append({**point, "kind": "waypoint", "label": str(number), "name": step["name"]})
    return line


def event_zones(schedule: dict, spawn_enu: tuple[float, float, float], spawn_yaw_deg: float = 0.0) -> list:
    """The wind / fault zones (flight_events.Zone) of the waypoints that set one:
    each round the leg from the waypoint to the next point the Drone flies to
    (the next waypoint, the first again for another round, or over the landing
    point; the waypoint itself when it is the last place)."""
    line = flight_path(schedule, spawn_enu, spawn_yaw_deg)
    zones = []
    for at, item in enumerate(line):
        if item["kind"] != "waypoint" or item.get("again"):
            continue
        point = schedule["waypoints"][int(item["label"]) - 1]
        if "wind" not in point and "fault" not in point:
            continue
        following = line[at + 1] if at + 1 < len(line) else item
        zones.append(flight_events.Zone(
            label=point["name"],
            a=(item["east_m"], item["north_m"], item["up_m"]),
            b=(following["east_m"], following["north_m"], following["up_m"]),
            width_m=point.get("zone_width_m", schedule["zone_width_m"]),
            height_m=point.get("zone_height_m", schedule["zone_height_m"]),
            wind=point.get("wind"), fault=point.get("fault"),
        ))
    return zones


def _hops(goto: dict, start: tuple[float, float, float], hop_m: float) -> Iterator[dict]:
    """A goto as hops of at most hop_m along the straight line (the last one is the goto)."""
    end = (goto["east_m"], goto["north_m"], goto["up_m"])
    count = 1 if hop_m is None else max(1, math.ceil(math.dist(start, end) / hop_m - 1e-9))
    for index in range(1, count):
        t = index / count
        yield {**goto, "hop": f"{index}/{count}", **dict(zip(("east_m", "north_m", "up_m"),
                                                             (a + (b - a) * t for a, b in zip(start, end))))}
    yield {**goto, **({"hop": f"{count}/{count}"} if count > 1 else {})}


def leg_timeout(step: dict, previous: tuple[float, float, float] | None) -> float:
    """A generous RPC timeout for a goto: its straight-line time, twice, plus a minute."""
    if previous is None:
        return 120.0
    distance = math.dist(previous, (step["east_m"], step["north_m"], step["up_m"]))
    return 2.0 * distance / step["speed_m_s"] + 60.0


# --- Spawn -------------------------------------------------------------------------

def spawn_from_marker(marker_path: Path) -> tuple[tuple[float, float, float], float]:
    """The Drone's spawn (ENU, yaw) as tools/drone_one.py wrote it into the City marker."""
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    spawn = marker.get("flight_plan", {}).get("runtime_spawn")
    if not isinstance(spawn, dict) or spawn.get("frame") != "ENU":
        raise ScheduleError(f"{marker_path} has no ENU runtime_spawn (configure the Composition first)")
    return (float(spawn["east_m"]), float(spawn["north_m"]), float(spawn["up_m"])), float(spawn["yaw_deg"])


# --- Running it --------------------------------------------------------------------
#
# Like the Car scenario executor this is an outside program (the Launcher starts
# it after the simulation, when no asset can register): it reads the Hakoniwa
# clock and calls the Drone service with Drone Core's shared-runtime RPC client.

TRACK_PERIOD_SEC = 0.1


class Runner:
    """Steps through the flight on Hakoniwa time."""

    def __init__(self, args: argparse.Namespace, schedule: dict, spawn_enu, spawn_yaw: float, client, clock) -> None:
        self.args = args
        self.flight = steps(schedule, spawn_enu, spawn_yaw)
        self.events = flight_events.EventState(event_zones(schedule, spawn_enu, spawn_yaw))
        self.position_warned: str | bool = False
        self.contact_fault = schedule.get("contact_fault")
        self.contact_rotor: int | None = None  # the rotor the first contact stopped
        self.last_hit_print = -math.inf
        self.airborne = False        # after the takeoff, until the landing starts
        self.collisions: int | None = None
        self.rotors = flight_events.rotor_arms(getattr(args, "rotor_config", None))
        # The flown track next to the summary (<summary>-track.csv): simulation s, east, north, up.
        self.track = None
        self.track_last: float | None = None
        if args.summary_json:
            path = args.summary_json.with_name(args.summary_json.stem + "-track.csv")
            path.parent.mkdir(parents=True, exist_ok=True)
            self.track = path.open("w", encoding="utf-8")
            self.track.write("simulation_sec,east_m,north_m,up_m,speed_m_s,collisions\n")
        self.spawn_enu = spawn_enu
        self.client = client
        self.clock = clock  # () -> simulation seconds
        self.pending = None
        self.current: dict | None = None
        self.wait_until: float | None = None
        self.land_until = 0.0
        self.previous = None
        self.done = False
        self.summary: dict[str, Any] = {"status": "running", "drone": args.drone, "spawn_enu_m": list(spawn_enu),
                                        "steps": []}

    def write_summary(self, status: str, error: str | None = None) -> None:
        self.summary["status"] = status
        if error:
            self.summary["error"] = error
        if self.args.summary_json:
            self.args.summary_json.parent.mkdir(parents=True, exist_ok=True)
            self.args.summary_json.write_text(json.dumps(self.summary, indent=2) + "\n", encoding="utf-8")

    def _submit(self, step: dict):
        op = step["op"]
        if op == "set_ready":
            return self.client.set_ready_async()
        if op == "takeoff":
            return self.client.takeoff_async(step["up_m"])
        if op == "goto":
            x, y, z = to_ros(step["east_m"], step["north_m"], step["up_m"])
            return self.client.goto_async(x, y, z, yaw_deg=ros_yaw(step["yaw_deg"]), speed_m_s=step["speed_m_s"],
                                          tolerance_m=step["tolerance_m"],
                                          timeout_sec=leg_timeout(step, self.previous))
        if op == "land":
            self.airborne = False  # touching down is not a contact fault
            # No RPC timeout: a timed-out land is cancelled, and Drone Core then
            # leaves landing mode and slides the Drone off where it came down.
            self.land_until = self.clock() + self.args.land_settle_sec
            return self.client.land_async(timeout_sec=0.0)
        raise ScheduleError(f"unknown step {op}")

    def _finished(self, step: dict, result) -> None:
        ok = not isinstance(result, Exception) and bool(getattr(result, "ok", False))
        record = {"op": step["op"], **{key: step[key] for key in ("name", "round", "hop") if key in step},
                  "simulation_sec": round(self.clock(), 3), "ok": ok}
        if not ok and step["op"] == "land":
            # Drone Core's land completion assumes ground at height 0: on a roof it
            # never answers although the Drone is down. The request is left open
            # (not cancelled) and the Drone taken as landed after land_settle_sec.
            record["note"] = f"land RPC: {result}; the Drone is taken as landed on the surface below"
            ok = True
        if not ok:
            raise ScheduleError(f"{step['op']} failed: {getattr(result, 'message', result)}")
        if step["op"] == "goto":
            self.previous = (step["east_m"], step["north_m"], step["up_m"])
        elif step["op"] == "takeoff":
            self.previous = (self.spawn_enu[0], self.spawn_enu[1], step["up_m"])
            self.airborne = True
        self.summary["steps"].append(record)
        print(f"SCHEDULE: done {record}", flush=True)

    def watch_events(self) -> None:
        """Record where the Drone flies (track_csv), stop the wall-side rotor when it
        touches something in flight (contact_fault), and send the wind / rotor faults
        of the zone it is in when they change."""
        if not self.events.zones and self.track is None and self.contact_fault is None:
            return
        try:
            state = drone_state(self.client)
        except Exception as exc:  # not up yet: next time
            message = f"{type(exc).__name__}: {exc}"
            if message != self.position_warned:  # print each new reason once
                print(f"SCHEDULE: no position for the wind / fault zones yet ({message})", flush=True)
                self.position_warned = message
            return
        east, north, up = state["enu"]
        self.record_track(east, north, up, state["speed"], state["collisions"])
        if self.contact_fault is not None and state["collisions"] is not None:
            if self.collisions is not None and state["collisions"] > self.collisions and self.airborne:
                if self.contact_rotor is None:
                    self.contact(state)
                else:
                    self.hit(state)
            self.collisions = state["collisions"]
        if not self.events.zones and not self.events.faults:
            return
        change = self.events.update(east, north, up)
        if change is None:
            return
        self.send_events(change, (east, north, up))

    def send_events(self, change, at) -> None:
        wind, faults = change
        send_disturbance(self.client, self.args.drone, wind, faults)
        record = {"simulation_sec": round(self.clock(), 3), "at_enu_m": [round(value, 2) for value in at],
                  "wind": None if wind is None else {"towards_deg": wind.towards_deg, "speed_m_s": wind.speed_m_s},
                  "rotor_scales": flight_events.rotor_scales(faults)}
        self.summary.setdefault("events", []).append(record)
        print(f"SCHEDULE: disturbance {record}", flush=True)

    def hit(self, state: dict) -> None:
        """Another contact after the fault (the wall again, the ground): recorded only; printed
        at most every 0.5 s (a Drone lying on the ground keeps touching it)."""
        east, north, up = state["enu"]
        record = {"simulation_sec": round(self.clock(), 3), "at_enu_m": [round(east, 2), round(north, 2), round(up, 2)],
                  "collisions": state["collisions"], "speed_m_s": round(state["speed"], 2)}
        self.summary.setdefault("hits", []).append(record)
        if self.clock() - self.last_hit_print >= 0.5:
            self.last_hit_print = self.clock()
            print(f"SCHEDULE: hit {record}", flush=True)

    def contact(self, state: dict) -> None:
        """The Drone touched something in flight for the first time: stop the rotor on the
        wall side (the way the wind blows; without wind, the way it moves) and keep it stopped."""
        towards = self.events.last_wind.towards_deg if self.events.last_wind is not None else state["heading_deg"]
        rotor = flight_events.wall_side_rotor(self.rotors, state["yaw_rad"], towards)
        self.contact_rotor = rotor
        self.events.hold_fault(rotor, self.contact_fault["scale"])
        east, north, up = state["enu"]
        record = {"simulation_sec": round(self.clock(), 3), "at_enu_m": [round(east, 2), round(north, 2), round(up, 2)],
                  "collisions": state["collisions"], "rotor": rotor, "towards_deg": round(towards, 1),
                  "speed_m_s": round(state["speed"], 2)}
        self.summary.setdefault("contacts", []).append(record)
        print(f"SCHEDULE: contact {record}", flush=True)
        change = self.events.update(east, north, up, force=True)
        if change is not None:
            self.send_events(change, (east, north, up))

    def record_track(self, east: float, north: float, up: float, speed: float | None = None,
                     collisions: int | None = None) -> None:
        """Append the Drone's position (Urban ENU), speed and contact count to the track CSV at
        most every TRACK_PERIOD_SEC of simulation time, written as it goes (a flight that ends
        in a crash keeps its track)."""
        if self.track is None:
            return
        now = self.clock()
        if self.track_last is not None and now - self.track_last < TRACK_PERIOD_SEC:
            return
        self.track_last = now
        speed_text = "" if speed is None else f"{speed:.3f}"
        count_text = "" if collisions is None else str(collisions)
        self.track.write(f"{now:.2f},{east:.3f},{north:.3f},{up:.3f},{speed_text},{count_text}\n")
        self.track.flush()

    def step_once(self) -> None:
        """Advance as far as the clock allows; returns at once."""
        self.watch_events()
        if self.done:
            return
        if self.wait_until is not None:
            if self.clock() < self.wait_until:
                return
            self.wait_until = None
        if self.pending is not None:
            self.client.poll_once()
            if not self.pending.done():
                if self.current["op"] == "land" and self.clock() >= self.land_until:
                    step, self.pending = self.current, None  # left open: see _submit
                    self._finished(step, "no answer (the land completion assumes ground at height 0)")
                return
            try:
                result = self.pending.result(timeout=0.0)
            except Exception as exc:  # a timeout or a failed call
                result = exc
            step, self.pending = self.current, None
            self._finished(step, result)
            return
        step = next(self.flight, None)
        if step is None:
            self.done = True
            print("SCHEDULE: flight done", flush=True)
            self.write_summary("done")
            return
        self.current = step
        if step["op"] == "wait":
            print(f"SCHEDULE: wait {step['sec']:.1f} s ({step['label']}) at {self.clock():.2f} s", flush=True)
            self.wait_until = self.clock() + step["sec"]
            return
        print(f"SCHEDULE: start {step} at {self.clock():.2f} s", flush=True)
        self.pending = self._submit(step)


def drone_state(link) -> dict:
    """Position (Urban ENU), yaw (ROS, rad), speed, heading of travel (Urban yaw, deg) and the
    contact count (None when unknown) of the Drone (drone_link.DroneLink.state)."""
    return link.state()


def send_disturbance(link, drone: str, wind, faults: dict[int, float]) -> None:
    """Wind and rotor faults into the simulator (the Viewer's fault panel writes the same disturb PDU)."""
    link.send_disturbance(wind, faults)


def foundation_offsets() -> Path | None:
    """The PDU offset files the Workspace foundation installed (next to its Python:
    install/python -> install/share/hakoniwa/offset); Drone Core's own default
    lies in a submodule the Workspace does not fetch."""
    candidate = Path(sys.prefix).resolve().parent / "share" / "hakoniwa" / "offset"
    return candidate if candidate.is_dir() else None


def fly(args: argparse.Namespace, schedule: dict, spawn, spawn_yaw: float) -> int:
    """Build the Drone's link (drone_link.py) and fly: Drone Core RPC, or MAVLink with --mavlink."""
    import hakopy
    from drone_link import MavlinkDroneLink, PduAccess, RpcDroneLink

    if args.mavlink:
        # PX4 SITL: commands and state over MAVLink; no RPC client, so nothing is
        # registered in shared memory after the simulation started.
        if hakopy.init_for_external() is False:
            raise ScheduleError("hakopy.init_for_external() failed")
        pdu = PduAccess(args.pdu_def, args.drone, hakopy) if args.pdu_def else None
        return run_flight(args, schedule, spawn, spawn_yaw, MavlinkDroneLink(args.mavlink, spawn, pdu), hakopy)

    sys.path.insert(0, str(args.drone_root.resolve() / "drone_api" / "external_rpc"))
    from hakosim_async_shared_rpc import AsyncSharedHakoniwaRpcDroneClient

    options = {}
    offsets = args.offset_path or foundation_offsets()
    if offsets is not None:
        options["offset_path"] = offsets
    client = AsyncSharedHakoniwaRpcDroneClient(
        # No client-side request timeout (Drone Core's default): each call carries
        # its own (the goto legs, the land), and a client timeout cancels a goto
        # midway, which brings the Drone down where it is.
        drone_name=args.drone, service_config_path=args.service_config.resolve(),
        poll_interval_sec=0.0, **options)

    while True:  # the Drone service registers its RPC services once it runs
        try:
            client.prepare_services(["DroneSetReady", "DroneTakeOff", "DroneGoTo", "DroneLand"])
            break
        except Exception as exc:
            print(f"SCHEDULE: waiting for the Drone service ({exc})", flush=True)
            time.sleep(1.0)
    pdu_def = args.pdu_def or rpc_pdu_def(client)
    return run_flight(args, schedule, spawn, spawn_yaw, RpcDroneLink(client, PduAccess(pdu_def, args.drone, hakopy)),
                      hakopy)


def rpc_pdu_def(client) -> Path:
    """The PDU definition of the RPC client's runtime service config (without --pdu-def)."""
    service = json.loads(Path(client.runtime_service_config_path).read_text(encoding="utf-8"))
    return Path(service["pdu_config_path"])


def run_flight(args: argparse.Namespace, schedule: dict, spawn, spawn_yaw: float, client, hakopy) -> int:
    def clock() -> float:
        return max(0, int(hakopy.simulation_time())) / 1_000_000.0

    runner = Runner(args, schedule, spawn, spawn_yaw, client, clock)
    print(f"SCHEDULE: {args.drone} from {args.schedule} (spawn ENU {spawn})"
          + (f" over MAVLink {args.mavlink}" if args.mavlink else ""), flush=True)
    try:
        while not runner.done:
            runner.step_once()
            time.sleep(args.poll_sec)
    except Exception as exc:
        traceback.print_exc()
        runner.write_summary("failed", str(exc))
        return 1
    # Done: stay up (the Launcher stops everything when a control exits).
    while True:
        time.sleep(1.0)


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--schedule", type=Path, required=True, help="YAML with a drones: section")
    parser.add_argument("--drone", default="Drone-1", help="the Drone's name (its drones: entry)")
    parser.add_argument("--drone-root", type=Path, help="hakoniwa-drone-core (for its external_rpc client)")
    parser.add_argument("--service-config", type=Path, help="Drone Core RPC service config")
    parser.add_argument("--city-marker", type=Path, help="the City marker with the Drone's runtime spawn")
    parser.add_argument("--summary-json", type=Path)
    parser.add_argument("--rotor-config", type=Path,
                        help="Drone config with the rotor positions (contact_fault; default: the EAMS Hexa's)")
    parser.add_argument("--offset-path", type=Path, help="PDU offset files (default: the Workspace foundation's)")
    parser.add_argument("--poll-sec", type=float, default=0.01, help="wall-clock pause between steps")
    parser.add_argument("--land-settle-sec", type=float, default=20.0,
                        help="simulation seconds after the land command after which the Drone is taken as down")
    parser.add_argument("--pdu-def", type=Path,
                        help="the simulator's PDU definition (the Drone's PDUs: state, contacts, wind and faults)")
    parser.add_argument("--mavlink", help="fly a PX4 SITL Drone over MAVLink at this connection "
                        "(e.g. udpin:127.0.0.1:14540) instead of Drone Core RPC")
    parser.add_argument("--check", action="store_true", help="print the flight and exit (no simulation)")
    parser.add_argument("--spawn", help="with --check: east,north,up of the spawn (default: from --city-marker)")
    parser.add_argument("--check-steps", type=int, default=40, help="with --check: how many steps to print")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        schedule = load_schedule(args.schedule, args.drone)
        if args.spawn:
            east, north, up = (float(value) for value in args.spawn.split(","))
            spawn, spawn_yaw = (east, north, up), 0.0
        elif args.city_marker:
            spawn, spawn_yaw = spawn_from_marker(args.city_marker)
        else:
            raise ScheduleError("give --city-marker (or --spawn with --check)")
    except ScheduleError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.check:
        for index, step in zip(range(args.check_steps), steps(schedule, spawn, spawn_yaw)):
            print(json.dumps(step, ensure_ascii=False))
        return 0
    if not args.mavlink and (args.drone_root is None or args.service_config is None):
        print("ERROR: --drone-root and --service-config are needed to fly", file=sys.stderr)
        return 2
    return fly(args, schedule, spawn, spawn_yaw)


if __name__ == "__main__":
    raise SystemExit(main())
