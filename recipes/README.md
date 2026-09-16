# Urban Mobility recipes

This directory contains the configuration and composition contract for the
PLATEAU Urban Car-1 checkpoint.

## Prerequisites

- The Business Pack Foundation runtime is installed under
  `../hakoniwa-business-pack/work/foundation`.
- A City World job has produced `city-world-receipt.json`, its MJCF, and GLB.
- The Generic Ackermann Golf Cart is generated in `hakoniwa-mbody-registry`.
- The Generic Ackermann plant is built in `hakoniwa-mujoco-robots`.
- A PS5 DualSense is available when interactive control is required.

Run all commands from the `hakoniwa-urban-mobility` repository root.

## Select a city

[`urban-car-1-viewer.yaml`](urban-car-1-viewer.yaml) is both the Car-1
composition recipe and its configure input. The City World receipt `path` is
the only city-selection setting:

```yaml
inputs:
  business_pack_city_receipt:
    path: ../hakoniwa-business-pack/work/remote-operation/city-world-worker/jobs/hokkaido-01100-lat43.062-lon141.355/build/world/city-world-receipt.json
```

The tool reads the city origin, extent, coordinate systems, MJCF path, and GLB
path from that receipt. Do not duplicate them in this configuration.

## Set the spawn pose

The spawn is relative to the City World origin in local ENU coordinates:

```yaml
inputs:
  golf_cart_model:
    spawn_pose_enu:
      frame: city_origin_local_enu
      east_m: 5.21
      north_m: -6.07
      up_m: 4.12
      yaw_deg: 11.5
```

- Position uses metres.
- Yaw uses degrees, positive counter-clockwise from East toward North.
- Roll and pitch are fixed to zero and are not configuration inputs.
- `up_m` is the Golf Cart body origin, so include terrain height and wheel
  clearance.

During configuration this is converted to the City World MJCF convention
`X=North, Y=-East, Z=Up`; yaw is converted to radians. Both representations
are recorded in `compose-receipt.json`.

## Configure and run

```bash
python3 tools/urban_car_1.py doctor \
  --config recipes/urban-car-1-viewer.yaml

python3 tools/urban_car_1.py configure \
  --config recipes/urban-car-1-viewer.yaml

python3 tools/urban_car_1.py start \
  --config recipes/urban-car-1-viewer.yaml
```

`configure` composes the City World and Golf Cart MJCF, compiles and validates
a MuJoCo-version-bound MJB, and generates the runtime and Launcher files. It
does not download or regenerate the city.

Use the existing PS5 mapping after startup:

- Left stick: steering
- Right stick vertical: throttle and reverse

Inspect or stop the background session from another terminal:

```bash
python3 tools/urban_car_1.py status \
  --config recipes/urban-car-1-viewer.yaml

python3 tools/urban_car_1.py stop \
  --config recipes/urban-car-1-viewer.yaml
```

For Viewer-only model inspection after configuration:

```bash
python3 tools/urban_car_1.py view \
  --config recipes/urban-car-1-viewer.yaml
```

## Generated files

The checked-in configuration writes generated artifacts beneath
`work/urban-car-1-viewer/`:

- `car-1-city.xml`: composed canonical MJCF
- `car-1-city.mjb`: validated runtime model
- `mujoco-materialization.json`: MuJoCo version and MJB provenance
- `compose-receipt.json`: city source, hashes, ENU spawn, and MJCF conversion
- `car-1-asset-manifest.json`: Ackermann runtime manifest
- `launcher.json`: Foundation Launcher configuration
- `runtime/launcher-session.json`: background-session state

These files are generated and are not committed.

## Changing cities

1. Change only `business_pack_city_receipt.path` to select another City World.
2. Update `spawn_pose_enu` to a driveable point in that city's local frame.
3. Run `doctor` and `configure` again.
4. Use the native Viewer to verify terrain clearance before driving.

The broader multi-vehicle and Drone integration stages are defined in
[`urban-mobility.yaml`](urban-mobility.yaml).
