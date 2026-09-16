# Urban Mobility Implementation Plan

The first executable path is deliberately car-first. It proves City World
composition, multi-vehicle namespaces, and common Hakoniwa time before the
Drone Fleet is introduced.

## Dependencies between stages

```text
S0 City World receipt
       |
       v
S1 one Car -----> S3 one Drone + one Car mirroring
       |                    |
       v                    |
S2 two Cars (implemented) --+
       |                    |
       +---------> S4 mixed Drone Fleet + two Cars
                              |
                              v
                     S5 MirrorImpulseSender demo
```

## Why the first vehicle is a single car

The city input, its coordinate frame, a composed MJCF, one vehicle binding,
and one controller command path can be validated without ambiguity. A failure
at this stage belongs to one of those boundaries, rather than to vehicle
namespacing, multiple processes, or Drone Fleet integration.

## Two-car checkpoint

Before adding a Drone, prove all of the following:

1. `Car-1` and `Car-2` have distinct MuJoCo body names.
2. They have distinct PDU namespaces and controller command paths.
3. Their actual poses can be published independently in `city_map`.
4. Exactly one process owns Hakoniwa Conductor time advancement.
5. One multi-body Car Fleet plant owns MuJoCo stepping for every real Car.

The current Recipe satisfies this checkpoint with `Car-1` and `Car-2`, one
`UrbanCarFleet` asset, independent AckermannDrive PDU namespaces, and combined
`UrbanFleet` JointState / MultiDOFJointState outputs.

The current pair uses two Golf Cart instances. The type-catalog contract still
allows either instance to be replaced by another forged Ackermann model. They
remain replaceable platform selections, not an assumption built into runtime
code.

## MJCF composition boundary

The City World is built outside this repository through the Business Pack and
stored under its `work/` directory. `hakoniwa-mbody-registry` composes that
static City World with a vehicle model to generate each consumer world.

Do not compose Cars and Drones into one global MJCF. The final topology has one
multi-Car world and one Drone Fleet world, each with the same city collider.
Dynamic peers from the other world are mirrors.

## Drone introduction

Start with one existing Drone Fleet type and one Car. Only after pose mirroring
and frame alignment are demonstrated should the scenario materialize mixed
Fleet types for Quad and Hexa. Drone type selection is per named Drone; the
Urban Mobility generator must not preserve the existing homogeneous-Fleet
assumption of the current City recipe.

## Contact introduction

Contact is the final initial milestone, not a prerequisite for movement.
`MirrorImpulseSender` reuses local MuJoCo contact between a real Car and a
collision-enabled mirrored Drone proxy, then emits `ImpulseCollision` to the
real Drone owner. The Car's simplified local response is contact with a static
obstacle. Viewer and policy logic receive a separate `ContactEvent`.
