#!/usr/bin/env python3
"""Execute a timed multi-vehicle Ackermann command scenario."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import math
from pathlib import Path
import time

import yaml

from urban_car import AckermannClientError, AckermannFleetClient


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


def finite_number(value: object, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ScenarioError(f"{field} must be a number") from error
    if not math.isfinite(number):
        raise ScenarioError(f"{field} must be finite")
    return number


def load_scenario(path: Path) -> Scenario:
    try:
        root = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ScenarioError(f"failed to load scenario {path}: {error}") from error
    if not isinstance(root, dict) or root.get("schema_version") != 1:
        raise ScenarioError("scenario schema_version must be 1")
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


def execute(scenario: Scenario, pdu_def: Path) -> None:
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
        print(
            f"Valid scenario: {scenario.name} | vehicles={','.join(scenario.schedules)} "
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
