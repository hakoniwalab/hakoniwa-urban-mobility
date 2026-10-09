# Urban Mobility Asset / Composition contract

## 1. Status and scope

This document fixes the Asset and Composition contract proposed in
[issue #5](https://github.com/hakoniwalab/hakoniwa-urban-mobility/issues/5)
(integrated City / Assets / Compose / Simulation browser pack). It is the
target contract; the existing Recipes in `recipes/experiments/` and
`recipes/usecases/` do not follow it yet. Section 7 maps them onto it.

The v1 goal is to re-integrate what already runs, as combinations of Assets:

```text
World + Car
World + Drone
World + Car + Drone
World + Drone (the finished FPV Drone as one more Drone Asset)

World = a PLATEAU City, or a plain ground with optional obstacles (section 6)
```

The runtime architecture does not change:

- Car and Drone are separate simulators (separate processes, separate
  physics). A Car-only or Drone-only Composition runs that simulator alone.
- A Car + Drone Composition starts both simulators; Hakoniwa owns time
  synchronization and PDU communication between them.
- The browser view is the existing Three.js viewer. The browser backend only
  drives the Recipe lifecycle (`configure`, `start`, `stop`, `status`).

The central user loop is place, simulate, stop, and place again (section 5.5).

## 2. Two layers

| Layer | Answers | Owned by | Changes when |
|---|---|---|---|
| Asset manifest | What the Asset is and what it can do | The Asset's source repository | The Asset itself changes |
| Composition | What the user selected for one simulation | The user (browser or CLI) | The user edits the scenario |

A Composition references Assets by `id`. It never copies an Asset's model,
controller, or PDU definitions; the builder resolves them from the manifest.

Browser and CLI produce the same Composition file and the same Recipe
workspace artifacts (issue #5 principle 4).

### 2.1 Standard managed Recipe

The current managed Recipe (`recipes/usecases/urban-car-rc.yaml`) mixes two
concerns: preparing the environment (dependencies, Foundation, Python
requirements) and selecting what to simulate (a tracked Car composition
template filled by `--city-receipt`). The contract separates them:

```text
Standard Urban managed Recipe (one, Business Pack contract)
  = environment: dependencies, Foundation, requirements
      dependencies are derived from the selected Assets' source.repository
  input: --composition <Composition file>
       | configure
Builder: reads the Composition and dispatches by Asset simulator
  |- Car builder    (ackermann-mujoco)
  |- Drone builder  (drone-core; the FPV Drone included)
       |
Launcher config, Three.js config, Recipe workspace
```

- The Composition is the only user-facing Recipe input. One entrypoint serves
  every combination:
  `tools/urban_mobility.py plan|configure|start|status|open-viewer|stop --composition <file>`.
  It delegates to `tools/urban_simulation.py`, the API the browser backend
  calls: `plan(composition)` validates the Composition and returns its route
  (`car`, `drone`, `integrated`, `fpv`), managed Recipe, and workspace
  (`Plan.to_json()` for the browser; the `plan` command prints it), and
  `run(command, composition)` executes a lifecycle command on that route.
  The Car and integrated routes run the managed Recipe lifecycle in
  `tools/urban_mobility.py --recipe <managed> --composition <file>`.
- Every route has a managed Recipe (the root manifest's `recipes`). The
  Drone (`recipes/usecases/urban-drone-rc.yaml`), fleet
  (`urban-drone-fleet.yaml`) and FPV (`urban-fpv-rc.yaml`) routes configure
  with their own tools (`drone_one.py`, `drone_fleet.py`, hakoniwa-fpv-drone's
  `tools/fpv.py`), so their Recipes only declare what those use: `configure`
  (and `doctor`) first run the Business Pack `recipe.py` on the route's Recipe,
  which clones missing repositories, checks the Foundation, and installs the
  Python packages, then run the tool. The FPV Recipe declares
  hakoniwa-fpv-drone itself; the dependencies of `tools/fpv.py` are declared
  by hakoniwa-fpv-drone's own Recipe.
- A Car-only Composition materializes only the Car dependencies; the Recipe
  never requires the union of all Assets.
- The Recipes in `recipes/experiments/` retire once their Composition
  reproduces them (section 7.1).

## 3. Asset manifest

### 3.1 Placement

The target placement is the Asset's source repository. For v1:

- `hakoniwa-fpv-drone` owns its manifests (`assets/<id>.asset.yaml`): the
  Master3X FPV Drone and the `fpv-training-course` plain World.
- All other manifests live in this repository under `assets/`, and point into
  their source repositories (`hakoniwa-mbody-registry`, `hakoniwa-drone-core`,
  ...). They move to their source repositories later without changing the
  schema.
- User-generated City Assets live in the Business Pack work directory,
  `work/urban/assets/cities/<id>.asset.yaml`, written by
  `tools/urban_assets.py register-city --receipt <city-world-receipt.json>`.
  The id defaults to the City World job name; `--title` gives the name shown
  for it (default: the id). Registration first checks the
  job against its contract (section 6.3) and refuses one that breaks it
  (`--no-check` registers anyway). Cities made with Environment Studio
  arrive as City World jobs in Urban's export folder
  (`assets.studio_city_jobs`, `work/urban/studio-cities`; each with
  `job.json` giving its title); Urban Studio registers each one that appears
  and unregisters it when it is removed (section 5.7). Tools read the catalog
  with `tools/urban_assets.py list --json` (id, kind, category, title,
  manifest, and a City's resolved receipt).
  `tools/urban_assets.py unregister-city --id <id>` removes a registration
  (the City World job is kept). `tools/urban_assets.py prune-cities`
  unregisters Cities whose City World Web UI job was deleted (Cities
  registered from those jobs before Environment Studio made them). Only Cities registered from a job
  under `work/recipes/city-world-web-ui/runtime/jobs/` are pruned, so a City
  registered from another location is never removed silently. A City whose
  receipt is missing stays in the catalog but is marked unavailable and cannot
  be selected as a World.
- `tools/urban_assets.py prune-cache` removes derived caches that no City or
  World uses any more (section 5.4); it is a dry run unless `--apply`.

The locations below are named in the root manifest
([`urban.manifest.yaml`](../urban.manifest.yaml), `assets`; see
[urban-manifest.md](urban-manifest.md)), with the managed Recipes and ports.
The catalog reads this repository's `assets/` (recursively), the top level of
every workspace repository's `assets/` (`<repo>/assets/*.asset.yaml`; those
directories also hold models, so they are not searched recursively), and the
user City Assets. `tools/urban_assets.py list` prints it.

### 3.2 Fields (vehicle)

```yaml
schema: hakoniwa.asset/v1
id: <unique id>                 # referenced by Compositions
kind: vehicle
category: car | drone
version: <semver>
title: <display name>
source:
  repository: <repository name>
  generator: <command that materializes the model, optional>
simulator: ackermann-mujoco | drone-core
model:
  physics: <MJCF path or generated-model reference>
  visual: <Three.js view model / GLB manifest>
preview:                        # optional Urban Studio 3D preview
  format: drone-type | view-model | fpv-assembly
  path: <drone_types-*.json | hako_viewer_model JSON | FPV Assembly Graph>
  type: <drone type name, drone-type only>
dimensions:                     # optional; Cars (route wall checks)
  width_m: <outer width>
  length_m: <outer length>
spawn:
  ground_clearance_m: <base-frame height above the ground at spawn>
viewer:
  front_camera: <optional vehicle-mounted Three.js camera>
pdu:
  definition: <PDU definition path>
controls:
  rc: <control program, section 4>
  api: <control program, section 4>
```

`spawn.ground_clearance_m` is the only height input; see section 5.4.

`dimensions` is a Car's outer size. Route checks keep half the width plus
0.2 m off building walls (`tools/route_check.py`): in Compose for the widest
Car following that route, in the Route tab for the widest Car Asset (a
route does not know its Cars). Without any, the check keeps 0.8 m.

`preview` names the display model the Studio's Assets tab turns in 3D
(`tools/asset_preview.py`): a hakoniwa-threejs-drone drone type (frame,
rotors and camera), a hakoniwa-mbody-registry view model, or an FPV Assembly
(exported once by the FPV generator into `work/urban/cache/preview/`).
Without it the card shows
"プレビューなし". Worlds need no field: a City shows its building outlines,
a plain World its display GLB.

Paths are relative to the manifest file unless they start with a repository
name placeholder such as `${repo:hakoniwa-drone-core}`.

An Asset may declare only one of `rc` / `api`. The browser offers only the
declared controls.

## 4. Controls: `rc` and `api`

Users choose one control per vehicle: `rc` (a game controller operates it) or
`api` (a program operates it remotely). The internal names used by the current
tools remain as implementation details:

| Category | `rc` today | `api` today |
|---|---|---|
| Car | `control_mode: ps5` (`apps/car/ps5_ackermann_sender.py`) | `control_mode: external_python` (`apps/car/scenario_executor.py`) |
| Drone | `control.mode: ps4-rc` (Drone Core `drone_api/rc/rc-custom.py`) | `control.mode: fleet-rpc` (`apps/drone/city_fleet_mission.py`) |

A Drone may also offer `schedule`: a program flies it by a schedule on
Hakoniwa time (`apps/drone/drone_schedule.py`), the `drones:` section of a
route scenario or of a file of its own. It uses the same RPC service as `api`
(`control.mode: fleet-rpc`). The EAMS Hexa declares it.

### 4.1 A control is a program with arguments

Car and Drone cannot share one control program, so each control declares the
program the Launcher starts and its argument template:

```yaml
controls:
  api:
    program: ${repo:hakoniwa-urban-mobility}/apps/car/scenario_executor.py
    scope: composition           # vehicle | composition | shared (section 4.3)
    args: [ "${param.scenario}", "--pdu-def", "${runtime.pdu_def}" ]
    params:
      scenario: { type: path, required: true, kinds: [car-route-scenario] }
```

Argument templates use two placeholder namespaces:

| Placeholder | Filled by | Examples |
|---|---|---|
| `${runtime.<name>}` | The builder at configure time; never written by the user | `pdu_def`, `service_config`, `city_marker`, `summary_json`, `drone_root` |
| `${param.<name>}` | The Composition (user input), validated against `params` | `scenario`, `mission`, `max_speed` |
| `${vehicle.name}` | The vehicle instance name in the Composition | `Car-1`, `Drone-1` |
| `${vehicle.index}` | The vehicle's 0-based index among the vehicles of its simulator | `0` |
| `${repo:<name>}` | A workspace repository; an argument starting with it is normalized as a path | `${repo:hakoniwa-drone-core}/drone_api` |

`${vehicle.*}` is not available to `composition`-scoped controls. An
unknown or unavailable placeholder is an error naming it.

Param values are typed by the manifest: `path` resolves against the
Composition file, `number` is written as a decimal, and a missing optional
param takes its `default`.

A control may set `cwd` (same placeholders). The manifest's `program`,
`args`, and `cwd` are the defaults. A Composition may replace `program` and
`args` (section 5.2) to run a user program; the process then runs in the
Composition directory, and `${runtime.*}` placeholders remain available, so
user programs receive the same generated paths.

`tools/urban_controls.py` expands the controls into Launcher processes named
`control-<vehicle>-<control>` (`control-<asset>-<control>` for a
`composition` scope). Each depends on its simulator's service asset and
starts after the simulation starts.

### 4.2 Program types

`program` is a Python file run by the Foundation Python unless `runner` says
otherwise:

```yaml
runner: python             # python (default) | executable
interpreter_args: ["-u"]   # python only: arguments before the program
```

Python control programs must not rely on the current directory or the script
directory being on `sys.path`: the Windows portable package runs an embedded
Python whose `._pth` file excludes both.

### 4.3 Scope

- `vehicle`: one program instance per vehicle (every `rc` control; Drone
  `api`).
- `composition`: one program instance drives all vehicles of that Asset that
  selected this control with the same arguments; vehicles with different
  arguments get another instance (`control-<asset>-<control>-2`, ...). The Car
  route scenario executor is this type: one executor per route.
- `shared`: one program instance drives every vehicle of the simulator that
  selected this control, whatever its Asset (`control-<simulator>-<control>`).
  `args` come once; each vehicle appends its `vehicle_args` (all placeholders,
  `${vehicle.*}` included) in Composition order. The Drone `schedule` is this
  type: one process flies every Drone (`--flight <drone>=<schedule>` each).
  The vehicles' `args` must expand alike.

A Car's route selection (`params.scenario`) is authoritative. A route's
`vehicles` list only provides offsets: when a route is selected by a different
set of Cars than it names, the builder writes a derived copy under
`work/urban/cache/routes/` naming exactly those Cars (keeping their offsets,
adding unnamed Cars 6 m behind, the leading Car at offset 0), and the Cars use
it. A Car on a route starts at the route start set back by its offset, facing
along the route; its placed spawn is not used. Route files are never modified.

### 4.4 Road friction and tire grip (Car routes)

A Car route point may set `road_friction`: the road's friction coefficient
on the leg from that point to the next one, in a band round the leg as wide
as the road (`route.road_width_m`, default 6 m, i.e. 3 m each side of the
line; a point's own `road_width_m` overrides it for its leg). The band's ends
are round; where two bands overlap (a corner) the nearer leg's holds.

The route executor watches where each Car actually is (its pose, not its
route target): in such a band its tires get the road friction times the
Car's `tire_grip` (Composition, section 5.1; default 1.0), sent as the
`tire_friction` PDU (std_msgs/Float64) when the value changes. Anywhere
else (a leg without `road_friction`, or off the road, e.g. after sliding
out of the band) the tires have the model's friction again (the builder
reads it from the vehicle model):

    tire friction = road_friction x tire_grip    (a leg with road_friction)
                  = the model's tire friction    (a leg without, e.g. 1.6)

```yaml
route:
  road_width_m: 6.0                # the band width for every leg (default 6)
  points:
    - {name: koen-dori-in, east_m: 120.0, north_m: -40.0, road_friction: 0.3}  # snow to the next point
    - {name: koen-dori-mid, east_m: 170.0, north_m: -40.0, road_friction: 0.3,
       road_width_m: 12.0}                                                     # a wider snowy square
    - {name: koen-dori-out, east_m: 220.0, north_m: -40.0}                     # the model's from here
```

0 is no friction at all: the wheels spin in place and the Car cannot drive
or steer on that leg (it only coasts through on its speed).

Guideline values for `road_friction` (sliding friction, rubber on the surface):

| Surface | road_friction |
|---|---|
| Dry asphalt / concrete | 0.8 - 1.0 |
| Wet asphalt | 0.4 - 0.6 |
| Snow | 0.2 - 0.3 |
| Ice | 0.05 - 0.1 |

The route line is drawn in the friction's colour and its band filled
see-through in it (Studio Route tab map and 3D view, and the Viewer's
planned path), so the wet or icy area shows: green dry (0.7 and up), yellow
wet (0.35 and up), orange snow (0.15 and up), purple ice. A leg without
`road_friction` keeps its usual blue line and no band.

`tire_grip` is the tire's share: 1.0 standard, about 1.2 for a high-grip
tire, about 0.8 for a worn one. This is a simplification: a real tire's grip
changes differently on each surface (a winter tire gains most on snow and
ice), whereas here one factor scales every surface.

How it is applied: the builder gives a Car on such a route a Robot Runtime
`geom_friction` component (hakoniwa-robot-runtime, configuration.md 3.2.5)
for its four tire collision geoms and writes `priority="1"` on those geoms in
the fleet MJCF. MuJoCo otherwise takes the larger friction of two touching
geoms (the World's surfaces are 1.0), so a lower value would have no effect;
with the priority, the tire's friction is the contact's. A Car whose route
sets no `road_friction` keeps the model unchanged (tire friction 1.6, the
model's own value, which already won over the World's 1.0), and so does an
`rc` Car (no route). The value is
written in one Hakoniwa step and applies from the next physics step.

### 4.5 Wind and rotor faults (Drone schedules)

A waypoint of a Drone schedule (`apps/drone/drone_schedule.py`, the
`schedule` control) may set a wind and/or a rotor fault on the leg from it
to the next point the Drone flies to:

```yaml
drones:
  - name: Drone-1
    zone_width_m: 2.0            # the zones round the legs (default 2 x 2 m)
    zone_height_m: 2.0
    waypoints:
      - {name: road-south, east_m: 20.2, north_m: -30.0, up_m: 35.0,
         wind: {towards_deg: 0.0, speed_m_s: 8.0},      # blows east on this leg
         zone_width_m: 8.0, zone_height_m: 6.0}         # this leg's zone
      - {name: road-north, east_m: 2.2, north_m: 60.0, up_m: 35.0,
         fault: {rotors: [0], scale: 0.0}}              # rotor 0 stops on this leg
```

The zone is a box round the leg: `zone_width_m` across it, `zone_height_m`
round its line (sheared along a climbing leg), half its width longer at
each end so a Drone holding at the waypoint is inside it. The schedule
watches the Drone's position (its `pos` PDU) and writes its `disturb` PDU
(hako_msgs/Disturbance; `apps/drone/flight_events.py`) when what applies
changes:

- wind: blows while the Drone is in the zone and stops when it leaves.
  `towards_deg` is where it blows to (Urban yaw: east 0, counter-clockwise).
- fault: the listed rotors (from 0, as the Viewer's fault panel numbers them)
  get the thrust `scale` (0 stopped, 1 nominal) once the Drone enters the
  zone, until the flight ends.

The Viewer's fault panel writes the same PDU, so leave it alone while a
schedule with zones flies. The zones are drawn see-through (cyan wind with
an arrow where it blows, red fault) on the Studio Flight tab map and 3D view
and in the Viewer's planned path.

## 5. Composition

### 5.1 Fields

```yaml
schema: hakoniwa.composition/v1
id: <composition id, becomes the Recipe id>
world: <World Asset id>                 # city or plain, section 6
vehicles:
  - name: <instance name, unique>
    asset: <vehicle Asset id>
    control: rc | api
    params: { <param>: <value> }          # optional, section 4.1
    spawn: { east_m, north_m, yaw_deg }   # section 5.4
    tire_grip: <number>                   # optional, Cars only, default 1.0 (section 4.4)
interactions: []                          # optional, section 5.3
viewer: { http_port, web_bridge_port }    # optional
```

The user places every vehicle explicitly; in the browser this is drag and
drop onto the World (section 5.5). The Composition therefore always lists each vehicle
instance, for Cars and Drones alike. Nothing generates vehicles implicitly:

- A Car route scenario (`api` param `scenario`) only drives vehicles. Every
  vehicle name the scenario references must be a `vehicles[]` entry that
  selected that scenario; the builder rejects unknown or missing names.
- A Drone launch point is the Drone's `spawn`. There is no automatic
  launch-point search.

### 5.2 Replacing a control program

```yaml
  - name: Drone-1
    asset: eams-hexa
    control: api
    program: ./my_patrol.py
    args: [ "--service-config", "${runtime.service_config}", "--route", "./patrol.json" ]
```

`program` and `args` replace the manifest defaults together. Relative paths
resolve against the Composition file.

### 5.3 Derived runtime

The builder derives the runtime from the Composition; the user does not list
it:

- Simulators to start: the set of `simulator` values of the selected vehicle
  Assets. One simulator means a standalone run; two mean both start and
  Hakoniwa synchronizes them.
- Control processes: one per `vehicle`-scoped control instance, one per
  distinct `composition`-scoped program.

Cross-simulator interactions (the current `drone_mirrors`: Drone mirror body
and contact impulse in the Car world) depend on the pair of Assets, not on one
Asset. They are optional Composition entries:

```yaml
interactions:
  - type: drone-mirror
    drone: Drone-1
    restitution_coefficient: 0.3
```

High-precision inter-Asset collision is not required (issue #5 non-goal).

### 5.4 Spawn height

The user gives only `east_m`, `north_m` (ENU, relative to the World origin)
and `yaw_deg`. The builder computes the height for every category:

```text
spawn up = ground height at (east_m, north_m) + Asset spawn.ground_clearance_m
```

Ground height is the top of the compiled World collision geometry at that
point, found by a downward MuJoCo ray: terrain and buildings for a City,
ground and obstacles for a plain World. A vehicle dropped on a rooftop starts
just above the roof. Visual-only geoms (no `contype` / `conaffinity`) are
left out of the ray: they are moved to a geom group no colliding geom uses,
which the ray does not see (starting again just below a visual-only geom
could start inside a colliding one under it, such as a line painted on a
road, and report that geom's underside); a model with every group taken
falls back to passing through them. The same rule applies to Cars and Drones; the builders
receive the result as an absolute height.

`tools/world_height.py` implements the ray on the City World MJCF named by
the receipt. The City World workflow converts PLATEAU into MJCF, but the ray
needs that MJCF compiled into a MuJoCo model, and a compile grows faster than
linearly with the mesh count (Shizuoka, 18k meshes: 116 s in one piece). The
height only needs the highest hit, so the World is split into up to 8 chunks
(meshes and primitive geoms spread evenly, the terrain in chunk 0) compiled
in parallel child processes; the ray is cast on every chunk and the highest
colliding hit wins (Shizuoka: 6.8 s, identical heights).

The compiled chunks are cached as MJBs under
`work/urban/cache/world-height/<fingerprint>-mujoco-<version>/`, keyed by the
MJCF, the files it references (issue #5 principle 3), and the MuJoCo version
(an MJB only loads in the version that wrote it). They belong to the City
Asset (section 6.1). Each entry's `manifest.json` records the MJCF path, which
is how `tools/urban_assets.py prune-cache` (`tools/urban_cache.py`) maps an
entry back to its World. The prune removes an entry when:

| Condition | Reason printed |
|---|---|
| its MJCF is under a City World Web UI job that no longer exists / whose City is not registered | `City World job deleted` / `City not registered` |
| its MJCF was deleted, or no longer has that fingerprint | `World MJCF deleted` / `World regenerated since` |
| it was written by another MuJoCo version and the running version has an entry with the same fingerprint (all other versions with `--other-mujoco-versions`) | `MuJoCo <v> (current <v>), superseded` |
| it is a `<key>.partial/` staging directory older than one hour | `abandoned compile` |
| its `manifest.json` is missing or unreadable | `manifest missing or unreadable` |

The version rule only removes superseded entries by default: run from an
interpreter with another MuJoCo (for example outside the Workspace shell), the
prune keeps the entries the Workspace uses. Names that are not cache entries
are left untouched. `--plain-world` also removes the plain-World MJCFs
(section 6.2), which are regenerated on demand. Nothing is removed without
`--apply`. The compile reports progress as plain lines and as
`[HAKO_PROGRESS] {"phase":"world_height_model","current":n,"total":m}`
events, the City World job progress format. MuJoCo Python is pinned to the
Drone Core version (`mujoco==3.13.0`) in the managed Recipe requirements, so
the Workspace Python that runs Urban Studio has it; without it, the height
falls back to the terrain hfield with a warning that rooftops are ignored.

The simulation itself still compiles the City together with the vehicles
into one MJB at `configure` (`multi_car.py`, `drone_one.py`); compiled
models cannot be merged afterwards, so the height model and the simulation
model are separate compiles.

Spawn edits do not recompile the World; like the current `spawn_pose_enu`,
they are applied to the runtime configuration before start.

### 5.5 Placement loop

The user iterates:

```text
place vehicles -> start -> observe -> stop -> adjust placement -> start ...
```

- A placement-only change (`spawn` of existing vehicles) needs no `configure`:
  `stop`, edit the Composition, `start`. The World model and generated Asset
  models are reused.
- Control params and replaced programs (section 5.2) also need no
  `configure`: every `start` regenerates the control processes.
- Adding or removing a vehicle, or changing its Asset or control (`rc` /
  `api`), requires `configure` before the next `start`.
- The browser does not check collisions while dragging. It shows the World's
  obstacles so the user can place around them; any remaining mismatch is found
  by running the simulation and adjusted in the next iteration.

Placement UI (Urban Studio, section 5.7):

| World | Coarse position | Fine position and yaw |
|---|---|---|
| `city` | Map view (OpenStreetMap): a click moves the selected vehicle there | Three.js view of the City GLB: drag the vehicle marker |
| `plain` | — (no map) | Three.js view of the whole plain World with its obstacles |

The map converts a click to ENU coordinates relative to the City origin
(from the City World Receipt). While a marker is dragged in the Three.js
view it rides on the World meshes; on release the backend height (section
5.4) gives the spawn height shown on the vehicle card. Yaw is set in 15°
steps or typed.

### 5.7 Browser UI (Urban Studio)

`tools/urban_studio.py` serves `web/` on `127.0.0.1` (default port 28090, the
root manifest's `urban-studio` port) with a JSON API over the tools of this
contract. Start it from the Business Pack Workspace shell so the simulations
it runs inherit the Workspace environment: `urban_studio.py` runs it in that
terminal (Ctrl+C stops it); `urban_studio.py start` runs it in the
background (its pid, port, and log under `work/urban/studio/`). `open` opens
the running one in the browser. `status` /
`stop` check and stop the Urban Studio on its port however it was started
(`stop` asks it to shut down with `POST /api/shutdown`, a JSON request, as
Ctrl+C would). Both ways of running check the port first: a running Urban
Studio is named with how to stop it (with `--open-browser` it is opened
instead), another program with how to pick another port. `GET /api/health`
names the Urban Studio answering. The tabs follow issue #5:

| Tab | Does | API |
|---|---|---|
| City | "Environment Studio で作る" starts Environment Studio through `tools/urban_city_authoring.py` (configure of `recipes/usecases/urban-city-authoring.yaml`, then of the Studio's own Recipe, then `env_studio.py start --export-dir <assets.studio_city_jobs>`) and opens its map page; "Environment Studio を停止" stops it. The Studio does not call Urban: every City World job it writes to the export folder (moved in when complete) is registered once (`urban_assets.py register-city --title <job.json title>`, which checks it, with the height model precompile), a job written again is registered again, and a City whose job was removed is unregistered. The page lists those jobs and the registered Cities, and warns when a running Studio writes elsewhere. The "キャッシュ" panel shows the Urban cache and what `prune-cache` would remove, and runs `urban_assets.py prune-cache --apply` (refused while another Studio command runs) | `GET /api/cities`, `POST /api/cities/environment-studio/start\|stop`, `GET /api/cache`, `POST /api/cache/prune` |
| Assets | lists World and vehicle Assets with their controls | `GET /api/assets` |
| Compose | edits a Composition: World, vehicles, control and params, placement (section 5.5) | `GET/PUT /api/compositions/<id>`, `GET /api/worlds/<id>[/glb\|/height]` |
| Route | edits Car route scenarios (waypoint loops) on the City World map, or by number for a plain World: points, dwell, speed, loops, and the Cars with their offsets. Saved under `work/urban/scenarios/<id>.yaml` with `meta.world`; examples in `recipes/scenarios/` are read-only and saving one writes a copy. In Compose an API Car picks its route from a selector (routes of the same World first) and warns when the route names no such Car or belongs to another World | `GET /api/scenarios`, `GET/PUT /api/scenarios/<id>` |
| Flight | edits a Drone flight (the `drones:` section of a YAML file, `apps/drone/drone_schedule.py`) on the City World map, with a 3D view of the World under it: the takeoff point (where the editor checks from; the Composition places the Drone), the waypoints with their height above the top of the World under each (ground or roof), speed, hold and yaw, and where it lands (back at the takeoff point, elsewhere, or not). Saving turns the heights into the World heights the schedule flies. Every leg is ray-cast on the World collision geometry along its centre and 1.5 m to each side, 1 m above and 1.5 m below (`tools/flight_check.py`); a blocked leg is red on the map and in 3D. Saved under `work/urban/scenarios/<id>.yaml` with `meta.world` and `meta.takeoff`. In Compose a Drone with the `schedule` control picks its flight from a selector, opens it in the Flight tab, and offers to move the Drone to the flight's takeoff point | `GET /api/flights`, `GET/PUT/DELETE /api/flights/<id>`, `POST /api/worlds/<id>/flight-check` |
| Simulation | runs `configure`, `start`, `stop`, `status` with live output and progress, and embeds the Viewer; the selector labels each Composition with its World and vehicle make-up and shows a summary (World, vehicles and control, route, save time) | `POST /api/compositions/<id>/<command>`, `GET /api/jobs/<job>`, `GET /api/compositions` |

A City World job counts as finished once its
`artifacts/result-manifest.json` exists (the Worker writes it last); a
registered City whose receipt changed (regenerated) is registered again.
Commands run as child processes, one at a time per Composition; their
`[HAKO_PROGRESS]` lines drive the progress bar. Examples come from
`recipes/compositions/` and from other workspace repositories'
`assets/*.composition.yaml` (the root manifest's `compositions`); an example
is listed only while every Asset it names (its World, vehicles, fleets) is in
the catalog, so the Shizuoka examples appear once that City is registered and
a repository's examples once it is checked out. They are read-only; saving
one writes an editable copy under `work/urban/compositions/`. A saved
Composition naming something the workspace lacks stays listed and says what
(`available`, `missing` in `GET /api/compositions`). A City whose receipt
says `"kind": "plain"` (an environment made without a map) is shown without
a map (`GET /api/worlds/<id>`: `map` false, no `origin`). Placement-only fields (the ground height
under a vehicle) are never saved.

Compose and Simulation share one Composition: opening or saving a
Composition in Compose selects it in Simulation, and the last one used is
reopened after a reload (browser storage). Simulation runs the saved file,
so it warns while Compose holds unsaved edits of that Composition.
"Compose で開く" (Simulation) and "Simulation へ" (Compose) move between the
two; opening another Composition asks before discarding unsaved edits.


### 5.6 Real-time pacing

Every route runs one real-time pacer (`apps/realtime/realtime_pacer.py`,
adapted from hakoniwa-fpv-drone's): a Hakoniwa asset whose time follows the
wall clock. The Conductor advances world time only while every asset is
within its max_delay, so the pacer bounds the whole simulation, Car and
Drone alike, to real time on every OS. The simulators therefore do not sleep
on their own: the Car plant runs with `realtime_sync_cycle_msec: 0`, and
Drone services with `--real-sleep-msec 0`. (Before, the Car plant slept
per sync cycle, which is coarse on Windows, and the Urban Drone routes had
no pacing once the Drone Show runner was replaced by the controls.)

`tools/urban_realtime.py` inserts the pacer (`urban-realtime-pacer`,
`before_start`, delta 10 ms) right after the Conductor owner, with that
owner's max_delay: the Car plant (100 ms) in the `car` route, the Drone
service (20 ms) in the `drone`, `integrated`, and `fpv` routes. It is
applied with the controls, after `configure` and at every `start`; in the
`fpv` route it replaces tools/fpv.py's own pacer, so one implementation paces
every route.

## 6. World Asset

Every Composition selects exactly one World Asset. There are two kinds; both
wrap an existing artifact instead of redefining it.

### 6.1 City (`kind: city`)

```yaml
schema: hakoniwa.asset/v1
id: hokkaido-01100-lat43.062-lon141.355
kind: city
version: <receipt build id>
receipt: <path to city-world-receipt.json>
```

The receipt remains the source of MJCF, GLB, origin, extent, and coordinate
systems. The City step of the browser produces a receipt through the existing
City World workflow (Business Pack / `hakoniwa-envsim`), then registers this
manifest.

A registered City is ready for placement: registration
(`urban_assets.py register-city`) also compiles the City height model of
section 5.4, reporting progress, so the placement loop only loads it.
`--no-precompile` defers it to the first `configure`. A later option is for
the City World workflow itself to emit that model and list it in the
receipt.

### 6.2 Plain (`kind: plain`)

A plain World is a ground plane with optional obstacles and no map. It uses
the existing FPV World YAML (`hakoniwa-fpv-drone/docs/fpv-world.md`): sky,
lights, ground size and color, contact parameters, and `gate` / `pylon` /
`box` obstacles, all with MuJoCo collision.

`plain` is a kind, not one World: any environment the user prepared
beforehand (the FPV training course, a custom World YAML) is a plain World
Asset, and a Composition that selects neither a City nor an environment
uses the default `plain-ground` (this repository's
`worlds/plain-ground.yaml`: open flat ground, 200 m x 200 m, no obstacles).
`tools/urban_assets.py register-world --world <World YAML> [--id <id>]`
registers an environment under `work/urban/assets/worlds/`; like
`register-city` it also prepares the World (below) unless
`--no-precompile`.

Cars and the EAMS Hexa run on a plain World through a generated City World
job (`tools/plain_world.py`, cached under `work/urban/worlds/`): the ground
becomes a flat hfield terrain, the obstacles keep the FPV generator's
geometry (yaw as quaternions, which no compiler angle setting of the MJCF they
are composed into can reinterpret), a GLB of
the same geometry serves Three.js and the collider view, and the receipt
carries `"kind": "plain"` with no geographic origin. The City routes then run
unchanged; for a plain receipt the Car and integrated viewers open Three.js
directly instead of the Map Viewer. The FPV Drone keeps its own route
(`fpv`) on plain Worlds, because tools/fpv.py already generates the vehicle
on the World YAML; in a City the same route composes the vehicle into the
City World (section 7.1, step 3.4 D).

```yaml
schema: hakoniwa.asset/v1
id: fpv-training-course
kind: plain
version: 0.1.0
world: ${repo:hakoniwa-fpv-drone}/recipes/environments/fpv-training-course.yaml
```

A World YAML without `obstacles` is the empty plain World. The same YAML also
produces the Three.js course (`fpv-course.json`), so the placement view shows
the same obstacles the physics uses.

The World YAML uses MuJoCo world coordinates; the contract uses ENU with the
World origin at the MuJoCo origin. The builder converts between them
(MuJoCo `x` = north, `y` = -east, as in the current City tooling).

For spawn heights (section 5.4) the FPV generator writes the World alone
(`generate_world_mujoco()`: ground and obstacles, the same geometry it
merges into the FPV vehicle model) to `work/urban/cache/plain-world/`, keyed
by the YAML and the generator source (`prune-cache --plain-world` clears it); the ray then lands a vehicle on the
ground or on an obstacle top. Without MuJoCo Python the height is the flat
ground.

### 6.3 City World job

Both kinds reach the builders as a City World job: the folder holding the
receipt, the World MJCF and GLB, the terrain hfield and the collider view.
[`schemas/city-world-job.yaml`](../schemas/city-world-job.yaml) is its
machine-readable contract, written from what the builders read:

| Part | Rule |
|---|---|
| Layout | `<job>/build/world/city-world-receipt.json` and `<job>/viewer/city-world-colliders.glb` (the collider view, made by hakoniwa-envsim's `mjcf_colliders2glb.py`); the job folder's name is the default City Asset id |
| Frames | the receipt's `coordinate_frame.coordinate_systems` are exactly `X=North,Y=-East,Z=Up` (MJCF) and `X=East,Y=Up,Z=-North` (GLB), both centred on `coordinate_frame.origin` |
| Receipt | `schema_version` 1, origin (latitude, longitude, altitude offset), half extents, `mjcf.path`, `glb.path`, `components.terrain_xml`; paths absolute; `kind` `city` (default) or `plain`; hashes checked when given |
| Terrain | `terrain-receipt.json` beside `components.terrain_xml` names the hfield: little-endian `int32 nrow, int32 ncol`, then `nrow x ncol` float32 altitudes (MJCF z = altitude - altitude offset) |
| World MJCF | only `size`, `asset`, `worldbody` at the top level (no `compiler`: an euler stays in degrees in every model it is composed into); the World brings its own ground as an hfield geom and no lights (each vehicle model keeps its own, named `sun`, `fill_light`, ...); no names the builders reserve for vehicles (`car_<n>_`, `vehicle_type_<n>_`, `mirror_drone_<n>_`) |
| Serving | the job lies under the workspace root the viewers serve |

`tools/city_world_job.py check <job folder | receipt> [--json]` checks a job
against the contract (errors fail; warnings, such as a missing
`buildings-glb-receipt.json` for the Drone city-max-clearance launch height,
pass). Any producer (the City World Web UI, `tools/plain_world.py`, an
authoring tool exporting its own World) runs it before registering.

## 7. Mapping from the existing Recipes

| Concept | `urban-car-one` | `urban-drone-one` | `drone-car-rc` | Contract |
|---|---|---|---|---|
| City | `inputs.business_pack_city_receipt.path` | `city_world.receipt` | `inputs.business_pack_city_receipt.path` | `world` → City World Asset |
| Vehicle model | `ackermann_vehicles.types[]` | `drone.profile` | `types[]` + `drone_mirrors[].mjcf_model` | `vehicles[].asset` → manifest `model` |
| Control | `control_mode: ps5` | `control.mode: ps4-rc` | `control_mode: external_python` | `vehicles[].control: rc / api` |
| API input | — | `mission.path` | `scenarios.car` | `vehicles[].params` |
| Spawn | `spawn_pose_enu` + `ground_clearance_m` | `spawn_pose_enu` + `up_m`, `launch_area` | `up_m`, `initial_position_mjcf` | `vehicles[].spawn` (east/north/yaw) + computed height |
| Vehicle list | `vehicles[]` | one Drone | `vehicles.generated_from_route` | explicit `vehicles[]` |
| Mirror | — | — | `drone_mirrors[]` | `interactions[]` |

Dropped by the contract: `launch_area` (the user places the Drone),
`vehicles.generated_from_route` (the user places the Cars), and absolute
heights (`up_m`, `initial_position_mjcf`; section 5.4). The existing
experiment Recipes keep them until they are migrated.

### 7.1 Migration steps

The migration keeps a running reference at every step:

1. Adapter: translate a Composition into the current tool inputs
   (`multi_car.py` composition, `drone_one.py` recipe) without changing the
   tools' internals. Order: City + Car, City + Drone, City + Car + Drone.
   All three are adapted by `tools/urban_composition.py` behind
   `urban_mobility.py <command> --composition <file>`, which selects the
   managed Recipe from the Composition simulators:

   | Composition | Tool | Managed Recipe |
   |---|---|---|
   | City + Car | `multi_car.py` | `recipes/usecases/urban-car-rc.yaml` |
   | City + one Drone | `drone_one.py` (own workspace) | — |
   | City + Car + one Drone | `urban_composer.py` | `recipes/experiments/urban-mobility-rc.yaml` |

   Every adapter re-reads the Composition at `start`, so placement-only
   edits need no `configure` (section 5.5); other edits are rejected before
   anything is written.

   Small tool changes made for explicit placement (existing Recipes keep
   their behavior):
   - `multi_car.py` accepts `ackermann_vehicles.route_scenario` for explicit
     vehicles; the scenario auto-starts and drives only the vehicles it
     names, which must be `external_python`. This makes Car `api` adaptable.
   - `urban_composer.py` accepts explicit Cars, an optional Car scenario,
     zero or one Drone Mirror, and merges every selected Car control
     (scenario executor and PS5 senders).

   The Drone adapters pass the spawn surface to the tools under their
   existing `rooftop` field name.
2. Parity: for each of the three combinations, the Composition path must
   generate the same Launcher configuration as the current Recipe (modulo
   paths and ids), and run.
3. Builders, in sub-steps:
   1. Control processes from manifest controls (done): the adapters replace
      the tools' control assets with the processes of section 4, after
      `configure` and at every `start`. `drone_one.py` applies them from
      `urban-composition-controls.json` whenever it writes its Launcher.
      This enables Car `rc` params, program replacement (section 5.2), and
      Drone `api` together with Cars (`urban_composer.py` now takes the
      Drone control from the Drone recipe instead of forcing PS4 RC). The
      generated processes match the tools' previous ones (tested per
      control).
   2. Ray-based spawn height on the World (done, section 5.4): Cars and
      Drones start on rooftops as well as on the ground. On open ground
      the ray equals the previous terrain sampling.
   3. Plain World + FPV Drone (done): `urban_mobility.py <command>
      --composition` runs a plain World + one FPV Drone through
      `hakoniwa-fpv-drone/tools/fpv.py` (output under `work/urban/fpv/<id>/`).
      A vehicle whose manifest has `source.generator.tool: tools/fpv.py` is
      an FPV Drone. `configure` generates the vehicle on the World YAML with
      the manifest's Assembly Graph and the Three.js viewer; `configure` and
      every `start` write the spawn into the runtime `drone_config_0.json`
      (`droneDynamics.position_meter` = [north, east, -up], NED yaw) and
      replace `fpv-remote-controller` with the manifest control. A different
      World or FPV Asset needs `configure`. The adapter rejects a manifest
      `ground_clearance_m` that differs from the generated report's
      `initial_pose.mujoco_z_m`.
   4. Consolidation and coverage, in parts:
      - A. Simulation model build progress (done): Business Pack
        `mujoco_model_compiler` reports `[HAKO_PROGRESS]` phase
        `mujoco_compile` (start, 10 s heartbeats, done) for every Urban
        simulation MJB.
      - B. One Composition API (done): `tools/urban_simulation.py`
        (`plan`, `run`, section 2.1). The adapter-only defaults
        (`launch_area`, the RC mission file, the `rooftop` field) stay
        inside it and `tools/urban_composition.py`.
      - C. Plain Worlds for Cars and the EAMS Hexa (done, section 6.2).
      - D. The FPV Drone in a City (done): the `fpv` route generates the
        vehicle with tools/fpv.py on open ground (`worlds/plain-ground.yaml`),
        composes its `drone_base` body with the City MJCF
        (hakoniwa-mbody-registry `compose_mujoco_world.py`, as for the Urban
        Hexa), compiles the result once into an MJB that Drone Core loads
        (`droneDynamics.mujoco.modelPath`; reused while the composed model is
        unchanged), and shows the City GLB in Three.js. Shizuoka: 119 s to
        compile, spawn on a 21 m rooftop verified.
      Rewriting the builders' internals to read the Composition directly is
      deferred: the adapters are tested for parity, so it would add risk
      without adding capability.
   5. Several Drones per Composition.
4. Browser UI (section 5.7), in steps:
   - UI-1 (done): Assets, Compose (form editing), Simulation (lifecycle
     jobs with output and progress, embedded Viewer).
   - UI-2 (done): placement in Compose: Three.js drag, City map click, and
     the backend spawn height per vehicle.
   - UI-3 (done): City: start the City World Web UI and register finished
     City World jobs automatically.

   Remaining limits: one Drone per Composition, the FPV Drone only alone
   (no Cars or other Drones with it), and the `eams-nominal-9kg` profile for
   the other City Drone. The Drone-only route still opens the Map Viewer on
   a plain World (with no map origin). Drone `api` together with Cars is
   configured but not yet run end to end.

## 8. Examples

### 8.1 Golf Cart (`assets/golf-cart.asset.yaml`)

```yaml
schema: hakoniwa.asset/v1
id: golf-cart
kind: vehicle
category: car
version: 0.1.0
title: Generic Ackermann Golf Cart
source:
  repository: hakoniwa-mbody-registry
simulator: ackermann-mujoco
model:
  physics: ${repo:hakoniwa-mbody-registry}/bodies/generic_ackermann_golf_cart/generated/model.minimal_world.xml
  contract: ${repo:hakoniwa-mbody-registry}/bodies/generic_ackermann_golf_cart/config/ackermann-forge.yaml
  visual: ${repo:hakoniwa-mbody-registry}/bodies/generic_ackermann_golf_cart/generated/view-model.json
spawn:
  ground_clearance_m: 0.45
controls:
  rc:
    program: ${repo:hakoniwa-urban-mobility}/apps/car/ps5_ackermann_sender.py
    scope: vehicle
    args: [ "--pdu-def", "${runtime.pdu_def}", "--rc-config", "${runtime.rc_config}",
            "--robot", "${vehicle.name}", "--pdu", "ackermann_cmd",
            "--max-speed", "${param.max_speed}" ]
    params:
      max_speed: { type: number, default: 3.5 }
  api:
    program: ${repo:hakoniwa-urban-mobility}/apps/car/scenario_executor.py
    scope: composition
    args: [ "${param.scenario}", "--pdu-def", "${runtime.pdu_def}" ]
    params:
      scenario: { type: path, required: true, kinds: [car-route-scenario] }
```

### 8.2 EAMS Hexa (`assets/eams-hexa.asset.yaml`)

```yaml
schema: hakoniwa.asset/v1
id: eams-hexa
kind: vehicle
category: drone
version: 0.1.0
title: Urban EAMS Hexa (nominal 9 kg)
source:
  repository: hakoniwa-urban-mobility
  profile: eams-nominal-9kg
simulator: drone-core
model:
  physics: ${repo:hakoniwa-urban-mobility}/config/drone/hexa/drone.xml
spawn:
  ground_clearance_m: 0.3      # example; set from the model's landing gear
pdu:
  definition: ${runtime.pdu_def}
controls:
  rc:
    program: ${repo:hakoniwa-drone-core}/drone_api/rc/rc-custom.py
    scope: vehicle
    args: [ "${runtime.pdu_def}", "${repo:hakoniwa-drone-core}/drone_api/rc/rc_config/ps4-control.json" ]
  api:
    program: ${repo:hakoniwa-urban-mobility}/apps/drone/city_fleet_mission.py
    scope: vehicle
    args: [ "--drone-root", "${runtime.drone_root}",
            "--service-config", "${runtime.service_config}",
            "--city-marker", "${runtime.city_marker}",
            "--mission", "${param.mission}",
            "--summary-json", "${runtime.summary_json}" ]
    params:
      mission: { type: path, required: true, kinds: [drone-mission] }
```

### 8.3 FPV Drone (`hakoniwa-fpv-drone/assets/fpv-drone-master3x.asset.yaml`)

```yaml
schema: hakoniwa.asset/v1
id: fpv-drone-master3x
kind: vehicle
category: drone
version: 0.1.0
title: FPV Drone (Master3X)
source:
  repository: hakoniwa-fpv-drone
  generator:
    tool: tools/fpv.py
    assembly: recipes/examples/master3x-visual-demo.assembly.json
simulator: drone-core
spawn:
  # The generated report's initial_pose.mujoco_z_m for this assembly.
  ground_clearance_m: 0.016
controls:
  rc:
    program: ${repo:hakoniwa-fpv-drone}/tools/fpv_rc_bootstrap.py
    interpreter_args: ["-u"]
    scope: vehicle
    cwd: ${repo:hakoniwa-fpv-drone}
    args: ["${runtime.pdu_def}", "${repo:hakoniwa-drone-core}/drone_api/rc/rc_config/ps4-control.json",
           "--rc-root", "${repo:hakoniwa-drone-core}/drone_api/rc"]
```

The vehicle model is generated, so the manifest has no `model` paths.

The FPV Drone is an `rc`-only Asset; the browser offers no `api` control for it.

### 8.4 Composition: City + Car + Drone

```yaml
schema: hakoniwa.composition/v1
id: urban-drone-car-rc
world: hokkaido-01100-lat43.062-lon141.355
vehicles:
  - name: Car-1
    asset: golf-cart
    control: api
    params: { scenario: recipes/scenarios/golf-cart-demo-loop.yaml }
    spawn: { east_m: 0.0, north_m: 0.0, yaw_deg: 0.0 }
  - name: Drone-1
    asset: eams-hexa
    control: rc
    spawn: { east_m: 5.0, north_m: -45.0, yaw_deg: 0.0 }
interactions:
  - type: drone-mirror
    drone: Drone-1
    restitution_coefficient: 0.3
```

### 8.5 Composition: plain World + FPV Drone

```yaml
schema: hakoniwa.composition/v1
id: fpv-course-rc
world: fpv-training-course
vehicles:
  - name: Drone-1
    asset: fpv-drone-master3x
    control: rc
    spawn: { east_m: 0.0, north_m: 0.0, yaw_deg: 0.0 }
```

## 9. Open items

- `${runtime.*}` names: fix the list per simulator once the builders are
  refactored.
- JSON Schema files for `hakoniwa.asset/v1` and `hakoniwa.composition/v1`,
  aligned with the Business Pack `schemas/` conventions.
- The compiled-model cache fingerprint (issue #5 principle 3) for the
  simulation MJBs; the World height model already has one (section 5.4).
- The Compose phase (issue #5) owns the simulation model build: the single
  World + vehicles MJB compile at `configure` reports progress in the same
  `[HAKO_PROGRESS]` format as the City height model (section 5.4).
