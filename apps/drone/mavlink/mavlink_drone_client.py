"""MAVLink drone client with the operations of HakoniwaRpcDroneClient.

Same method names, arguments, frame and responses as
hakoniwa-drone-core drone_api/external_rpc/hakosim_rpc.py, so the same operations work on a
PX4 (or, later, ArduPilot) vehicle over MAVLink:

  set_ready()  takeoff(alt_m)  get_state()  goto(x, y, z, yaw_deg, ...)  land()

Frame: ROS (x forward/north, y left, z up, yaw counter-clockwise from x,
degrees), relative to the autopilot's local origin. The autopilot uses NED;
the client converts (x, -y, -z, -yaw).

Each operation waits for its result (takeoff: altitude reached, goto:
within tolerance, land: on the ground and disarmed) and returns ok/message.
The autopilot keeps the last target after the client exits.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from autopilot import LANDED_IN_AIR, LANDED_ON_GROUND, VehicleLink, select_backend

DEFAULT_CONNECTION = "udpin:127.0.0.1:14540"  # PX4 SITL onboard API port; 14550 stays for QGroundControl
HEARTBEAT_MAX_AGE_SEC = 3.0  # PX4 sends HEARTBEAT at 1 Hz
POSITION_MAX_AGE_SEC = 1.0
ARM_RETRY_SEC = 1.0


@dataclass
class Vector3:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


@dataclass
class Quaternion:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    w: float = 1.0


@dataclass
class Pose:
    position: Vector3 = field(default_factory=Vector3)
    orientation: Quaternion = field(default_factory=Quaternion)


@dataclass
class CommandResponse:
    ok: bool
    message: str


@dataclass
class StateResponse:
    ok: bool
    is_ready: bool
    mode: str
    message: str
    current_pose: Pose
    armed: bool = False
    landed_state: int = 0


def quaternion_from_euler(roll: float, pitch: float, yaw: float) -> Quaternion:
    cr, sr = math.cos(roll / 2), math.sin(roll / 2)
    cp, sp = math.cos(pitch / 2), math.sin(pitch / 2)
    cy, sy = math.cos(yaw / 2), math.sin(yaw / 2)
    return Quaternion(
        x=sr * cp * cy - cr * sp * sy,
        y=cr * sp * cy + sr * cp * sy,
        z=cr * cp * sy - sr * sp * cy,
        w=cr * cp * cy + sr * sp * sy,
    )


class MavlinkDroneClient:
    def __init__(
        self,
        connection: str = DEFAULT_CONNECTION,
        drone_name: str = "Drone",
        autopilot: str = "auto",
        ready_timeout_sec: float = 30.0,
    ) -> None:
        self.drone_name = drone_name
        self.ready_timeout_sec = ready_timeout_sec
        self.link = VehicleLink(connection)
        self.backend = select_backend(self.link, autopilot)
        self.link.pump(0.5)

    # Frames -------------------------------------------------------------------

    @staticmethod
    def _ros_to_ned(x: float, y: float, z: float) -> tuple[float, float, float]:
        return x, -y, -z

    def _ros_position(self) -> tuple[float, float, float]:
        s = self.link.state
        return s.x, -s.y, -s.z

    # Operations ---------------------------------------------------------------

    def set_ready(self) -> CommandResponse:
        """Wait until the autopilot reports its local and global position."""
        if not self.link.wait_until(lambda s: self.link.has_position(), self.ready_timeout_sec):
            return CommandResponse(False, "no position from the autopilot (EKF not ready?)")
        return CommandResponse(True, f"ready ({self.backend.name}, {self.backend.mode_name()})")

    def takeoff(self, alt_m: float, *, tolerance_m: float = 0.3, timeout_sec: float = 60.0) -> CommandResponse:
        ready = self.set_ready()
        if not ready.ok:
            return ready
        if not self.link.state.armed:
            # Right after start PX4 rejects arming until its preflight checks pass
            # (heading estimate, sensor biases): retry until the timeout.
            deadline = time.time() + timeout_sec
            while True:
                ok, message = self.backend.arm()
                if ok:
                    break
                if time.time() >= deadline:
                    return CommandResponse(False, message + " " + " / ".join(self.link.state.status_texts[-3:]))
                self.link.pump(ARM_RETRY_SEC)
        ok, message = self.backend.takeoff(alt_m)
        if not ok:
            return CommandResponse(False, message)
        reached = self.link.wait_until(
            lambda s: -s.z >= alt_m - tolerance_m and s.landed_state == LANDED_IN_AIR, timeout_sec
        )
        if not reached:
            return CommandResponse(False, f"takeoff: {alt_m} m not reached in {timeout_sec} s (alt {-self.link.state.z:.2f} m)")
        return CommandResponse(True, f"takeoff: at {-self.link.state.z:.2f} m")

    def get_state(self) -> StateResponse:
        self.link.pump(0.3)
        s = self.link.state
        x, y, z = self._ros_position()
        # NED attitude to ROS: roll, -pitch, -yaw
        orientation = quaternion_from_euler(s.roll, -s.pitch, -s.yaw)
        alive = self.link.is_alive(HEARTBEAT_MAX_AGE_SEC)
        return StateResponse(
            ok=alive,
            is_ready=alive and self.link.has_position(POSITION_MAX_AGE_SEC),
            mode=self.backend.mode_name(),
            message=f"armed={s.armed} landed_state={s.landed_state}" + ("" if alive else " (no heartbeat)"),
            current_pose=Pose(Vector3(x, y, z), orientation),
            armed=s.armed,
            landed_state=s.landed_state,
        )

    def goto(
        self,
        x: float,
        y: float,
        z: float,
        yaw_deg: float = 0.0,
        *,
        speed_m_s: float = 1.0,
        tolerance_m: float = 0.5,
        timeout_sec: float = 30.0,
    ) -> CommandResponse:
        if self.link.state.landed_state != LANDED_IN_AIR:
            self.link.pump(0.5)
            if self.link.state.landed_state != LANDED_IN_AIR:
                return CommandResponse(False, "goto: the vehicle is not in the air (take off first)")
        x_ned, y_ned, z_ned = self._ros_to_ned(x, y, z)
        ok, message = self.backend.goto(x_ned, y_ned, z_ned, math.radians(-yaw_deg), speed_m_s)
        if not ok:
            return CommandResponse(False, message)

        def arrived(s) -> bool:
            return math.dist((s.x, s.y, s.z), (x_ned, y_ned, z_ned)) <= tolerance_m

        if not self.link.wait_until(arrived, timeout_sec):
            px, py, pz = self._ros_position()
            return CommandResponse(False, f"goto: not within {tolerance_m} m in {timeout_sec} s (at {px:.2f}, {py:.2f}, {pz:.2f})")
        px, py, pz = self._ros_position()
        return CommandResponse(True, f"goto: at ({px:.2f}, {py:.2f}, {pz:.2f})")

    def land(self, timeout_sec: float = 0.0) -> CommandResponse:
        self.link.pump(0.3)
        if self.link.state.landed_state == LANDED_ON_GROUND and not self.link.state.armed:
            return CommandResponse(True, "land: already on the ground, disarmed")
        ok, message = self.backend.land()
        if not ok:
            return CommandResponse(False, message)
        wait_sec = timeout_sec if timeout_sec > 0 else 120.0
        landed = self.link.wait_until(lambda s: s.landed_state == LANDED_ON_GROUND and not s.armed, wait_sec)
        if not landed:
            return CommandResponse(False, f"land: not on the ground and disarmed in {wait_sec} s")
        return CommandResponse(True, "land: on the ground, disarmed")


def print_response_elapsed(prefix: str, start_time: float) -> None:
    print(f"INFO: {prefix} elapsed_sec={time.time() - start_time:.3f}")
