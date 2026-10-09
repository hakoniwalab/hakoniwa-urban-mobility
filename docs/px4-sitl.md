# EAMS Hexa in PX4 SITL

Fly the EAMS nominal 9 kg hexa with the PX4 autopilot in SITL, and operate it
over MAVLink with the same takeoff, goto, land and get_state operations as the
Drone RPC API.

| Part | Where |
|---|---|
| Managed Recipe (dependencies) | `recipes/usecases/urban-drone-px4.yaml` (urban.manifest.yaml `drone-px4`) |
| Vehicle and tuned PX4 parameters | `config/drone/hexa-px4` |
| Build, prepare and start | `tools/px4_sitl.py` (build: `tools/px4_sitl_build.bash`) |
| MAVLink client and commands | `apps/drone/mavlink` |
| In Urban Studio (a City World, a Flight) | asset `eams-hexa-px4`, `tools/drone_px4.py`, `apps/drone/drone_link.py` (section 6) |

```text
PX4 SITL  <-- TCP 4560 (HIL sensors / actuators) -->  Drone Core aircraft service (MuJoCo, 3 ms)
   |
   +-- UDP 14540 --> apps/drone/mavlink (takeoff, goto, land, get_state)
   +-- UDP 14550 --> QGroundControl (optional)
```

The aircraft service is the public `hakoniwa-drone-core` v4.1.1 binary
(`mac-main_hako_aircraft_service_px4`). Run the commands below inside the
Hakoniwa Business Pack Workspace, where `python` is the Foundation Python.

## 1. Dependencies

The managed Recipe `recipes/usecases/urban-drone-px4.yaml` declares what the
drone route needs plus PX4-Autopilot (pinned to `a1726d3`, the revision verified
with the Hakoniwa SITL lockstep) and `pymavlink`
(`recipes/requirements/urban-drone-px4.txt`). As for every Urban route, the
Business Pack Recipe lifecycle checks and materializes them: `plan` and
`doctor` report what is missing, `configure` clones PX4-Autopilot at that
revision (with its submodules) and installs `pymavlink` into the Foundation
Python. In Urban Studio this happens when a Composition with `eams-hexa-px4` is
configured (section 6); by hand:

```bash
python ../hakoniwa-business-pack/tools/recipe.py doctor --recipe recipes/usecases/urban-drone-px4.yaml
python ../hakoniwa-business-pack/tools/recipe.py configure --recipe recipes/usecases/urban-drone-px4.yaml
```

Nothing has to be set up by hand. Like the other repositories, PX4-Autopilot
lives next to this one (`../PX4-Autopilot`); configure clones it there with its
submodules when it is missing (the first time takes a while: PX4 is large), and
`tools/drone_px4.py` and `tools/px4_sitl.py` find it there.

To reuse a PX4-Autopilot checkout that is somewhere else (at the same revision),
point the Recipe's override variable `PX4_AUTOPILOT_ROOT` at it; this is only a
shortcut for a machine that already has one.

The PX4 build needs `cmake`, `ninja`, a C++ compiler and `python3`
(macOS: Xcode and `brew install cmake ninja`). PX4's `Tools/setup/macos.sh` is not
needed.

## 2. Build PX4 SITL

```bash
python tools/px4_sitl.py build
```

The build goes to `build/px4-sitl/` (PX4's Python packages to
`build/px4-sitl/venv`, the compiler cache next to it). The PX4-Autopilot checkout
is only read: PX4's CMake writes two VS Code files into `.vscode/` on every
configure, and the script puts them back. It compares the checkout's git status
(ignored files included) before and after, and stops if anything changed. The
first build takes a few minutes.

## 3. Prepare the runtime

```bash
python tools/px4_sitl.py prepare
```

`build/px4-sitl/runtime/` gets PX4's startup data with the EAMS airframe
(`900002_hakoniwa_eams`), an empty PX4 rootfs (parameters and logs go there),
the vehicle config with absolute paths, and an executable copy of the aircraft
service. Prepare again after changing `config/drone/hexa-px4`.

## 4. Start

```bash
python tools/px4_sitl.py start
```

The Hakoniwa launcher starts PX4, then the aircraft service (with the MuJoCo
viewer; `--no-viewer` to run without it), and starts the simulation. Wait for
PX4's `Ready for takeoff!`. `Ctrl-C` stops everything.

## 5. Fly

In another terminal, with a Python that has `pymavlink`:

```bash
python apps/drone/mavlink/commands/takeoff_client.py 5
python apps/drone/mavlink/commands/goto_client.py 10 0 5 0 --speed 3
python apps/drone/mavlink/commands/goto_client.py 0 0 5 180 --speed 3
python apps/drone/mavlink/commands/get_state_client.py
python apps/drone/mavlink/commands/land_client.py
```

Positions are in the RPC API's ROS frame (x forward, y left, z up, yaw
counter-clockwise in degrees). Each command waits for its result and exits with 1
when it fails. See `apps/drone/mavlink/README.md`.

## 6. In Urban Studio: a City World and a Flight

The asset `eams-hexa-px4` is the EAMS hexa flown by PX4. In Compose, pick it for
the Drone of a City World Composition (alone, or with Cars), give it the
`schedule` control and a Flight made in the Flight tab, then run configure and
start as for `eams-hexa`. Configure gets PX4-Autopilot and builds PX4 the first
time.

What changes from `eams-hexa` (`tools/drone_one.py` with profile
`eams-nominal-9kg-px4`, `tools/drone_px4.py`):

| Part | `eams-hexa` | `eams-hexa-px4` |
|---|---|---|
| Simulator | Drone Core drone service | PX4 SITL + the Drone Core aircraft service, under the same Launcher asset name (`drone-service-1`), so readiness, the real-time pacer, the Viewer and the controls stay as they are |
| Vehicle in the City model | `config/drone/hexa`, 1 ms | `config/drone/hexa-px4`, 3 ms MuJoCo step and HIL sensor period |
| Magnetic field | as in the config | looked up in PX4's World Magnetic Model at the City origin (`tools/px4_magnetic.py`): the PX4 EKF checks the field against it |
| PX4 home | – | the City origin |
| Landing contact | skids and a landing box | skids only: on the City mesh the box made the IMU chatter about 4 m/s² at rest, and PX4 refused to arm ("High Accelerometer Bias") |
| `schedule` control | `RpcDroneLink`: commands over Drone Core RPC, state from the Drone's PDUs | `MavlinkDroneLink` (`drone_schedule.py --mavlink`): commands and state over MAVLink, sent relative to the spawn (PX4's local origin); no RPC client |
| Wind and rotor faults (Flight zones) | the `disturb` PDU | the same `disturb` PDU |
| Controls offered | `rc`, `api`, `schedule` | `schedule` |

The PX4 runtime (startup data with the EAMS airframe, rootfs, aircraft service)
is under `<recipe workspace>/runtime/px4`; PX4's output goes to
`<recipe workspace>/logs/px4-sitl.out`.

`tools/urban_simulation.py` picks the managed Recipe variant that declares
PX4-Autopilot and `pymavlink` for a Composition whose Drone is `eams-hexa-px4`:
`recipes/usecases/urban-drone-px4.yaml` for the Drone alone (instead of
`urban-drone-rc.yaml`), `recipes/experiments/urban-mobility-px4.yaml` with Cars
(instead of `urban-mobility-rc.yaml`; urban.manifest.yaml `drone-px4`,
`integrated-px4`). Configure materializes PX4-Autopilot and installs `pymavlink`
first.

With Cars, `tools/urban_composer.py` keeps PX4 SITL in the merged Launcher, gives
the aircraft service the unified PDU definition, and starts the Car plant after
it (the Drone side owns the Conductor; the Car plant joins with
`--external-conductor`), then `hako-cmd start`.

### One interface for the flight (`apps/drone/drone_link.py`)

The `schedule` control flies either Drone through one interface: commands
(set_ready, takeoff, goto, land), state (position, yaw, speed, contact count) and
disturbance (wind, rotor faults). `RpcDroneLink` is Drone Core's; `MavlinkDroneLink`
is PX4's. Both read and write the Drone's PDUs through hakopy with the channel ids
of the simulator's PDU definition (`--pdu-def`, the control's `${runtime.pdu_def}`)
and register nothing in shared memory.

The MAVLink link must not create a Drone Core RPC client: the PX4 aircraft service
offers no RPC services, so the client would register the service channels itself,
after the simulation started. The PDU data is sized at the start from what the
assets registered, and every asset that loads it later (the Car scenario executor)
then fails with `shared memory data_size mismatch`.

The PX4 aircraft service does not write the `status` PDU, so the contact count is
unknown (None) with PX4. `tools/drone_one.py configure` then builds PX4 SITL into
`build/px4-sitl` when it is not built yet (the first build takes a few minutes).

## Troubleshooting

| Symptom | Cause, action |
|---|---|
| The launcher waits for asset `drone` and times out | Seen once with the same runtime; starting again worked. Make sure no PX4 or aircraft service from an earlier run is left (`pgrep -fl 'bin/px4\|aircraft_service'`) |
| A command stops with `no heartbeat on udpin:127.0.0.1:14540` although PX4 printed `Ready for takeoff!` | Seen once; after stopping and starting again, MAVLink came on 14540 and 14550. Check with `lsof -nP -iUDP:14540` that no other process holds the port |
| PX4 stays at `Preflight Fail: ekf2 missing data` | The aircraft service is not sending sensors yet: wait, or check its output |
| `arm: rejected` | PX4 refuses to arm in some modes; the client switches to Hold first. A preflight failure (see PX4's output) also rejects arming |
| `takeoff failed: arm: rejected` | PX4's preflight checks have not passed: right after start the heading estimate takes a few seconds (the client retries arming until the takeoff timeout). Look at PX4's `Preflight Fail` lines |
| Yaw drifts or the heading is wrong | The magnetic field and the position in `drone_config_0.json` must match (`config/drone/hexa-px4/README.md`) |

## Verification

| Date | Platform | Result |
|---|---|---|
| 2026-10-09 | macOS arm64, PX4 `a1726d3`, Drone Core v4.1.1 aircraft service | build, prepare, start; takeoff 5 m, three gotos (yaw 0°, 90°, 180°), land: all ok |
| 2026-10-09 | same, Hokkaido City World with two Cars (`schedule` with a wind zone) | the Drone flew its Flight with Cars driving; the wind zone was applied through the `disturb` PDU and PX4 held its route; no shared memory mismatch |
| 2026-10-09 | same, Shizuoka City World, Composition with `eams-hexa-px4` and a Flight (`tools/urban_simulation.py` configure/start as Studio runs them) | from a roof: takeoff 15 m, three waypoints, back, land on the roof; the flown track within 0.1 m of the waypoints; the Viewer shows the Drone |
