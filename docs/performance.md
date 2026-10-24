# Performance settings

What makes an Urban run fast, and what keeps it repeatable. Each setting is
listed with where it applies today, what it does, what it cost or gained when
measured, and what to watch for. The measurements come from Town04 (an
OpenDRIVE highway city, 13k road pieces), lockstep, Mac and Windows, 2026-10-17.

## In every run (set when the vehicle model is generated)

| Setting | Where | Effect |
|---|---|---|
| City-only static fusion | `tools/fuse_static_city.py`, `tools/drone_one.py` | The City is compiled with `fusestatic` before Drone bodies are attached, so named jointless Drone bodies (including propellers) cannot be fused away. Town04 X500 fell from 744 to 6 bodies and from 1656 to 60.6 µs per RK4 1 ms step. |
| MuJoCo sleep: `<flag sleep="enable"/>`, every Car/Drone root `sleep="never"` | `tools/multi_car.py`, `tools/drone_one.py` | The City's static geoms are posed once instead of every step while moving vehicles never sleep. Town04 X500's optimized and unoptimized 1-second free-fall trajectories had 0 m maximum z difference. Cars are bit-identical to a run without it; Windows Car lockstep with sleep off was 10–60 % slower. `URBAN_MUJOCO_SLEEP=0` at configure leaves sleep out, for measuring. |
| Cars parked apart in the model | `tools/multi_car.py` | The model puts each Car in a row 6 m apart, 50 m up; the runtime then moves them to their spawn poses. MuJoCo computes the state once at load, before the poses are applied. With all Cars at the origin, that overlapped them, and 20 or more Cars overflowed MuJoCo's arena at load. |
| MuJoCo 3.16.0, pinned | Foundation | A Windows plant that linked an older MuJoCo (3.13) ran 3× slower, and fusestatic had no effect. |

## Runs without the real-time pacer (`simulation.realtime: false`)

| Setting | Effect | Watch for |
|---|---|---|
| `simulation.step_msec` (default 10) with physics substeps | The plant runs one Hakoniwa step as 5 MuJoCo steps of 2 ms, like CARLA's synchronous tick with substepping. That is 5× fewer Hakoniwa handoffs. | The step must be a whole number of the model's timestep. |
| `simulation.lockstep: true` | The plant steps only when the program's commands for the step have arrived, and the program waits for the plant's state. Runs are bit-identical under any CPU load. | Costs about 25 % against the free run: the program's work and the plant's run one after the other (Gauss-Seidel). |
| `simulation.lockstep_pipeline: true` | The Conductor's max_delay becomes two steps. The program works out the next commands while the plants step (Jacobi). Each plant step takes the commands from the state two steps before. Still bit-identical. | One step more control delay than plain lockstep, so results differ from plain lockstep (each mode is repeatable on its own). |
| `simulation.plant_processes: K` | The Cars split over K plant processes, each with its own MuJoCo model. | The same K gives identical runs. A different K changes the results: MuJoCo solves each model jointly, and Cars in different processes do not collide. Needs `viewer.enabled: false`. Hakoniwa allows 16 assets, so K ≤ 15 with one program. |

Measured best: 40 Cars, 4 plant processes, pipelined lockstep. RTF 3.05 on Mac and 1.28 on Windows, against 0.85 and 0.44 for plain lockstep in one process. Beyond 4 processes the CPU is shared with the program and the gain stops. With 80 or more Cars the program (one Python process) bounds the step.

## The City

- **Rebuild OpenDRIVE cities with the current generator** (hakoniwa-scenario-pro `xodr2recipe.py`; the road detail setting stays the same). Town04 built before 2026-10-16 had 44,107 road pieces; rebuilt, it has 13,305, with trajectories within 6 cm and 1.4× the speed. Every static piece is posed (without sleep) and swept (broadphase) every step.
- The road detail `light` is only about 8 % faster than `balanced`, and it changes trajectories (mean 0.6 m over 35 s). Use `balanced`.
- Where the road pieces are cut changes trajectories, even with the same surface: moving the cuts moved 40 Cars by 0.2 m on average over 35 s.

## Program side (a scenario program driving many Cars)

- Use the PDU types' own converters (`pdu_conv_*`). Encode a value that does not change (such as a road friction written every step) once, and reuse its bytes as a fresh `bytearray` per write. On Mac, that took 40 Cars' frictions from 0.6 ms a step to almost nothing.
- A lockstep program waits for every plant process's state (one state robot per process, listed in `config/car/urban-car-plants.json`).

## Platform notes

- **Hakoniwa Conductor loop** (core-cpp #75, core-pro #105): no allocation per loop, and it yields when the time cannot move. `HAKO_CONDUCTOR_IDLE_SLEEP_USEC` makes it sleep instead. On Mac, 50 µs freed one core at the same speed. **Do not use it on Windows**: a short sleep lasts 1 ms or more there, and runs were 30–60 % slower.
- **Windows plant DLLs**: the Urban plant uses its own copies of the Hakoniwa DLLs in `build\bin`. After a Foundation core update, run Urban `configure` so that the plant picks up the new DLLs.
- **Windows Foundation install**: `cmake --install` of the core rewrites `foundation\config\cpp_core_config.json` (same values, different path style). Check it after an install.

## Not yet in interactive runs

These work only without the real-time pacer and the viewer today:
- physics substeps in real-time runs
- `plant_processes` with the browser viewer
- the pipelined lockstep with a viewer attached as an observer
