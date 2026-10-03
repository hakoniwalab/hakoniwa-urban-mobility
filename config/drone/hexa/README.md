# Urban EAMS Hexa runtime assets

This directory is the Urban-owned source of truth for the EAMS nominal 9 kg
six-rotor vehicle used by the Drone-only and integrated Urban Mobility recipes.

- `drone.xml`: MuJoCo vehicle model
- `drone_config_0.json`: six-rotor dynamics and mixer contract
- `controller-params.txt`: PS4 RC parameter baseline
- `controller-tuning.txt`: tuned gains overlaid on the selected controller mode
- `controller-api-tuning.txt`: gains overlaid for API (Fleet RPC) flight only:
  no horizontal velocity integral (it wound up on long gotos), lower
  speed / climb / yaw-rate limits, straight legs (`POS_DIST_CONTROL 1`), a
  softer position gain and more altitude damping, so scheduled flights
  (`apps/drone/drone_schedule.py`) follow their lines and stop without
  overshooting far

The executable service, visual-state publisher, RC client, and MuJoCo runtime
come from the public `hakoniwa-drone-core` v4.1.1 distribution. Configuration
does not read `hakoniwa-drone-pro`; generated copies stay under the Business
Pack Recipe workspace.
