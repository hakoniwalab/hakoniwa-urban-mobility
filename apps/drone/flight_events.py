"""Wind and rotor faults where a Drone flies (its schedule's event zones).

A waypoint of a Drone schedule (apps/drone/drone_schedule.py) may set
`wind: {towards_deg, speed_m_s}` and/or `fault: {rotors, scale}` on the leg
from it to the next point the Drone flies to. The leg's zone is a box round
the leg: zone_width_m wide across it, zone_height_m tall round its line
(sheared along a climbing leg), and half its width longer at each end, so a
Drone holding at the waypoint is well inside it, not on its edge. The
point's own zone_width_m / zone_height_m override the schedule's (default
2 m x 2 m).

While the Drone is in a wind zone the wind blows (towards_deg: where it blows
to, Urban yaw: east 0, counter-clockwise); out of every wind zone it stops.
A fault holds once the Drone has entered its zone: the rotors listed (from 0,
as the Viewer's fault panel) get the thrust scale (0 stopped, 1 nominal) until
the flight ends. Both reach Drone Core as the `disturb` PDU
(hako_msgs/Disturbance): d_wind in the ROS frame (x north, y west) and
d_user_custom[1] one scale per rotor (urban_fault_injection.py).
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

DEFAULT_ZONE_WIDTH_M = 2.0
DEFAULT_ZONE_HEIGHT_M = 2.0
DISTURB_PDU = "disturb"
ROTOR_FAULT_SLOT = 1  # d_user_custom[1]: rotor thrust scales; [0] is GPS's


@dataclass(frozen=True)
class Wind:
    towards_deg: float  # Urban yaw: east 0, counter-clockwise
    speed_m_s: float

    def ros_vector(self) -> tuple[float, float, float]:
        """The wind in Drone Core's frame (ROS: x north, y west, z up)."""
        theta = math.radians(self.towards_deg)
        east, north = self.speed_m_s * math.cos(theta), self.speed_m_s * math.sin(theta)
        return (north, -east, 0.0)


@dataclass(frozen=True)
class Fault:
    rotors: tuple[int, ...]  # from 0
    scale: float             # 0 stopped .. 1 nominal


@dataclass(frozen=True)
class Zone:
    """A box round the leg from a to b (Urban ENU), as wide and tall as given."""

    label: str
    a: tuple[float, float, float]
    b: tuple[float, float, float]
    width_m: float
    height_m: float
    wind: Wind | None = None
    fault: Fault | None = None

    def _frame(self) -> tuple[float, float, float, float]:
        """(unit along east, unit along north, horizontal length) of the leg; a
        leg with no horizontal length (straight up or down) faces east."""
        de, dn = self.b[0] - self.a[0], self.b[1] - self.a[1]
        length = math.hypot(de, dn)
        if length < 1e-6:
            return 1.0, 0.0, 0.0
        return de / length, dn / length, length

    def contains(self, east_m: float, north_m: float, up_m: float) -> bool:
        ue, un, length = self._frame()
        de, dn = east_m - self.a[0], north_m - self.a[1]
        along = de * ue + dn * un
        across = -de * un + dn * ue
        if abs(across) > self.width_m / 2.0:
            return False
        if length == 0.0:  # a vertical leg: a square column from one height to the other
            if abs(along) > self.width_m / 2.0:
                return False
            low, high = sorted((self.a[2], self.b[2]))
            return low - self.height_m / 2.0 <= up_m <= high + self.height_m / 2.0
        if not -self.width_m / 2.0 <= along <= length + self.width_m / 2.0:
            return False
        ratio = min(1.0, max(0.0, along / length))  # past an end: that end's height
        line_up = self.a[2] + (self.b[2] - self.a[2]) * ratio
        return abs(up_m - line_up) <= self.height_m / 2.0

    def corners(self) -> list[list[float]]:
        """The box's 8 corners for a viewer: the bottom face (a-left, a-right,
        b-right, b-left), then the top face in the same order."""
        ue, un, length = self._frame()
        half_w, half_h = self.width_m / 2.0, self.height_m / 2.0
        left = (-un * half_w, ue * half_w)
        if length == 0.0:  # a vertical leg: a square column from one height to the other
            low, high = sorted((self.a[2], self.b[2]))
            ends = [((self.a[0] - ue * half_w, self.a[1] - un * half_w), low - half_h, high + half_h),
                    ((self.a[0] + ue * half_w, self.a[1] + un * half_w), low - half_h, high + half_h)]
        else:  # half the width past each end
            ends = [((self.a[0] - ue * half_w, self.a[1] - un * half_w), self.a[2] - half_h, self.a[2] + half_h),
                    ((self.b[0] + ue * half_w, self.b[1] + un * half_w), self.b[2] - half_h, self.b[2] + half_h)]
        (a_en, a_low, a_high), (b_en, b_low, b_high) = ends
        footprint = [(a_en, +1, a_low, a_high), (a_en, -1, a_low, a_high),
                     (b_en, -1, b_low, b_high), (b_en, +1, b_low, b_high)]
        bottom = [[en[0] + side * left[0], en[1] + side * left[1], low] for en, side, low, _ in footprint]
        top = [[en[0] + side * left[0], en[1] + side * left[1], high] for en, side, _, high in footprint]
        return [[round(value, 3) for value in corner] for corner in bottom + top]

    def viewer(self) -> dict:
        """The zone as the Viewer draws it (hakoniwa-threejs-drone flightPaths[].zones)."""
        result: dict[str, Any] = {"label": self.label, "corners": self.corners()}
        if self.wind is not None:
            result["wind"] = {"towards_deg": self.wind.towards_deg, "speed_m_s": self.wind.speed_m_s}
        if self.fault is not None:
            result["fault"] = {"rotors": list(self.fault.rotors), "scale": self.fault.scale}
        return result


class EventState:
    """What the zones do to a Drone as it flies: the wind it is in, the faults
    it has entered. update() returns the disturbance to send when it changed."""

    def __init__(self, zones: list[Zone]):
        self.zones = zones
        self.faults: dict[int, float] = {}  # rotor -> scale, held once entered
        self.sent: tuple | None = None

    def update(self, east_m: float, north_m: float, up_m: float) -> tuple[Wind | None, dict[int, float]] | None:
        wind = None
        for zone in self.zones:
            if not zone.contains(east_m, north_m, up_m):
                continue
            if zone.wind is not None and wind is None:
                wind = zone.wind
            if zone.fault is not None:
                for rotor in zone.fault.rotors:
                    self.faults[rotor] = min(self.faults.get(rotor, 1.0), zone.fault.scale)
        state = (wind, tuple(sorted(self.faults.items())))
        if state == (self.sent or (None, ())):
            return None
        self.sent = state
        return wind, dict(self.faults)


def rotor_scales(faults: dict[int, float]) -> list[float]:
    """One thrust scale per rotor, rotor 0 first, up to the highest faulted one."""
    count = max(faults, default=-1) + 1
    return [faults.get(rotor, 1.0) for rotor in range(count)]


def disturbance_pdu(wind: Wind | None, faults: dict[int, float]) -> bytes:
    """The disturb PDU's bytes (hako_msgs/Disturbance) for the wind and the rotor faults."""
    from hakoniwa_pdu.pdu_msgs.hako_msgs.pdu_conv_Disturbance import py_to_pdu_Disturbance
    from hakoniwa_pdu.pdu_msgs.hako_msgs.pdu_pytype_Disturbance import Disturbance
    from hakoniwa_pdu.pdu_msgs.hako_msgs.pdu_pytype_DisturbanceUserCustom import DisturbanceUserCustom

    disturbance = Disturbance()
    x, y, z = wind.ros_vector() if wind is not None else (0.0, 0.0, 0.0)
    disturbance.d_wind.value.x, disturbance.d_wind.value.y, disturbance.d_wind.value.z = x, y, z
    gps = DisturbanceUserCustom()
    rotors = DisturbanceUserCustom()
    rotors.data = rotor_scales(faults)
    disturbance.d_user_custom = [gps, rotors]
    return py_to_pdu_Disturbance(disturbance)
