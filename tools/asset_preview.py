"""Vehicle Asset previews for Urban Studio.

A vehicle manifest may declare how its display model is assembled:

    preview:
      format: drone-type            # hakoniwa-threejs-drone drone_types-*.json
      path: ${repo:hakoniwa-threejs-drone}/config/drone_types-hexa-eams.json
      type: hexa_eams

    preview:
      format: view-model            # hakoniwa-mbody-registry hako_viewer_model
      path: ${repo:hakoniwa-mbody-registry}/bodies/.../view-model.json

    preview:
      format: fpv-assembly          # hakoniwa-fpv-drone Assembly Graph
      path: ${repo:hakoniwa-fpv-drone}/recipes/examples/<name>.assembly.json

An FPV Assembly is turned into GLBs and a drone type once, by the FPV
generator's Three.js exporter, and kept under work/urban/cache/preview/.

preview_parts() flattens either into GLB parts, each placed in the vehicle
frame (ROS / MuJoCo: X forward, Y left, Z up) by a position, a quaternion
(x, y, z, w) and a uniform scale. A part's basis says how its GLB is
authored: "three" (glTF Y-up, the Drone models) or "flu" (X forward, Y left,
Z up, the MuJoCo view models). The Studio renders them in three.js.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import sys

import urban_assets

FORMATS = {"drone-type", "view-model", "fpv-assembly"}
FPV_ROOT = urban_assets.WORKSPACE / "hakoniwa-fpv-drone"
CACHE_ROOT = urban_assets.BUSINESS_PACK / "work/urban/cache/preview"


class PreviewError(RuntimeError):
    pass


def _quaternion_from_rpy(rpy, degrees: bool) -> tuple[float, float, float, float]:
    """ROS roll-pitch-yaw (R = Rz(yaw) Ry(pitch) Rx(roll)) as (x, y, z, w)."""
    roll, pitch, yaw = (
        math.radians(float(value)) if degrees else float(value) for value in (rpy or (0, 0, 0))
    )
    cr, sr = math.cos(roll / 2), math.sin(roll / 2)
    cp, sp = math.cos(pitch / 2), math.sin(pitch / 2)
    cy, sy = math.cos(yaw / 2), math.sin(yaw / 2)
    return (
        sr * cp * cy - cr * sp * sy,
        cr * sp * cy + sr * cp * sy,
        cr * cp * sy - sr * sp * cy,
        cr * cp * cy + sr * sp * sy,
    )


def _multiply(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def _rotate(q, v):
    x, y, z, w = q
    conjugate = (-x, -y, -z, w)
    rx, ry, rz, _ = _multiply(_multiply(q, (*v, 0.0)), conjugate)
    return (rx, ry, rz)


def _pose(xyz=None, rpy=None, *, degrees: bool):
    return (tuple(float(value) for value in (xyz or (0, 0, 0))), _quaternion_from_rpy(rpy, degrees))


def _compose(parent, child):
    (pp, pq), (cp, cq) = parent, child
    offset = _rotate(pq, cp)
    return (tuple(p + o for p, o in zip(pp, offset)), _multiply(pq, cq))


def _part(path: Path, pose, basis: str, scale: float = 1.0) -> dict:
    position, quaternion = pose
    return {
        "path": path,
        "basis": basis,
        "position": [round(value, 6) for value in position],
        "quaternion": [round(value, 6) for value in quaternion],
        "scale": float(scale),
    }


def _load_json(path: Path, label: str):
    if not path.is_file():
        raise PreviewError(f"{label} not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise PreviewError(f"invalid {label}: {path}: {error}") from error


def _drone_type_parts(path: Path, type_name: str) -> list[dict]:
    types = _load_json(path, "drone type definitions")
    drone = types.get(type_name)
    if not isinstance(drone, dict):
        raise PreviewError(f"drone type {type_name} not found: {path}")
    base = path.parent
    parts = []
    frame = drone.get("model")
    if frame:
        parts.append(_part(
            (base / frame["model_path"]).resolve(),
            _pose(frame.get("pos"), frame.get("hpr"), degrees=True),
            "three",
            frame.get("scale", 1.0),
        ))
    # Rotors and mounted cameras: the mount pose, then the model's own offset.
    for mounted in [*drone.get("rotors", []), *drone.get("cameras", [])]:
        model = mounted.get("model")
        if not model:
            continue
        pose = _compose(
            _pose(mounted.get("pos"), mounted.get("hpr"), degrees=True),
            _pose(model.get("pos"), model.get("hpr"), degrees=True),
        )
        parts.append(_part((base / model["model_path"]).resolve(), pose, "three", model.get("scale", 1.0)))
    return parts


def _view_model_parts(path: Path) -> list[dict]:
    view = _load_json(path, "view model")
    if view.get("format") != "hako_viewer_model":
        raise PreviewError(f"not a hako_viewer_model: {path}")
    assets = {item["id"]: (path.parent / item["path"]).resolve() for item in view.get("assets", [])}
    base = view["base"]
    # Mount angles are radians (hakoniwa-threejs-drone src/vehicle.js).
    mount = base.get("mount", {})
    poses = {base["name"]: _pose(mount.get("xyz"), mount.get("rpy"), degrees=False)}
    parts = [_part(assets[base["asset"]], poses[base["name"]], "flu")] if base.get("asset") else []
    pending = [*view.get("fixed_parts", []), *view.get("movable_parts", [])]
    while pending:
        ready = [item for item in pending if item["parent"] in poses]
        if not ready:
            raise PreviewError(f"view model parts have unknown parents: {[item['name'] for item in pending]}")
        for item in ready:
            mount = item.get("mount", {})
            poses[item["name"]] = _compose(
                poses[item["parent"]], _pose(mount.get("xyz"), mount.get("rpy"), degrees=False)
            )
            if item.get("asset"):
                parts.append(_part(assets[item["asset"]], poses[item["name"]], "flu"))
            pending.remove(item)
    return parts


def _fpv_assembly_types(assembly: Path, asset_id: str) -> tuple[Path, str]:
    """Export an FPV Assembly's display model once; return (drone types, type name)."""
    if not assembly.is_file():
        raise PreviewError(f"FPV Assembly not found: {assembly}")
    exporter = FPV_ROOT / "src/fpv_drone_generator/threejs_assets.py"
    digest = hashlib.sha256()
    for path in (assembly, exporter):
        if not path.is_file():
            raise PreviewError(f"FPV generator not found: {path}")
        digest.update(path.read_bytes())
    output = CACHE_ROOT / f"{asset_id}-{digest.hexdigest()[:16]}"
    types_path = output / "drone-types.json"
    if not types_path.is_file():
        source = str(FPV_ROOT / "src")
        if source not in sys.path:
            sys.path.insert(0, source)
        try:
            from fpv_drone_generator.assembly import load_assembly_graph, resolve_assembly
            from fpv_drone_generator.catalog import load_catalogs
            from fpv_drone_generator.threejs_assets import export_threejs_assets

            resolved = resolve_assembly(load_assembly_graph(assembly), load_catalogs(FPV_ROOT / "catalogs"))
            export_threejs_assets(resolved, output)
        except Exception as error:  # noqa: BLE001 - any generator failure means no preview
            raise PreviewError(f"FPV preview generation failed for {asset_id}: {error}") from error
    types = _load_json(types_path, "FPV drone types")
    return types_path, next(iter(types))


def validate(preview, asset_id: str) -> None:
    if preview is None:
        return
    if not isinstance(preview, dict) or preview.get("format") not in FORMATS:
        raise PreviewError(f"Asset {asset_id} preview.format must be one of {sorted(FORMATS)}")
    if not isinstance(preview.get("path"), str):
        raise PreviewError(f"Asset {asset_id} preview has no path")
    if preview["format"] == "drone-type" and not isinstance(preview.get("type"), str):
        raise PreviewError(f"Asset {asset_id} drone-type preview needs a type")


def preview_parts(asset) -> list[dict]:
    """The GLB parts of a vehicle Asset's preview ([] when it declares none)."""
    preview = asset.data.get("preview")
    if preview is None:
        return []
    validate(preview, asset.id)
    path = asset.resolve(preview["path"])
    if preview["format"] == "drone-type":
        parts = _drone_type_parts(path, preview["type"])
    elif preview["format"] == "fpv-assembly":
        parts = _drone_type_parts(*_fpv_assembly_types(path, asset.id))
    else:
        parts = _view_model_parts(path)
    missing = [str(part["path"]) for part in parts if not part["path"].is_file()]
    if missing:
        raise PreviewError(f"Asset {asset.id} preview models not found: {missing}")
    return parts
