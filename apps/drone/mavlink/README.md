# MAVLink drone client

Fly a PX4 (later also ArduPilot) vehicle over MAVLink with the same operations
as the Drone RPC API (`hakoniwa-drone-core` `drone_api/external_rpc`):
takeoff, goto, land and get_state. A PX4 SITL vehicle can be driven like a
Drone Core vehicle.

It depends on `pymavlink` only (no Hakoniwa packages), so it works with any
MAVLink vehicle, simulated or real.

## Commands

Run them while PX4 SITL is running (`docs/px4-sitl.md`), with a Python that has
`pymavlink` (the managed Recipe `recipes/usecases/urban-drone-px4.yaml` installs it into the Foundation Python; `recipes/requirements/urban-drone-px4.txt`).

| Command | Operation | RPC counterpart |
|---|---|---|
| `commands/takeoff_client.py [alt_m]` | arm and take off; waits for the altitude | `external_rpc/commands/takeoff_client.py` |
| `commands/goto_client.py [x y z yaw]` | go to a position; waits until within tolerance. `--speed`, `--tolerance`, `--timeout-sec` | `external_rpc/commands/goto_client.py` |
| `commands/land_client.py` | land; waits until on the ground and disarmed | `external_rpc/commands/land_client.py` |
| `commands/get_state_client.py` | position, attitude, mode, armed, landed state | `external_rpc/commands/get_state_client.py` |

```bash
python apps/drone/mavlink/commands/takeoff_client.py 5
python apps/drone/mavlink/commands/goto_client.py 10 0 5 0 --speed 3
python apps/drone/mavlink/commands/land_client.py
```

Common options:

| Option | Default | Meaning |
|---|---|---|
| `--connection` | `udpin:127.0.0.1:14540` | PX4 SITL's API port; 14550 stays free for QGroundControl |
| `--autopilot` | `auto` | `px4` or `ardupilot` (taken from HEARTBEAT when `auto`) |
| `--drone` | `Drone` | the name shown in the output |

## Frame

The ROS frame of the RPC API: x forward (north), y left, z up, yaw counter-clockwise
from x in degrees, relative to the autopilot's local origin (the takeoff point).
The autopilot uses NED; the client converts (x, −y, −z, −yaw).

## Differences from the RPC API

Names, arguments, frame and responses are the same; these differ:

| Item | RPC API | MAVLink client |
|---|---|---|
| `set_ready` | sends `DroneSetReady` to the Drone service | sends nothing; waits for the autopilot's position (EKF) |
| `takeoff` | readiness is checked by the command (`get_state`, then `set_ready` if needed) | every `takeoff` waits like `set_ready` |
| `land` with `timeout_sec=0` | the service call's default timeout | waits up to 120 s |
| `get_state` `ok` | the service response | a HEARTBEAT within 3 s; `is_ready` also needs positions within 1 s |
| command exit code | 0 even when the response is NG | 1 when `ok=False`, so scripts can detect failures |

## Structure

| File | Content |
|---|---|
| `mavlink_drone_client.py` | `MavlinkDroneClient`: the operations, frames and responses |
| `autopilot.py` | `VehicleLink` (the connection and the vehicle state) and the autopilot backends |
| `commands/` | the command-line clients above |
| `tests/` | unit tests with a fake link (`python -m pytest apps/drone/mavlink/tests`) |

### PX4

| Operation | MAVLink |
|---|---|
| takeoff | when disarmed: switch to Hold (AUTO.LOITER; PX4 refuses to arm in AUTO.LAND, the mode left after a landing), then arm, retrying every second until the takeoff timeout while PX4's preflight checks fail (right after start the heading estimate takes a few seconds). `MAV_CMD_NAV_TAKEOFF` with param7 = AMSL altitude (PX4 enters takeoff from any mode) |
| goto | `MAV_CMD_DO_REPOSITION` to latitude, longitude and AMSL altitude. PX4 reads param4 (yaw) in radians, not in degrees as the MAVLink spec says |
| land | `MAV_CMD_NAV_LAND` |

These are PX4 Auto-mode commands: the vehicle holds the target after the command
exits (no offboard setpoint stream is needed).

### ArduPilot (planned)

`ArduPilotBackend` is a placeholder (its operations raise `NotImplementedError`).
The plan: GUIDED mode, `MAV_CMD_NAV_TAKEOFF` (relative altitude),
`SET_POSITION_TARGET_GLOBAL_INT` for goto (held in GUIDED), LAND mode. The client
and the commands stay the same.

## Verification

| Date | Vehicle | Sequence | Result |
|---|---|---|---|
| 2026-10-09 | EAMS 9 kg hexa, PX4 SITL with the Drone Core v4.1.1 aircraft service | takeoff 5 m → goto (10, 0, 5, 0°) → (10, −10, 5, 90°) → (0, 0, 5, 180°) → land | every response ok=True |
