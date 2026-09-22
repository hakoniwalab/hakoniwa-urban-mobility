# Urban EAMS Hexa runtime assets

This directory is the Urban-owned source of truth for the EAMS nominal 9 kg
six-rotor vehicle used by the Drone-only and integrated Urban Mobility recipes.

- `drone.xml`: MuJoCo vehicle model
- `drone_config_0.json`: six-rotor dynamics and mixer contract
- `controller-params.txt`: PS4 RC parameter baseline
- `controller-tuning.txt`: tuned gains overlaid on the selected controller mode

The executable service, visual-state publisher, RC client, and MuJoCo runtime
come from the public `hakoniwa-drone-core` v4.1.1 distribution. Configuration
does not read `hakoniwa-drone-pro`; generated copies stay under the Business
Pack Recipe workspace.
