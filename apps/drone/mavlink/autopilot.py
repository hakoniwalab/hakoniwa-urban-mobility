"""MAVLink link and autopilot backends for MavlinkDroneClient.

VehicleLink keeps one MAVLink connection and the latest vehicle state.
An AutopilotBackend turns the client's operations into the commands of one
autopilot. PX4 is implemented; ArduPilot has its place here, so the client
and the commands stay the same when it is added.

Depends on pymavlink only (no Hakoniwa packages), so it can be reused by
other projects that drive PX4 or ArduPilot over MAVLink.
"""
from __future__ import annotations

import abc
import math
import time
from dataclasses import dataclass, field

from pymavlink import mavutil

M = mavutil.mavlink
EARTH_RADIUS_M = 6378137.0

# MAV_LANDED_STATE
LANDED_UNDEFINED = 0
LANDED_ON_GROUND = 1
LANDED_IN_AIR = 2
LANDED_TAKEOFF = 3
LANDED_LANDING = 4


@dataclass
class VehicleState:
    """Latest values received from the vehicle (NED, SI units)."""

    heartbeat_time: float = 0.0
    autopilot: int | None = None
    armed: bool = False
    custom_mode: int = 0
    system_status: int = 0
    landed_state: int = LANDED_UNDEFINED
    # LOCAL_POSITION_NED
    local_time: float = 0.0
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    vx: float = 0.0
    vy: float = 0.0
    vz: float = 0.0
    # ATTITUDE (rad)
    roll: float = 0.0
    pitch: float = 0.0
    yaw: float = 0.0
    # GLOBAL_POSITION_INT (deg, m AMSL)
    global_time: float = 0.0
    lat_deg: float = 0.0
    lon_deg: float = 0.0
    alt_amsl_m: float = 0.0
    status_texts: list[str] = field(default_factory=list)


class VehicleLink:
    """One MAVLink connection and the vehicle state read from it."""

    def __init__(self, connection: str, heartbeat_timeout_sec: float = 20.0) -> None:
        self.mav = mavutil.mavlink_connection(connection)
        heartbeat = self.mav.wait_heartbeat(timeout=heartbeat_timeout_sec)
        if heartbeat is None:
            raise TimeoutError(f"no heartbeat on {connection} within {heartbeat_timeout_sec} s")
        self.target_system = self.mav.target_system
        self.target_component = self.mav.target_component or 1
        self.state = VehicleState()
        self._apply(heartbeat)

    def _apply(self, msg) -> None:
        kind = msg.get_type()
        now = time.time()
        s = self.state
        if kind == "HEARTBEAT":
            if msg.get_srcSystem() != self.target_system or msg.type == M.MAV_TYPE_GCS:
                return
            s.heartbeat_time = now
            s.autopilot = msg.autopilot
            s.armed = bool(msg.base_mode & M.MAV_MODE_FLAG_SAFETY_ARMED)
            s.custom_mode = msg.custom_mode
            s.system_status = msg.system_status
        elif kind == "EXTENDED_SYS_STATE":
            s.landed_state = msg.landed_state
        elif kind == "LOCAL_POSITION_NED":
            s.local_time = now
            s.x, s.y, s.z, s.vx, s.vy, s.vz = msg.x, msg.y, msg.z, msg.vx, msg.vy, msg.vz
        elif kind == "ATTITUDE":
            s.roll, s.pitch, s.yaw = msg.roll, msg.pitch, msg.yaw
        elif kind == "GLOBAL_POSITION_INT":
            s.global_time = now
            s.lat_deg = msg.lat / 1e7
            s.lon_deg = msg.lon / 1e7
            s.alt_amsl_m = msg.alt / 1000.0
        elif kind == "STATUSTEXT":
            s.status_texts.append(msg.text)

    def pump(self, duration_sec: float = 0.0) -> None:
        """Read every pending message; keep reading for duration_sec."""
        # The stream never pauses (100+ messages/s), so stop on time, not on an empty queue.
        if duration_sec <= 0:
            for _ in range(2000):
                msg = self.mav.recv_match(blocking=False)
                if msg is None:
                    return
                self._apply(msg)
            return
        end = time.time() + duration_sec
        while time.time() < end:
            msg = self.mav.recv_match(blocking=True, timeout=0.05)
            if msg is not None:
                self._apply(msg)

    def wait_until(self, condition, timeout_sec: float, poll_sec: float = 0.1) -> bool:
        end = time.time() + timeout_sec
        while time.time() < end:
            self.pump(poll_sec)
            if condition(self.state):
                return True
        return False

    def command_long(self, command: int, *params: float) -> None:
        values = list(params) + [0.0] * (7 - len(params))
        self.mav.mav.command_long_send(self.target_system, self.target_component, command, 0, *values)

    def command_int(self, command: int, frame: int, params: tuple, x: int, y: int, z: float) -> None:
        p = list(params) + [0.0] * (4 - len(params))
        self.mav.mav.command_int_send(
            self.target_system, self.target_component, frame, command, 0, 0, *p, x, y, z
        )

    def wait_ack(self, command: int, timeout_sec: float = 5.0):
        end = time.time() + timeout_sec
        while time.time() < end:
            msg = self.mav.recv_match(blocking=True, timeout=0.1)
            if msg is None:
                continue
            self._apply(msg)
            if msg.get_type() == "COMMAND_ACK" and msg.command == command:
                return msg
        return None

    # Position frame helpers ------------------------------------------------

    def has_position(self, max_age_sec: float | None = None) -> bool:
        s = self.state
        if s.local_time <= 0.0 or s.global_time <= 0.0:
            return False
        if max_age_sec is None:
            return True
        now = time.time()
        return now - s.local_time <= max_age_sec and now - s.global_time <= max_age_sec

    def is_alive(self, max_age_sec: float) -> bool:
        return time.time() - self.state.heartbeat_time <= max_age_sec

    def origin(self) -> tuple[float, float, float]:
        """Latitude, longitude and AMSL altitude of the local NED origin."""
        s = self.state
        lat0 = s.lat_deg - math.degrees(s.x / EARTH_RADIUS_M)
        lon0 = s.lon_deg - math.degrees(s.y / (EARTH_RADIUS_M * math.cos(math.radians(s.lat_deg))))
        return lat0, lon0, s.alt_amsl_m + s.z

    def local_to_global(self, x_ned: float, y_ned: float, z_ned: float) -> tuple[float, float, float]:
        lat0, lon0, alt0 = self.origin()
        lat = lat0 + math.degrees(x_ned / EARTH_RADIUS_M)
        lon = lon0 + math.degrees(y_ned / (EARTH_RADIUS_M * math.cos(math.radians(lat0))))
        return lat, lon, alt0 - z_ned


class AutopilotBackend(abc.ABC):
    """Autopilot-specific commands. Positions are NED, yaw is NED (rad)."""

    name = "unknown"

    def __init__(self, link: VehicleLink) -> None:
        self.link = link

    @abc.abstractmethod
    def mode_name(self) -> str: ...

    @abc.abstractmethod
    def arm(self) -> tuple[bool, str]: ...

    @abc.abstractmethod
    def takeoff(self, altitude_up_m: float) -> tuple[bool, str]:
        """Start a takeoff to altitude_up_m above the local origin (returns once accepted)."""

    @abc.abstractmethod
    def goto(self, x_ned: float, y_ned: float, z_ned: float, yaw_ned_rad: float, speed_m_s: float) -> tuple[bool, str]:
        """Command a position; the autopilot holds it after the command (returns once accepted)."""

    @abc.abstractmethod
    def land(self) -> tuple[bool, str]: ...


def _ack_result(ack, what: str) -> tuple[bool, str]:
    if ack is None:
        return False, f"{what}: no COMMAND_ACK"
    if ack.result in (M.MAV_RESULT_ACCEPTED, M.MAV_RESULT_IN_PROGRESS):
        return True, f"{what}: accepted"
    return False, f"{what}: rejected (MAV_RESULT {ack.result})"


class Px4Backend(AutopilotBackend):
    """PX4: NAV_TAKEOFF, DO_REPOSITION and NAV_LAND (Auto modes; no offboard stream)."""

    name = "px4"
    # PX4 custom_mode: main mode in bits 16..23, sub mode in bits 24..31
    _MAIN = {1: "MANUAL", 2: "ALTCTL", 3: "POSCTL", 4: "AUTO", 5: "ACRO", 6: "OFFBOARD", 7: "STABILIZED", 8: "RATTITUDE"}
    _AUTO = {1: "READY", 2: "TAKEOFF", 3: "LOITER", 4: "MISSION", 5: "RTL", 6: "LAND", 8: "FOLLOW", 9: "PRECLAND"}

    def mode_name(self) -> str:
        mode = self.link.state.custom_mode
        main = (mode >> 16) & 0xFF
        sub = (mode >> 24) & 0xFF
        name = self._MAIN.get(main, f"MAIN{main}")
        if main == 4:
            name += "." + self._AUTO.get(sub, f"SUB{sub}")
        return name

    def _set_hold(self) -> tuple[bool, str]:
        # AUTO.LOITER (Hold). PX4 refuses to arm in AUTO.LAND, the mode left after a landing.
        self.link.command_long(M.MAV_CMD_DO_SET_MODE, M.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED, 4, 3)
        return _ack_result(self.link.wait_ack(M.MAV_CMD_DO_SET_MODE), "hold mode")

    def arm(self) -> tuple[bool, str]:
        if self.mode_name() != "AUTO.LOITER":
            ok, message = self._set_hold()
            if not ok:
                return ok, message
        self.link.command_long(M.MAV_CMD_COMPONENT_ARM_DISARM, 1)
        return _ack_result(self.link.wait_ack(M.MAV_CMD_COMPONENT_ARM_DISARM), "arm")

    def takeoff(self, altitude_up_m: float) -> tuple[bool, str]:
        # param7: takeoff altitude AMSL (navigator_main.cpp); NaN keeps position and yaw.
        _, _, alt0 = self.link.origin()
        nan = float("nan")
        self.link.command_long(M.MAV_CMD_NAV_TAKEOFF, nan, 0, 0, nan, nan, nan, alt0 + altitude_up_m)
        return _ack_result(self.link.wait_ack(M.MAV_CMD_NAV_TAKEOFF), "takeoff")

    def goto(self, x_ned: float, y_ned: float, z_ned: float, yaw_ned_rad: float, speed_m_s: float) -> tuple[bool, str]:
        # DO_REPOSITION: param1 ground speed, param2 change-mode flag, param4 yaw.
        # PX4 uses param4 as radians (navigator_main.cpp), not degrees as in the MAVLink spec.
        lat, lon, alt = self.link.local_to_global(x_ned, y_ned, z_ned)
        self.link.command_int(
            M.MAV_CMD_DO_REPOSITION,
            M.MAV_FRAME_GLOBAL,
            (speed_m_s, M.MAV_DO_REPOSITION_FLAGS_CHANGE_MODE, 0.0, yaw_ned_rad),
            int(round(lat * 1e7)),
            int(round(lon * 1e7)),
            alt,
        )
        return _ack_result(self.link.wait_ack(M.MAV_CMD_DO_REPOSITION), "goto")

    def land(self) -> tuple[bool, str]:
        nan = float("nan")
        self.link.command_long(M.MAV_CMD_NAV_LAND, 0, 0, 0, nan, nan, nan, nan)
        return _ack_result(self.link.wait_ack(M.MAV_CMD_NAV_LAND), "land")


class ArduPilotBackend(AutopilotBackend):
    """ArduPilot (planned): GUIDED mode, NAV_TAKEOFF (relative altitude),
    SET_POSITION_TARGET_GLOBAL_INT for goto (held in GUIDED), LAND mode.
    Not implemented yet; the client and the commands do not change when it is."""

    name = "ardupilot"

    def _not_yet(self, what: str) -> tuple[bool, str]:
        raise NotImplementedError(f"ArduPilot {what} is not implemented yet (planned for urban mobility)")

    def mode_name(self) -> str:
        return f"MODE{self.link.state.custom_mode}"

    def arm(self) -> tuple[bool, str]:
        return self._not_yet("arm")

    def takeoff(self, altitude_up_m: float) -> tuple[bool, str]:
        return self._not_yet("takeoff")

    def goto(self, x_ned: float, y_ned: float, z_ned: float, yaw_ned_rad: float, speed_m_s: float) -> tuple[bool, str]:
        return self._not_yet("goto")

    def land(self) -> tuple[bool, str]:
        return self._not_yet("land")


BACKENDS = {"px4": Px4Backend, "ardupilot": ArduPilotBackend}


def select_backend(link: VehicleLink, autopilot: str = "auto") -> AutopilotBackend:
    if autopilot == "auto":
        if link.state.autopilot == M.MAV_AUTOPILOT_PX4:
            autopilot = "px4"
        elif link.state.autopilot == M.MAV_AUTOPILOT_ARDUPILOTMEGA:
            autopilot = "ardupilot"
        else:
            raise RuntimeError(f"unknown autopilot in HEARTBEAT: {link.state.autopilot}")
    return BACKENDS[autopilot](link)
