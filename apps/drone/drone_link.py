"""One interface for flying a Drone, whatever flies it (apps/drone/drone_schedule.py).

A DroneLink gives the flight program:

- commands: set_ready_async, takeoff_async, goto_async, land_async, as futures
  (done(), result(timeout)), plus poll_once();
- state(): position (Urban ENU), yaw (ROS, rad), speed, heading of travel
  (Urban yaw, deg) and the contact count (None when unknown);
- send_disturbance(wind, faults): wind and rotor faults into the simulator.

Two implementations:

| | RpcDroneLink (Drone Core control) | MavlinkDroneLink (PX4 SITL) |
|---|---|---|
| commands | Drone Core RPC | MAVLink (apps/drone/mavlink) |
| state | the Drone's PDUs (pos, velocity, status) | MAVLink; the contact count from the status PDU |
| disturbance | the disturb PDU | the disturb PDU |

Commands take Drone Core's world ROS frame (x north, y west, z up from the
World origin; yaw counter-clockwise from north, degrees). MavlinkDroneLink
sends them relative to the spawn, PX4's local origin.

The PDUs are read and written through hakopy with the channel ids and sizes
of the simulator's PDU definition (PduAccess). Nothing is registered in
shared memory: the PDU data is created at the simulation start from what the
assets registered, so a later registration (an RPC client's service
channels) changes the size every later asset expects. That is why the
MAVLink link has no RPC client.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
MAVLINK_DIR = ROOT / "apps" / "drone" / "mavlink"
# How long to wait for PX4's first heartbeat: with several PX4 SITL processes
# one can take well over 20 s to open its API link.
MAVLINK_HEARTBEAT_TIMEOUT_SEC = 120.0


class DroneLinkError(RuntimeError):
    pass


# --- PDUs ----------------------------------------------------------------------------

class PduAccess:
    """Read and write one robot's PDUs in shared memory by name (hakopy, no registration)."""

    def __init__(self, pdu_def: Path, robot: str, hakopy) -> None:
        self.robot = robot
        self.hakopy = hakopy
        self.channels = self.channels_of(Path(pdu_def), robot)

    @staticmethod
    def channels_of(pdu_def: Path, robot: str) -> dict[str, tuple[int, int]]:
        """{PDU name: (channel id, size)} of a robot in a PDU definition (paths + robots)."""
        definition = json.loads(pdu_def.read_text(encoding="utf-8"))
        paths = {item["id"]: item["path"] for item in definition.get("paths", [])}
        entry = next((item for item in definition.get("robots", []) if item.get("name") == robot), None)
        if entry is None or entry.get("pdutypes_id") not in paths:
            raise DroneLinkError(f"{pdu_def} has no PDU types for {robot}")
        types_path = Path(paths[entry["pdutypes_id"]])
        if not types_path.is_absolute():
            types_path = pdu_def.parent / types_path
        types = json.loads(types_path.read_text(encoding="utf-8"))
        return {item["name"]: (int(item["channel_id"]), int(item["pdu_size"])) for item in types}

    def read(self, name: str) -> bytearray | None:
        channel, size = self.channels[name]
        data = self.hakopy.pdu_read(self.robot, channel, size)
        return bytearray(data) if data else None

    def write(self, name: str, raw: bytes | bytearray) -> bool:
        channel, _ = self.channels[name]
        return bool(self.hakopy.pdu_write(self.robot, channel, bytearray(raw), len(raw)))


def _twist(raw):
    from hakoniwa_pdu.pdu_msgs.geometry_msgs.pdu_conv_Twist import pdu_to_py_Twist

    return pdu_to_py_Twist(raw)


def contact_count(pdu: PduAccess | None) -> int | None:
    """The status PDU's contact count, or None when unknown: no status PDU, or one nobody
    wrote yet (the PX4 SITL aircraft service does not write it)."""
    if pdu is None or "status" not in pdu.channels:
        return None
    raw = pdu.read("status")
    if raw is None:
        return None
    from hakoniwa_pdu.pdu_msgs.hako_msgs.pdu_conv_DroneStatus import pdu_to_py_DroneStatus

    try:
        return int(pdu_to_py_DroneStatus(raw).collided_counts)
    except ValueError:  # never written: no PDU metadata
        return None


def write_disturbance(pdu: PduAccess, wind, faults) -> None:
    import flight_events

    if not pdu.write(flight_events.DISTURB_PDU, flight_events.disturbance_pdu(wind, faults)):
        raise DroneLinkError(f"cannot write {pdu.robot}'s {flight_events.DISTURB_PDU} PDU")


# --- Drone Core control: RPC commands ------------------------------------------------

class RpcDroneLink:
    """Commands over Drone Core RPC; state and disturbance through the Drone's PDUs."""

    def __init__(self, rpc_client, pdu: PduAccess) -> None:
        self.rpc = rpc_client
        self.pdu = pdu

    def set_ready_async(self):
        return self.rpc.set_ready_async()

    def takeoff_async(self, alt_m: float):
        return self.rpc.takeoff_async(alt_m)

    def goto_async(self, x: float, y: float, z: float, yaw_deg: float = 0.0, **kwargs):
        return self.rpc.goto_async(x, y, z, yaw_deg=yaw_deg, **kwargs)

    def land_async(self, timeout_sec: float = 0.0):
        return self.rpc.land_async(timeout_sec=timeout_sec)

    def poll_once(self) -> None:
        self.rpc.poll_once()

    def state(self) -> dict:
        raw = self.pdu.read("pos")
        if raw is None:
            raise DroneLinkError("no pos PDU yet")
        pose = _twist(raw)
        speed, heading = 0.0, 0.0
        velocity_raw = self.pdu.read("velocity")
        if velocity_raw is not None:
            velocity = _twist(velocity_raw).linear
            speed = math.sqrt(float(velocity.x) ** 2 + float(velocity.y) ** 2 + float(velocity.z) ** 2)
            heading = math.degrees(math.atan2(float(velocity.x), -float(velocity.y)))
        return {"enu": (-float(pose.linear.y), float(pose.linear.x), float(pose.linear.z)),
                "yaw_rad": float(pose.angular.z), "speed": speed, "heading_deg": heading,
                "collisions": contact_count(self.pdu)}

    def send_disturbance(self, wind, faults) -> None:
        write_disturbance(self.pdu, wind, faults)


# --- PX4 SITL: MAVLink commands ------------------------------------------------------

def _import_mavlink_client():
    # pymavlink comes with the managed Recipe (recipes/requirements/urban-drone-px4.txt).
    if str(MAVLINK_DIR) not in sys.path:
        sys.path.insert(0, str(MAVLINK_DIR))
    from mavlink_drone_client import MavlinkDroneClient

    return MavlinkDroneClient


class MavlinkDroneLink:
    """Commands and state over MAVLink; the contact count and disturbance through the PDUs."""

    def __init__(self, connection: str, spawn_enu: tuple[float, float, float], pdu: PduAccess | None,
                 autopilot: str = "PX4 SITL") -> None:
        self.spawn_enu = spawn_enu
        self.autopilot = autopilot  # names the PX4 process in a connection error
        self.pdu = pdu
        self._connection = connection
        self._client = None
        self._busy = 0
        # One worker: MAVLink commands run one after the other, like the schedule.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mavlink")

    def _mavlink(self):
        if self._client is None:
            try:
                self._client = _import_mavlink_client()(connection=self._connection,
                                                        heartbeat_timeout_sec=MAVLINK_HEARTBEAT_TIMEOUT_SEC)
            except TimeoutError as exc:
                # PX4's startup script now and then stops before it opens the API
                # link: a PX4 SITL bug in its command server (px4_daemon), which
                # tools/px4-patches works around; a new start recovers.
                raise DroneLinkError(
                    f"{self.autopilot} did not open its MAVLink API on {self._connection} within "
                    f"{MAVLINK_HEARTBEAT_TIMEOUT_SEC:.0f} s. This is a known PX4 SITL startup bug "
                    "(a startup command of PX4 never returns). To recover, stop the simulation and "
                    "start it again (docs/px4-sitl.md Troubleshooting)"
                ) from exc
        return self._client

    def _relative(self, x_world: float, y_world: float, z_world: float) -> tuple[float, float, float]:
        east0, north0, up0 = self.spawn_enu
        return x_world - north0, y_world + east0, z_world - up0

    def _submit(self, call) -> Future:
        self._busy += 1

        def run():
            try:
                return call()
            finally:
                self._busy -= 1

        return self._executor.submit(run)

    def set_ready_async(self) -> Future:
        return self._submit(lambda: self._mavlink().set_ready())

    def takeoff_async(self, alt_m: float) -> Future:
        rise = alt_m - self.spawn_enu[2]
        return self._submit(lambda: self._mavlink().takeoff(rise))

    def goto_async(self, x: float, y: float, z: float, yaw_deg: float = 0.0, *, speed_m_s: float = 1.0,
                   tolerance_m: float = 0.5, timeout_sec: float = 30.0) -> Future:
        rx, ry, rz = self._relative(x, y, z)
        return self._submit(lambda: self._mavlink().goto(rx, ry, rz, yaw_deg, speed_m_s=speed_m_s,
                                                         tolerance_m=tolerance_m, timeout_sec=timeout_sec))

    def land_async(self, timeout_sec: float = 0.0) -> Future:
        return self._submit(lambda: self._mavlink().land(timeout_sec=timeout_sec))

    def poll_once(self) -> None:
        """Futures complete on their own thread."""

    def state(self) -> dict:
        if self._client is None:
            raise DroneLinkError("the MAVLink connection is not open yet")
        link = self._client.link
        if not self._busy:
            link.pump(0.0)  # between commands nothing else reads the link
        state = link.state
        if not link.has_position():
            raise DroneLinkError("no position from PX4 yet")
        east0, north0, up0 = self.spawn_enu
        speed = math.sqrt(state.vx ** 2 + state.vy ** 2 + state.vz ** 2)
        return {"enu": (east0 + state.y, north0 + state.x, up0 - state.z), "yaw_rad": -state.yaw,
                "speed": speed, "heading_deg": math.degrees(math.atan2(state.vx, state.vy)),
                "collisions": contact_count(self.pdu)}

    def send_disturbance(self, wind, faults) -> None:
        if self.pdu is None:
            raise DroneLinkError("no PDU definition for the disturbance (--pdu-def)")
        write_disturbance(self.pdu, wind, faults)
