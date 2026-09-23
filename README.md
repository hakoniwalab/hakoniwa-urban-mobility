# Hakoniwa Urban Mobility

Hakoniwa Urban Mobility is an integration suite for coordinated drones and
ground vehicles in a shared [PLATEAU](https://www.mlit.go.jp/plateau/)
city environment. It composes existing Hakoniwa vehicle simulators into a
repeatable urban-mobility scenario; it does not replace their vehicle models,
flight controllers, or physics engines.

The first demonstration target is two Virtual Drone Show drones and multiple
Ackermann-steered vehicles moving through the same city area, with shared
browser visualization and safety-aware interaction.

> **Status:** the City World + two independently controlled typed Ackermann vehicles
> is runnable. The full two-Car + two-Drone scenario, cross-world mirrors, and
> contact handling remain under development.

The step-by-step migration to the managed Business Pack Recipe/Foundation
contract is tracked in
[`docs/foundation-task.md`](docs/foundation-task.md).
The target ownership and lifecycle contract is defined in
[`docs/foundation-contract.md`](docs/foundation-contract.md).

## Standard managed Recipe entrypoint

`tools/urban_mobility.py` is the user-facing lifecycle entrypoint. The
simulation topology is selected with `--recipe`; the Python entrypoint does
not own a fixed Urban use case.

The first managed use-case Recipe is one RC-controlled Golf Cart:

```bash
python tools/urban_mobility.py plan \
  --recipe recipes/usecases/urban-car-rc.yaml
python tools/urban_mobility.py doctor \
  --recipe recipes/usecases/urban-car-rc.yaml
python tools/urban_mobility.py configure \
  --recipe recipes/usecases/urban-car-rc.yaml \
  --city-receipt /path/to/city-world-receipt.json
```

The Recipe selects the Car/RC topology and a tracked configuration template.
`--city-receipt` fills the City World input at configure time. The receipt is
the artifact produced by the City World/PLATEAU workflow; Urban Mobility does
not redownload or regenerate the city.

After configuration, use the same Recipe identity for runtime operations:

```bash
python tools/urban_mobility.py check-rc --recipe recipes/usecases/urban-car-rc.yaml
python tools/urban_mobility.py start --recipe recipes/usecases/urban-car-rc.yaml
python tools/urban_mobility.py status --recipe recipes/usecases/urban-car-rc.yaml
python tools/urban_mobility.py open-viewer --recipe recipes/usecases/urban-car-rc.yaml
python tools/urban_mobility.py stop --recipe recipes/usecases/urban-car-rc.yaml
```

The existing integrated Drone + multi-Car Recipe remains available as a
separate use case under `recipes/experiments/urban-mobility-rc.yaml`.
This separation lets future Recipes describe shared-world Car/Drone simulation,
Drone Show, or other topologies without growing `urban_mobility.py` into a
topology-specific script.

On Windows, enter the Business Pack workspace first:

```powershell
PS C:\project\urban\hakoniwa-business-pack> python tools\workspace.py enter
(hako) PS C:\project\urban\hakoniwa-business-pack> cd ..\hakoniwa-urban-mobility
```

The lower-level `multi_car.py`, `drone_one.py`, and legacy RC tools remain
component regression/composition helpers rather than the primary setup path.
See [`docs/operation-managed-recipe.md`](docs/operation-managed-recipe.md).


## Recipe-selected one-Drone checkpoint

Prepare the pinned public native distribution first. This selects `mac.zip`,
`lnx.zip`, or `win.zip` for the host, verifies the v4.1.1 SHA-256, and installs
the MuJoCo 3.13.0 runtime declared by Drone Core:

```bash
python3 tools/drone_one.py prepare-native
```

The first Drone milestone runs one `hakoniwa-drone-core` Fleet vehicle in a
City World selected by `recipes/experiments/urban-drone-one.yaml`. The checked-in
recipe selects the compact Hokkaido/Sapporo world used by the Car scenario. It resolves
a level launch area from the City Receipt and executes `SetReady -> TakeOff ->
GetState -> GoTo -> Land` through `FleetRpcController`. The GoTo target is 2.5 m
from the actual post-takeoff pose, so the mission does not duplicate the
Drone/MuJoCo coordinate conversion.

```bash
python3 tools/drone_one.py configure \
  --recipe recipes/experiments/urban-drone-one.yaml
python3 tools/drone_one.py doctor
python3 tools/drone_one.py start
python3 tools/drone_one.py status
```

The checked-in recipe selects `control.mode: ps4-rc`. Change it to `fleet-rpc`
for the automatic mission, then run configure again. `--rc` remains only as a
compatibility override for older commands.

`python3 tools/drone_one.py open-viewer` opens the 3D-first layout with the
map at lower left. Add `--colliders` to overlay the MJCF Collider GLB as green
wireframes.

Press Cross (button 0) once to enable the latched RadioControl mode. The left
stick controls throttle/yaw and the right stick controls pitch/roll.
The complete operating and model-regeneration procedure is in
[`docs/operation-one-drone-ps4-browser.md`](docs/operation-one-drone-ps4-browser.md).

To open Drone Core's native MuJoCo viewer during the mission, start with:

```bash
python3 tools/drone_one.py start --mujoco-viewer
```

Press `c` to switch follow/free camera, `v` to reset the camera orientation,
and `1` to follow the single Drone. The window closes when the checkpoint
mission finishes and the Launcher terminates.

The Drone starts at local altitude 7 m with its motors unpowered. The mission
waits until velocity and height are stable on the PLATEAU DEM, then runs
`SetReady`, `TakeOff`, `GoTo`, and `Land`. The standalone ground plane from the
Drone template is removed during City composition; the DEM is the only ground.
The checkpoint uses a deliberately slow 2.5 m translation and observation
holds between phases so its motion remains visible in the native viewer.

`start` launches the PLATEAU browser view and the mission together. The
browser uses the Urban-managed EAMS Hexa and reads motor channels 0
through 5. The native service binary remains Drone Core v4.1.1.
The mission result is written to the Business Pack Recipe workspace as
`validation/urban-drone-mission.json`; a successful run contains all four
command phases and the actual takeoff, target, and landing poses.

## Golf Cart + PS4-controlled Drone Core demo

The two-asset demo uses one Launcher and one Conductor. The Drone service owns
the Conductor, while the Car asset joins it with `--external-conductor`. The
Golf Cart waits at its initial position until the operator explicitly starts
its one-lap route. The Urban EAMS Hexa is operated with a PS4 controller
through Drone Core's `drone_api/rc/rc-custom.py`. Only the Car-side MuJoCo
viewer is shown, including the mirrored Drone.

Connect the PS4 controller, then configure and run with the Foundation Python:

```bash
../hakoniwa-business-pack/work/foundation/install/python/bin/python3 \
  tools/drone_car_rc.py configure
../hakoniwa-business-pack/work/foundation/install/python/bin/python3 \
  tools/drone_car_rc.py start
```

When both assets are ready, start the Golf Cart route from another terminal:

```bash
../hakoniwa-business-pack/work/foundation/install/python/bin/python3 \
  tools/drone_car_rc.py car-start
```

`car-start` runs the one-lap scenario in the foreground, so `Ctrl-C` stops the
Golf Cart without terminating the Drone or the simulation.

The physical Drone and Car-side Mirror are generated from the same Urban-owned
Hexa model. Cross-simulator resting contact remains outside the
Impulse-only Mirror contract.

The controller uses mode 2: the left stick controls throttle and yaw, and the
right stick controls pitch and roll. Press the Cross button (button 0) once to
enable the latched RadioControl mode; it is not a dead-man switch.

Inspect the background session with `status` and terminate it with `stop`.
The generated RC controller configuration and Urban Hexa parameter set
are copied to
`../hakoniwa-business-pack/work/recipes/urban-drone-car-rc/config/drone/rc/`.
The tracked Drone Core configuration and controller parameter files are not edited.

Drone Core v4's public service binary opens MuJoCo models through its XML path,
so this adapter uses the generated City XML at runtime while retaining MJB
compile/reload validation as configuration evidence. Its Land RPC also assumes
that the landing datum is local Z=0. On an elevated PLATEAU surface the vehicle
reports `Landed` but the RPC response times out; the checkpoint records success
only after a follow-up `GetState` reports `Landed` below the flight altitude.
This compatibility rule belongs at the Drone adapter boundary and must not leak
into the later mirror contract.

## Why this repository exists

Urban scenarios cross component boundaries:

- a drone fleet is owned by the Virtual Drone Show / Drone Core stack;
- the concrete Urban Car application is owned here and runs on the generic
  Robot Runtime with the shared MuJoCo backend;
- the PLATEAU city is generated by the environment simulator;
- cross-vehicle scenario timing, safety policy, and presentation belong to
  none of those vehicle-specific components.

This repository owns that integration layer together with the concrete Urban
Car application, vehicle-specific configuration, and launch composition. It
does not own the generic Robot Runtime or MuJoCo physics backend.

## Target architecture

Two MuJoCo worlds are intentionally used. Each owns the physics of its real
vehicles and contains the same lightweight static city collider. Dynamic
vehicles owned by the other world are represented as pose-driven mirrors.

```text
                  PLATEAU City World / city_map (common ENU frame)
                                      |
             +------------------------+------------------------+
             |                                                 |
    Drone Fleet MuJoCo world                         Car Fleet MuJoCo world
    - real Drone-1 / Drone-2                         - real Car-1 / Car-2
    - mirrored Car-1 / Car-2                         - mirrored Drone-1 / Drone-2
    - local city collider                             - local city collider
             |                                                 |
             +-------------- actual pose / state -------------+
                                      |
                    Urban mobility scenario runner
                    - timeline and routes
                    - safety policy
                    - Fleet API / car-command adapters
                                      |
                         Map Viewer / Three.js presentation
```

The city is duplicated as a static collider in both worlds by design. A full
PLATEAU mesh is not a physics target: each demo uses a bounded region of
interest and lightweight ground, road, building, curb, and bridge colliders.

## Ownership and boundaries

| Repository | Owns |
| --- | --- |
| [`hakoniwa-drone-show`](https://github.com/hakoniwalab/hakoniwa-drone-show) | Virtual Drone Show Fleet, show plans, Fleet API integration, Drone presentation state |
| [`hakoniwa-drone-core`](https://github.com/toppers/hakoniwa-drone-core) | Public v4.1.1 native Drone physics, flight control, RC client, and runtime configuration |
| [`hakoniwa-robot-runtime`](https://github.com/hakoniwalab/hakoniwa-robot-runtime) | Generic actuator runtime, Ackermann controller, and JointState / MultiDOF state contracts |
| [`hakoniwa-mujoco-robots`](https://github.com/hakoniwalab/hakoniwa-mujoco-robots) | MuJoCo physics and Viewer backend, mirror bodies, and local contact-to-impulse support |
| [`hakoniwa-envsim`](https://github.com/hakoniwalab/hakoniwa-envsim) | PLATEAU City World generation and static collision geometry |
| **this repository** | Urban Car application and concrete configuration, city-scale scenario, shared coordinate contract, safety policy, launch composition, and end-to-end acceptance tests |
| [`hakoniwa-business-pack`](https://github.com/hakoniwalab/hakoniwa-business-pack) | Installable recipes, operational guidance, and composed validation evidence |

Generic controller and physics logic must remain with their owning components.
This repository owns concrete vehicle parameters and command frontends, and
sends high-level commands such as route targets, hold, resume, and emergency
stop; it does not implement a new flight controller or vehicle dynamics model.

## Coordinate and time contract

All cross-component state uses one `city_map` frame:

- local ENU metres, with a single PLATEAU origin per scenario;
- entity ID and timestamp on every state message;
- pose as position plus orientation;
- actual vehicle state, rather than a planned trajectory, drives mirrors;
- one Hakoniwa simulation clock and one explicit Conductor owner.

Adapters perform any Drone Core NED/FRD or vehicle FLU conversion at their
component boundary. Frame conversion must not be scattered through scenario
logic.

## Interactions and collision scope

### Static city contact

`Drone × building`, `Car × road`, and `Car × building` are solved directly in
the MuJoCo world that owns the vehicle. They are not cross-world events.

### Drone–car contact: initial simplified model

The initial demo deliberately uses a one-way, practical approximation:

```text
real Car × collision-enabled mirrored Drone
  -> local MuJoCo contact
  -> MirrorImpulseSender detects contact enter
  -> ImpulseCollision to the real Drone owner
  -> ContactEvent to scenario safety and the viewer

Car response: local contact with a static mirrored obstacle
Drone response: existing Drone Core ImpulseCollision handling
```

`MirrorImpulseSender` is a planned generalization of the existing
`ImpulseDisturbanceSender` in `hakoniwa-mujoco-robots`. It retains contact
point, normal, speed threshold, restitution, and cooldown handling. A mirror
target must have a separate collision proxy; visual-only mirror geometry stays
non-colliding.

This is intentionally **not** a momentum-conserving two-body co-simulation.
Mirrored bodies are pose-driven and their `qvel` is not the authoritative
remote velocity. Correct reciprocal impulses, friction, and continuous
time-of-impact resolution are future work.

## Initial scenario

1. Select a small PLATEAU region of interest, initially around 100 m × 100 m.
2. Start two low-speed Ackermann vehicles on road routes.
3. Start two Virtual Drone Show drones from a small, time-indexed flight plan.
4. Publish actual poses to the opposite world's mirrors.
5. Detect vehicle–drone contact and emit an audience-visible safety event.
6. Apply the initial safety policy: car brake/stop and drone hold, pause, or
   ascent according to the scenario.

The checked-in configuration selects the validated compact Hokkaido/Sapporo
city region, two Golf Cart instances, and Quad and Hexa drones. Those are
demonstration choices, not hard-coded product requirements.

## Configuration-first vehicle composition

The scenario must be able to substitute cities, vehicle platforms, and entity
counts without changing scenario-runner code. It therefore separates a
component-provided **platform profile** from a demo-specific **scenario**.

```text
platform profile                         scenario
----------------                         --------
owner adapter                  +----->   entity ID and role
source repository / artifact   |         selected platform profile
vehicle type and visual asset  |         spawn pose and route
collision proxy                |         safety policy
command/state endpoints   -----+         city receipt and timeline
```

A platform profile references its component-owned source of truth rather than
copying vehicle dynamics into this repository. For example, the initial
catalog may select a Golf Cart or Hunter Ackermann profile, and a Quad or
Hexa Drone Fleet type. Future scenarios can use a different vehicle model,
drone count, city receipt, or controller adapter while retaining the same
scenario contract.

The current Drone Fleet configuration format already permits each named Drone
to select a type. The Urban Mobility integration must materialize mixed Fleet
types (for example `quad-mujoco` and `hexa-mujoco`) rather than assuming one
shared Drone type for all entities.

## Planned repository layout

```text
scenarios/       Human-authored city, entity, route, and safety definitions
schemas/         Versioned scenario and city_map contracts
src/             Scenario runner and component adapters
launch/          Generated or hand-authored local launch compositions
tests/           Deterministic integration and acceptance tests
docs/            Coordinate, timing, safety, and operational documentation
```

## Build the Urban Car application

The Ackermann vehicle application is owned and built here. It consumes the
generic contracts and adapters from `hakoniwa-robot-runtime` and the MuJoCo
backend from `hakoniwa-mujoco-robots`; neither sibling repository owns the
Urban executable.

```bash
cmake -S . -B build
cmake --build build -j

build/bin/urban-car-hakoniwa-asset --help
```

The default sibling-checkout layout is the Business Pack workspace. Alternate
locations can be selected at configure time with
`HAKONIWA_ROBOT_RUNTIME_ROOT`, `HAKONIWA_MUJOCO_ROBOTS_ROOT`,
`HAKONIWA_PDU_REGISTRY_ROOT`, and `HAKONIWA_FOUNDATION_PREFIX`.

The target explicitly enables Robot Runtime's mobile-base extension. Existing
Robot Arm applications do not enable or link Ackermann control or MultiDOF
vehicle-state output. Use `-DHAKO_URBAN_ENABLE_VIEWER=OFF` for a headless
build.

The application accepts a manifest-driven Runtime instance:

```bash
build/bin/urban-car-hakoniwa-asset \
  --manifest ../hakoniwa-business-pack/work/recipes/urban-multi-car-viewer/config/car/urban-car-asset-manifest.json

# Model inspection without registering a Hakoniwa asset
build/bin/urban-car-hakoniwa-asset \
  --manifest ../hakoniwa-business-pack/work/recipes/urban-multi-car-viewer/config/car/urban-car-asset-manifest.json \
  --view-model

# Headless XML/MJB compatibility check
build/bin/urban-car-hakoniwa-asset \
  --manifest ../hakoniwa-business-pack/work/recipes/urban-multi-car-viewer/config/car/urban-car-asset-manifest.json \
  --validate-model
```

The `multi_car.py` recipe materializes the Urban-owned AckermannDrive,
JointState, and MultiDOF contracts, compiles the composed city model to MJB,
and launches the application with explicit wall-clock pacing. When browser
visualization is enabled it also generates a read-only WebBridge and a compact
Three.js scene. Vehicle geometry remains owned by the standard
`hako_viewer_model` generated in `hakoniwa-mbody-registry`; the browser does
not reconstruct MJCF or vehicle dynamics. Application ownership is independent
of Robot Arm Pack.

## Delivery stages

1. **Topology:** one Drone and one Car; common `city_map`; bidirectional pose
   mirroring; one Conductor owner.
2. **Demo scale:** two Drones and two Cars; PLATEAU ROI; unified viewer.
3. **Safety interaction:** collision proxy, `MirrorImpulseSender`,
   `ContactEvent`, brake/hold policy, and repeatable evidence.
4. **Mission interface evolution:** retain the scenario contract while
   replacing the Drone Fleet API adapter with ArduPilot SITL / MAVLink.
5. **Higher-fidelity contact:** evaluate shared velocity state and reciprocal
   external impulses only if the demo requires them.

The executable dependency plan and the car-first integration checkpoints are
defined in [`docs/implementation-plan.md`](docs/implementation-plan.md). The
first concrete composition contract is
[`recipes/urban-mobility.yaml`](recipes/urban-mobility.yaml).
The Car Fleet viewer and control checkpoint is
[`recipes/multi-car-viewer.yaml`](recipes/multi-car-viewer.yaml).

### Urban Car Fleet operation

The recipe wrapper materializes only local generated files under `work/` and
reuses component-owned tools and assets. Choose driveable spawns relative to
the receipt origin in local ENU before configuring; the city origin is not
assumed to be a road. The complete operating guide is in
[`recipes/README.md`](recipes/README.md).
The checked-in hotel demo generates ten Golf Carts at 4.5 m route spacing;
changing the scenario fleet `count` regenerates both spawn poses and runtime
contracts.

For the one-Car browser checkpoint, use
[`recipes/experiments/urban-car-one.yaml`](recipes/experiments/urban-car-one.yaml).
The complete macOS operation guide is
[`docs/operation-one-car-ps5-browser.md`](docs/operation-one-car-ps5-browser.md).
The recipe selects the City World receipt, Golf Cart model, initial pose,
PS5 RC control, browser presentation, and generated workspace. `start` brings
up the simulator, WebBridge, HTTP server, and PS5 sender together. The native
MuJoCo Viewer is not required for this checkpoint.

```bash
python3 tools/multi_car.py configure \
  --config recipes/experiments/urban-car-one.yaml
python3 tools/multi_car.py check-ps5 \
  --config recipes/experiments/urban-car-one.yaml
python3 tools/multi_car.py start \
  --config recipes/experiments/urban-car-one.yaml

python3 tools/multi_car.py open-viewer --colliders \
  --config recipes/experiments/urban-car-one.yaml

python3 tools/multi_car.py stop \
  --config recipes/experiments/urban-car-one.yaml
```

```bash
python3 tools/multi_car.py doctor --config recipes/multi-car-viewer.yaml
python3 tools/multi_car.py configure --config recipes/multi-car-viewer.yaml
python3 tools/multi_car.py start --config recipes/multi-car-viewer.yaml

# Open the Three.js URL printed by configure (default):
# Use the Map Viewer URL printed by configure. Its viewerConfigPath points to
# /hakoniwa-business-pack/work/recipes/urban-multi-car-viewer/config/threejs/viewer-config.json

# Drive any externally controlled vehicle from another terminal:
../hakoniwa-business-pack/work/foundation/install/python/bin/python3 \
  apps/car/ackermann_command.py --robot Car-1 drive \
  --speed 1.0 --steering-deg 15 --duration 3

../hakoniwa-business-pack/work/foundation/install/python/bin/python3 \
  apps/car/ackermann_command.py --robot Car-2 drive \
  --speed -1.0 --steering-deg -15 --duration 3

# Or run the simulation-time-based two-Car convoy:
../hakoniwa-business-pack/work/foundation/install/python/bin/python3 \
  apps/car/scenario_executor.py recipes/scenarios/two-car-convoy.yaml

# Or continuously follow the hotel drop-off loop using fleet pose feedback:
../hakoniwa-business-pack/work/foundation/install/python/bin/python3 \
  apps/car/scenario_executor.py recipes/scenarios/hotel-convoy-loop.yaml

# Later, from another terminal:
python3 tools/multi_car.py status --config recipes/multi-car-viewer.yaml
python3 tools/multi_car.py stop --config recipes/multi-car-viewer.yaml
```

The YAML selects a city by its City World receipt `path`; changing that one
path is sufficient to switch cities. Origin, extent, coordinate systems, and
artifact paths are derived from the receipt. Each spawn is relative to the
receipt's city origin. Position uses local ENU metres (`east_m`, `north_m`,
`up_m`); `yaw_deg` is positive counter-clockwise from East. Roll and pitch are
fixed to zero. The tool converts this to MuJoCo's `X=North, Y=-East, Z=Up`
frame and radians during configuration, and records both representations in
the composition receipt.

Scenario schema version 1 is the original simulation-time command sequence.
Schema version 2 is a closed ENU waypoint route: the executor reads
`UrbanFleet/vehicle_states`, projects each vehicle onto the route, and applies
pure-pursuit steering. A shared virtual route position plus each vehicle's
`route_offset_m` preserves convoy spacing. Waypoint `dwell_sec` stops the whole
formation, and `loop_count: forever` repeats until Ctrl-C.

`inputs.ackermann_vehicles.vehicles.generated_from_route` selects a catalogued
type and derives the checked-in fleet names and spawn poses from the route
scenario. Change its `vehicles.generate.count` to switch fleet size without
duplicating vehicle entries. Explicit vehicle arrays remain supported for
mixed types and per-vehicle control modes. The control mode selects
`external_python` or `ps5` independently.
`start` always opens the Urban-owned Car application with the shared native
MuJoCo Viewer backend. In `ps5` mode the Launcher also starts the PS5 sender;
in `external_python` mode a separate process owns command publication. Use
`view` after `configure` for a Viewer-only model inspection.

`configure` preserves the composed XML as the canonical materialization, then
compiles and reload-validates a version-bound MJB with the exact MuJoCo library
linked by the Ackermann plant. Runtime loads this MJB, avoiding a full City
World XML compilation on every start.

This single-host checkpoint exclusively owns the shared Foundation runtime
while active. Its Launcher cleans only the configured Hakoniwa mmap/lock files
before starting the plant, waits until `UrbanCarFleet` is registered, and only then
issues `hako-cmd start`. Do not run another Foundation SHM simulation at the
same time. `stop` uses the Launcher session's normal termination path.

## Non-goals for the initial release

- A single MuJoCo model combining the independently owned Car and Drone worlds.
- Full-city high-density PLATEAU collision meshes.
- A new drone flight controller or car dynamics implementation.
- Physically exact vehicle–drone impact, friction, or momentum conservation.
- ArduPilot/MAVLink integration before the Fleet API scenario is proven.

## Related work

- [Mirror impulse generalization issue](https://github.com/hakoniwalab/hakoniwa-mujoco-robots/issues/58)
- [Legacy Ackermann sample retirement](https://github.com/hakoniwalab/hakoniwa-mujoco-robots/issues/60)
