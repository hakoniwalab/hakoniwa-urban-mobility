# One Urban EAMS Hexa: PS4 control and browser visualization

This procedure runs one Urban-managed EAMS six-rotor Drone with the public
Hakoniwa Drone Core v4.1.1 service binary in the PLATEAU City
World selected by `recipes/experiments/urban-drone-one.yaml`, controls it with
a PS4 controller, and displays it in the
Map Viewer / Three.js browser view. It does not start the Golf Cart.

## Prerequisites

- Run commands from the `hakoniwa-urban-mobility` repository root.
- Connect the PS4 controller to the host before starting the Launcher.
- Prepare the City World Receipt selected by `city_world.receipt` in the recipe.
- Keep the sibling repositories `hakoniwa-business-pack`,
  `hakoniwa-drone-core`, `hakoniwa-mbody-registry`, and
  `hakoniwa-threejs-drone` in the Business Pack workspace.

## Configure

```bash
python3 tools/drone_one.py prepare-native
python3 tools/drone_one.py configure \
  --recipe recipes/experiments/urban-drone-one.yaml
python3 tools/drone_one.py doctor
```

`prepare-native` selects the host's v4.1.1 release archive, verifies its
SHA-256, and installs the MuJoCo 3.13.0 runtime declared by Drone Core. The
checked-in recipe uses `control.mode: ps4-rc`; `configure` materializes the
RadioController configuration and Urban-owned Hexa model/PID set into the
Recipe workspace without editing the Core checkout.

MuJoCo version ownership is aligned as follows:

- `hakoniwa-drone-core/MUJOCO_VERSION.txt` is the native Drone runtime authority.
- `hakoniwa-mujoco-robots` builds its Viewer/backend against 3.13.0.
- `hakoniwa-mbody-registry` pins 3.13.0 for the Ackermann tooling that imports
  the Python MuJoCo package.
- `hakoniwa-envsim` only emits MJCF/XML and does not pin or link a MuJoCo
  runtime, so it requires no version change.

Key generated files are:

- `../hakoniwa-business-pack/work/recipes/urban-drone-one/rc/drone_config_0.json`
- `../hakoniwa-business-pack/work/recipes/urban-drone-one/rc/controller-params.txt`
- `../hakoniwa-business-pack/work/recipes/urban-drone-one/config/launcher.json`

## Start and open the browser

```bash
python3 tools/drone_one.py start
python3 tools/drone_one.py status
python3 tools/drone_one.py open-viewer
```

This one-Launcher recipe owns the Foundation runtime directory and removes
stale Hakoniwa mmap/lock files automatically before starting its assets. A
previously terminated Car or Drone session therefore does not require a host
restart. The runtime loads the configure-time compiled and reload-validated
City MJB. The Launcher waits for the Drone service to register before starting
dependent assets.

To overlay the MJCF collision geometry as green wireframes, open the optional
Collider view instead:

```bash
python3 tools/drone_one.py open-viewer --colliders
```

Both modes keep Three.js as the main view and place the map in a small panel
at the lower left. The default command displays only the City GLB; Collider
wireframes are loaded only when `--colliders` is specified.

The Drone starts from `drone.spawn_pose_enu` in the recipe and settles on the
PLATEAU DEM. Wait for it to settle before enabling RadioControl. The browser
uses the EAMS Hexa model and six independently animated propellers. The
upper-right inset is the onboard road-monitoring camera, mounted forward and
pitched 50 degrees downward. If an older model is cached, reload the page with
`Cmd+Shift+R`.

The City MJB is generated from Urban's `config/drone/hexa` model and the
selected City Receipt. The same generated Hexa model is used for the
Car-side Mirror in the integrated demo.

The pose uses the City World's local ENU frame: `east_m`, `north_m`, `up_m`,
and ENU `yaw_deg` (0 degrees faces east; 90 degrees faces north). To move the
Drone after the initial configure, stop it, edit only `spawn_pose_enu`, and
start it again:

```bash
python3 tools/drone_one.py stop
# edit recipes/experiments/urban-drone-one.yaml
python3 tools/drone_one.py start
```

`start` converts ENU to the Drone Core Fleet config's NED convention and updates
the generated `api-current.json`; it does not rebuild City World or MJCF.
Changing `city_world.receipt`, controller, model, or viewer settings still
requires `configure`.

## PS4 controls

Press Cross (button 0) once to enable the latched RadioControl mode. It is not
a dead-man switch.

| Input | Operation |
| --- | --- |
| Left stick up/down | Throttle |
| Left stick left/right | Yaw |
| Right stick up/down | Pitch |
| Right stick left/right | Roll |

## Stop

```bash
python3 tools/drone_one.py stop
```

Confirm that the command reports `TERMINATED`. A later automatic Fleet RPC run
must be configured again without `--rc`:

```bash
python3 tools/drone_one.py configure
```
