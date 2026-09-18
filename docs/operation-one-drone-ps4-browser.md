# One EAMS Hexa Drone: PS4 control and browser visualization

This procedure runs one EAMS nominal 9 kg Hexa-X Drone in the Hokkaido
PLATEAU world, controls it with a PS4 controller, and displays it in the
Map Viewer / Three.js browser view. It does not start the Golf Cart.

## Prerequisites

- Run commands from the `hakoniwa-urban-mobility` repository root.
- Connect the PS4 controller to macOS before starting the Launcher.
- Prepare the Hokkaido City World Receipt used by `tools/drone_one.py`.
- Keep the sibling repositories `hakoniwa-business-pack`,
  `hakoniwa-drone-pro`, `hakoniwa-mbody-registry`, and
  `hakoniwa-threejs-drone` in the Business Pack workspace.

## Configure

```bash
python3 tools/drone_one.py configure --rc
python3 tools/drone_one.py doctor
```

`configure --rc` copies the generated RadioController configuration and tuned
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
`configure --rc` and `doctor` so the Recipe-local browser resources are
refreshed.
