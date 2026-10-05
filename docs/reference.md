# Hakoniwa Urban Mobility: operations reference

Command-line operation of Compositions, Urban Studio, the managed Recipes and
the People tools, moved from the README.

## Compositions and Urban Studio

A Composition selects a World (a PLATEAU City or a plain World), places
vehicle Assets (Golf Cart, EAMS Hexa, FPV Drone), and picks each vehicle's
control (`rc` or `api`). The contract is
[`docs/asset-contract.md`](asset-contract.md); examples are in
`recipes/compositions/`. The root manifest
[`urban.manifest.yaml`](../urban.manifest.yaml) names the repository's parts and
ports ([`docs/urban-manifest.md`](urban-manifest.md)). One entrypoint runs every combination:

```bash
python tools/urban_mobility.py plan --composition recipes/compositions/plain-hexa-rc.yaml
python tools/urban_mobility.py configure --composition recipes/compositions/plain-hexa-rc.yaml
python tools/urban_mobility.py start --composition recipes/compositions/plain-hexa-rc.yaml
```

Urban Studio is the browser UI over the same API (Assets, Compose,
Simulation). Start it from the Business Pack Workspace shell
(`python tools/workspace.py enter`, then in `hakoniwa-business-pack`), so the
simulations it runs inherit the Workspace environment:

```bash
python ../hakoniwa-urban-mobility/tools/urban_studio.py start --open-browser   # in the background
python ../hakoniwa-urban-mobility/tools/urban_studio.py open                   # open the running one in the browser
python ../hakoniwa-urban-mobility/tools/urban_studio.py status
python ../hakoniwa-urban-mobility/tools/urban_studio.py stop                   # before leaving the Workspace
```

Without `start`, `urban_studio.py` runs it in that terminal (Ctrl+C or
`stop` ends it). It serves `http://127.0.0.1:28090/` and saves Compositions
under `hakoniwa-business-pack/work/urban/compositions/`.

A World can also come from
[Hakoniwa Environment Studio](https://github.com/hakoniwalab/hakoniwa-environment-studio):
its "urban-mobility へ" button exports an environment as a City World job
(`schemas/city-world-job.yaml`) and registers it as a City Asset, which
Compose then offers as a World. Its README walks through the whole flow
(make an environment, register it, compose it with a car here, simulate).

With an EAMS Hexa, the Viewer's left panel has a Fault Injection box: one
thrust-scale slider per rotor (1.0 nominal, 0.0 failed) plus wind direction
and speed, sent to the Drone's `disturb` PDU when a slider is released.
The controller does not reallocate thrust, so a failed rotor makes the Hexa
spin and tilt (`tools/urban_fault_injection.py`).

- **City**: "Environment Studio で作る" starts
  [Hakoniwa Environment Studio](https://github.com/hakoniwalab/hakoniwa-environment-studio)
  (configured first through `recipes/usecases/urban-city-authoring.yaml`) with
  Urban's export folder (`work/urban/studio-cities`, the manifest's
  `assets.studio_city_jobs`) and opens its map page. The Studio does not know
  Urban: it writes the Cities it makes (PLATEAU City Worlds, edited
  environments) there as City World jobs, and Urban Studio checks and
  registers each job that appears (`urban_assets.py register-city`, with the
  one-time height model compile) and unregisters it when the job is removed.
  From a terminal: `python tools/urban_city_authoring.py start|status|open|stop`.
  The City page's "キャッシュ" panel shows the cache sizes, and
  "Urban キャッシュを整理" runs `python tools/urban_assets.py prune-cache --apply`
  (see Cache cleanup).
- **Route**: draw a Car route (a closed loop of waypoints) on the City World
  map: click to add points, drag to move them, and set dwell, speed, loops, and
  which Cars follow it. Routes are saved under
  `hakoniwa-business-pack/work/urban/scenarios/`. In Compose, an API-controlled
  Car picks its route from a selector. "削除" removes a saved route; examples
  stay, and a route a saved Composition still uses is kept until no Car
  picks it.
- **Flight**: draw a Drone flight on the City World map and check it in 3D:
  the takeoff point, waypoints with their height above the ground or roof
  under them, speed, hold, yaw, and where it lands. Legs that pass a building
  too closely are shown in red. Flights are saved next to the routes; in
  Compose, a Drone with the `schedule` control picks its flight.
- **Compose**: place vehicles by dragging them in the 3D view, or by clicking
  the map for a City World. The spawn height is the ground (rooftops and
  obstacles included) plus the vehicle's clearance. Adding the Drone Core
  Quad adds a fleet: its count, spacing, and grid centre are set on its card,
  the grid is drawn as dots, and a map click moves the centre. A fleet runs
  alone (no other vehicles in the same Composition, for now).

## Cache cleanup

Spawn heights use compiled World models cached under
`hakoniwa-business-pack/work/urban/cache/world-height/` (one entry per World
and MuJoCo version). Run from the Workspace shell, so the MuJoCo version is
the one the simulations use:

```bash
python tools/urban_assets.py prune-cache            # dry run: what would be removed and why
python tools/urban_assets.py prune-cache --apply    # delete
```

It removes entries of deleted or unregistered Cities, of Worlds regenerated
since, abandoned compiles, and entries of another MuJoCo version once the
current version has its own entry for that World. `--other-mujoco-versions`
removes the latter regardless; `--plain-world` also clears the plain-world
MJCF cache (regenerated on demand); `--json` prints the report as JSON.
A registered City's World and the City World jobs are never touched.

The large PLATEAU downloads (gigabytes) belong to the Business Pack City World
Web UI and are cleaned there:
`python tools/recipe/city_world_web_ui.py cache-clean --job-sources --source-cache [--apply]`.

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
See [`docs/operation-managed-recipe.md`](operation-managed-recipe.md).

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
[`docs/operation-one-drone-ps4-browser.md`](operation-one-drone-ps4-browser.md).

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

## Hakoniwa People (箱庭人間) moved from outside

People that an external program (a script, a planner, an AI agent) moves by
name, as the Urban Car is driven from outside. Each person is the
`hakoniwa_person` body of hakoniwa-mbody-registry: in MuJoCo a capsule that
slides and turns; its arms and legs are animated (they swing with the distance
walked), not simulated. `apps/people/people_plant.py` runs every person in one
MuJoCo world as the Hakoniwa asset `HakoniwaPeople`.

| PDU | type | |
|---|---|---|
| `<name>/cmd_vel` | `geometry_msgs/Twist` | linear.x east, linear.y north (m/s, ENU); the person faces where it walks; angular.z turns it while it stands |
| `<name>/animation` | `std_msgs/String` | `auto` (walk while moving, idle otherwise), `walk`, `idle`, `wave`, `sit` |
| `UrbanPeople/vehicle_states` | `sensor_msgs/MultiDOFJointState` | each person's pose (MuJoCo frame, as the car fleet's) |
| `UrbanPeople/joint_states` | `sensor_msgs/JointState` | the animated `<name>/shoulder_*_joint`, `<name>/hip_*_joint` |

```bash
python tools/people_sim.py configure --people recipes/people/people-three.yaml
python tools/people_sim.py start --people recipes/people/people-three.yaml   # prints the viewer URL
python apps/people/hakoniwa_people.py --pdu-def <work>/people/people-three/config/people-pdudef.json walk-to Person-1 --east 2 --north -4
python apps/people/hakoniwa_people.py --pdu-def <...>/people-pdudef.json animate Person-2 wave
python tools/people_sim.py stop --people recipes/people/people-three.yaml
```

From Python, `PeopleClient` (apps/people/hakoniwa_people.py) offers
`set_velocity`, `set_velocities` (several people at once), `set_animation`,
`stop` (also back to `auto`), `stop_all`, `poses` and `people()` (who is
here: look, pose, animation), and the blocking demo helper `walk_to`.
Commands stay until the next one. An agent moving many people loops: read
`poses()`, decide, `set_velocities()`, as `apps/people/crowd_demo.py` does
for the ten people of `recipes/people/people-crowd.yaml`.

A recipe may name an `environment` (an Environment Studio Recipe; configure
writes it as a City World first) or a `world` (a City World receipt): the
people then bump into its stalls and buildings and step up onto what is lower
than 0.32 m (a square's deck), as in `recipes/people/people-stall-street.yaml`.
The plant measures the ground under each person with a ray and lifts it there.

People also join a Composition of Cars as vehicles of the Assets
`hakoniwa-person-{visitor,staff,passerby,child}` (control `external`, moved
from outside; or `api`, riding as a scenario says: below), as in `recipes/compositions/plain-golf-cart-people.yaml`. The Car
route then starts the people plant after the Car plant, in the same
Hakoniwa time (`tools/urban_people.py`): people walk the same City World and
bump into its buildings, and into the cars too: each car is a box of its
Asset's size in the people's world, moved with the car (the cars do not feel
the people; an agent reading both positions keeps them apart). The viewer
shows both.

Every contact of a person with a car, another person or the city is an
event (who, with what, Hakoniwa time, position, relative speed; when it
starts and ends): written to the contact log `logs/people-contacts.jsonl`,
published as the person's `<name>/contact` (`hako_msgs/ContactEvent` of
hakoniwa-pdu-registry), and read with `people.contacts()` (new events of
everyone) or `people.contact(name)`.
The people's PDU definition is the Car Recipe's `config/people/people-pdudef.json`.

People ride the cars whose Asset declares `seats` (the golf cart: `driver`,
`passenger`): `people.ride("Person-1", "Car-1", "driver")` (the
`<name>/ride` PDU, `std_msgs/String` "Car-1/driver") seats the person, who
then follows the car's pose in the sit pose without colliding;
`people.get_off("Person-1")` puts it beside its seat, back on the ground.

### People riding a car route scenario

A car route scenario (`apps/car/scenario_executor.py`, the Car's control
`api`) may carry a `people:` section, which the executor ignores: who rides
its car, where each waits, at which stop (a route point with `dwell_sec`) and
in which seat they get on, where they get off and walk to. People whose
control is `api` run it with `apps/people/ride_plan.py` (one process for all
of them): when the car stands at a rider's stop, the rider walks to its
seat's door (round the back of the car for the far side) and gets on; at the
destination it gets off and walks on. The car keeps the executor's
simulation-time schedule (ride_plan only reads its pose), nothing is random,
and every event (arrived, boarding, ride, departed, alight, reached) is
written with Hakoniwa time to `logs/people-rides.jsonl`:

```bash
python tools/urban_mobility.py configure --composition recipes/compositions/plain-hakoniwa-cart-ride.yaml
python tools/urban_mobility.py start --composition recipes/compositions/plain-hakoniwa-cart-ride.yaml
```

`recipes/scenarios/hakoniwa-cart-station-pickup.yaml`: the 箱庭カート comes to
a stop where three people wait, takes them to a destination 30 m on and lets
them off. Two runs give the same events, within the 0.1 s the people's side
polls at.

### A scene run from outside: the festival director

`apps/people/festival_director.py` runs a scene file (`recipes/people/scenes/*.yaml`)
over the People API and the Urban Car API, the loop an agent would run:
stall staff wave behind their counters; visitors and children walk from
stall to stall, line up at a stall (the head of the line is served, the
others step up), keep a little apart, and some sit down at a square's table
afterwards and talk (`sit_talk`) when someone else sits there; a golf cart
shuttle (a car with control `external`, driven by the director) stops for a
visitor nearby to get on and lets them off at the next stop, slowing and
stopping for people ahead. Cars driven with a controller (control `rc`) are
watched too: near misses and contacts are written with Hakoniwa time to the
Car Recipe's `logs/festival-director.jsonl`.
`recipes/people/scenes/sapporo-festival.yaml` is the さっぽろ駅前祭り: the
Sapporo Station south plaza made into a festival street in Environment
Studio (stalls, squares, lights for the night mode).

```bash
python apps/people/festival_director.py recipes/people/scenes/sapporo-festival.yaml \
    --people-pdu-def <work>/recipes/urban-car-rc/config/people/people-pdudef.json \
    --car-pdu-def <work>/recipes/urban-car-rc/config/car/urban-car-pdudef.json --duration 600
```

Car Assets also offer control `external`: an outside program drives the car
through the Urban Car API (`apps/car/urban_car.py`) and the Launcher starts
nothing for it.

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
