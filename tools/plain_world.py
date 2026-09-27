#!/usr/bin/env python3
"""Materialize a plain World as a City World job (asset-contract 6.2).

A plain World (World YAML: ground and optional gate / pylon / box obstacles)
is written in the layout of a City World Worker job, so the existing City
builders (tools/multi_car.py, tools/drone_one.py, tools/urban_composer.py)
run on it unchanged:

  <job>/build/world/city-world-receipt.json   receipt, with "kind": "plain"
  <job>/build/world/city-world.xml            flat hfield terrain + obstacles
  <job>/build/world/city-world.glb            the same geometry for Three.js
  <job>/viewer/city-world-colliders.glb       collider view (same geometry)
  <job>/build/components/terrain/             terrain.xml, terrain.hf, terrain-receipt.json

The ground is a flat hfield like a City terrain; obstacles come from the FPV
generator (generate_world_mujoco), so their geometry equals the FPV course.
A plain World has no geographic origin; viewers open Three.js directly.
Jobs are cached by the World YAML and the generator sources.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import shutil
import struct
import sys
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
BUSINESS_PACK = WORKSPACE / "hakoniwa-business-pack"
JOBS_DIR = BUSINESS_PACK / "work/urban/worlds"
FPV_GENERATOR_SRC = WORKSPACE / "hakoniwa-fpv-drone/src"
MJCF_FRAME = "X=North,Y=-East,Z=Up"
GLB_FRAME = "X=East,Y=Up,Z=-North"
# A flat hfield: 2 x 2 zero samples. MuJoCo needs a positive elevation range.
HFIELD_ELEVATION_M = 0.001
HFIELD_BASE_M = 0.1
GROUND_THICKNESS_M = 0.02


class PlainWorldError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fpv_generator():
    if str(FPV_GENERATOR_SRC) not in sys.path:
        sys.path.insert(0, str(FPV_GENERATOR_SRC))
    from fpv_drone_generator.generators.mujoco import generate_world_mujoco
    from fpv_drone_generator.world import load_world

    return load_world, generate_world_mujoco


def job_key(world_yaml: Path) -> str:
    generator = FPV_GENERATOR_SRC / "fpv_drone_generator/generators/mujoco.py"
    digest = hashlib.sha256()
    for path in (world_yaml, generator, Path(__file__)):
        digest.update(path.read_bytes())
    return f"{world_yaml.stem}-{digest.hexdigest()[:16]}"


def _yaw_quat(euler: str) -> str:
    """Convert the FPV generator's 'rx ry rz' degrees (yaw only) to a quaternion."""
    rx, ry, rz = (float(value) for value in euler.split())
    if rx or ry:
        raise PlainWorldError(f"plain World obstacles rotate about Z only: euler={euler}")
    half = math.radians(rz) / 2.0
    return f"{math.cos(half):.12g} 0 0 {math.sin(half):.12g}"


def _obstacle_bodies(world, scratch: Path) -> list[ET.Element]:
    """Return the course obstacle bodies with radian-free (quaternion) rotations.

    City World MJCFs use the default radian angles, so the FPV generator's
    degree Euler angles are rewritten before the bodies are merged.
    """
    _, generate_world_mujoco = _fpv_generator()
    generated = scratch / "fpv-world.xml"
    generate_world_mujoco(world, generated)
    bodies = []
    for body in ET.parse(generated).getroot().find("worldbody"):
        if body.tag != "body":
            continue
        euler = body.attrib.pop("euler", None)
        if euler is not None:
            body.set("quat", _yaw_quat(euler))
        bodies.append(body)
    generated.unlink()
    return bodies


def _numbers(values) -> str:
    return " ".join(f"{float(value):.12g}" for value in values)


def _write_terrain(terrain_dir: Path, half_ns: float, half_ew: float, rgba) -> tuple[Path, Path]:
    hfield = terrain_dir / "terrain.hf"
    hfield.write_bytes(struct.pack("<ii4f", 2, 2, 0.0, 0.0, 0.0, 0.0))
    size = f"{half_ns:.12g} {half_ew:.12g} {HFIELD_ELEVATION_M} {HFIELD_BASE_M}"
    terrain_xml = terrain_dir / "terrain.xml"
    terrain_xml.write_text(
        '<mujoco model="plain_terrain">\n'
        f'  <asset>\n    <hfield name="plateau_terrain" file="terrain.hf" size="{size}"/>\n  </asset>\n'
        '  <worldbody>\n'
        f'    <geom name="plateau_ground" type="hfield" hfield="plateau_terrain" pos="0 0 0" rgba="{_numbers(rgba)}"/>\n'
        '  </worldbody>\n</mujoco>\n',
        encoding="utf-8",
    )
    (terrain_dir / "terrain-receipt.json").write_text(json.dumps({
        "schema_version": 1,
        "kind": "plain",
        "half_extent_m": {"north_south": half_ns, "east_west": half_ew},
        "coordinate_system": MJCF_FRAME,
        "nrow": 2,
        "ncol": 2,
        "minimum_altitude_m": 0.0,
        "maximum_altitude_m": 0.0,
        "altitude_offset_m": 0.0,
        "hfield": {"path": str(hfield), "sha256": _sha256(hfield)},
        "mjcf": str(terrain_xml),
    }, indent=2) + "\n", encoding="utf-8")
    return terrain_xml, hfield


def _write_world_mjcf(path: Path, world, bodies: list[ET.Element], half_ns: float, half_ew: float) -> None:
    root = ET.Element("mujoco", {"model": "plain_world"})
    asset = ET.SubElement(root, "asset")
    ET.SubElement(asset, "hfield", {
        "name": "plateau_terrain",
        "file": "../components/terrain/terrain.hf",
        "size": f"{half_ns:.12g} {half_ew:.12g} {HFIELD_ELEVATION_M} {HFIELD_BASE_M}",
    })
    worldbody = ET.SubElement(root, "worldbody")
    ET.SubElement(worldbody, "geom", {
        "name": "plateau_ground",
        "type": "hfield",
        "hfield": "plateau_terrain",
        "pos": "0 0 0",
        "rgba": _numbers(world.ground_rgba),
        "friction": _numbers(world.contact.ground_friction),
    })
    worldbody.extend(bodies)
    ET.indent(root, space="  ")
    path.write_text(ET.tostring(root, encoding="unicode") + "\n", encoding="utf-8")


def _quat_matrix(quat: str):
    import numpy

    w, x, y, z = (float(value) for value in quat.split())
    return numpy.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])


def _write_glb(path: Path, world, bodies: list[ET.Element], half_ns: float, half_ew: float) -> int:
    """Write the ground and obstacles as a GLB in the City World GLB frame."""
    import numpy
    import trimesh

    def colored(mesh, rgba):
        # Vertex colors: face colors would need scipy to convert on export.
        color = [int(round(255 * value)) for value in rgba]
        mesh.visual = trimesh.visual.ColorVisuals(mesh, vertex_colors=numpy.tile(color, (len(mesh.vertices), 1)))
        return mesh

    meshes = []
    ground = trimesh.creation.box(extents=[2 * half_ns, 2 * half_ew, GROUND_THICKNESS_M])
    ground.apply_translation([0.0, 0.0, -GROUND_THICKNESS_M / 2.0])
    meshes.append(colored(ground, world.ground_rgba))
    for body in bodies:
        body_position = numpy.array([float(value) for value in body.get("pos", "0 0 0").split()])
        body_rotation = _quat_matrix(body.get("quat", "1 0 0 0"))
        for geom in body.iter("geom"):
            size = [float(value) for value in geom.get("size", "").split()]
            kind = geom.get("type", "sphere")
            if kind == "box":
                mesh = trimesh.creation.box(extents=[2 * value for value in size])
            elif kind == "cylinder":
                mesh = trimesh.creation.cylinder(radius=size[0], height=2 * size[1], sections=32)
            else:
                raise PlainWorldError(f"plain World GLB does not support {kind} geoms")
            transform = numpy.eye(4)
            transform[:3, :3] = body_rotation
            transform[:3, 3] = body_position + body_rotation @ numpy.array(
                [float(value) for value in geom.get("pos", "0 0 0").split()]
            )
            mesh.apply_transform(transform)
            rgba = [float(value) for value in geom.get("rgba", "0.7 0.7 0.7 1").split()]
            meshes.append(colored(mesh, rgba))
    # MJCF (X=North, Y=-East, Z=Up) -> GLB (X=East, Y=Up, Z=-North).
    to_glb = numpy.array([
        [0.0, -1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [-1.0, 0.0, 0.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ])
    scene = trimesh.Scene()
    for index, mesh in enumerate(meshes):
        mesh.apply_transform(to_glb)
        scene.add_geometry(mesh, node_name=f"plain_world_{index}")
    path.write_bytes(scene.export(file_type="glb"))
    return len(meshes)


def materialize(world_yaml: Path, *, jobs_dir: Path | None = None) -> Path:
    """Return the City World receipt of a plain World, generating the job once."""
    world_yaml = world_yaml.expanduser().resolve()
    if not world_yaml.is_file():
        raise PlainWorldError(f"plain World YAML not found: {world_yaml}")
    job = (jobs_dir or JOBS_DIR) / job_key(world_yaml)
    receipt_path = job / "build/world/city-world-receipt.json"
    if receipt_path.is_file():
        return receipt_path

    load_world, _ = _fpv_generator()
    world = load_world(world_yaml)
    half_ns, half_ew = (float(value) for value in world.ground_size_m)
    staging = job.with_name(job.name + ".partial")
    shutil.rmtree(staging, ignore_errors=True)
    world_dir = staging / "build/world"
    terrain_dir = staging / "build/components/terrain"
    viewer_dir = staging / "viewer"
    for directory in (world_dir, terrain_dir, viewer_dir):
        directory.mkdir(parents=True)
    try:
        bodies = _obstacle_bodies(world, staging)
        _write_terrain(terrain_dir, half_ns, half_ew, world.ground_rgba)
        _write_world_mjcf(world_dir / "city-world.xml", world, bodies, half_ns, half_ew)
        mesh_count = _write_glb(world_dir / "city-world.glb", world, bodies, half_ns, half_ew)
        shutil.copy2(world_dir / "city-world.glb", viewer_dir / "city-world-colliders.glb")
        staging.replace(job)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    world_dir = job / "build/world"
    mjcf, glb = world_dir / "city-world.xml", world_dir / "city-world.glb"
    terrain_xml = job / "build/components/terrain/terrain.xml"
    # The terrain receipt was written in staging; point it at the final job.
    terrain_receipt = terrain_xml.with_name("terrain-receipt.json")
    data = json.loads(terrain_receipt.read_text(encoding="utf-8"))
    data["hfield"]["path"] = str(terrain_xml.with_name("terrain.hf"))
    data["mjcf"] = str(terrain_xml)
    terrain_receipt.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    receipt_path.write_text(json.dumps({
        "schema_version": 1,
        "kind": "plain",
        "world_yaml": str(world_yaml),
        "world_frame": str(world_dir),
        "coordinate_frame": {
            "schema_version": 1,
            # A plain World has no geographic origin; viewers must not map it.
            "origin": {"latitude": 0.0, "longitude": 0.0, "altitude_offset_m": 0.0},
            "half_extent_m": {"north_south": half_ns, "east_west": half_ew},
            "coordinate_systems": {"mjcf": MJCF_FRAME, "glb": GLB_FRAME},
            "altitude_reference": "plain World ground at z = 0",
        },
        "mjcf": {"path": str(mjcf), "sha256": _sha256(mjcf)},
        "glb": {"path": str(glb), "bytes": glb.stat().st_size, "sha256": _sha256(glb)},
        "components": {
            "terrain_xml": str(terrain_xml),
            "extra_mjcf": [],
            "mjcf_geom_counts": {"terrain": 1, "obstacles": len(world.obstacles)},
            "glb_mesh_count": mesh_count,
        },
    }, indent=2) + "\n", encoding="utf-8")
    return receipt_path


def is_plain(receipt: dict) -> bool:
    return receipt.get("kind") == "plain"
