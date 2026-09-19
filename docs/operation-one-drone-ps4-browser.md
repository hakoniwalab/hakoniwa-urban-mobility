# One EAMS Hexa Drone: PS4 control and browser visualization

This procedure runs one EAMS nominal 9 kg Hexa-X Drone in the PLATEAU City
World selected by `recipes/experiments/urban-drone-one.yaml`, controls it with
a PS4 controller, and displays it in the
Map Viewer / Three.js browser view. It does not start the Golf Cart.

## Prerequisites

- Run commands from the `hakoniwa-urban-mobility` repository root.
- Connect the PS4 controller to macOS before starting the Launcher.
- Prepare the City World Receipt selected by `city_world.receipt` in the recipe.
- Keep the sibling repositories `hakoniwa-business-pack`,
  `hakoniwa-drone-pro`, `hakoniwa-mbody-registry`, and
  `hakoniwa-threejs-drone` in the Business Pack workspace.

## Configure

```bash
python3 tools/drone_one.py configure \
  --recipe recipes/experiments/urban-drone-one.yaml
python3 tools/drone_one.py doctor
```

The checked-in recipe uses `control.mode: ps4-rc`. `configure` copies the generated RadioController configuration and tuned
EAMS parameters into the Recipe workspace. It does not modify the tracked
configuration in `hakoniwa-drone-pro`.

Key generated files are:

- `../hakoniwa-business-pack/work/recipes/drone-fleet-single-host/rc/drone_config_0.json`
- `../hakoniwa-business-pack/work/recipes/drone-fleet-single-host/rc/controller-params.txt`
- `../hakoniwa-business-pack/work/recipes/drone-fleet-single-host/config/launcher.json`

## Start and open the browser

```bash
python3 tools/drone_one.py start
python3 tools/drone_one.py status
python3 tools/drone_one.py open-viewer
```

To overlay the MJCF collision geometry as green wireframes, open the optional
Collider view instead:

```bash
python3 tools/drone_one.py open-viewer --colliders
```

Both modes keep Three.js as the main view and place the map in a small panel
at the lower left. The default command displays only the City GLB; Collider
wireframes are loaded only when `--colliders` is specified.

The Drone initially falls from 7 m and settles on the PLATEAU DEM. Wait for it
to settle before enabling RadioControl. The browser uses the EAMS body GLB and
six independently animated propellers. The upper-right inset is the onboard
road-monitoring camera, mounted forward and pitched 50 degrees downward. If an
older model is cached, reload the page with `Cmd+Shift+R`.

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

## Browser model regeneration

The checked-in EAMS GLB is generated from the Drone PRO MJCF with the existing
`hakoniwa-mbody-registry` converter. `--visible-only` removes transparent
physics-only geoms, and `--target-frame threejs` converts MJCF/ROS FLU axes to
Three.js right/up/back axes.

```bash
cd ../hakoniwa-mbody-registry
python tools/mjcf2glb.py \
  ../hakoniwa-drone-pro/tuning/vehicle/eams/generated/nominal-9kg/drone.xml \
  --output-dir /tmp/eams-hexa-glb \
  --split-by body \
  --visible-only \
  --target-frame threejs
```

Copy `/tmp/eams-hexa-glb/drone_base.glb` to
`../hakoniwa-threejs-drone/assets/models/eams-hexa-frame.glb`, then rerun
`configure` and `doctor` so the Recipe-local browser resources are
refreshed.
