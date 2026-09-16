#!/usr/bin/env python3
"""Execute timed or closed-loop route scenarios for Urban Ackermann vehicles."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import math
from pathlib import Path
import time

import yaml

from urban_car import AckermannClientError, AckermannFleetClient, VehiclePose
from route_geometry import (
    RouteGeometry,
    RoutePoint,
    expand_route_vehicles,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PDU_DEF = ROOT / "work/multi-car-viewer/urban-car-pdudef.json"


class ScenarioError(RuntimeError):
    """The scenario does not satisfy the timed-command contract."""


@dataclass(frozen=True)
class TimedCommand:
    at_sec: float
    duration_sec: float
    speed_m_s: float
    steering_rad: float
    label: str

    @property
    def end_sec(self) -> float:
        return self.at_sec + self.duration_sec


@dataclass(frozen=True)
class Scenario:
    name: str
    rate_hz: float
    start_delay_sec: float
    tail_sec: float
    schedules: dict[str, tuple[TimedCommand, ...]]

    @property
    def duration_sec(self) -> float:
        return max(
            command.end_sec
            for commands in self.schedules.values()
            for command in commands
        ) + self.tail_sec


@dataclass(frozen=True)
class RouteControl:
    speed_m_s: float
    lookahead_m: float
    position_gain: float
    wheelbase_m: float
    max_steering_rad: float


@dataclass(frozen=True)
class RouteVehicle:
    name: str
    offset_m: float


@dataclass(frozen=True)
class RouteScenario:
    name: str
    rate_hz: float
    start_delay_sec: float
    loop_count: int | None
    vehicles: tuple[RouteVehicle, ...]
    points: tuple[RoutePoint, ...]
    control: RouteControl


class RouteCursor:
    """Simulation-time route progress with repeatable waypoint dwell periods."""

    def __init__(self, geometry: RouteGeometry, speed_m_s: float, loop_count: int | None):
        self.geometry = geometry
        self.speed_m_s = speed_m_s
        self.loop_count = loop_count
        self.distance_m = 0.0
        self.hold_remaining_sec = 0.0
        self.finished = False
        self._last_stop_distance: float | None = None
        self._stops = tuple(
            (geometry.cumulative[index], point.dwell_sec, point.name)
            for index, point in enumerate(geometry.points)
            if point.dwell_sec > 0.0
        )

    @property
    def moving(self) -> bool:
        return not self.finished and self.hold_remaining_sec <= 0.0

    def _next_stop(self) -> tuple[float, float, str] | None:
        epsilon = 1e-7
        lap = math.floor((self.distance_m + epsilon) / self.geometry.length)
        candidates = []
        for stop_s, dwell, name in self._stops:
            absolute = lap * self.geometry.length + stop_s
            if absolute <= self.distance_m + epsilon:
                absolute += self.geometry.length
            candidates.append((absolute, dwell, name))
        return min(candidates, default=None)

    def advance(self, delta_sec: float) -> str | None:
        if self.finished or delta_sec <= 0.0:
            return None
        if self.hold_remaining_sec > 0.0:
            self.hold_remaining_sec = max(0.0, self.hold_remaining_sec - delta_sec)
            return None
        target_distance = self.distance_m + self.speed_m_s * delta_sec
        end_distance = (math.inf if self.loop_count is None
                        else self.loop_count * self.geometry.length)
        next_stop = self._next_stop()
        if next_stop is not None and next_stop[0] <= min(target_distance, end_distance):
            self.distance_m = next_stop[0]
            self.hold_remaining_sec = next_stop[1]
            self._last_stop_distance = next_stop[0]
            return next_stop[2]
        self.distance_m = min(target_distance, end_distance)
        if self.distance_m >= end_distance:
            self.finished = True
        return None


def finite_number(value: object, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ScenarioError(f"{field} must be a number") from error
    if not math.isfinite(number):
        raise ScenarioError(f"{field} must be finite")
    return number


def load_route_scenario(root: dict) -> RouteScenario:
    name = str(root.get("name", "")).strip()
    if not name:
        raise ScenarioError("scenario name must not be empty")
    rate_hz = finite_number(root.get("rate_hz", 50.0), "rate_hz")
    start_delay_sec = finite_number(
        root.get("start_delay_sec", 1.0), "start_delay_sec"
    )
    if rate_hz <= 0.0 or start_delay_sec < 0.0:
        raise ScenarioError("rate_hz must be positive and start_delay_sec non-negative")
    loop_value = root.get("loop_count", "forever")
    if loop_value == "forever":
        loop_count = None
    elif isinstance(loop_value, int) and not isinstance(loop_value, bool) and loop_value > 0:
        loop_count = loop_value
    else:
        raise ScenarioError("loop_count must be a positive integer or 'forever'")

    vehicle_inputs = root.get("vehicles")
    try:
        expanded_vehicles = expand_route_vehicles(vehicle_inputs)
    except ValueError as error:
        raise ScenarioError(str(error)) from error
    if not expanded_vehicles:
        raise ScenarioError("vehicles must not be empty")
    vehicles = []
    names: set[str] = set()
    for index, item in enumerate(expanded_vehicles):
        vehicle_name = item.name
        offset_m = item.offset_m
        if not vehicle_name or vehicle_name in names:
            raise ScenarioError("vehicle names must be non-empty and unique")
        if not math.isfinite(offset_m):
            raise ScenarioError(f"vehicles[{index}].route_offset_m must be finite")
        if offset_m > 0.0:
            raise ScenarioError("route_offset_m must be zero or negative")
        names.add(vehicle_name)
        vehicles.append(RouteVehicle(vehicle_name, offset_m))

    route = root.get("route")
    if not isinstance(route, dict) or route.get("closed") is not True:
        raise ScenarioError("route.closed must be true")
    point_inputs = route.get("points")
    if not isinstance(point_inputs, list) or len(point_inputs) < 3:
        raise ScenarioError("route.points must contain at least three points")
    points = []
    point_names: set[str] = set()
    for index, item in enumerate(point_inputs):
        if not isinstance(item, dict):
            raise ScenarioError(f"route.points[{index}] must be an object")
        point_name = str(item.get("name", f"point-{index + 1}")).strip()
        east_m = finite_number(item.get("east_m"), f"route.points[{index}].east_m")
        north_m = finite_number(item.get("north_m"), f"route.points[{index}].north_m")
        dwell_sec = finite_number(item.get("dwell_sec", 0.0),
                                  f"route.points[{index}].dwell_sec")
        if not point_name or point_name in point_names or dwell_sec < 0.0:
            raise ScenarioError("route point names must be unique and dwell non-negative")
        point_names.add(point_name)
        points.append(RoutePoint(point_name, east_m, north_m, dwell_sec))

    control_input = root.get("control")
    if not isinstance(control_input, dict):
        raise ScenarioError("control must be an object")
    control = RouteControl(
        speed_m_s=finite_number(control_input.get("speed_m_s"), "control.speed_m_s"),
        lookahead_m=finite_number(control_input.get("lookahead_m"), "control.lookahead_m"),
        position_gain=finite_number(
            control_input.get("position_gain", 1.0), "control.position_gain"
        ),
        wheelbase_m=finite_number(control_input.get("wheelbase_m"), "control.wheelbase_m"),
        max_steering_rad=math.radians(finite_number(
            control_input.get("max_steering_deg"), "control.max_steering_deg"
        )),
    )
    if any(value <= 0.0 for value in (
        control.speed_m_s, control.lookahead_m, control.position_gain,
        control.wheelbase_m, control.max_steering_rad,
    )):
        raise ScenarioError("route control values must be positive")
    geometry = RouteGeometry(tuple(points))
    if max(-vehicle.offset_m for vehicle in vehicles) >= geometry.length / 2.0:
        raise ScenarioError("vehicle route offsets must be less than half the route length")
    return RouteScenario(
        name=name,
        rate_hz=rate_hz,
        start_delay_sec=start_delay_sec,
        loop_count=loop_count,
        vehicles=tuple(vehicles),
        points=tuple(points),
        control=control,
    )


def load_scenario(path: Path) -> Scenario | RouteScenario:
    try:
        root = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ScenarioError(f"failed to load scenario {path}: {error}") from error
    if not isinstance(root, dict):
        raise ScenarioError("scenario root must be an object")
    if root.get("schema_version") == 2:
        return load_route_scenario(root)
    if root.get("schema_version") != 1:
        raise ScenarioError("scenario schema_version must be 1 or 2")
    name = str(root.get("name", "")).strip()
    if not name:
        raise ScenarioError("scenario name must not be empty")
    rate_hz = finite_number(root.get("rate_hz", 50.0), "rate_hz")
    start_delay_sec = finite_number(
        root.get("start_delay_sec", 1.0), "start_delay_sec"
    )
    tail_sec = finite_number(root.get("tail_sec", 0.5), "tail_sec")
    if rate_hz <= 0.0 or start_delay_sec < 0.0 or tail_sec < 0.0:
        raise ScenarioError("rate_hz must be positive; delay values must be non-negative")

    vehicle_inputs = root.get("vehicles")
    if not isinstance(vehicle_inputs, list) or not vehicle_inputs:
        raise ScenarioError("vehicles must be a non-empty array")
    schedules: dict[str, tuple[TimedCommand, ...]] = {}
    for vehicle_index, vehicle in enumerate(vehicle_inputs):
        if not isinstance(vehicle, dict):
            raise ScenarioError(f"vehicles[{vehicle_index}] must be an object")
        robot = str(vehicle.get("name", "")).strip()
        if not robot or robot in schedules:
            raise ScenarioError(f"vehicle names must be non-empty and unique: {robot!r}")
        inputs = vehicle.get("commands")
        if not isinstance(inputs, list) or not inputs:
            raise ScenarioError(f"vehicle {robot} commands must be a non-empty array")
        commands = []
        for command_index, item in enumerate(inputs):
            if not isinstance(item, dict):
                raise ScenarioError(f"{robot}.commands[{command_index}] must be an object")
            field = f"{robot}.commands[{command_index}]"
            at_sec = finite_number(item.get("at_sec"), f"{field}.at_sec")
            duration_sec = finite_number(
                item.get("duration_sec"), f"{field}.duration_sec"
            )
            speed_m_s = finite_number(item.get("speed_m_s"), f"{field}.speed_m_s")
            steering_deg = finite_number(
                item.get("steering_deg", 0.0), f"{field}.steering_deg"
            )
            if at_sec < 0.0 or duration_sec <= 0.0:
                raise ScenarioError(f"{field} requires at_sec >= 0 and duration_sec > 0")
            commands.append(TimedCommand(
                at_sec=at_sec,
                duration_sec=duration_sec,
                speed_m_s=speed_m_s,
                steering_rad=math.radians(steering_deg),
                label=str(item.get("label", f"command-{command_index + 1}")),
            ))
        commands.sort(key=lambda command: command.at_sec)
        for previous, current in zip(commands, commands[1:]):
            if current.at_sec < previous.end_sec:
                raise ScenarioError(
                    f"vehicle {robot} has overlapping commands: "
                    f"{previous.label!r} and {current.label!r}"
                )
        schedules[robot] = tuple(commands)

    return Scenario(name, rate_hz, start_delay_sec, tail_sec, schedules)


def active_command(
    commands: tuple[TimedCommand, ...], elapsed_sec: float
) -> TimedCommand | None:
    for command in commands:
        if command.at_sec <= elapsed_sec < command.end_sec:
            return command
        if command.at_sec > elapsed_sec:
            break
    return None


def execute_timed(scenario: Scenario, pdu_def: Path) -> None:
    robots = tuple(scenario.schedules)
    print(
        f"Scenario: {scenario.name} | vehicles={','.join(robots)} "
        f"duration={scenario.duration_sec:.2f}s rate={scenario.rate_hz:g}Hz"
    )
    with AckermannFleetClient(pdu_def, robots, rate_hz=scenario.rate_hz) as fleet:
        fleet.stop(repeat=1)
        if scenario.start_delay_sec:
            print(f"Starting in {scenario.start_delay_sec:g}s...")
            time.sleep(scenario.start_delay_sec)
        timeline_start = fleet.simulation_time_sec()
        last_simulation_time = timeline_start
        next_tick = time.monotonic()
        previous_labels: dict[str, str | None] = {robot: None for robot in robots}
        period = 1.0 / scenario.rate_hz
        try:
            while True:
                simulation_time = fleet.simulation_time_sec()
                if simulation_time + 1e-6 < last_simulation_time:
                    raise ScenarioError(
                        "Hakoniwa simulation time moved backwards during scenario execution"
                    )
                last_simulation_time = simulation_time
                elapsed = simulation_time - timeline_start
                if elapsed >= scenario.duration_sec:
                    break
                for robot, commands in scenario.schedules.items():
                    command = active_command(commands, elapsed)
                    label = None if command is None else command.label
                    if label != previous_labels[robot]:
                        if command is None:
                            print(f"[{elapsed:7.3f}] {robot}: stop")
                        else:
                            print(
                                f"[{elapsed:7.3f}] {robot}: {command.label} "
                                f"speed={command.speed_m_s:g}m/s "
                                f"steering={math.degrees(command.steering_rad):g}deg"
                            )
                        previous_labels[robot] = label
                    fleet.send(
                        robot,
                        0.0 if command is None else command.speed_m_s,
                        0.0 if command is None else command.steering_rad,
                    )
                next_tick += period
                delay = next_tick - time.monotonic()
                if delay > 0.0:
                    time.sleep(delay)
                else:
                    next_tick = time.monotonic()
        except KeyboardInterrupt:
            print("Scenario interrupted; stopping all vehicles.")
    print("Scenario finished; all vehicles stopped.")


def route_command(
    geometry: RouteGeometry,
    cursor: RouteCursor,
    vehicle: RouteVehicle,
    pose: VehiclePose,
    control: RouteControl,
) -> tuple[float, float]:
    actual_s = geometry.project(pose.east_m, pose.north_m)
    target_s = cursor.distance_m + vehicle.offset_m
    progress_error = geometry.signed_error(target_s, actual_s)
    feed_forward = control.speed_m_s if cursor.moving else 0.0
    speed = max(0.0, min(
        control.speed_m_s * 1.25,
        feed_forward + control.position_gain * progress_error,
    ))
    if speed <= 1e-3:
        return 0.0, 0.0
    target_east, target_north = geometry.sample(target_s + control.lookahead_m)
    bearing = math.atan2(target_north - pose.north_m, target_east - pose.east_m)
    heading_error = math.atan2(
        math.sin(bearing - pose.yaw_rad),
        math.cos(bearing - pose.yaw_rad),
    )
    target_distance = max(
        control.lookahead_m,
        math.hypot(target_east - pose.east_m, target_north - pose.north_m),
    )
    steering = math.atan2(
        2.0 * control.wheelbase_m * math.sin(heading_error),
        target_distance,
    )
    steering = max(-control.max_steering_rad,
                   min(control.max_steering_rad, steering))
    return speed, steering


def execute_route(scenario: RouteScenario, pdu_def: Path) -> None:
    robots = tuple(vehicle.name for vehicle in scenario.vehicles)
    geometry = RouteGeometry(scenario.points)
    cursor = RouteCursor(geometry, scenario.control.speed_m_s, scenario.loop_count)
    loops = "forever" if scenario.loop_count is None else str(scenario.loop_count)
    print(
        f"Route scenario: {scenario.name} | vehicles={','.join(robots)} "
        f"route={geometry.length:.1f}m loops={loops} rate={scenario.rate_hz:g}Hz"
    )
    with AckermannFleetClient(pdu_def, robots, rate_hz=scenario.rate_hz) as fleet:
        fleet.stop(repeat=1)
        if scenario.start_delay_sec:
            print(f"Starting in {scenario.start_delay_sec:g}s...")
            time.sleep(scenario.start_delay_sec)
        last_simulation_time = fleet.simulation_time_sec()
        next_tick = time.monotonic()
        period = 1.0 / scenario.rate_hz
        previous_lap = 0
        try:
            while not cursor.finished:
                simulation_time = fleet.simulation_time_sec()
                if simulation_time + 1e-6 < last_simulation_time:
                    raise ScenarioError(
                        "Hakoniwa simulation time moved backwards during route execution"
                    )
                delta_sec = max(0.0, simulation_time - last_simulation_time)
                last_simulation_time = simulation_time
                stop_name = cursor.advance(delta_sec)
                lap = int(cursor.distance_m // geometry.length)
                if lap != previous_lap:
                    previous_lap = lap
                    print(f"[{simulation_time:9.3f}] convoy lap {lap + 1}")

                poses = fleet.vehicle_poses()
                missing = [robot for robot in robots if robot not in poses]
                if missing:
                    raise ScenarioError(
                        "vehicle state PDU is missing: " + ", ".join(missing)
                    )
                if stop_name is not None:
                    positions = ", ".join(
                        f"{robot}=({poses[robot].east_m:.2f}E,"
                        f"{poses[robot].north_m:.2f}N)"
                        for robot in robots
                    )
                    print(
                        f"[{simulation_time:9.3f}] convoy stop: {stop_name} "
                        f"for {cursor.hold_remaining_sec:g}s | {positions}"
                    )
                for vehicle in scenario.vehicles:
                    speed, steering = route_command(
                        geometry, cursor, vehicle, poses[vehicle.name], scenario.control
                    )
                    fleet.send(vehicle.name, speed, steering)

                next_tick += period
                delay = next_tick - time.monotonic()
                if delay > 0.0:
                    time.sleep(delay)
                else:
                    next_tick = time.monotonic()
        except KeyboardInterrupt:
            print("Route scenario interrupted; stopping all vehicles.")
    print("Route scenario finished; all vehicles stopped.")


def execute(scenario: Scenario | RouteScenario, pdu_def: Path) -> None:
    if isinstance(scenario, RouteScenario):
        execute_route(scenario, pdu_def)
    else:
        execute_timed(scenario, pdu_def)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("scenario", type=Path)
    result.add_argument("--pdu-def", type=Path, default=DEFAULT_PDU_DEF)
    result.add_argument(
        "--dry-run", action="store_true",
        help="validate and summarize the scenario without connecting to Hakoniwa",
    )
    return result


def main() -> int:
    args = parser().parse_args()
    scenario_path = args.scenario.expanduser().resolve()
    scenario = load_scenario(scenario_path)
    if args.dry_run:
        if isinstance(scenario, RouteScenario):
            geometry = RouteGeometry(scenario.points)
            loops = "forever" if scenario.loop_count is None else str(scenario.loop_count)
            print(
                f"Valid route scenario: {scenario.name} | "
                f"vehicles={','.join(vehicle.name for vehicle in scenario.vehicles)} "
                f"route={geometry.length:.2f}m loops={loops} "
                f"rate={scenario.rate_hz:g}Hz"
            )
        else:
            print(
                f"Valid scenario: {scenario.name} | "
                f"vehicles={','.join(scenario.schedules)} "
                f"duration={scenario.duration_sec:.2f}s rate={scenario.rate_hz:g}Hz"
            )
        return 0
    execute(scenario, args.pdu_def.expanduser().resolve())
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AckermannClientError, ScenarioError, ValueError) as error:
        print(f"error: {error}")
        raise SystemExit(2)
