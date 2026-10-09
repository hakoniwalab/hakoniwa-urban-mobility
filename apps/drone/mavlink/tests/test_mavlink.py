from __future__ import annotations

import importlib
import inspect
import math
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


MAVLINK_RPC_DIR = Path(__file__).resolve().parents[1]
COMMANDS_DIR = MAVLINK_RPC_DIR / "commands"
for import_dir in (MAVLINK_RPC_DIR, COMMANDS_DIR):
    if str(import_dir) not in sys.path:
        sys.path.insert(0, str(import_dir))

import autopilot  # noqa: E402
import mavlink_drone_client  # noqa: E402
from autopilot import (  # noqa: E402
    ArduPilotBackend,
    LANDED_IN_AIR,
    LANDED_ON_GROUND,
    Px4Backend,
    VehicleLink,
    VehicleState,
    select_backend,
)
from mavlink_drone_client import MavlinkDroneClient, quaternion_from_euler  # noqa: E402


M = autopilot.M


class RecordingLink:
    origin = VehicleLink.origin
    local_to_global = VehicleLink.local_to_global
    has_position = VehicleLink.has_position
    is_alive = VehicleLink.is_alive

    def __init__(self) -> None:
        self.state = VehicleState(local_time=1.0, global_time=1.0)
        self.commands: list[tuple] = []
        self.acks: dict[int, list[object | None]] = {}
        self.wait_states: list[VehicleState] = []
        self.pump_calls: list[float] = []

    def command_long(self, command: int, *params: float) -> None:
        self.commands.append(("long", command, params))

    def command_int(
        self, command: int, frame: int, params: tuple, x: int, y: int, z: float
    ) -> None:
        self.commands.append(("int", command, frame, params, x, y, z))

    def wait_ack(self, command: int, timeout_sec: float = 5.0):
        values = self.acks.get(command, [])
        return values.pop(0) if values else SimpleNamespace(result=M.MAV_RESULT_ACCEPTED)

    def pump(self, duration_sec: float = 0.0) -> None:
        self.pump_calls.append(duration_sec)

    def wait_until(self, condition, timeout_sec: float, poll_sec: float = 0.1) -> bool:
        if condition(self.state):
            return True
        if not self.wait_states:
            return False
        for state in self.wait_states:
            self.state = state
            if condition(state):
                return True
        return False


class RecordingBackend:
    name = "recording"

    def __init__(self, link: RecordingLink) -> None:
        self.link = link
        self.calls: list[tuple] = []
        self.arm_result = (True, "arm accepted")
        self.takeoff_result = (True, "takeoff accepted")
        self.goto_result = (True, "goto accepted")
        self.land_result = (True, "land accepted")

    def mode_name(self) -> str:
        return "TEST"

    def arm(self):
        self.calls.append(("arm",))
        return self.arm_result

    def takeoff(self, altitude_up_m: float):
        self.calls.append(("takeoff", altitude_up_m))
        return self.takeoff_result

    def goto(
        self,
        x_ned: float,
        y_ned: float,
        z_ned: float,
        yaw_ned_rad: float,
        speed_m_s: float,
    ):
        self.calls.append(
            ("goto", x_ned, y_ned, z_ned, yaw_ned_rad, speed_m_s)
        )
        return self.goto_result

    def land(self):
        self.calls.append(("land",))
        return self.land_result


def make_client(
    link: RecordingLink | None = None, backend: object | None = None
) -> tuple[MavlinkDroneClient, RecordingLink, object]:
    actual_link = link or RecordingLink()
    actual_backend = backend or RecordingBackend(actual_link)
    client = MavlinkDroneClient.__new__(MavlinkDroneClient)
    client.drone_name = "Drone"
    client.ready_timeout_sec = 1.0
    client.link = actual_link
    client.backend = actual_backend
    return client, actual_link, actual_backend


def px4_custom_mode(main: int, sub: int = 0) -> int:
    return (main << 16) | (sub << 24)


def ack(result: int) -> SimpleNamespace:
    return SimpleNamespace(result=result)


def test_origin_and_local_to_global_use_current_global_and_local_positions() -> None:
    link = RecordingLink()
    link.state.x = 120.0
    link.state.y = -45.0
    link.state.z = -8.0
    link.state.lat_deg = 35.5
    link.state.lon_deg = 139.75
    link.state.alt_amsl_m = 108.0

    lat0, lon0, alt0 = link.origin()
    assert lat0 == pytest.approx(
        35.5 - math.degrees(120.0 / autopilot.EARTH_RADIUS_M)
    )
    assert lon0 == pytest.approx(
        139.75
        - math.degrees(
            -45.0
            / (autopilot.EARTH_RADIUS_M * math.cos(math.radians(35.5)))
        )
    )
    assert alt0 == pytest.approx(100.0)

    lat, lon, alt = link.local_to_global(10.0, 20.0, -5.0)
    assert lat == pytest.approx(lat0 + math.degrees(10.0 / autopilot.EARTH_RADIUS_M))
    assert lon == pytest.approx(
        lon0
        + math.degrees(
            20.0 / (autopilot.EARTH_RADIUS_M * math.cos(math.radians(lat0)))
        )
    )
    assert alt == pytest.approx(105.0)


def test_client_goto_converts_ros_position_and_yaw_to_ned() -> None:
    client, link, backend = make_client()
    link.state.landed_state = LANDED_IN_AIR
    link.wait_states = [
        VehicleState(x=4.2, y=3.1, z=-2.2, landed_state=LANDED_IN_AIR)
    ]

    response = client.goto(
        4.0, -3.0, 2.0, 90.0, speed_m_s=2.5, tolerance_m=0.5, timeout_sec=8.0
    )

    assert response.ok
    assert backend.calls == [("goto", 4.0, 3.0, -2.0, -math.pi / 2, 2.5)]


def test_get_state_converts_ned_position_and_attitude_to_ros() -> None:
    client, link, _ = make_client()
    link.state.heartbeat_time = 99.0
    link.state.local_time = 99.0
    link.state.global_time = 99.0
    link.state.x, link.state.y, link.state.z = 1.0, 2.0, -3.0
    link.state.roll = math.radians(10.0)
    link.state.pitch = math.radians(20.0)
    link.state.yaw = math.radians(-30.0)

    with patch.object(autopilot.time, "time", return_value=100.0):
        response = client.get_state()

    assert response.ok
    assert response.is_ready
    assert (
        response.current_pose.position.x,
        response.current_pose.position.y,
        response.current_pose.position.z,
    ) == pytest.approx((1.0, -2.0, 3.0))
    assert response.current_pose.orientation == quaternion_from_euler(
        math.radians(10.0), math.radians(-20.0), math.radians(30.0)
    )


def test_get_state_reports_stale_heartbeat_as_not_ok_and_not_ready() -> None:
    client, link, _ = make_client()
    link.state.heartbeat_time = 96.999
    link.state.local_time = 100.0
    link.state.global_time = 100.0

    with patch.object(autopilot.time, "time", return_value=100.0):
        response = client.get_state()

    assert not response.ok
    assert not response.is_ready
    assert response.message.endswith(" (no heartbeat)")


def test_get_state_reports_fresh_heartbeat_but_stale_position_as_not_ready() -> None:
    client, link, _ = make_client()
    link.state.heartbeat_time = 100.0
    link.state.local_time = 98.999
    link.state.global_time = 100.0

    with patch.object(autopilot.time, "time", return_value=100.0):
        response = client.get_state()

    assert response.ok
    assert not response.is_ready
    assert "no heartbeat" not in response.message


def test_has_position_max_age_boundary_and_legacy_no_age_behavior() -> None:
    link = RecordingLink()
    link.state.local_time = 99.0
    link.state.global_time = 99.0

    with patch.object(autopilot.time, "time", return_value=100.0):
        assert link.has_position()
        assert link.has_position(1.0)

        link.state.local_time = 98.999
        assert link.has_position()
        assert not link.has_position(1.0)

        link.state.local_time = 99.0
        link.state.global_time = 98.999
        assert link.has_position()
        assert not link.has_position(1.0)

        link.state.local_time = 0.0
        assert not link.has_position()
        assert not link.has_position(1.0)


def test_px4_commands_contain_expected_mavlink_values() -> None:
    link = RecordingLink()
    link.state.lat_deg = 35.0
    link.state.lon_deg = 139.0
    link.state.alt_amsl_m = 55.0
    link.state.x, link.state.y, link.state.z = 5.0, -2.0, -3.0
    backend = Px4Backend(link)

    assert backend.takeoff(7.0)[0]
    _, _, alt0 = link.origin()
    kind, command, params = link.commands[-1]
    assert (kind, command) == ("long", M.MAV_CMD_NAV_TAKEOFF)
    assert math.isnan(params[0])
    assert params[6] == pytest.approx(alt0 + 7.0)

    assert backend.goto(12.0, -4.0, -6.0, -0.75, 3.5)[0]
    expected_lat, expected_lon, expected_alt = link.local_to_global(12.0, -4.0, -6.0)
    kind, command, frame, params, x, y, z = link.commands[-1]
    assert (kind, command, frame) == (
        "int",
        M.MAV_CMD_DO_REPOSITION,
        M.MAV_FRAME_GLOBAL,
    )
    assert params == pytest.approx(
        (3.5, M.MAV_DO_REPOSITION_FLAGS_CHANGE_MODE, 0.0, -0.75)
    )
    assert x == round(expected_lat * 1e7)
    assert y == round(expected_lon * 1e7)
    assert z == pytest.approx(expected_alt)

    assert backend.land()[0]
    kind, command, params = link.commands[-1]
    assert (kind, command) == ("long", M.MAV_CMD_NAV_LAND)
    assert all(value == 0 or math.isnan(value) for value in params)


def test_px4_arm_switches_to_hold_before_arm_and_reports_rejection() -> None:
    link = RecordingLink()
    link.state.custom_mode = px4_custom_mode(4, 6)  # AUTO.LAND
    link.acks[M.MAV_CMD_DO_SET_MODE] = [ack(M.MAV_RESULT_ACCEPTED)]
    link.acks[M.MAV_CMD_COMPONENT_ARM_DISARM] = [ack(M.MAV_RESULT_DENIED)]

    ok, message = Px4Backend(link).arm()

    assert not ok
    assert "rejected" in message
    assert link.commands[0] == (
        "long",
        M.MAV_CMD_DO_SET_MODE,
        (M.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED, 4, 3),
    )
    assert link.commands[1] == (
        "long",
        M.MAV_CMD_COMPONENT_ARM_DISARM,
        (1,),
    )


def test_takeoff_already_armed_skips_hold_mode_and_sends_takeoff_only() -> None:
    link = RecordingLink()
    link.state.armed = True
    link.state.custom_mode = px4_custom_mode(3)  # POSCTL, not AUTO.LOITER
    link.state.landed_state = LANDED_ON_GROUND
    link.state.lat_deg = 35.0
    link.state.lon_deg = 139.0
    link.state.alt_amsl_m = 50.0
    link.wait_states = [
        VehicleState(z=-5.0, landed_state=LANDED_IN_AIR, local_time=1.0, global_time=1.0)
    ]
    client, _, _ = make_client(link, Px4Backend(link))

    response = client.takeoff(5.0)

    assert response.ok
    assert [entry[1] for entry in link.commands] == [M.MAV_CMD_NAV_TAKEOFF]


def test_takeoff_retries_arming_until_the_timeout_without_sending_takeoff() -> None:
    # PX4 rejects arming until its preflight checks pass: takeoff retries, then gives up.
    client, link, backend = make_client()
    link.state.armed = False
    backend.arm_result = (False, "arm rejected")

    with patch.object(mavlink_drone_client, "ARM_RETRY_SEC", 0.0):
        response = client.takeoff(5.0, timeout_sec=0.05)

    assert not response.ok
    assert "arm rejected" in response.message
    assert backend.calls and all(call == ("arm",) for call in backend.calls)


def test_takeoff_arms_once_preflight_passes_and_then_takes_off() -> None:
    client, link, backend = make_client()
    link.state.armed = False
    results = iter([(False, "arm rejected"), (False, "arm rejected"), (True, "arm: accepted")])
    original_arm = backend.arm

    def arm():
        original_arm()
        return next(results)

    backend.arm = arm
    link.wait_states = [
        VehicleState(z=-5.0, landed_state=LANDED_IN_AIR, local_time=1.0, global_time=1.0)
    ]

    with patch.object(mavlink_drone_client, "ARM_RETRY_SEC", 0.0):
        response = client.takeoff(5.0, timeout_sec=2.0)

    assert response.ok
    assert backend.calls == [("arm",), ("arm",), ("arm",), ("takeoff", 5.0)]


def test_takeoff_reports_timeout_when_altitude_or_landed_state_is_not_reached() -> None:
    client, link, backend = make_client()
    link.state.armed = True
    link.wait_states = [
        VehicleState(z=-4.8, landed_state=LANDED_ON_GROUND, local_time=1.0, global_time=1.0)
    ]

    response = client.takeoff(5.0, tolerance_m=0.3, timeout_sec=2.0)

    assert not response.ok
    assert "not reached" in response.message
    assert backend.calls == [("takeoff", 5.0)]


def test_goto_does_not_send_when_vehicle_is_not_in_air() -> None:
    client, link, backend = make_client()
    link.state.landed_state = LANDED_ON_GROUND

    response = client.goto(1.0, 2.0, 3.0)

    assert not response.ok
    assert "not in the air" in response.message
    assert backend.calls == []


@pytest.mark.parametrize(
    ("arrived_position", "expected_ok"),
    [((1.2, -2.1, -3.1), True), ((2.0, -2.0, -3.0), False)],
)
def test_goto_uses_three_dimensional_tolerance_and_reports_timeout(
    arrived_position: tuple[float, float, float], expected_ok: bool
) -> None:
    client, link, backend = make_client()
    link.state.landed_state = LANDED_IN_AIR
    link.wait_states = [
        VehicleState(
            x=arrived_position[0],
            y=arrived_position[1],
            z=arrived_position[2],
            landed_state=LANDED_IN_AIR,
        )
    ]

    response = client.goto(1.0, 2.0, 3.0, tolerance_m=0.5, timeout_sec=4.0)

    assert response.ok is expected_ok
    assert backend.calls[0][0] == "goto"
    if not expected_ok:
        assert "not within 0.5 m" in response.message


def test_land_already_grounded_and_disarmed_does_not_send_command() -> None:
    client, link, backend = make_client()
    link.state.landed_state = LANDED_ON_GROUND
    link.state.armed = False

    response = client.land()

    assert response.ok
    assert backend.calls == []


class EndlessConnection:
    def __init__(self) -> None:
        self.calls = 0

    def recv_match(self, *, blocking: bool, timeout: float | None = None):
        self.calls += 1
        return SimpleNamespace(
            get_type=lambda: "STATUSTEXT", text="still streaming"
        )


def make_vehicle_link_with_connection(connection: EndlessConnection) -> VehicleLink:
    link = VehicleLink.__new__(VehicleLink)
    link.mav = connection
    link.target_system = 1
    link.target_component = 1
    link.state = VehicleState()
    return link


def test_pump_with_duration_returns_even_when_messages_never_stop() -> None:
    connection = EndlessConnection()
    link = make_vehicle_link_with_connection(connection)

    started = time.monotonic()
    link.pump(0.02)
    elapsed = time.monotonic() - started

    assert connection.calls > 0
    assert elapsed < 0.5


def test_zero_duration_pump_caps_an_endless_queue_at_2000_messages() -> None:
    connection = EndlessConnection()
    link = make_vehicle_link_with_connection(connection)

    link.pump(0)

    assert connection.calls == 2000


def test_select_backend_auto_detects_px4_and_ardupilot_and_rejects_unknown() -> None:
    link = RecordingLink()
    link.state.autopilot = M.MAV_AUTOPILOT_PX4
    assert isinstance(select_backend(link), Px4Backend)
    link.state.autopilot = M.MAV_AUTOPILOT_ARDUPILOTMEGA
    assert isinstance(select_backend(link), ArduPilotBackend)
    link.state.autopilot = M.MAV_AUTOPILOT_GENERIC
    with pytest.raises(RuntimeError, match="unknown autopilot"):
        select_backend(link)


def test_ardupilot_operations_are_explicitly_not_implemented() -> None:
    backend = ArduPilotBackend(RecordingLink())
    for operation in (
        backend.arm,
        lambda: backend.takeoff(5.0),
        lambda: backend.goto(1.0, 2.0, -3.0, 0.0, 1.0),
        backend.land,
    ):
        with pytest.raises(NotImplementedError, match="ArduPilot"):
            operation()


def test_public_client_methods_keep_the_rpc_operation_argument_contract() -> None:
    signatures = {
        name: inspect.signature(getattr(MavlinkDroneClient, name))
        for name in ("set_ready", "takeoff", "get_state", "goto", "land")
    }

    assert list(signatures["set_ready"].parameters) == ["self"]
    assert list(signatures["get_state"].parameters) == ["self"]
    assert list(signatures["takeoff"].parameters) == [
        "self",
        "alt_m",
        "tolerance_m",
        "timeout_sec",
    ]
    assert list(signatures["goto"].parameters) == [
        "self",
        "x",
        "y",
        "z",
        "yaw_deg",
        "speed_m_s",
        "tolerance_m",
        "timeout_sec",
    ]
    assert signatures["goto"].parameters["yaw_deg"].default == 0.0
    assert signatures["goto"].parameters["speed_m_s"].default == 1.0
    assert signatures["goto"].parameters["tolerance_m"].default == 0.5
    assert signatures["goto"].parameters["timeout_sec"].default == 30.0
    assert list(signatures["land"].parameters) == ["self", "timeout_sec"]
    assert signatures["land"].parameters["timeout_sec"].default == 0.0


class CommandClient:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def takeoff(self, *args, **kwargs):
        self.calls.append(("takeoff", args, kwargs))
        return SimpleNamespace(ok=True, message="ok")

    def goto(self, *args, **kwargs):
        self.calls.append(("goto", args, kwargs))
        return SimpleNamespace(ok=True, message="ok")

    def land(self, *args, **kwargs):
        self.calls.append(("land", args, kwargs))
        return SimpleNamespace(ok=True, message="ok")

    def get_state(self):
        self.calls.append(("get_state", (), {}))
        return SimpleNamespace(
            ok=True,
            is_ready=True,
            mode="TEST",
            message="ok",
            current_pose=SimpleNamespace(
                position=SimpleNamespace(x=0.0, y=0.0, z=0.0),
                orientation=SimpleNamespace(w=1.0, x=0.0, y=0.0, z=0.0),
            ),
        )


@pytest.mark.parametrize(
    ("module_name", "expected_call"),
    [
        ("takeoff_client", ("takeoff", (0.5,), {"timeout_sec": 60.0})),
        (
            "goto_client",
            (
                "goto",
                (1.0, 0.0, 0.5, 0.0),
                {"speed_m_s": 1.0, "tolerance_m": 0.5, "timeout_sec": 30.0},
            ),
        ),
        ("land_client", ("land", (), {"timeout_sec": 0.0})),
        ("get_state_client", ("get_state", (), {})),
    ],
)
def test_command_defaults_and_argument_order_match_rpc_operations(
    module_name: str, expected_call: tuple
) -> None:
    module = importlib.import_module(module_name)
    client = CommandClient()
    captured_args = []

    def fake_make_client(args):
        captured_args.append(args)
        return client

    with patch.object(module, "make_client", side_effect=fake_make_client), patch.object(
        sys, "argv", [f"{module_name}.py"]
    ):
        assert module.main() == 0

    assert client.calls == [expected_call]
    args = captured_args[0]
    assert args.connection == mavlink_drone_client.DEFAULT_CONNECTION
    assert args.drone_name == "Drone"
    assert args.autopilot == "auto"
