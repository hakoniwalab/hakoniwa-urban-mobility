#!/usr/bin/env python3
"""Reusable AckermannDrive clients for Urban Car PDU endpoints."""

from __future__ import annotations

import math
import ctypes
import os
from pathlib import Path
import platform
import sys
import time
from typing import NamedTuple


ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT.parent
sys.path.insert(0, str(WORKSPACE / "hakoniwa-pdu-registry"))

from hakoniwa_pdu.impl.pdu_channel_config import PduChannelConfig  # noqa: E402
from pdu.python.ackermann_msgs.pdu_conv_AckermannDrive import (  # noqa: E402
    py_to_pdu_AckermannDrive,
)
from pdu.python.ackermann_msgs.pdu_pytype_AckermannDrive import AckermannDrive  # noqa: E402
from pdu.python.sensor_msgs.pdu_conv_MultiDOFJointState import (  # noqa: E402
    pdu_to_py_MultiDOFJointState,
)


class AckermannClientError(RuntimeError):
    """The external PDU client could not initialize or publish a command."""


class VehiclePose(NamedTuple):
    """One vehicle pose expressed in the City World local ENU frame."""

    east_m: float
    north_m: float
    up_m: float
    yaw_rad: float


class HakoniwaPollingTransport:
    """External PDU transport backed by Hakoniwa's public polling C API.

    The callback-oriented ``hakopy.init_for_external()`` API does not attach
    the PDU data mmap in foundation builds compiled with
    ``FIX_PDU_CREATE_TIMING``.  The polling API has an explicit
    ``hakoniwa_asset_load_pdu_data()`` operation and is therefore the correct
    transport for a short-lived external command process.
    """

    def __init__(self, pdu_def: Path, runtime_asset: str = "UrbanCarFleet"):
        self.pdu_def = pdu_def
        self.runtime_asset = runtime_asset.encode("utf-8")
        self._channels = PduChannelConfig(str(pdu_def))
        install = Path(
            os.environ.get(
                "HAKONIWA_CORE_ROOT",
                WORKSPACE / "hakoniwa-business-pack/work/foundation/install",
            )
        ).expanduser().resolve()
        core_config = install.parent / "config/cpp_core_config.json"
        os.environ.setdefault("HAKO_CONFIG_PATH", str(core_config))
        if platform.system() == "Darwin":
            candidates = (
                install / "lib/libshakoc.1.0.0.dylib",
                install / "lib/libshakoc.dylib",
            )
        else:
            candidates = (
                install / "lib/libshakoc.so.1",
                install / "lib/libshakoc.so",
            )
        library = next((path for path in candidates if path.is_file()), None)
        if library is None:
            raise AckermannClientError(
                f"Hakoniwa polling library not found under {install / 'lib'}"
            )
        self._lib = ctypes.CDLL(str(library))
        self._lib.hakoniwa_asset_init.restype = ctypes.c_int
        self._lib.hakoniwa_asset_load_pdu_data.restype = None
        self._lib.hakoniwa_asset_get_worldtime.restype = ctypes.c_longlong
        self._lib.hakoniwa_asset_get_pdu_channel.argtypes = [
            ctypes.c_char_p,
            ctypes.c_int,
        ]
        self._lib.hakoniwa_asset_get_pdu_channel.restype = ctypes.c_int
        self._lib.hakoniwa_asset_write_pdu.argtypes = [
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_size_t,
        ]
        self._lib.hakoniwa_asset_write_pdu.restype = ctypes.c_int
        self._lib.hakoniwa_asset_read_pdu.argtypes = [
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_size_t,
        ]
        self._lib.hakoniwa_asset_read_pdu.restype = ctypes.c_int
        self._connected = False

    def connect(self) -> None:
        if self._lib.hakoniwa_asset_init() != 0:
            raise AckermannClientError(
                "failed to attach to Hakoniwa; start the Urban Car runtime first"
            )
        self._lib.hakoniwa_asset_load_pdu_data()
        self._connected = True

    def channel_id(self, robot: str, pdu: str) -> int:
        configured = self._channels.get_pdu_channel_id(robot, pdu)
        if configured < 0 or not self._connected:
            return -1
        actual = self._lib.hakoniwa_asset_get_pdu_channel(
            robot.encode("utf-8"), configured
        )
        return configured if actual >= 0 else -1

    def simulation_time_sec(self) -> float:
        return max(0, int(self._lib.hakoniwa_asset_get_worldtime())) / 1_000_000.0

    def send(self, robot: str, pdu: str, payload: bytearray) -> bool:
        channel_id = self._channels.get_pdu_channel_id(robot, pdu)
        if channel_id < 0 or not self._connected:
            return False
        raw = bytes(payload)
        buffer = ctypes.create_string_buffer(raw, len(raw))
        # The event-aware writer is required here.  AckermannDrive is consumed
        # by a receive-event subscriber, so a raw/nolock mmap write would
        # update the bytes without waking the controller.
        return self._lib.hakoniwa_asset_write_pdu(
            self.runtime_asset,
            robot.encode("utf-8"),
            channel_id,
            buffer,
            len(raw),
        ) == 0

    def read(self, robot: str, pdu: str) -> bytearray | None:
        channel_id = self._channels.get_pdu_channel_id(robot, pdu)
        pdu_size = self._channels.get_pdu_size(robot, pdu)
        if channel_id < 0 or pdu_size <= 0 or not self._connected:
            return None
        buffer = ctypes.create_string_buffer(pdu_size)
        if self._lib.hakoniwa_asset_read_pdu(
            self.runtime_asset,
            robot.encode("utf-8"),
            channel_id,
            buffer,
            pdu_size,
        ) != 0:
            return None
        return bytearray(buffer.raw)

    def close(self) -> None:
        self._connected = False


class AckermannFleetClient:
    """Publish commands for multiple named cars through one PDU service."""

    def __init__(
        self,
        pdu_def: str | Path,
        robots: list[str] | tuple[str, ...],
        pdu: str = "ackermann_cmd",
        rate_hz: float = 50.0,
        publish_timeout_sec: float = 1.0,
    ):
        self.pdu_def = Path(pdu_def).expanduser().resolve()
        self.robots = tuple(robot.strip() for robot in robots)
        self.pdu = pdu.strip()
        self.rate_hz = float(rate_hz)
        self.publish_timeout_sec = float(publish_timeout_sec)
        if not self.pdu_def.is_file():
            raise ValueError(f"PDU definition not found: {self.pdu_def}")
        if not self.robots or any(not robot for robot in self.robots) or not self.pdu:
            raise ValueError("robot list and PDU name must not be empty")
        if len(set(self.robots)) != len(self.robots):
            raise ValueError("robot names must be unique")
        if not math.isfinite(self.rate_hz) or self.rate_hz <= 0.0:
            raise ValueError("rate_hz must be finite and positive")
        if (
            not math.isfinite(self.publish_timeout_sec)
            or self.publish_timeout_sec < 0.0
        ):
            raise ValueError("publish_timeout_sec must be finite and non-negative")
        self._transport: HakoniwaPollingTransport | None = None

    @property
    def connected(self) -> bool:
        return self._transport is not None

    def simulation_time_sec(self) -> float:
        if self._transport is None:
            raise AckermannClientError("client is not connected")
        return self._transport.simulation_time_sec()

    def connect(self) -> "AckermannFleetClient":
        if self.connected:
            return self
        transport = HakoniwaPollingTransport(self.pdu_def)
        transport.connect()
        for robot in self.robots:
            if transport.channel_id(robot, self.pdu) < 0:
                transport.close()
                raise AckermannClientError(
                    f"unknown Ackermann command PDU: {robot}/{self.pdu}"
                )
        self._transport = transport
        return self

    def send(self, robot: str, speed_m_s: float, steering_rad: float = 0.0) -> None:
        if robot not in self.robots:
            raise ValueError(f"robot is not managed by this client: {robot}")
        speed = float(speed_m_s)
        steering = float(steering_rad)
        if not math.isfinite(speed) or not math.isfinite(steering):
            raise ValueError("speed and steering angle must be finite")
        if self._transport is None:
            raise AckermannClientError("client is not connected")
        command = AckermannDrive()
        command.speed = speed
        command.steering_angle = steering
        payload = py_to_pdu_AckermannDrive(command)
        deadline = time.monotonic() + self.publish_timeout_sec
        while not self._transport.send(robot, self.pdu, payload):
            # The event-aware writer reports false while this channel's prior
            # receive event is pending. A heavy Viewer scene can therefore
            # apply normal, transient backpressure to an external publisher.
            if time.monotonic() >= deadline:
                raise AckermannClientError(
                    "timed out publishing Ackermann command "
                    f"after {self.publish_timeout_sec:g}s: {robot}/{self.pdu}; "
                    "the runtime may be paused, stopped, or not consuming commands"
                )
            time.sleep(min(0.002, 0.25 / self.rate_hz))

    def vehicle_poses(
        self,
        state_robot: str = "UrbanFleet",
        state_pdu: str = "vehicle_states",
    ) -> dict[str, VehiclePose]:
        """Read the latest fleet body states and convert MuJoCo coordinates to ENU."""
        if self._transport is None:
            raise AckermannClientError("client is not connected")
        raw = self._transport.read(state_robot, state_pdu)
        if raw is None:
            raise AckermannClientError(
                f"failed to read vehicle state PDU: {state_robot}/{state_pdu}"
            )
        try:
            state = pdu_to_py_MultiDOFJointState(raw)
        except (IndexError, TypeError, ValueError) as error:
            raise AckermannClientError("invalid vehicle state PDU payload") from error
        if len(state.joint_names) != len(state.transforms):
            raise AckermannClientError(
                "vehicle state PDU has mismatched names and transforms"
            )
        result: dict[str, VehiclePose] = {}
        for name, transform in zip(state.joint_names, state.transforms):
            if not name or name in result:
                raise AckermannClientError(
                    "vehicle state PDU contains an empty or duplicate name"
                )
            position = transform.translation
            quaternion = transform.rotation
            # The state publisher reports MuJoCo X=North, Y=-East, Z=Up.
            # Recover MuJoCo yaw from ROS-order quaternion [x,y,z,w], then
            # rotate the heading into ENU, where zero yaw points East.
            yaw_mjcf = math.atan2(
                2.0 * (quaternion.w * quaternion.z + quaternion.x * quaternion.y),
                1.0 - 2.0 * (quaternion.y ** 2 + quaternion.z ** 2),
            )
            yaw_enu = math.atan2(
                math.sin(yaw_mjcf + math.pi / 2.0),
                math.cos(yaw_mjcf + math.pi / 2.0),
            )
            pose = VehiclePose(
                east_m=-float(position.y),
                north_m=float(position.x),
                up_m=float(position.z),
                yaw_rad=yaw_enu,
            )
            if not all(math.isfinite(value) for value in (
                pose.east_m, pose.north_m, pose.up_m, pose.yaw_rad
            )):
                raise AckermannClientError(
                    f"vehicle state PDU contains non-finite pose: {name}"
                )
            result[name] = pose
        return result

    def stop(self, robots: list[str] | tuple[str, ...] | None = None, repeat: int = 3) -> None:
        if repeat < 1:
            raise ValueError("stop repeat count must be positive")
        selected = self.robots if robots is None else tuple(robots)
        if any(robot not in self.robots for robot in selected):
            raise ValueError("stop contains a robot not managed by this client")
        period = 1.0 / self.rate_hz
        for index in range(repeat):
            for robot in selected:
                self.send(robot, 0.0, 0.0)
            if index + 1 < repeat:
                time.sleep(period)

    def close(self, *, stop: bool = True) -> None:
        if self._transport is None:
            return
        try:
            if stop:
                self.stop()
        finally:
            self._transport.close()
            self._transport = None

    def __enter__(self) -> "AckermannFleetClient":
        return self.connect()

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        try:
            self.close(stop=True)
        except AckermannClientError:
            if exc_value is None:
                raise


class AckermannClient:
    """Single-car convenience wrapper over :class:`AckermannFleetClient`."""

    def __init__(
        self,
        pdu_def: str | Path,
        robot: str,
        pdu: str = "ackermann_cmd",
        rate_hz: float = 50.0,
    ):
        self.robot = robot.strip()
        self.rate_hz = float(rate_hz)
        self._fleet = AckermannFleetClient(pdu_def, [self.robot], pdu, rate_hz)

    @property
    def connected(self) -> bool:
        return self._fleet.connected

    def connect(self) -> "AckermannClient":
        self._fleet.connect()
        return self

    def send(self, speed_m_s: float, steering_rad: float = 0.0) -> None:
        self._fleet.send(self.robot, speed_m_s, steering_rad)

    def drive(
        self,
        speed_m_s: float,
        steering_rad: float,
        duration_sec: float,
        *,
        stop_after: bool = True,
    ) -> None:
        duration = float(duration_sec)
        if not math.isfinite(duration) or duration <= 0.0:
            raise ValueError("duration_sec must be finite and positive")
        period = 1.0 / self.rate_hz
        deadline = time.monotonic() + duration
        try:
            while True:
                self.send(speed_m_s, steering_rad)
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    break
                time.sleep(min(period, remaining))
        finally:
            if stop_after:
                self.stop()

    def stop(self, repeat: int = 3) -> None:
        self._fleet.stop(repeat=repeat)

    def close(self, *, stop: bool = True) -> None:
        self._fleet.close(stop=stop)

    def __enter__(self) -> "AckermannClient":
        return self.connect()

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close(stop=True)
