# Urban Mobility recipes

This directory contains the configuration and composition contract for the
PLATEAU Urban Car Fleet checkpoint.

## City-independent managed Recipe

`recipes/experiments/urban-mobility-rc.yaml` is the managed dependency and
Foundation contract for the integrated Drone + multi-Car demo. It does not
select a particular city. The later composition config supplies a City World
Receipt and a matching route/scenario; Shizuoka is the first regression input.

Use `tools/urban_mobility.py` as the standard lifecycle entrypoint. Its
`plan`, `doctor`, and `configure` commands delegate to the Business Pack
Recipe engine with `urban-mobility-rc.yaml`; the lower-level Car and Drone
tools remain regression and composition helpers.

```bash
python tools/urban_mobility.py plan
python tools/urban_mobility.py doctor
python tools/urban_mobility.py configure
```

After configuration, use the same entrypoint for runtime lifecycle operations:

```bash
python tools/urban_mobility.py start
python tools/urban_mobility.py status
python tools/urban_mobility.py open-viewer
python tools/urban_mobility.py stop
```

On Windows, enter the Business Pack workspace first with
`python tools\\workspace.py enter` from the Business Pack repository, then
return to this repository and run the commands above. Do not hard-code
`work/foundation/install/python/bin/python3`; the Business Pack workspace
contract resolves `Scripts/python.exe` on Windows and the POSIX layout on
macOS/Linux.

See
[`docs/operation-managed-recipe.md`](../docs/operation-managed-recipe.md)
for the Windows-first operating procedure.

## Recipe-selected one Drone City World

`tools/drone_one.py` materializes the first Drone-side checkpoint from
`recipes/experiments/urban-drone-one.yaml`. The Urban recipe selects the City
World Receipt, Fleet experiment, mission, launch area, initial altitude,
control mode, and Viewer defaults. Its paths are resolved relative to the
recipe file. It reuses the Business Pack single-host Fleet builder through an
explicit Urban-owned workspace and the City/MuJoCo composer from
`hakoniwa-drone-show`, but replaces the Show runner with the Urban-owned direct
Fleet RPC mission in `apps/drone/city_fleet_mission.py`.

```bash
python3 tools/drone_one.py configure \
  --recipe recipes/experiments/urban-drone-one.yaml
python3 tools/drone_one.py doctor
python3 tools/drone_one.py start
python3 tools/drone_one.py status
```

To select another City World or tune the checkpoint, copy
`urban-drone-one.yaml` and change its `city_world.receipt`, `initial_altitude_m`,
`launch_area`, `control.mode`, mission path, or Viewer defaults. Pass that file
only to configure; its resolved paths and SHA-256 are saved in the generated
workspace and reused by `doctor`, `start`, `status`, `open-viewer`, and `stop`.

```bash
python3 tools/drone_one.py configure --recipe recipes/experiments/my-drone.yaml
```

Add `--mujoco-viewer` to `start` to display Drone Core's native MuJoCo
window while the checkpoint runs:

```bash
python3 tools/drone_one.py start --mujoco-viewer
```

Use `c` for follow/free camera, `v` to reset the camera orientation, and `1`
to follow the single Drone. The native window exits with the mission Launcher.

The generated Fleet position starts Drone-1 at local altitude 7 m. Before
`SetReady`, the mission monitors the raw position and velocity PDUs and waits
for the unpowered vehicle to settle on the PLATEAU DEM. The City composer
removes the Drone template's flat ground plane, so no fallback floor is used.
The 2.5 m move runs at 0.25 m/s, with short holds after settle, takeoff, move,
and landing so each phase can be inspected in the native viewer.

The checked-in mission settings are in
`config/drone/city-one-mission.json`. `move_offset_m` is interpreted from the
actual post-takeoff pose and is limited to the launch area's verified 3 m
safety margin. The current `[2.5, 0.0]` mission takes off 2 m above the resolved
surface, moves 2.5 m on the first RPC horizontal axis, and lands.

This Recipe owns the Business Pack `work/recipes/urban-drone-one` workspace.
It no longer writes into the `drone-fleet-single-host` workspace. The shared
Foundation Core runtime remains exclusive, so stop any active Recipe before
starting another one.

## Prerequisites

- The Business Pack Foundation runtime is installed under
  `../hakoniwa-business-pack/work/foundation`.
- A City World job has produced `city-world-receipt.json`, its MJCF, and GLB.
- Every selected Ackermann type is generated in `hakoniwa-mbody-registry`.
- The Urban Car application is built in this repository and uses
  `hakoniwa-robot-runtime` with the `hakoniwa-mujoco-robots` physics backend.
- A PS5 DualSense is available when interactive control is required.

Run all commands from the `hakoniwa-urban-mobility` repository root.

## Select a city

[`multi-car-viewer.yaml`](multi-car-viewer.yaml) is both the Car Fleet
composition recipe and its configure input. The City World receipt `path` is
the only city-selection setting:

```yaml
inputs:
  business_pack_city_receipt:
    path: ../hakoniwa-business-pack/work/remote-operation/city-world-worker/jobs/hokkaido-01100-lat43.062-lon141.355/build/world/city-world-receipt.json
```

The tool reads the city origin, extent, coordinate systems, MJCF path, and GLB
path from that receipt. Do not duplicate them in this configuration.

## Define vehicle types and route-generated instances

The checked-in demo derives every vehicle's initial pose from the route scenario:

```yaml
inputs:
  ackermann_vehicles:
    types:
      - type: golf_cart
        mjcf: ../hakoniwa-mbody-registry/bodies/generic_ackermann_golf_cart/generated/model.minimal_world.xml
        contract: ../hakoniwa-mbody-registry/bodies/generic_ackermann_golf_cart/config/ackermann-forge.yaml
        view_model: ../hakoniwa-mbody-registry/bodies/generic_ackermann_golf_cart/generated/view-model.json
    vehicles:
      generated_from_route:
        scenario: recipes/scenarios/hotel-convoy-loop.yaml
        type: golf_cart
        control_mode: external_python
        up_m: 4.6
```

- The route scenario generates names, route offsets, ENU positions, and yaw.
- Position and route spacing use metres.
- Generated yaw is positive counter-clockwise from East toward North.
- Roll and pitch are fixed to zero and are not configuration inputs.
- `up_m` is the selected model's top-level body origin, so include terrain
  height and that type's wheel clearance.

Type names and generated vehicle names must be unique. The vehicle `type` references
one catalog entry. `mjcf` is the rigid-body source; `contract` is the matching
mbody-registry Ackermann forge contract supplying freejoint, joint, actuator,
and geometry bindings; `view_model` is the generated browser presentation
contract and its per-part GLBs. This keeps model-specific physics and visual
hierarchy metadata out of Urban and Three.js code.

Vehicle names become their PDU robot names. During configuration each pose is
converted to the City World MJCF convention
`X=North, Y=-East, Z=Up`; yaw is converted to radians. Both representations
are recorded in `compose-receipt.json`.

## Configure and run

For one PS5 RC-controlled Golf Cart with browser visualization, use the compact
experiment recipe. `start` launches the PS5 sender with the Car runtime and
browser services; the native MuJoCo Viewer is not needed:

```bash
python3 tools/multi_car.py doctor \
  --config recipes/experiments/urban-car-one.yaml
python3 tools/multi_car.py configure \
  --config recipes/experiments/urban-car-one.yaml
python3 tools/multi_car.py check-ps5 \
  --config recipes/experiments/urban-car-one.yaml
python3 tools/multi_car.py start \
  --config recipes/experiments/urban-car-one.yaml

python3 tools/multi_car.py open-viewer --colliders \
  --config recipes/experiments/urban-car-one.yaml
```

Change `inputs.business_pack_city_receipt.path` to select a different generated
City World. The route scenario remains an explicit city-local input because its
ENU waypoints and vehicle start pose must match the selected roads.

```bash
python3 tools/multi_car.py doctor \
  --config recipes/multi-car-viewer.yaml

python3 tools/multi_car.py configure \
  --config recipes/multi-car-viewer.yaml

python3 tools/multi_car.py start \
  --config recipes/multi-car-viewer.yaml
```

`configure` composes the City World and all selected vehicle instances into one MJCF,
namespaces their bodies, joints, geoms, and actuators, compiles and validates
a MuJoCo-version-bound MJB, and generates one fleet Runtime and Launcher. With
`browser_visualization.enabled: true`, it also generates the WebBridge and
Three.js configuration under
`../hakoniwa-business-pack/work/recipes/urban-multi-car-viewer/config/`. It does not download or
regenerate the city or vehicle GLBs.

### Browser visualization

The generated Launcher starts one WebBridge and one workspace-root HTTP
server. `configure` prints the complete browser URL; with the checked-in ports
it is:

```text
http://127.0.0.1:8000/hakoniwa-map-viewer/src/client/index.html?threejsRoot=%2Fhakoniwa-threejs-drone&viewerConfigPath=%2Fhakoniwa-business-pack%2Fwork%2Frecipes%2Furban-multi-car-viewer%2Fconfig%2Fthreejs%2Fviewer-config.json&layout=three-main
```

The browser loads the City World GLB and each vehicle type's standard
`hako_viewer_model`. It consumes the existing variable-length state channels:

```text
UrbanFleet/vehicle_states  sensor_msgs/MultiDOFJointState
UrbanFleet/joint_states    sensor_msgs/JointState
```

The WebBridge is read-only for these channels. Vehicle commands continue to
use each vehicle's shared-memory `ackermann_cmd`; no control or physics logic
is moved into JavaScript. Current JointState publication covers the
actuator-backed front steering and driven rear-wheel joints. Passive front
wheel spin is not yet published by the generic Runtime and therefore is not
animated independently.

`inputs.ackermann_runtime.realtime_sync_cycle_msec` controls wall-clock pacing.
The default `2` ms matches the current Ackermann MJCF timestep, so simulation time
tracks real time. Set it to `0` only for explicit faster-than-real-time runs.

### Select the command source

The Recipe selects exactly one command owner per vehicle:

```yaml
inputs:
  ackermann_vehicles:
    vehicles:
      - name: Car-1
        type: golf_cart
        control_mode: ps5
      - name: Car-2
        type: golf_cart
        control_mode: external_python
```

Run `configure` again after changing a mode. `external_python` leaves that
vehicle's `<name>/ackermann_cmd` to a separate process. `ps5` adds one
Urban-owned PS5 sender for that vehicle to the generated Launcher. Publishing
from two senders to the same vehicle is unsupported because the newest command
would win.

### External Python control

With `control_mode: external_python`, start the runtime and publish independent
fixed-duration commands from other terminals:

```bash
FOUNDATION_PYTHON=../hakoniwa-business-pack/work/foundation/install/python/bin/python3

$FOUNDATION_PYTHON apps/car/ackermann_command.py \
  --robot Car-1 drive \
  --speed 1.5 \
  --steering-deg 0 \
  --duration 3

$FOUNDATION_PYTHON apps/car/ackermann_command.py \
  --robot Car-2 drive \
  --speed -1.0 \
  --steering-deg -20 \
  --duration 4

$FOUNDATION_PYTHON apps/car/ackermann_command.py --robot Car-1 stop
```

`drive` republishes at 50 Hz so the Runtime command timeout does not expire,
then sends repeated zero commands before exiting. Positive steering turns
left; speed is metres per second and steering input is degrees.

The same client can be used by an external Python program:

```python
from pathlib import Path
import sys

sys.path.insert(0, str(Path("apps/car").resolve()))
from urban_car import AckermannClient

with AckermannClient(
    pdu_def="../hakoniwa-business-pack/work/recipes/urban-multi-car-viewer/config/car/urban-car-pdudef.json",
    robot="Car-1",
) as car:
    car.drive(speed_m_s=1.0, steering_rad=0.2, duration_sec=3.0)
```

The Robot Runtime retains the last valid input only until `timeout_sec`, so a
long-running controller must call `send()` continuously. Context-manager exit
publishes stop commands and closes the external PDU service.

Vehicle state is published once per Runtime tick through the shared
`UrbanFleet/vehicle_states` `sensor_msgs/MultiDOFJointState` channel. Its
variable-length arrays follow Recipe vehicle order (`Car-1` through `Car-10`
in the checked-in configuration). `UrbanFleet/joint_states` similarly contains
the namespaced joints for every configured vehicle.

### Timed multi-vehicle scenarios

[`scenarios/two-car-convoy.yaml`](scenarios/two-car-convoy.yaml) defines a
staggered command timeline independently for each vehicle:

```yaml
vehicles:
  - name: Car-1
    commands:
      - at_sec: 0.0
        duration_sec: 4.0
        speed_m_s: 1.0
        steering_deg: 0.0
  - name: Car-2
    commands:
      - at_sec: 0.8
        duration_sec: 4.0
        speed_m_s: 1.0
        steering_deg: 0.0
```

Validate without connecting to Hakoniwa:

```bash
FOUNDATION_PYTHON=../hakoniwa-business-pack/work/foundation/install/python/bin/python3
$FOUNDATION_PYTHON apps/car/scenario_executor.py \
  recipes/scenarios/two-car-convoy.yaml --dry-run
```

After starting the Recipe, execute the convoy from another terminal:

```bash
$FOUNDATION_PYTHON apps/car/scenario_executor.py \
  recipes/scenarios/two-car-convoy.yaml
```

The timeline uses Hakoniwa simulation time, so pausing simulation also pauses
timeline progress. One shared PDU service continuously republishes every
vehicle command at `rate_hz`; gaps send zero commands. Commands for the same
vehicle must not overlap. Normal completion, validation failure, and Ctrl+C
all end with repeated stop commands for the complete scenario fleet.

### Closed-loop hotel convoy

[`scenarios/hotel-convoy-loop.yaml`](scenarios/hotel-convoy-loop.yaml) uses
local ENU waypoints and the published `UrbanFleet/vehicle_states` feedback.
The first vehicle follows a virtual point on the closed route and each
following vehicle receives a generated negative `route_offset_m`. The checked-in
10-Car fleet uses 4.5 m spacing, including the five-second hotel
stop. The executor converts the MuJoCo state frame back to ENU, projects each
car onto the route, and calculates steering with a pure-pursuit controller.

```yaml
vehicles:
  generate:
    count: 10
    name_prefix: Car-
    route_spacing_m: 4.5
```

Changing `count` is sufficient to select a smaller fleet. `configure` samples
the same route offsets to generate matching MJCF spawn poses. Fleet state PDU
buffers grow automatically with the configured vehicle count.

```bash
$FOUNDATION_PYTHON apps/car/scenario_executor.py \
  recipes/scenarios/hotel-convoy-loop.yaml --dry-run

$FOUNDATION_PYTHON apps/car/scenario_executor.py \
  recipes/scenarios/hotel-convoy-loop.yaml
```

`loop_count: forever` repeats until Ctrl-C. A positive integer runs that many
laps. The route belongs to the selected City World receipt; when the receipt
changes, define and validate a new ENU waypoint set rather than reusing these
coordinates blindly.

### PS5 control

When at least one vehicle uses `control_mode: ps5`, confirm the controller
before startup:

```bash
python3 tools/multi_car.py check-ps5 \
  --config recipes/multi-car-viewer.yaml
```

The Urban-owned PS5 sender publishes `ackermann_msgs/AckermannDrive`:

- Left stick: steering
- Right stick vertical: throttle and reverse

### PS5 regression checklist

Use this checklist when changing the Urban AckermannDrive path. The input
mapping and observed vehicle direction must remain stable.

1. `check-ps5` reports a selected `DualSense` or `Wireless Controller`, with
   no Hakoniwa runtime active.
2. Start the recipe and leave both sticks neutral. The car remains stopped and
   steering returns to centre.
3. Push the right stick forward. The car moves forward without steering.
4. Pull the right stick backward. The car reverses without steering.
5. While moving slowly forward, move the left stick left and then right. The
   front wheels and vehicle yaw follow the same direction.
6. Release both sticks. Drive velocity returns to zero and steering returns to
   centre without continued motion commands.
7. Stop the recipe normally; `status` reports `TERMINATED` and no controller
   sender remains active.

Retain the generated Launcher logs under
`../hakoniwa-business-pack/work/recipes/urban-multi-car-viewer/logs/` when
investigating a regression.

Inspect or stop the background session from another terminal:

```bash
python3 tools/multi_car.py status \
  --config recipes/multi-car-viewer.yaml

python3 tools/multi_car.py stop \
  --config recipes/multi-car-viewer.yaml
```

For Viewer-only model inspection after configuration:

```bash
python3 tools/multi_car.py view \
  --config recipes/multi-car-viewer.yaml
```

## Generated files

The checked-in configuration writes generated artifacts beneath
`../hakoniwa-business-pack/work/recipes/urban-multi-car-viewer/`:

- `config/car/urban-cars-city.xml`: composed canonical multi-Car MJCF
- `config/car/urban-cars-city.mjb`: validated runtime model
- `validation/mujoco-materialization.json`: MuJoCo version and MJB provenance
- `validation/compose-receipt.json`: city source, hashes, ENU spawn, and MJCF conversion
- `config/car/urban-car-asset-manifest.json`: multi-Car Ackermann runtime manifest
- `config/car/urban-car-pdudef.json`: per-Car commands plus shared fleet-state PDU definitions
- `config/launcher.json`: Foundation Launcher configuration
- `runtime/launcher-session.json`: background-session state

These files are generated and are not committed.

## Changing cities

1. Change only `business_pack_city_receipt.path` to select another City World.
2. Update every vehicle's `spawn_pose_enu` to a driveable point in that city's local frame.
3. Run `doctor` and `configure` again.
4. Use the native Viewer to verify terrain clearance before driving.

The broader Drone integration stages are defined in
[`urban-mobility.yaml`](urban-mobility.yaml).
