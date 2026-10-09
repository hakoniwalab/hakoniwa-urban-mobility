"""The schedule's Drone client over MAVLink (PX4 SITL), for apps/drone/drone_schedule.py.

drone_schedule.py drives a Drone with Drone Core's shared-runtime RPC client:
set_ready/takeoff/goto/land as futures, poll_once(), and the Drone's PDUs
(get_raw_pdu, the PDU manager for the disturb PDU). With PX4 SITL the
commands go to PX4 over MAVLink instead (apps/drone/mavlink), while the PDUs
stay Hakoniwa's: the aircraft service writes the same pos/velocity/status
PDUs and reads the same disturb PDU as the Drone service.

Frames: the schedule speaks Drone Core's world ROS frame (x north, y west,
z up, from the World origin). PX4's local origin is where its EKF started,
the Drone's spawn, so positions are sent relative to the spawn.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
MAVLINK_DIR = ROOT / "apps" / "drone" / "mavlink"


def _import_client():
    # pymavlink comes with the managed Recipe (recipes/requirements/urban-drone-px4.txt).
    if str(MAVLINK_DIR) not in sys.path:
        sys.path.insert(0, str(MAVLINK_DIR))
    from mavlink_drone_client import MavlinkDroneClient

    return MavlinkDroneClient


class MavlinkScheduleClient:
    """set_ready/takeoff/goto/land over MAVLink as futures; PDUs through the Hakoniwa client."""

    def __init__(self, connection: str, spawn_enu: tuple[float, float, float], pdu_client) -> None:
        self.spawn_enu = spawn_enu
        self.pdu_client = pdu_client  # Drone Core's RPC client, used for its PDUs only
        self._runtime = pdu_client._runtime
        self._connection = connection
        self._client = None
        # One worker: MAVLink commands run one after the other, like the schedule.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mavlink")

    def _mavlink(self):
        if self._client is None:
            self._client = _import_client()(connection=self._connection)
        return self._client

    def _relative(self, x_world: float, y_world: float, z_world: float) -> tuple[float, float, float]:
        east0, north0, up0 = self.spawn_enu
        return x_world - north0, y_world + east0, z_world - up0

    def _submit(self, call) -> Future:
        return self._executor.submit(call)

    # The RPC client's interface used by drone_schedule.Runner ------------------

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

    def get_raw_pdu(self, pdu_name: str):
        return self.pdu_client.get_raw_pdu(pdu_name)
