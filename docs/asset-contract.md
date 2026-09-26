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
  `tools/urban_mobility.py configure|start|status|open-viewer|stop --composition <file>`.
- A Car-only Composition materializes only the Car dependencies; the Recipe
  never requires the union of all Assets.
- The Recipes in `recipes/experiments/` retire once their Composition
  reproduces them (section 7.1).

## 3. Asset manifest

### 3.1 Placement

The target placement is the Asset's source repository. For v1:

- `hakoniwa-fpv-drone` owns its manifest (`assets/<id>.asset.yaml`).
- All other manifests live in this repository under `assets/`, and point into
  their source repositories (`hakoniwa-mbody-registry`, `hakoniwa-drone-core`,
  ...). They move to their source repositories later without changing the
  schema.
- User-generated City Assets live in the Business Pack work directory,
  `work/urban/assets/cities/<id>.asset.yaml`, written by
  `tools/urban_assets.py register-city --receipt <city-world-receipt.json>`.
  The id defaults to the City World job name.

`tools/urban_assets.py list` prints the catalog (both directories).

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

### 4.1 A control is a program with arguments

Car and Drone cannot share one control program, so each control declares the
program the Launcher starts and its argument template:

```yaml
controls:
  api:
    program: ${repo:hakoniwa-urban-mobility}/apps/car/scenario_executor.py
    scope: composition           # vehicle | composition (section 4.3)
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
runner: python        # python (default) | executable
```

Python control programs must not rely on the current directory or the script
directory being on `sys.path`: the Windows portable package runs an embedded
Python whose `._pth` file excludes both.

### 4.3 Scope

- `vehicle`: one program instance per vehicle (every `rc` control; Drone
  `api`).
- `composition`: one program instance drives all vehicles of that Asset that
  selected this control. The Car route scenario executor is this type: the
  scenario file lists the vehicles it drives. The builder rejects a
  Composition where those vehicles do not all select the same `api` program.

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
passed through. The same rule applies to Cars and Drones; the builders
receive the result as an absolute height.

`tools/world_height.py` implements the ray on the City World MJCF named by
the receipt. The compiled model is cached as an MJB under
`work/urban/cache/world-height/`, keyed by a fingerprint of the MJCF and the
files it references (issue #5 principle 3). A City compiles once, which can
take minutes; `urban_assets.py register-city` does it at registration
(`--no-precompile` skips it), so the placement loop only loads the cache.
Without MuJoCo Python (the managed Recipe configure installs it), the
height falls back to the terrain hfield with a warning that rooftops are
ignored.

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

Placement UI:

| World | Coarse position | Fine position and yaw |
|---|---|---|
| `city` | Map Viewer (select the area on the map) | Three.js view with City GLB and collider overlay |
| `plain` | — (Map Viewer has no map) | Three.js overview of the whole plain World with its obstacles |

The Map Viewer hands the selected point to the Three.js view as ENU
coordinates relative to the City origin (from the City World Receipt).

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

### 6.2 Plain (`kind: plain`)

A plain World is a ground plane with optional obstacles and no map. It uses
the existing FPV World YAML (`hakoniwa-fpv-drone/docs/fpv-world.md`): sky,
lights, ground size and color, contact parameters, and `gate` / `pylon` /
`box` obstacles, all with MuJoCo collision.

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
   3. Plain World + FPV Drone.
   4. Builders read the Composition directly; the adapter-only defaults
      (`launch_area`, the RC mission file, the `rooftop` field) go away.
   5. Several Drones per Composition.

   Remaining limits: one Drone per Composition, the `eams-nominal-9kg` Drone
   profile only, and City Worlds only. Drone `api` together with Cars is
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
  generator: tools/fpv.py configure --assembly recipes/examples/master3x-visual-demo.assembly.json
simulator: drone-core
model:
  physics: ${runtime.vehicle_dir}/drone.xml
  visual: ${runtime.threejs_dir}/assets/manifest.json
spawn:
  ground_clearance_m: 0.05     # example; set from the model's landing geometry
controls:
  rc:
    program: ${repo:hakoniwa-fpv-drone}/tools/fpv_rc_bootstrap.py
    scope: vehicle
    args: [ "${runtime.pdu_def}", "${repo:hakoniwa-drone-core}/drone_api/rc/rc_config/ps4-control.json",
            "--rc-root", "${repo:hakoniwa-drone-core}/drone_api/rc" ]
```

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

- Plain World builders: today only the FPV generator consumes the World YAML
  (it merges the course into the FPV Drone MJCF). The Car plant and the Urban
  Drone builder need a plain World path equivalent to their City World path.
- `${runtime.*}` names: fix the list per simulator once the builders are
  refactored.
- JSON Schema files for `hakoniwa.asset/v1` and `hakoniwa.composition/v1`,
  aligned with the Business Pack `schemas/` conventions.
- The compiled-model cache fingerprint (issue #5 principle 3).
