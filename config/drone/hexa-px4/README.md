# Urban EAMS Hexa for PX4 SITL

The EAMS nominal 9 kg six-rotor vehicle of `../hexa`, set up to fly with the
PX4 autopilot in SITL (`tools/px4_sitl.py`, `docs/px4-sitl.md`).

| File | Content |
|---|---|
| `drone.xml` | `../hexa/drone.xml` with the MuJoCo timestep at 3 ms (the PX4 SITL sensor period) |
| `drone_config_0.json` | `../hexa/drone_config_0.json` with the SITL settings below |
| `controller-params.txt` | a copy of `../hexa/controller-params.txt` (the config refers to it; PX4 does the control in SITL) |
| `px4/900002_hakoniwa_eams` | PX4 airframe: geometry, EKF and the tuned gains below |
| `px4/hakoniwa_eams.params` | the same parameters as a plain `param set-default` list |

In a City World (asset `eams-hexa-px4`, `docs/px4-sitl.md` section 6) the City
model is made from this `drone.xml` without the landing box of `../hexa`
(only the skids touch the ground), and the magnetic field is looked up at the
City origin.

The executable aircraft service comes from the public `hakoniwa-drone-core`
v4.1.1 distribution (`mac-main_hako_aircraft_service_px4`).

## SITL settings in `drone_config_0.json`

| Setting | Value | Why |
|---|---|---|
| `simulation.timeStep` | 0.003 | PX4 SITL runs the rate controller on every 3 ms HIL sensor sample |
| `simulation.mavlink_tx_period_msec.hil_sensor` | 3 | the HIL sensor period |
| `simulation.location` | 47.641468, −122.140165, 121.321 m | PX4 SITL's default home (also set by `tools/px4_sitl.py`) |
| `simulation.location.magneticField` | 53045.1 nT, declination 15.306°, inclination 68.984° | the field at that home. With 0 the PX4 EKF has no heading: yaw sat 140° off in the tuning |
| `components.sensors.gps` | `eph` 0.2, `epv` 0.3, `sacc` 0.1 | GPS accuracy reported to the EKF |

The position and the field go together: PX4 checks the measured field against
its world magnetic model at the GPS position. To fly at another place, change
both (and the `PX4_HOME_*` in `tools/px4_sitl.py`).

## Tuned PX4 parameters

Tuned on this vehicle model at the SITL timing (physics and rate 3 ms, attitude
6 ms, outer loops 21 ms) with the Hakoniwa Drone PRO PX4-EKF tuning (policy v3:
hover, angle with yaw, altitude, horizontal velocity) and its position phase
(missions that combine moves and 180° turns), on 2026-10-09.

| PX4 parameter | Value |
|---|---|
| `MC_ROLL_P`, `MC_PITCH_P`, `MC_YAW_P` | 8, 8, 5 |
| `MC_ROLLRATE_P/I/D`, `MC_PITCHRATE_P/I/D` | 1, 0.25, 0.025 |
| `MC_YAWRATE_P` | 0.6 |
| `MPC_Z_P`, `MPC_Z_VEL_P/I/D_ACC` | 1.75, 10, 5.5, 0.5 |
| `MPC_XY_P` | 1.2 (position phase; 6 made the vehicle oscillate after a turn and crash) |
| `MPC_XY_VEL_P/I/D_ACC` | 8.5, 0, 0.4 |

Some gains are above PX4's suggested ranges, which are meant for small quads;
they were chosen and checked on this 9 kg hexa.

## Vehicle-derived parameters

| PX4 parameter | Value | Source |
|---|---|---|
| `MPC_THR_HOVER` | 0.310 | (ω_hover / ω_max)² from mass, `Ct` and the maximum rotor speed |
| `THR_MDL_FAC` | 0.663 | PX4's output goes to the Hakoniwa rotor as PWM duty; this curve passes the rotor model's hover point (duty 0.476 gives the hover thrust). For SITL only: on hardware set it from the real motor |
| `CA_ROTORn_KM` | ±0.040 | the rotor's `Cq/Ct` (PX4's default 0.05 overstates yaw authority) |
| `CA_ROTORn_PX/PY/PZ`, `CA_ROTORn_CT` | rotor geometry, 47.4 N per rotor | `drone_config_0.json` |

## Operating limits

From the vehicle's capability (thrust to weight 3.2, roll acceleration about
46 rad/s², yaw about 8.6 rad/s²) and how a 9 kg vehicle should fly over a town:

| PX4 parameter | Value |
|---|---|
| `MPC_TILTMAX_AIR` | 25° |
| `MPC_XY_VEL_MAX` | 10 m/s |
| `MPC_Z_VEL_MAX_UP` / `DN` | 3 / 1.5 m/s (slower descent: a heavy multirotor can lose lift in its own downwash) |
| `MC_ROLLRATE_MAX`, `MC_PITCHRATE_MAX` | 120°/s |
| `MC_YAWRATE_MAX` | 60°/s |

When the vehicle model or these limits change, the gains have to be tuned again.
