#!/usr/bin/env python3
"""The Hakoniwa People plant: N 箱庭人間 in one MuJoCo world, as a Hakoniwa asset.

Each person is a capsule that slides and turns (bodies/hakoniwa_person in
hakoniwa-mbody-registry). Per person it reads, every step:

- ``<name>/cmd_vel`` (geometry_msgs/Twist): linear.x = east, linear.y = north
  velocity in m/s (the City World's ENU); the person turns to face where it
  walks. angular.z (rad/s, left positive) turns it while it stands.
- ``<name>/animation`` (std_msgs/String): ``auto`` (or empty: walk while
  moving, idle otherwise), ``walk``, ``idle``, ``wave`` or ``sit``.
- ``<name>/ride`` (std_msgs/String): ``<vehicle>/<seat>`` (for example
  ``Car-1/driver``) to sit on a car's seat and go with it; empty to get off
  (beside the seat). Riders follow the car's pose (the Car fleet's
  vehicle_states) and do not collide while they ride.

and it writes, for all people, the same state PDUs as the Urban Car fleet so
the browser viewer shows them unchanged:

- ``<state robot>/vehicle_states`` (sensor_msgs/MultiDOFJointState): one
  transform per person, MuJoCo frame (X north, Y west, Z up).
- ``<state robot>/joint_states`` (sensor_msgs/JointState): the animated
  shoulder and hip angles as ``<name>/<joint>``. The walk is animation: the
  limbs swing with the distance walked; they do not move the person.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

LIMB_JOINTS = ("shoulder_left_joint", "shoulder_right_joint", "hip_left_joint", "hip_right_joint")
ANIMATIONS = ("auto", "walk", "idle", "wave", "sit")
STRIDE_M = 1.0          # distance walked per full swing cycle (an adult)
MAX_SWING_RAD = math.radians(30)
MOVING_M_S = 0.05
TURN_GAIN = 5.0
MAX_TURN_RAD_S = 3.0
STEP_M = 0.32           # what a person steps up onto (the body's collider starts there)
HIP_M = 0.66            # an adult's hip above the feet (the body's hip joints)
GET_OFF_M = 0.75        # how far beside its seat a rider gets off


@dataclass
class Gait:
    """The animation state of one person."""

    phase: float = 0.0
    clock: float = 0.0
    angles: dict = field(default_factory=lambda: {joint: 0.0 for joint in LIMB_JOINTS})


def _approach(current: float, target: float, rate: float, dt: float) -> float:
    step = rate * dt
    return target if abs(target - current) <= step else current + math.copysign(step, target - current)


def animate(gait: Gait, mode: str, speed: float, dt: float, scale: float = 1.0) -> dict[str, float]:
    """Limb angles (rad) for one step. Positive turns a limb backwards about
    the person's left (y) axis; the arms swing opposite to the legs."""
    gait.clock += dt
    if mode not in ANIMATIONS or mode == "auto":
        mode = "walk" if speed > MOVING_M_S else "idle"
    targets = {joint: 0.0 for joint in LIMB_JOINTS}
    if mode == "walk":
        gait.phase = (gait.phase + 2 * math.pi * max(speed, 0.6) * dt / (STRIDE_M * scale)) % (2 * math.pi)
        swing = MAX_SWING_RAD * min(1.0, max(speed, 0.6) / 1.4)
        s = math.sin(gait.phase)
        targets = {"hip_left_joint": swing * s, "hip_right_joint": -swing * s,
                   "shoulder_left_joint": -swing * s, "shoulder_right_joint": swing * s}
        gait.angles = targets  # follows the phase exactly
        return dict(gait.angles)
    if mode == "idle":
        sway = math.radians(2) * math.sin(gait.clock * 1.3)
        targets.update(shoulder_left_joint=sway, shoulder_right_joint=-sway)
    elif mode == "wave":
        targets["shoulder_right_joint"] = math.radians(-150) + math.radians(18) * math.sin(gait.clock * 2 * math.pi * 1.5)
    elif mode == "sit":
        targets.update(hip_left_joint=math.radians(-90), hip_right_joint=math.radians(-90),
                       shoulder_left_joint=math.radians(-15), shoulder_right_joint=math.radians(-15))
    rate = math.radians(240)
    gait.angles = {joint: _approach(gait.angles[joint], targets[joint], rate, dt) for joint in LIMB_JOINTS}
    return dict(gait.angles)


def wrap(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))


def drive(east: float, north: float, yaw_rate: float, yaw_mjcf: float) -> tuple[float, float, float]:
    """MuJoCo actuator targets (move_x north, move_y west, turn) for an ENU velocity."""
    vx, vy = north, -east
    if math.hypot(vx, vy) > MOVING_M_S:
        turn = TURN_GAIN * wrap(math.atan2(vy, vx) - yaw_mjcf)
    else:
        turn = yaw_rate
    return vx, vy, max(-MAX_TURN_RAD_S, min(MAX_TURN_RAD_S, turn))


class PeoplePlant:
    def __init__(self, config: dict):
        import mujoco

        self.mujoco = mujoco
        self.config = config
        self.model = mujoco.MjModel.from_xml_path(config["world_xml"])
        self.data = mujoco.MjData(self.model)
        self.delta_usec = int(config["delta_usec"])
        self.substeps = max(1, round(self.delta_usec / 1e6 / self.model.opt.timestep))
        self.people = []
        for person in config["people"]:
            name = person["name"]
            joint = lambda suffix: self.model.joint(f"{name}/{suffix}")
            actuator = lambda suffix: self.model.actuator(f"{name}/{suffix}").id
            turn = joint("turn_joint")
            self.data.qpos[turn.qposadr[0]] = math.radians(person["spawn"].get("yaw_deg", 0.0)) - math.pi / 2
            self.people.append({
                "name": name,
                "scale": float(person.get("scale", 1.0)),
                "body": self.model.body(f"{name}/person").id,
                "slide_x": joint("slide_x_joint").dofadr[0],
                "slide_y": joint("slide_y_joint").dofadr[0],
                "turn_qpos": turn.qposadr[0],
                "actuators": (actuator("move_x"), actuator("move_y"), actuator("turn")),
                "lift": actuator("lift"),
                "slide_x_qpos": joint("slide_x_joint").qposadr[0],
                "slide_y_qpos": joint("slide_y_joint").qposadr[0],
                "slide_z_qpos": joint("slide_z_joint").qposadr[0],
                "collision": self.model.geom(f"{name}/person_collision").id,
                "spawn_xy": tuple(float(v) for v in self.model.body(f"{name}/person").pos[:2]),
                "command": (0.0, 0.0, 0.0),
                "animation": "auto",
                "ride": "",
                "riding": None,  # (vehicle, seat offset) while riding
                "gait": Gait(),
                "angles": {joint: 0.0 for joint in LIMB_JOINTS},
            })
        self.vehicle_poses = {}
        mujoco.mj_forward(self.model, self.data)
        # Stand each person on the ground under its spawn.
        for person in self.people:
            ground = self.ground_under(person)
            self.data.qpos[person["slide_z_qpos"]] = ground
            self.data.ctrl[person["lift"]] = ground
        mujoco.mj_forward(self.model, self.data)

    def ground_under(self, person) -> float:
        """The height of what the person stands on: a ray down from a step
        above its feet (the ground, a deck), not what it would bump into."""
        import numpy as np

        x, y, z = self.data.xpos[person["body"]]
        start = np.array([x, y, z + STEP_M + 0.05])
        groups = np.array([1, 0, 0, 0, 0, 0], dtype=np.uint8)  # the world's geoms, not people's
        geomid = np.array([-1], dtype=np.int32)
        distance = self.mujoco.mj_ray(self.model, self.data, start, np.array([0.0, 0.0, -1.0]),
                                      groups, 1, person["body"], geomid)
        return float(start[2] - distance) if distance >= 0 else 0.0

    # --- PDUs ----------------------------------------------------------------------

    def read_commands(self, pdu) -> None:
        from hakoniwa_pdu.pdu_msgs.geometry_msgs.pdu_conv_Twist import pdu_to_py_Twist
        from hakoniwa_pdu.pdu_msgs.std_msgs.pdu_conv_String import pdu_to_py_String

        for person in self.people:
            raw = pdu.read_pdu_raw_data(person["name"], "cmd_vel")
            if raw:
                try:
                    twist = pdu_to_py_Twist(raw)
                    values = (float(twist.linear.x), float(twist.linear.y), float(twist.angular.z))
                    if all(math.isfinite(value) for value in values):
                        person["command"] = values
                except (IndexError, TypeError, ValueError):
                    pass
            raw = pdu.read_pdu_raw_data(person["name"], "animation")
            if raw:
                try:
                    text = str(pdu_to_py_String(raw).data).strip().lower()
                except (IndexError, TypeError, ValueError, UnicodeDecodeError):
                    text = ""
                person["animation"] = text if text in ANIMATIONS else "auto"
            raw = pdu.read_pdu_raw_data(person["name"], "ride")
            if raw:
                try:
                    person["ride"] = str(pdu_to_py_String(raw).data).strip()
                except (IndexError, TypeError, ValueError, UnicodeDecodeError):
                    pass
        self.read_vehicles(pdu)

    def read_vehicles(self, pdu) -> None:
        """The cars' poses (MuJoCo frame) for riders."""
        self.vehicle_poses = {}
        robot = self.config.get("vehicle_state_robot")
        if not robot or not any(person["ride"] or person["riding"] for person in self.people):
            return
        from hakoniwa_pdu.pdu_msgs.sensor_msgs.pdu_conv_MultiDOFJointState import pdu_to_py_MultiDOFJointState

        raw = pdu.read_pdu_raw_data(robot, "vehicle_states")
        if not raw:
            return
        try:
            state = pdu_to_py_MultiDOFJointState(raw)
        except (IndexError, TypeError, ValueError):
            return
        for name, transform in zip(state.joint_names, state.transforms):
            q, p = transform.rotation, transform.translation
            yaw = math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y ** 2 + q.z ** 2))
            self.vehicle_poses[name] = (float(p.x), float(p.y), float(p.z), yaw)

    def write_states(self, pdu) -> None:
        from hakoniwa_pdu.pdu_msgs.sensor_msgs.pdu_conv_JointState import py_to_pdu_JointState
        from hakoniwa_pdu.pdu_msgs.sensor_msgs.pdu_conv_MultiDOFJointState import py_to_pdu_MultiDOFJointState
        from hakoniwa_pdu.pdu_msgs.sensor_msgs.pdu_pytype_JointState import JointState
        from hakoniwa_pdu.pdu_msgs.sensor_msgs.pdu_pytype_MultiDOFJointState import MultiDOFJointState
        from hakoniwa_pdu.pdu_msgs.geometry_msgs.pdu_pytype_Transform import Transform
        from hakoniwa_pdu.pdu_msgs.geometry_msgs.pdu_pytype_Twist import Twist
        from hakoniwa_pdu.pdu_msgs.geometry_msgs.pdu_pytype_Wrench import Wrench

        robot = self.config["state_robot"]
        stamp_usec = int(round(self.data.time * 1e6))
        joints = JointState()
        joints.header.frame_id = "city_map"
        joints.header.stamp.sec, joints.header.stamp.nanosec = divmod(stamp_usec, 1_000_000)
        joints.header.stamp.nanosec *= 1000
        states = MultiDOFJointState()
        states.header.frame_id = "city_map"
        states.header.stamp.sec, states.header.stamp.nanosec = joints.header.stamp.sec, joints.header.stamp.nanosec
        names, positions = [], []
        for person in self.people:
            for joint in LIMB_JOINTS:
                names.append(f"{person['name']}/{joint}")
                positions.append(person["angles"][joint])
            transform = Transform()
            x, y, z = self.data.xpos[person["body"]]
            w, qx, qy, qz = self.data.xquat[person["body"]]
            transform.translation.x, transform.translation.y, transform.translation.z = float(x), float(y), float(z)
            transform.rotation.x, transform.rotation.y = float(qx), float(qy)
            transform.rotation.z, transform.rotation.w = float(qz), float(w)
            twist = Twist()
            twist.linear.x = float(self.data.qvel[person["slide_x"]])
            twist.linear.y = float(self.data.qvel[person["slide_y"]])
            states.joint_names.append(person["name"])
            states.transforms.append(transform)
            states.twist.append(twist)
            states.wrench.append(Wrench())
        joints.name, joints.position = names, positions
        joints.velocity, joints.effort = [0.0] * len(names), [0.0] * len(names)
        pdu.flush_pdu_raw_data_nowait(robot, "joint_states", py_to_pdu_JointState(joints))
        pdu.flush_pdu_raw_data_nowait(robot, "vehicle_states", py_to_pdu_MultiDOFJointState(states))

    # --- Stepping --------------------------------------------------------------------

    def step(self) -> None:
        dt = self.delta_usec / 1e6
        for person in self.people:
            east, north, yaw_rate = person["command"]
            yaw = float(self.data.qpos[person["turn_qpos"]])
            for actuator, value in zip(person["actuators"], drive(east, north, yaw_rate, yaw)):
                self.data.ctrl[actuator] = value
            self.data.ctrl[person["lift"]] = self.ground_under(person)
        for _ in range(self.substeps):
            self.mujoco.mj_step(self.model, self.data)
        if any(person["ride"] or person["riding"] for person in self.people):
            for person in self.people:
                self.update_ride(person)
            self.mujoco.mj_forward(self.model, self.data)
        for person in self.people:
            speed = math.hypot(self.data.qvel[person["slide_x"]], self.data.qvel[person["slide_y"]])
            mode = "sit" if person["riding"] else person["animation"]
            person["angles"] = animate(person["gait"], mode, speed, dt, person["scale"])

    def seat(self, ride: str):
        """(vehicle, seat offset in its body frame) of '<vehicle>/<seat>', or None."""
        vehicle, _, seat = ride.partition("/")
        offset = self.config.get("vehicles", {}).get(vehicle, {}).get("seats", {}).get(seat or "driver")
        return (vehicle, tuple(offset)) if offset is not None and vehicle in self.vehicle_poses else None

    def place(self, person, x: float, y: float, z: float, yaw: float) -> None:
        sx, sy = person["spawn_xy"]
        self.data.qpos[person["slide_x_qpos"]] = x - sx
        self.data.qpos[person["slide_y_qpos"]] = y - sy
        self.data.qpos[person["slide_z_qpos"]] = z
        self.data.qpos[person["turn_qpos"]] = yaw
        self.data.ctrl[person["lift"]] = z
        for dof in (person["slide_x"], person["slide_y"]):
            self.data.qvel[dof] = 0.0

    def update_ride(self, person) -> None:
        if person["ride"] and not person["riding"]:
            found = self.seat(person["ride"])
            if found is None:
                return  # no such car or seat (yet): stay
            person["riding"] = found
            self.model.geom_contype[person["collision"]] = 0
            self.model.geom_conaffinity[person["collision"]] = 0
        if not person["riding"]:
            return
        vehicle, (sx, sy, sz) = person["riding"]
        pose = self.vehicle_poses.get(vehicle)
        if pose is None:
            return
        x, y, z, yaw = pose
        c, s = math.cos(yaw), math.sin(yaw)
        if person["ride"]:
            # The hip on the seat, the feet below it (the legs swing forward).
            feet_z = z + sz - HIP_M * person["scale"]
            self.place(person, x + c * sx - s * sy, y + s * sx + c * sy, feet_z, yaw)
            return
        # Get off beside the seat, on the ground, colliding again.
        side = GET_OFF_M + abs(sy)
        ox, oy = sx, math.copysign(side, sy if sy else 1.0)
        self.place(person, x + c * ox - s * oy, y + s * ox + c * oy, 0.0, yaw)
        person["riding"] = None
        self.mujoco.mj_kinematics(self.model, self.data)
        self.data.qpos[person["slide_z_qpos"]] = self.ground_under(person)
        self.data.ctrl[person["lift"]] = self.data.qpos[person["slide_z_qpos"]]
        self.model.geom_contype[person["collision"]] = 1
        self.model.geom_conaffinity[person["collision"]] = 1


def run(config: dict) -> int:
    import hakopy
    from hakoniwa_pdu.impl.shm_communication_service import ShmCommunicationService
    from hakoniwa_pdu.pdu_manager import PduManager

    plant = PeoplePlant(config)
    pdu = PduManager()
    pdu.initialize(config_path=config["pdu_def"], comm_service=ShmCommunicationService())
    pdu.start_service_nowait()
    delta = plant.delta_usec
    publish_every = max(1, round(config.get("state_period_usec", 20000) / delta))

    def on_manual_timing_control(_context):
        start_wall = time.monotonic()
        count = 0
        while True:
            if not hakopy.usleep(delta):
                break
            pdu.run_nowait()
            plant.read_commands(pdu)
            plant.step()
            count += 1
            if count % publish_every == 0:
                plant.write_states(pdu)
            if config.get("realtime", True):
                ahead = start_wall + count * delta / 1e6 - time.monotonic()
                if ahead > 0:
                    time.sleep(ahead)
        return 0

    callbacks = {"on_initialize": lambda _c: 0, "on_simulation_step": None,
                 "on_manual_timing_control": on_manual_timing_control, "on_reset": lambda _c: 0}
    if config.get("owns_conductor", True):
        hakopy.conductor_start(delta, config.get("max_delay_usec", 100_000))
    if not hakopy.asset_register(config["asset_name"], config["pdu_def"], callbacks, delta,
                                 hakopy.HAKO_ASSET_MODEL_PLANT):
        print("ERROR: failed to register the Hakoniwa People asset", file=sys.stderr)
        return 1
    print(f"Hakoniwa People: {len(plant.people)} people, asset {config['asset_name']}", flush=True)
    hakopy.start()
    if config.get("owns_conductor", True):
        hakopy.conductor_stop()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("config", type=Path, help="people plant config written by tools/people_sim.py configure")
    args = parser.parse_args()
    return run(json.loads(args.config.read_text(encoding="utf-8")))


if __name__ == "__main__":
    raise SystemExit(main())
