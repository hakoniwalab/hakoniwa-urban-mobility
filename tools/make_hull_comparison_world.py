#!/usr/bin/env python3
"""Make a comparison World whose one PLATEAU bridge collides as one convex hull.

Demo 3-3 flies a drone under a footbridge of the Tocho City World twice: as
Envsim built it (the bridge is many thin pieces, bridge_piece_*), and as one
mesh made of all those pieces' vertices. MuJoCo collides with a mesh's convex
hull, so that bridge becomes one solid (deck, piers, and the space under it).

Only the collision changes. The new City World job

  <out>/build/world/city-world.xml           the source World MJCF without the
                                             bridge's pieces, plus one mesh
                                             (vertices only) and one geom
  <out>/build/world/city-world-receipt.json  the source receipt, its mjcf
                                             replaced, comparison_only added
  <out>/build/components/...                 the files the MJCF names by a
                                             relative path (the terrain hfield),
                                             copied: the MJCF stays relative
  <out>/viewer/city-world-colliders.glb      the source's collider view

keeps the source's look (city-world.glb), terrain, and every other collider;
the source job is not changed. The receipt names files by absolute path, as
the City World job contract asks (schemas/city-world-job.yaml); a portable
package rewrites them (tools/urban_portable.py).

    python tools/make_hull_comparison_world.py --source-city tokyo-13104-multi-lat35_689-lon139_691 \\
        --bridge brid_75151184 --id tocho-bridge-a-hull --title "..."

registers the result as the City Asset --id (tools/urban_assets.py
register-city). tools/urban_demo_worlds.py build --hull runs it for demo 3-3.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SURFACES = Path("components/bridges/debug/bridge-surfaces.json")   # below the source build
COLLIDER_VIEW = ("city-world-colliders.glb", "city-world-colliders-receipt.json")
PIECE_MESH = re.compile(r'\s*<mesh\b[^>]*?\bname="(bridge_piece_\d+)"[^>]*/>')
PIECE_GEOM = re.compile(r'\s*<geom\b[^>]*?\bname="(bridge_piece_\d+)"[^>]*/>')
VERTEX = re.compile(r'\bvertex="([^"]*)"')
FILE_ATTRIBUTE = re.compile(r'(\bfile=")([^"]+)(")')


class HullWorldError(RuntimeError):
    pass


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def source_files(receipt_path: Path) -> dict:
    """The source City World job's files, from its receipt: the receipt, the
    World MJCF, the Envsim build it is in, and its viewer folder."""
    receipt_path = receipt_path.expanduser().resolve()
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        mjcf = Path(receipt["mjcf"]["path"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HullWorldError(f"City World の receipt が読めません: {receipt_path}: {exc}") from exc
    if not mjcf.is_absolute():
        mjcf = receipt_path.parent / mjcf
    build = mjcf.parent.parent
    # An exported job (Urban's studio-cities) has its own viewer copy; else the build's job has it.
    viewer = next((folder for folder in (receipt_path.parents[2] / "viewer", build.parent / "viewer")
                   if (folder / COLLIDER_VIEW[0]).is_file()), None)
    return {"receipt_path": receipt_path, "receipt": receipt, "mjcf": mjcf, "build": build, "viewer": viewer}


def bridge_pieces(surfaces: dict, bridge: str) -> tuple[str, list[str]]:
    """(the bridge's full id, the ids of its pieces) for a bridge id or its prefix."""
    pieces = surfaces.get("pieces", [])
    bridges = sorted({str(piece.get("bridge_id")) for piece in pieces})
    matches = [item for item in bridges if item.startswith(bridge)]
    if len(matches) != 1:
        found = "見つかりません" if not matches else f"{len(matches)} 個に当てはまります（{', '.join(matches)}）"
        raise HullWorldError(f"橋 {bridge} が{found}。この World の橋: {', '.join(bridges) or 'なし'}")
    full = matches[0]
    return full, [str(piece["id"]) for piece in pieces if piece.get("bridge_id") == full]


def hull_name(bridge_id: str) -> str:
    """bridge_hull_<the first 8 characters of the PLATEAU id> (brid_75151184-... -> bridge_hull_75151184)."""
    return "bridge_hull_" + bridge_id.removeprefix("brid_")[:8]


def replace_bridge(xml: str, pieces: list[str], name: str) -> tuple[str, int]:
    """The MJCF with the pieces' meshes and geoms replaced by one mesh of all
    their vertices and one geom (the first piece's attributes); returns it and
    the number of vertices."""
    ids = set(pieces)
    vertices: list[str] = []
    template: list[str] = []

    def drop_mesh(match: re.Match) -> str:
        if match.group(1) not in ids:
            return match.group(0)
        found = VERTEX.search(match.group(0))
        if found:
            vertices.extend(found.group(1).split())
        return ""

    def drop_geom(match: re.Match) -> str:
        if match.group(1) not in ids:
            return match.group(0)
        template.append(match.group(0).strip())
        return ""

    result = PIECE_MESH.sub(drop_mesh, xml)
    result = PIECE_GEOM.sub(drop_geom, result)
    if not vertices or not template:
        raise HullWorldError(f"MJCF に橋の部品（{', '.join(sorted(ids)[:3])} ...）がありません")
    if len(vertices) % 3:
        raise HullWorldError(f"橋の部品の頂点の数が 3 の倍数ではありません（{len(vertices)}）")
    mesh = f'\n    <mesh name="{name}" vertex="{" ".join(vertices)}" />'
    geom = re.sub(r'\bname="bridge_piece_\d+"', f'name="{name}"', template[0])
    geom = re.sub(r'\bmesh="bridge_piece_\d+"', f'mesh="{name}"', geom)
    if "</asset>" not in result or "</worldbody>" not in result:
        raise HullWorldError("MJCF に </asset> か </worldbody> がありません")
    result = result.replace("</asset>", mesh + "\n  </asset>", 1)
    result = result.replace("</worldbody>", "  " + geom + "\n  </worldbody>", 1)
    return result, len(vertices) // 3


def relative_files(xml: str) -> list[str]:
    """The relative file= paths of an MJCF (MuJoCo reads them from its folder)."""
    return sorted({value for _, value, _ in FILE_ATTRIBUTE.findall(xml)
                   if not Path(value).is_absolute() and not re.match(r"^[A-Za-z]:[\\/]", value)})


def make_hull_world(source_receipt: Path, bridge: str, out: Path, source_id: str | None = None,
                    force: bool = False) -> dict:
    """Write the comparison City World job at out (see the module doc); returns a summary."""
    source = source_files(source_receipt)
    out = out.expanduser().resolve()
    if out.exists() and not force:
        raise HullWorldError(f"{out} はもうあります（作り直すときは --force）")
    surfaces_path = source["build"] / SURFACES
    try:
        surfaces = json.loads(surfaces_path.read_text(encoding="utf-8"))
        xml = source["mjcf"].read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        raise HullWorldError(f"元の City World が読めません（橋の付いた PLATEAU の City World が必要です）: {exc}") from exc
    if source["viewer"] is None:
        raise HullWorldError(f"元の City World に当たり判定の表示（viewer/{COLLIDER_VIEW[0]}）がありません")
    bridge_id, pieces = bridge_pieces(surfaces, bridge)
    name = hull_name(bridge_id)
    world_xml, vertex_count = replace_bridge(xml, pieces, name)

    staging = out.with_name(out.name + ".partial")
    shutil.rmtree(staging, ignore_errors=True)
    world = staging / "build" / "world"
    world.mkdir(parents=True)
    try:
        for relative in relative_files(world_xml):
            target = (world / relative).resolve()
            if staging.resolve() not in target.parents:
                raise HullWorldError(f"MJCF のファイル {relative} が World の外を指しています")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2((source["mjcf"].parent / relative).resolve(), target)
        data = world_xml.encode("utf-8")
        (world / "city-world.xml").write_bytes(data)
        (staging / "viewer").mkdir()
        for file_name in COLLIDER_VIEW:
            if (source["viewer"] / file_name).is_file():
                shutil.copy2(source["viewer"] / file_name, staging / "viewer" / file_name)
        receipt = dict(source["receipt"])
        receipt["mjcf"] = {"path": str(out / "build" / "world" / "city-world.xml"), "sha256": _sha256(data)}
        receipt["comparison_only"] = {
            "purpose": "demo 3-3 comparison: one PLATEAU bridge put into MuJoCo as a single mesh (its convex hull collides)",
            "bridge_id": bridge_id, "pieces_replaced": len(pieces), "hull_geom": name, "hull_vertices": vertex_count,
            **({"source_city": source_id} if source_id else {}),
            "source_job": str(source["build"].parent),
            "made_by": "hakoniwa-urban-mobility tools/make_hull_comparison_world.py",
        }
        (world / "city-world-receipt.json").write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n",
                                                       encoding="utf-8")
        if out.exists():
            shutil.rmtree(out)
        os.replace(staging, out)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {"job": str(out), "receipt": str(out / "build" / "world" / "city-world-receipt.json"),
            "bridge_id": bridge_id, "pieces_replaced": len(pieces), "hull_geom": name, "hull_vertices": vertex_count}


def city_receipt(asset_id: str) -> Path:
    """The receipt of a registered City Asset."""
    sys.path.insert(0, str(ROOT / "tools"))
    import urban_assets

    asset = urban_assets.catalog().get(asset_id)
    if asset is None or asset.kind != "city":
        raise HullWorldError(f"City Asset {asset_id} がありません（先に元の City を作って登録してください）")
    receipt = asset.resolve(str(asset.data["receipt"]))
    if not receipt.is_file():
        raise HullWorldError(f"City Asset {asset_id} の receipt がありません: {receipt}")
    return receipt


def register(receipt: Path, asset_id: str, title: str | None, precompile: bool = True) -> int:
    """Register the job as a City Asset (urban_assets.py register-city: the contract check, then the height model)."""
    command = [sys.executable, str(ROOT / "tools" / "urban_assets.py"), "register-city", "--receipt", str(receipt),
               "--id", asset_id, *(["--title", title] if title else []), *([] if precompile else ["--no-precompile"])]
    return subprocess.run(command, cwd=ROOT, check=False).returncode


def default_out(asset_id: str) -> Path:
    sys.path.insert(0, str(ROOT / "tools"))
    import urban_manifest

    return urban_manifest.work_dir() / "urban" / "comparison-worlds" / asset_id


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = result.add_mutually_exclusive_group(required=True)
    source.add_argument("--source-city", help="the City Asset id of the source World")
    source.add_argument("--source-receipt", type=Path, help="the source City World receipt (instead of --source-city)")
    result.add_argument("--bridge", required=True, help="the PLATEAU bridge id, or its prefix (brid_75151184)")
    result.add_argument("--id", required=True, help="the City Asset id of the comparison World")
    result.add_argument("--title", help="the name Urban Studio shows for it")
    result.add_argument("--out", type=Path, help="the job folder (default: <work>/urban/comparison-worlds/<id>)")
    result.add_argument("--force", action="store_true", help="replace the job folder when it is there")
    result.add_argument("--no-register", action="store_true", help="only write the job")
    result.add_argument("--no-precompile", action="store_true", help="register without the height model compile")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        receipt = args.source_receipt or city_receipt(args.source_city)
        out = args.out or default_out(args.id)
        summary = make_hull_world(receipt, args.bridge, out, args.source_city, args.force)
    except HullWorldError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"比較用の World を作りました: {summary['job']}")
    print(f"  橋 {summary['bridge_id']} の部品 {summary['pieces_replaced']} 個 -> {summary['hull_geom']}"
          f"（頂点 {summary['hull_vertices']} 個の凸包）")
    if args.no_register:
        return 0
    return register(Path(summary["receipt"]), args.id, args.title, not args.no_precompile)


if __name__ == "__main__":
    raise SystemExit(main())
