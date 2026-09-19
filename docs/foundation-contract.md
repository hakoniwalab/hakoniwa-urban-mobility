# Urban Mobility Recipe / Foundation contract

## 1. Status and scope

This document fixes the ownership and runtime contract required by
[`foundation-task.md`](foundation-task.md) Step 1. It is the target contract
for the migration; current tools may still violate it until their respective
steps are complete.

The first integrated target is:

```text
Recipe-selected City World (the first regression uses Shizuoka)
  + 1 EAMS Hexa Drone controlled by PS4 RC
  + N Golf Carts controlled by a route scenario
  + 1 Launcher
  + 1 Conductor owner
  + 1 WebBridge endpoint
  + 1 HTTP server
  + 1 Three.js / Map Viewer UI
```

Mirror and Impulse behavior may be reused but is not redesigned by this
contract.

## 2. Recipe identities

Three executable topologies remain distinct. They may share builder functions
but must not share generated files or Launcher sessions.

| Recipe ID | Purpose | Workspace |
| --- | --- | --- |
| `urban-car-one` | One-Car regression and PS5 operation | `${HAKONIWA_WORK_DIR}/recipes/urban-car-one` |
| `urban-drone-one` | One-Drone regression and PS4 operation | `${HAKONIWA_WORK_DIR}/recipes/urban-drone-one` |
| `urban-mobility-rc` | One PS4 Drone plus scenario-driven multiple Cars in a selected City | `${HAKONIWA_WORK_DIR}/recipes/urban-mobility-rc` |

`${HAKONIWA_WORK_DIR}` is resolved by the Business Pack workdir contract. Its
default is `<business-pack-root>/work`. Urban code must not recreate this
resolution by concatenating repository-relative strings.

The single-vehicle Recipes are retained as migration and diagnosis fixtures.
The integrated Recipe is the user-facing base for the next demo.

## 3. Configuration layers

### 3.1 Urban managed Recipe manifest

The Urban-owned manifest uses the Business Pack Recipe engine and owns the
managed execution contract:

- Recipe ID and human-readable goal
- Foundation requirements
- Recipe-local source requirements
- target and execution environment
- agency and license boundary
- standard Recipe workspace and environment materialization
- entrypoint into the Urban operator

It does not own a concrete City job, vehicle route, PID gain, or controller
mapping.

Manifest:

```text
hakoniwa-urban-mobility/recipes/experiments/urban-mobility-rc.yaml
```

### 3.2 Urban composition config

The Urban config owns the concrete system composition:

- City World Receipt
- Drone type, count, spawn pose, control mode, and PID source
- Car types, count, spawn policy, and control mode
- selected Car scenario
- simulation cycle settings
- Viewer layout, camera, Collider, and port settings

Target config:

```text
hakoniwa-urban-mobility/recipes/experiments/
  urban-mobility-rc.yaml
```

### 3.3 Urban scenario

The scenario owns vehicle behavior rather than system composition:

- named Car routes
- ordered waypoints
- forward and return segments
- target speed
- dwell time at each endpoint
- initial route phase and launch delay per Car

Target scenario:

```text
hakoniwa-urban-mobility/recipes/scenarios/
  shizuoka-road-shuttle.yaml
```

The scenario does not select Foundation components, City artifacts, Drone
models, browser ports, or Launcher ownership.

## 4. Source-of-truth boundary

| Data | Source of truth | Generated consumer |
| --- | --- | --- |
| Foundation capabilities and limits | Business Pack Recipe manifest | generated `foundation-requirements.yaml` and Foundation evaluator |
| installed Foundation state | installed Component Receipts | `plan` and `doctor` evaluation |
| City coordinate and artifact contract | City World Receipt | Car and Drone composers, Viewer config |
| Drone vehicle and physics | Drone PRO EAMS source model/config | Recipe-local Drone MJCF/MJB and type config |
| Drone PID tuning | Urban tracked PID file selected by composition config | Recipe-local `controller-params.txt` at `start` |
| Drone RC mapping | Drone PRO tracked RC mapping selected by composition config | PS4 controller asset arguments |
| Car physical/view model | MBody registry tracked model contract | Recipe-local Car MJCF/MJB and Viewer asset |
| Car count and type selection | Urban composition config | manifest, PDU definition, Launcher assets |
| Car routes and timing | Urban scenario file | scenario executor runtime input |
| initial poses | Urban composition/scenario inputs | Fleet and plant runtime state at `start` |
| PDU, Bridge, Viewer, Launcher config | no hand-edited generated source | Recipe workspace `config/` |
| runtime logs and validation | runtime execution | Recipe workspace `logs/` and `validation/` |

Generated files under `work/` must never be documented as the file to edit.
Every supported change must point to one tracked source-of-truth file.

## 5. Foundation and Recipe workspace boundary

Shared Foundation paths:

```text
${HAKONIWA_WORK_DIR}/foundation/install
${HAKONIWA_WORK_DIR}/foundation/config
${HAKONIWA_WORK_DIR}/foundation/runtime
${HAKONIWA_WORK_DIR}/foundation/build
```

Recipe-local paths:

```text
${HAKONIWA_WORK_DIR}/recipes/<recipe-id>/config
${HAKONIWA_WORK_DIR}/recipes/<recipe-id>/assets
${HAKONIWA_WORK_DIR}/recipes/<recipe-id>/missions
${HAKONIWA_WORK_DIR}/recipes/<recipe-id>/logs
${HAKONIWA_WORK_DIR}/recipes/<recipe-id>/runtime
${HAKONIWA_WORK_DIR}/recipes/<recipe-id>/validation
```

Foundation owns installed executables, libraries, Python, common Core config,
shared mmap runtime, and installed Receipts. A Recipe owns its PDU topology,
vehicle composition, Bridge forwarding, Viewer scene, Launcher, session, and
behavior evidence.

An independent Recipe workspace prevents generated-file overwrites. It does
not permit concurrent simulation through the shared Core runtime. Only one
Recipe may own the shared runtime unless a later design explicitly introduces
a supported isolation mechanism.

## 6. Runtime ownership

### 6.1 Integrated topology

```text
Business Pack PDU Python Launcher
  |
  +-- Drone PRO service (Drone-1 physics + built-in Conductor owner)
  |
  +-- PS4 RC controller (Drone-1 command producer)
  |
  +-- Urban Car Fleet plant (all real Cars, external Conductor mode)
  |
  +-- Car scenario executor (commands for all route-driven Cars)
  |
  +-- visual-state publisher / state adapter
  |
  +-- one Recipe-local WebBridge
  |
  `-- one workspace HTTP server
```

Ownership table:

| Concern | Owner | Required rule |
| --- | --- | --- |
| Launcher process lifecycle | Foundation-installed PDU Python Launcher | exactly one Launcher session per Recipe execution |
| simulation time / Conductor | first Drone PRO service process | exactly one Conductor owner |
| Drone physics | Drone PRO service | one real Drone in the first integrated target |
| Drone RC command | PS4 RC controller asset | targets only `Drone-1` |
| Car physics | one Urban Car Fleet plant | one MuJoCo model and step for every real Car |
| Car commands | one scenario executor or explicit controller per Car | unique robot/PDU namespaces |
| dynamic cross-world proxies | owning simulator's Mirror plant | mirrors never become a second real physics owner |
| browser state transport | one Recipe-local WebBridge | forwards both Drone and Urban Fleet state |
| web content | one Recipe-local HTTP server | serves Map Viewer, Three.js, config, and generated assets |
| visual observation | one Map Viewer / Three.js client | combined City, Drone, Cars, map, and attached camera |

The Car plant must join the Drone-owned simulation clock and must not start a
second Conductor.

### 6.2 Ports

Default ports are Recipe inputs with the following owners:

| Port | Purpose | Runtime owner |
| --- | --- | --- |
| `8000` | HTTP assets and Viewer | Recipe HTTP server |
| `8765` | Fleet WebSocket frames | Recipe WebBridge |
| `54111` | Launcher control endpoint | PDU Python Launcher |

`start` must check all configured ports before mutating the shared runtime.
`open-viewer` must verify the selected Recipe session and its HTTP endpoint; a
listener owned by another Recipe is an error, not readiness.

## 7. Reused Drone Fleet builder contract

Urban may reuse Drone Fleet generation, but not another Recipe's identity or
workspace. The reusable builder must receive explicit inputs equivalent to:

```text
workspace
foundation install/config/runtime paths
experiment definition
Drone source root
Viewer source root
City Receipt
Launcher composition policy or post-generation hook
```

It must return explicit generated artifact paths and validation metadata. It
must not:

- read a module-global Recipe ID to select its output directory
- replace another module's function at import time
- write into `drone-fleet-single-host` when invoked for Urban
- start Foundation builds as a hidden configure side effect
- treat a sibling Git checkout as proof of installed Foundation state

The original Drone Fleet Recipe must use the same extracted builder so that
Urban does not maintain a forked copy.

## 8. Lifecycle command contract

| Command | Responsibility | Must not do |
| --- | --- | --- |
| `plan` | resolve sources, Foundation requirements, workdir, and intended actions | build, configure, or start assets |
| `doctor` | inspect installed Receipts, required artifacts, generated config, ports, and compatibility | silently rebuild Foundation |
| `configure` | materialize Recipe-local PDU, physics, Bridge, Viewer, Launcher, guide, and validation inputs | start runtime or edit tracked source |
| `start` | runtime preflight, apply start-time pose/PID values, clean permitted stale state, launch assets, wait for Launcher RUNNING and required readiness | recompile City models or launch another Recipe's session |
| `status` | report selected Recipe session, asset/readiness summary, and paths | fall back to a different Recipe's session file |
| `stop` | terminate only the selected Recipe session and verify child/port cleanup | terminate unrelated processes by broad pattern |
| `open-viewer` | verify selected Recipe HTTP readiness and open its generated URL | open a URL merely because port 8000 responds |

The three states below remain distinct:

```text
Foundation SATISFIED
  -> Launcher session RUNNING
  -> Demo Ready
```

`Demo Ready` for the integrated target requires live Drone and Car state, HTTP
response, WebSocket upgrade/data, and an operator handoff for PS4 control.

## 9. Foundation requirements versus Urban configuration

Foundation requirements declare only reusable capabilities and required
limits. Initial required components are:

- `hakoniwa-core-pro`
- `hakoniwa-pdu-python`
- `hakoniwa-pdu-endpoint`
- `hakoniwa-pdu-bridge-core`

Exact build limits are derived from the configured topology before Foundation
evaluation. They are not copied from a convenient installed Receipt.

Urban composition retains:

- City and model selection
- Car/Drone counts and names
- PDU topology inputs
- scenario and controller choice
- physics composition
- camera and Viewer layout
- Launcher asset dependencies

## 10. Migration compatibility rules

- A migration Step must preserve the Step 0 baseline before adding new behavior.
- One-Car and one-Drone Recipes may temporarily wrap old generators, but their
  output paths must conform before the integrated Recipe is built.
- Compatibility aliases must have a removal condition and must not keep two
  sources of truth alive indefinitely.
- Tracked PID, scenario, and composition inputs may be copied into a workspace;
  edits made only to the generated copies are unsupported.
- Absolute paths may appear in generated runtime files, but tracked inputs and
  path resolution must remain relocatable.
- Existing user-owned dirty files in sibling repositories are preserved.

## 11. Step 1 decisions

The following decisions are fixed for the remaining task sequence:

1. Use city-independent `urban-mobility-rc`; keep Shizuoka as the first example input.
2. Keep `urban-car-one` and `urban-drone-one` as independent regression Recipes.
3. Keep concrete composition and scenarios in the Urban repository.
4. Keep the managed Recipe manifest and Foundation/source requirements in the
   Urban repository and evaluate it with the Business Pack Recipe engine.
5. Use one Launcher, Drone-owned Conductor time, one Car Fleet plant, one
   WebBridge, and one HTTP server in the integrated topology.
6. Store generated evidence by Recipe ID; do not reuse
   `drone-fleet-single-host` as the Urban workspace.
7. Implement common path and builder contracts before constructing the
   multi-Car demo.
