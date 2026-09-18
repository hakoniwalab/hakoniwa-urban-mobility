#!/usr/bin/env python3
"""Step-wise Golf Cart route control for composed Urban demo scenarios."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from route_geometry import RouteGeometry
from scenario_executor import (
    RouteCursor,
    RouteScenario,
    RouteVehicle,
    ScenarioError,
    route_command,
)
from urban_car import VehiclePose


@dataclass(frozen=True)
class CarRouteSnapshot:
    simulation_time_sec: float
    route_progress_m: float
    pose: VehiclePose
    speed_m_s: float
    steering_rad: float
    finished: bool
    stop_name: str | None = None


class CarRouteController:
    """Drive exactly one Golf Cart around a configured closed route."""

    def __init__(self, scenario: RouteScenario):
        if len(scenario.vehicles) != 1:
            raise ScenarioError("composed demo requires exactly one Car")
        if scenario.loop_count is None:
            raise ScenarioError("composed demo route must have a finite loop_count")
        self.scenario = scenario
        self.vehicle: RouteVehicle = scenario.vehicles[0]
        self.geometry = RouteGeometry(scenario.points)
        self.cursor = RouteCursor(
            self.geometry, scenario.control.speed_m_s, scenario.loop_count
        )
        self._last_simulation_time: float | None = None

    @property
    def finished(self) -> bool:
        return self.cursor.finished

    def start(self, fleet: Any) -> None:
        fleet.stop(repeat=1)
        self._last_simulation_time = float(fleet.simulation_time_sec())

    def step(self, fleet: Any) -> CarRouteSnapshot:
        if self._last_simulation_time is None:
            raise ScenarioError("Car route controller has not been started")
        simulation_time = float(fleet.simulation_time_sec())
        if simulation_time + 1.0e-6 < self._last_simulation_time:
            raise ScenarioError("Hakoniwa simulation time moved backwards")
        delta_sec = max(0.0, simulation_time - self._last_simulation_time)
        self._last_simulation_time = simulation_time
        stop_name = self.cursor.advance(delta_sec)
        poses = fleet.vehicle_poses()
        if self.vehicle.name not in poses:
            raise ScenarioError(
                f"vehicle state PDU is missing: {self.vehicle.name}"
            )
        pose = poses[self.vehicle.name]
        speed, steering = route_command(
            self.geometry,
            self.cursor,
            self.vehicle,
            pose,
            self.scenario.control,
        )
        fleet.send(self.vehicle.name, speed, steering)
        return CarRouteSnapshot(
            simulation_time_sec=simulation_time,
            route_progress_m=self.cursor.distance_m,
            pose=pose,
            speed_m_s=speed,
            steering_rad=steering,
            finished=self.cursor.finished,
            stop_name=stop_name,
        )

    def stop(self, fleet: Any) -> None:
        fleet.stop(repeat=3)
