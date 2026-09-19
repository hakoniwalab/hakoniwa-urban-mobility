# Urban Mobility Foundation migration baseline

## 1. Scope

This document records the pre-migration regression baseline for
[`foundation-task.md`](foundation-task.md) Step 0 and
[Issue #2](https://github.com/hakoniwalab/hakoniwa-urban-mobility/issues/2).
It was collected on 2026-09-19 on macOS arm64 before changing workdir,
Foundation, Recipe workspace, or Launcher ownership.

The baseline covers the current one-Car and one-Drone recipes separately. It
does not claim that both recipes can run concurrently.

## 2. Source revisions

All listed repositories used branch `main` at collection time.

| Repository | Revision | Working tree note |
| --- | --- | --- |
| `hakoniwa-urban-mobility` | `b396d93c8f06248d10d15ddd5b76862373702ad1` | This baseline documentation and stale `MUJOCO_LOG.TXT` were uncommitted |
| `hakoniwa-business-pack` | `f738a2000405df0c3efa920470d2c438273a0871` | Untracked `task.md` preserved |
| `hakoniwa-drone-pro` | `a61e3aacd55a5f50546166735b33c4ffca99cb5b` | Existing generated/vendor/work files preserved |
| `hakoniwa-drone-show` | `99a10ac3a3375bda92de34169ad9fa151d0fad61` | Clean |
| `hakoniwa-robot-runtime` | `1706f7c7da6c6b475e5f02767172cf7db10e7799` | Clean |
| `hakoniwa-mujoco-robots` | `8c25a6d9ed02ea9bfb5c6d63b0782793c014a7c5` | Clean |
| `hakoniwa-mbody-registry` | `8717c1a7d1ce074fe3e1526234a6e3f780eff0fc` | Untracked `test.xml` preserved |
| `hakoniwa-threejs-drone` | `eb0c2be7691d4c4583d5bad107c3414c328afcb7` | Clean |
| `hakoniwa-map-viewer` | `6e30097a4f4934cfe71400c45ee15b5717a2d790` | Clean |
| `hakoniwa-envsim` | `50ed4223b5527898769a86c920f703a788b248fe` | Clean |
| `hakoniwa-pdu-python` | `049fde71f2aab627ff822d01b12d8ee748eb9478` | Clean |
| `hakoniwa-core-pro` | `e56655eed9b602d2e675c35cf713500eefd9ee10` | Clean; installed Receipt records an older installed revision |
| `hakoniwa-pdu-endpoint` | `2430ee9435c2a345357e13825779135786f0a160` | Existing untracked validation build preserved; installed Receipt records an older installed revision |
| `hakoniwa-pdu-bridge-core` | `17ed32023c25a08aeaa957dbaf7f6982d722ad35` | Existing Python cache preserved; installed Receipt records an older installed revision |

Remote URLs use `git@github.com:hakoniwalab/<repository>.git`.

## 3. Installed Foundation

Resolved Foundation prefix:

```text
/Users/tmori/project/business-pack/hakoniwa-business-pack/work/foundation/install
```

Python contract:

```text
Python 3.12.3
cache tag: cpython-312
Core SOABI: cpython-312-darwin
platform: macOS arm64
```

Installed component Receipts relevant to the Urban recipes:

| Component | Version | Installed source revision | Relevant capabilities |
| --- | --- | --- | --- |
| `hakoniwa-core-pro` | `1.0.0` | `b8818ec47619f6026739f7d71e2d22829dea4752` | shared memory, `hako-cmd`, Python binding |
| `hakoniwa-pdu-python` | `1.6.9` | `049fde71f2aab627ff822d01b12d8ee748eb9478` | background Launcher lifecycle, SHM, external RPC, WebSocket |
| `hakoniwa-pdu-endpoint` | `1.0.0` | `93a926c520a76f401f52d7ba4e816e5ad54d7c36` | Core callback/polling, TCP, UDP, WebSocket, storage |
| `hakoniwa-pdu-bridge-core` | `1.0.0` | `e5c567948b42512abc36a8cea2b4d8a151f6c145` | Hakoniwa app, WebBridge, Fleet config format |

The four Receipts share these build limits:

```text
asset_num=16
pdu_channel_max=8192
recv_event_max=4096
service_client_max=256
service_max=1024
client_name_len_max=64
service_name_len_max=128
```

The installed revisions, rather than the current sibling checkout revisions,
are the authoritative Foundation state for reuse evaluation.

## 4. City World input

Receipt:

```text
../hakoniwa-business-pack/work/remote-operation/city-world-worker/jobs/
  shizuoka-22203-lat35.099-lon138.859/build/world/city-world-receipt.json
```

Coordinate contract:

```text
origin latitude: 35.099382
origin longitude: 138.858781
altitude offset: 6.011970946679594 m
half extent: north/south 104.8 m, east/west 45.4 m
MJCF: X=North, Y=-East, Z=Up
GLB: X=East, Y=Up, Z=-North
```

Artifact evidence:

| Artifact | Evidence |
| --- | --- |
| City MJCF SHA-256 | `d138dd3c445b9483ac1e8444c9cc72a5b7c07d805c63390451db119d3a0156dc` |
| City GLB SHA-256 | `a28e035812ae04e6ac43fa00fa42fa9cc5df9bf878196ca8b662c040b7b74086` |
| City GLB size | `7,215,104` bytes |
| MJCF geoms | terrain `1`, buildings `10,662`, total `10,663` |

## 5. Automated tests

Commands:

```bash
../hakoniwa-business-pack/work/foundation/install/python/bin/python3 \
  -m unittest discover -s tests -p 'test_*.py'
ctest --test-dir build --output-on-failure
```

Initial execution found one stale assertion: the tracked Car recipe starts at
ENU `(0, 0, yaw 0)` while `test_control_modes.py` still expected the earlier
`(45, 7.5, yaw -117.76)` pose. The assertion was aligned with the tracked
Recipe without changing runtime behavior.

Final result:

```text
Python: 43 tests passed
CTest: 7/7 tests passed
```

## 6. One-Car runtime baseline

Recipe:

```text
recipes/experiments/urban-car-one.yaml
```

Lifecycle executed:

```bash
python3 tools/multi_car.py configure \
  --config recipes/experiments/urban-car-one.yaml
python3 tools/multi_car.py doctor \
  --config recipes/experiments/urban-car-one.yaml
python3 tools/multi_car.py start \
  --config recipes/experiments/urban-car-one.yaml
python3 tools/multi_car.py status \
  --config recipes/experiments/urban-car-one.yaml
python3 tools/multi_car.py stop \
  --config recipes/experiments/urban-car-one.yaml
```

Observed result:

- City composition generated `10,663` worldbody children.
- MuJoCo `3.9.0` compiled and reload-validated the MJB.
- Runtime initial pose was Car-1 `z=2.873 m` over terrain `z=2.423 m`.
- Launcher reached `RUNNING` with PID `10542`.
- DualSense Wireless Controller was detected with 6 axes and 17 buttons.
- HTTP Map Viewer returned `200 OK`.
- WebSocket returned `101 Switching Protocols` and streamed `UrbanFleet` frames.
- `stop` returned `TERMINATED`; ports `8000` and `8765` were released.

Human observation confirmed during the one-Car implementation before this
automated baseline rerun:

- PS5 steering/throttle response
- Three.js Car pose and visual scale
- lower-left map marker
- upper-right forward camera
- optional green Collider overlay

## 7. One-Drone runtime baseline

Recipe:

```text
recipes/experiments/urban-drone-one.yaml
```

Lifecycle executed:

```bash
python3 tools/drone_one.py configure \
  --recipe recipes/experiments/urban-drone-one.yaml
python3 tools/drone_one.py doctor
python3 tools/drone_one.py start
python3 tools/drone_one.py status
python3 tools/drone_one.py stop
```

Observed result:

- City composition generated `10,663` worldbody children and removed one template ground.
- Runtime MJB used MuJoCo `3.9.0` and passed reload validation.
- Spawn ENU was `(0.0, 0.0, 10.3)` with yaw `0` degrees.
- Resolved terrain reference was `2.401 m`; flight clearance was `2.0 m`.
- Foundation evaluation returned `SATISFIED` for the four required components.
- Launcher reached `RUNNING` with PID `11123`.
- HTTP Three.js client returned `200 OK`.
- WebSocket returned `101 Switching Protocols` and streamed Fleet frames.
- Before `start`, configure-generated and Urban-owned PID files differed.
- At `start`, the runtime PID SHA-256 became identical to the Urban source:
  `371de7018c66c38d8d4c9314e9d995da4f0fbcf34245717fa641cb9ffbb1b6e4`.
- `stop` returned `TERMINATED`; ports `8000`, `8765`, and `54111` were released.

Human observation confirmed during the one-Drone implementation before this
automated baseline rerun:

- PS4 RadioControl enable and stick response
- stable takeoff/hover/landing with the current PID source
- Three.js Drone pose, Hexa model, landing gear, and six propellers
- lower-left map marker
- upper-right monitoring camera
- optional green Collider overlay

## 8. Known pre-migration constraints

- `multi_car.py` resolves Foundation from the fixed Business Pack `work/` path.
- `drone_one.py` writes into `work/recipes/drone-fleet-single-host`.
- `drone_one.py` replaces `base.write_launcher` at module scope.
- The standalone Car and Drone recipes compete for HTTP `8000` and WebSocket `8765`.
- `open-viewer` can open a wrong-session URL when another Recipe owns the port.
- `status` may report a sandbox-denied control connection as `STALE`; the same
  endpoint returned `RUNNING` when invoked with host access.
- Drone `configure` overwrites the generated PID file; Drone `start` correctly
  restores the Urban-owned PID source.
- Installed Foundation revisions intentionally differ from some current source
  checkouts. Migration must compare Recipe requirements with Receipts and must
  not infer installed state from sibling Git HEADs.

## 9. Step 0 completion gate

Step 0 is complete. The owner confirmed the listed Car and Drone observations
during development and identified the one-vehicle configurations as complete
enough to serve as the migration baseline. This rerun independently confirmed
their tracked inputs, generated physics models, Launcher lifecycle, HTTP,
WebSocket, PID restoration, and cleanup.
