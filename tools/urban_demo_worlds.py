#!/usr/bin/env python3
"""Make the demo Worlds of demos 3-1 to 3-5 again on this Workspace.

The demos name three City Assets (demos/demo-worlds.json):

  tokyo-13104-multi-lat35_689-lon139_691  Tocho: a PLATEAU City World made in
                                          Environment Studio (demos 3-1 to 3-4,
                                          3-5 koen)
  sapporo-rotary-snow                     the Sapporo Station south rotary in
                                          snow: an Environment Studio Recipe on
                                          a PLATEAU import (demo 3-5)
  tocho-bridge-a-hull                     Tocho with footbridge A as one convex
                                          hull (demo 3-3 comparison;
                                          tools/make_hull_comparison_world.py)

    python tools/urban_demo_worlds.py build --all        demos, tocho, sapporo, hull, in this order
    python tools/urban_demo_worlds.py build --tocho      (or --sapporo, --hull, --demos; several may be given)
    python tools/urban_demo_worlds.py check [--adopt]    are the three Cities and the demo Compositions there

--demos copies the demo Compositions, route and flight scenarios, and the
people scene (demos/urban) into the work directory. Tocho and Sapporo
download PLATEAU (internet; tens of minutes). Tocho is meant to be made in
Environment Studio's map page (demos/README.md lists the values); --tocho is
the same build without the page. What is there already is kept (--force
makes it again). It runs in the Business Pack Workspace (Windows too: no
shell needed):

    python tools/workspace.py run -- python ../hakoniwa-urban-mobility/tools/urban_demo_worlds.py build --all
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
DEMOS = ROOT / "demos"
SPEC = DEMOS / "demo-worlds.json"
POLL_SEC = 5.0
STARTER_CATALOG = Path("catalogs") / "starter" / "catalog.yaml"   # in hakoniwa-environment-studio

sys.path.insert(0, str(ROOT / "tools"))


class DemoWorldError(RuntimeError):
    pass


def say(message: str) -> None:
    print(message, flush=True)


def load_spec(path: Path = SPEC) -> dict:
    spec = json.loads(path.read_text(encoding="utf-8"))
    spec["_base"] = path.parent
    return spec


def spec_file(spec: dict, relative: str) -> Path:
    return Path(spec["_base"]) / relative


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def work_dir() -> Path:
    import urban_manifest

    return urban_manifest.work_dir()


def environment_studio() -> Path:
    """Environment Studio's checkout ($HAKONIWA_ENVIRONMENT_STUDIO_ROOT, else next to this repository)."""
    import urban_city_authoring

    return urban_city_authoring.studio_root()


def _environment_studio():
    """Environment Studio's modules (its tools folder); they need the Workspace."""
    tools = environment_studio() / "tools"
    if not (tools / "env_cityworld.py").is_file():
        raise DemoWorldError(f"hakoniwa-environment-studio がありません: {tools.parent}")
    if str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    import env_cityworld
    import env_urban
    import env_workspace

    return env_cityworld, env_urban, env_workspace


# --- The Environment Studio id of a PLATEAU City World ------------------------------------

PREFECTURES = (
    "hokkaido aomori iwate miyagi akita yamagata fukushima ibaraki tochigi gunma saitama chiba tokyo "
    "kanagawa niigata toyama ishikawa fukui yamanashi nagano gifu shizuoka aichi mie shiga kyoto osaka "
    "hyogo nara wakayama tottori shimane okayama hiroshima yamaguchi tokushima kagawa ehime kochi fukuoka "
    "saga nagasaki kumamoto oita miyazaki kagoshima okinawa").split()


def _fixed(value: float, digits: int) -> str:
    """JavaScript's Number.prototype.toFixed (the page's rounding, half away from zero)."""
    from decimal import ROUND_HALF_UP, Decimal

    return str(Decimal(value).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP))


def studio_city_world_id(center: tuple[float, float], city_codes: list[str]) -> str:
    """The id Environment Studio's map page gives a PLATEAU City World
    (web/cityworlds.js generatedJobId): <prefecture>-<first city code>[-multi]
    -lat<3 decimals>-lon<3 decimals>, "." as "_". The city codes are the
    municipalities of every PLATEAU file the area's inspection selects."""
    codes = sorted(set(city_codes))
    city = codes[0] if codes else "00000"
    prefecture_index = int(city[:2]) if city[:2].isdigit() else 0
    prefecture = (PREFECTURES[prefecture_index - 1] if 1 <= prefecture_index <= len(PREFECTURES)
                  else f"pref{city[:2]}")
    multiple = "-multi" if len(codes) > 1 else ""
    return (f"{prefecture}-{city}{multiple}-lat{_fixed(center[0], 3)}-lon{_fixed(center[1], 3)}").replace(".", "_")


def studio_title(inspection: dict, selection: dict) -> str:
    """The page's name for it: the buildings' municipalities and the size."""
    names = inspection.get("building_municipalities") or list(
        dict.fromkeys(item["city"] for item in inspection.get("municipalities", [])))
    half = selection["half_extent_m"]
    east_west, north_south = round(half["east_west"] * 2), round(half["north_south"] * 2)
    size = f"{east_west} m 四方" if half["east_west"] == half["north_south"] else f"{east_west} × {north_south} m"
    return f"{'・'.join(names) or 'PLATEAU'} 付近（{size}）"


def check_studio_id(request: dict) -> None:
    """Ask PLATEAU (no download) which municipalities the area has, as the
    page's 診断 does, and say whether the page would give the demo id."""
    _environment_studio()
    import env_plateau

    selection = request["selection"]
    center = (selection["center"]["latitude"], selection["center"]["longitude"])
    half = (selection["half_extent_m"]["north_south"], selection["half_extent_m"]["east_west"])
    try:
        inspection = env_plateau.inspect(center, half)
    except env_plateau.InspectionError as exc:
        raise DemoWorldError(f"PLATEAU のカタログに問い合わせできません（インターネット接続が必要です）: {exc}") from exc
    if inspection.get("status") != "available":
        raise DemoWorldError(f"PLATEAU にこの範囲のデータがそろっていません: {inspection.get('reason')}")
    page_id = studio_city_world_id(center, [item["city_code"] for item in inspection["municipalities"]])
    cities = ", ".join("{} ({})".format(item["city"], item["year"]) for item in inspection["municipalities"])
    say(f"  PLATEAU: {cities}")
    if page_id == request["id"]:
        say(f"  Environment Studio の画面でも同じ ID になります: {page_id}")
    else:
        say(f"  注意: 画面で作ると ID は {page_id} になります（PLATEAU の市区町村が変わったため）。"
            f"デモは {request['id']} を使うので、このスクリプトはその ID で作ります。"
            "画面で作ったときは check --adopt でデモの ID を付けてください。")
    title = studio_title(inspection, selection)
    if title != request.get("name"):
        say(f"  （画面での名前は「{title}」になります）")


# --- City World builds ------------------------------------------------------------------------

def build_city_world(request: dict, force: bool = False) -> Path:
    """Build a PLATEAU City World as Environment Studio's page does
    (env_cityworld: Envsim's build, then the viewer files); returns its build
    folder. One already built with this id is used again unless force."""
    env_cityworld, _env_urban, env_workspace = _environment_studio()
    job_id = request["id"]
    build = env_cityworld.WORK / job_id / "build"
    if (build / "world" / "city-world-receipt.json").is_file() and (build.parent / "viewer").is_dir() and not force:
        say(f"  City World {job_id} はもうあります（作り直すときは --force）: {build}")
        return build
    roots = [env_workspace.work_dir(), env_cityworld.WORK.resolve()]
    body = {key: value for key, value in request.items()}
    body["overwrite"] = True
    try:
        status = env_cityworld.BUILDS.start(body, roots)
    except env_cityworld.BuildError as exc:
        raise DemoWorldError(f"City World の生成を始められません: {exc}") from exc
    say(f"  生成中（ログ: {status['log']}）")
    shown = None
    try:
        while status["state"] == "running":
            time.sleep(POLL_SEC)
            status = env_cityworld.BUILDS.status(job_id)
            progress = status.get("progress") or {}
            line = f"  {progress.get('percent', 0)}% {progress.get('message', '')}".rstrip()
            if line != shown:
                say(line)
                shown = line
    except KeyboardInterrupt:
        say("  中止します…")
        env_cityworld.BUILDS.cancel(job_id)
        raise
    if status["state"] != "done":
        lines = status.get("errors") or status.get("log_tail", [])[-8:]
        detail = "\n    ".join(lines)
        hint = ""
        if (status.get("failure") or {}).get("code") == "DEM_UNCOVERED":
            hint = "\n  地形（DEM）が範囲を覆っていません（PLATEAU のデータが変わった可能性があります）"
        raise DemoWorldError(f"City World {job_id} を作れませんでした（{status['state']}、ログ: {status['log']}）"
                             f"\n    {detail}{hint}")
    say(f"  City World ができました: {status['build']}")
    return Path(status["build"])


def registered_city(asset_id: str):
    """The registered City Asset with this id whose receipt is there, or None."""
    import urban_assets

    asset = urban_assets.catalog().get(asset_id)
    if asset is None or asset.kind != "city":
        return None
    return asset if urban_assets.city_receipt_available(asset) else None


def register_city(receipt: Path, asset_id: str, title: str, precompile: bool) -> None:
    """urban_assets.py register-city (the City World job check, then the height model)."""
    say(f"  City Asset {asset_id} を登録します" + ("（高さモデルの compile に数分かかります）" if precompile else ""))
    command = [sys.executable, str(ROOT / "tools" / "urban_assets.py"), "register-city", "--receipt", str(receipt),
               "--id", asset_id, "--title", title, *([] if precompile else ["--no-precompile"])]
    if subprocess.run(command, cwd=ROOT, check=False).returncode != 0:
        raise DemoWorldError(f"City Asset {asset_id} を登録できませんでした（{receipt}）")


# --- Steps ----------------------------------------------------------------------------------------

def install_demos(spec: dict, work: Path, force: bool = False) -> dict:
    """Copy demos/urban (Compositions, scenarios, scenes) into work/urban.
    A file that is there with other contents is kept (an edit), unless force."""
    source = spec_file(spec, spec["demos"]["files"])
    counts = {"copied": 0, "same": 0, "kept": 0}
    for path in sorted(item for item in source.rglob("*") if item.is_file()):
        target = work / "urban" / path.relative_to(source)
        if target.is_file() and target.read_bytes() == path.read_bytes():
            counts["same"] += 1
            continue
        if target.exists() and not force:
            say(f"  そのままにします（内容が違います。置き換えるときは --force）: {target}")
            counts["kept"] += 1
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        counts["copied"] += 1
    say(f"  コピー {counts['copied']}・同じ {counts['same']}・そのまま {counts['kept']}（{work / 'urban'}）")
    return counts


def build_tocho(spec: dict, force: bool = False, from_build: Path | None = None, precompile: bool = True) -> Path:
    """The Tocho City World, exported to Urban's folder (as the page's
    export does) and registered with the demo id."""
    import urban_manifest

    tocho = spec["tocho"]
    request = read_json(spec_file(spec, tocho["request"]))
    existing = registered_city(tocho["asset_id"])
    if existing is not None and not force:
        say(f"  City {tocho['asset_id']} はもう登録されています（作り直すときは --force）")
        return existing.resolve(str(existing.data["receipt"]))
    _env_cityworld, env_urban, _ = _environment_studio()
    if from_build is None:
        check_studio_id(request)
        build = build_city_world(request, force)
    else:
        build = from_build.expanduser().resolve()
        say(f"  作ってある City World を使います: {build}")
    export_dir = urban_manifest.path("assets.studio_city_jobs")
    say(f"  Urban の受け取りフォルダに書き出します: {export_dir}")
    try:
        exported = env_urban.export_city_world(build, export_dir, tocho["title"])
    except env_urban.ExportError as exc:
        raise DemoWorldError(f"City World を書き出せません: {exc}") from exc
    receipt = Path(exported["receipt"])
    register_city(receipt, tocho["asset_id"], tocho["title"], precompile)
    return receipt


def catalog_reference(recipes: Path) -> str:
    """The Recipe's catalog: path, relative to the Recipe folder (as the Studio writes it)."""
    return Path(os.path.relpath(environment_studio() / STARTER_CATALOG, recipes)).as_posix()


def install_recipe(source: Path, target: Path, force: bool = False) -> Path:
    """Write the demo Recipe into the Studio's Recipe folder with its catalog
    path for this Workspace."""
    text = source.read_text(encoding="utf-8")
    text, count = re.subn(r"^catalog: .*$", f"catalog: {catalog_reference(target.parent)}", text, count=1,
                          flags=re.MULTILINE)
    if count != 1:
        raise DemoWorldError(f"Recipe に catalog: がありません: {source}")
    if target.is_file() and target.read_text(encoding="utf-8") != text and not force:
        raise DemoWorldError(f"Recipe {target} はもうあって内容が違います（置き換えるときは --force）")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def recipe_files(recipe: dict) -> list[str]:
    """The files a Recipe names (terrain and object params), relative to its folder."""
    names = []
    terrain = (recipe.get("terrain") or {}).get("params") or {}
    names += [str(terrain[key]) for key in ("dem", "visual") if terrain.get(key)]
    for item in recipe.get("objects") or []:
        params = (item or {}).get("params") or {}
        names += [str(params[key]) for key in ("visual", "collision") if params.get(key)]
    return names


def check_recipe_files(recipe_path: Path) -> list[str]:
    """The files the Recipe names that are not there."""
    import yaml

    recipe = yaml.safe_load(recipe_path.read_text(encoding="utf-8"))
    return [name for name in recipe_files(recipe) if not (recipe_path.parent / name).is_file()]


def compare_base(recipe_path: Path, base_path: Path) -> tuple[int, int]:
    """(objects of the base Recipe that the demo Recipe has with other values,
    objects the demo Recipe has that the base does not): how far a new PLATEAU
    import is from the one the demo was made on."""
    import yaml

    base = {item["id"]: item for item in yaml.safe_load(base_path.read_text(encoding="utf-8")).get("objects") or []}
    demo = {item["id"]: item for item in yaml.safe_load(recipe_path.read_text(encoding="utf-8")).get("objects") or []}
    changed = sum(1 for key in demo if key in base and base[key] != demo[key])
    return changed, sum(1 for key in demo if key not in base)


def build_sapporo(spec: dict, force: bool = False, from_build: Path | None = None, precompile: bool = True) -> Path:
    """sapporo-351-exact from PLATEAU (a City World, imported as parts), the
    demo Recipe on it, its World (Environment Studio's export), and the City Asset."""
    sapporo = spec["sapporo"]
    existing = registered_city(sapporo["asset_id"])
    if existing is not None and not force:
        say(f"  City {sapporo['asset_id']} はもう登録されています（作り直すときは --force）")
        return existing.resolve(str(existing.data["receipt"]))
    _env_cityworld, _env_urban, env_workspace = _environment_studio()
    workspace = env_workspace.recipe_workspace()
    recipes = workspace / "recipes"
    base = sapporo["base_recipe"]
    base_path = recipes / f"{base['id']}.yaml"
    if base_path.is_file() and not force:
        say(f"  [a] 部品の Recipe {base['id']} はもうあります（作り直すときは --force）: {base_path}")
    else:
        if from_build is None:
            say("  [a] 札幌駅南口の City World を PLATEAU から作ります（インターネット接続が必要）")
            build = build_city_world(read_json(spec_file(spec, sapporo["city_world"])), force)
        else:
            build = from_build.expanduser().resolve()
            say(f"  [a] 作ってある City World を使います: {build}")
        say(f"  [b] City World を部品の Recipe {base['id']} にします（建物の LOD2 の見た目・当たり判定、地形）")
        command = [sys.executable, str(environment_studio() / "tools" / "env_citygml.py"), "--envsim-build", str(build),
                   "--terrain", base["terrain"], "--name", base["name"], "--out", str(base_path),
                   "--catalog", str(environment_studio() / STARTER_CATALOG)]
        if subprocess.run(command, cwd=environment_studio(), check=False).returncode != 0:
            raise DemoWorldError(f"Recipe {base['id']} を作れませんでした")
    say(f"  [c] デモの Recipe を置きます: {sapporo['asset_id']}")
    recipe_path = install_recipe(spec_file(spec, sapporo["recipe"]), recipes / f"{sapporo['asset_id']}.yaml", force)
    missing = check_recipe_files(recipe_path)
    if missing:
        raise DemoWorldError(
            f"Recipe {recipe_path.name} の参照するファイルが {len(missing)} 個ありません（{', '.join(missing[:3])} ...）。"
            f"PLATEAU のデータ（建物の ID）が {base['id']} を作ったときと変わった可能性があります")
    changed, extra = compare_base(recipe_path, base_path)
    if changed:
        say(f"  注意: {base['id']} の部品のうち {changed} 個が、デモを作ったときと違います"
            "（デモの Recipe の値を使います）")
    say(f"    部品 {extra} 個（停止線・横断歩道・屋台・広場・停留所）を加えた Recipe: {recipe_path}")
    job = workspace / "urban" / sapporo["asset_id"]
    say(f"  [d] World を書き出します（Environment Studio の export）: {job}")
    command = [sys.executable, str(environment_studio() / "tools" / "env_urban.py"), "export", str(recipe_path),
               "--out", str(job)]
    if subprocess.run(command, cwd=environment_studio(), check=False).returncode != 0:
        raise DemoWorldError(f"Recipe {recipe_path.name} の World を書き出せませんでした")
    receipt = job / "build" / "world" / "city-world-receipt.json"
    register_city(receipt, sapporo["asset_id"], sapporo["title"], precompile)
    return receipt


def build_hull(spec: dict, force: bool = False, precompile: bool = True) -> Path:
    """The demo 3-3 comparison World from the Tocho City (make_hull_comparison_world)."""
    import make_hull_comparison_world as hull_tool

    hull = spec["hull"]
    existing = registered_city(hull["asset_id"])
    if existing is not None and not force:
        say(f"  City {hull['asset_id']} はもう登録されています（作り直すときは --force）")
        return existing.resolve(str(existing.data["receipt"]))
    source = registered_city(hull["source_asset_id"])
    if source is None:
        raise DemoWorldError(f"元の City {hull['source_asset_id']} がありません。先に都庁を作ってください（--tocho）")
    out = work_dir() / hull["out"]
    try:
        summary = hull_tool.make_hull_world(source.resolve(str(source.data["receipt"])), hull["bridge"], out,
                                            hull["source_asset_id"], force=force)
    except hull_tool.HullWorldError as exc:
        raise DemoWorldError(str(exc)) from exc
    say(f"  橋 {summary['bridge_id']} の部品 {summary['pieces_replaced']} 個を、頂点 {summary['hull_vertices']} 個の"
        f"凸包 1 つにしました: {summary['job']}")
    receipt = Path(summary["receipt"])
    register_city(receipt, hull["asset_id"], hull["title"], precompile)
    return receipt


# --- check ----------------------------------------------------------------------------------------

def _receipt_frame(receipt: Path) -> dict | None:
    try:
        return read_json(receipt).get("coordinate_frame")
    except (OSError, ValueError):
        return None


def same_selection(frame: dict | None, request: dict) -> bool:
    """Whether a receipt's frame is the request's centre and half extents."""
    if not isinstance(frame, dict):
        return False
    try:
        origin, half = frame["origin"], frame["half_extent_m"]
        center, wanted = request["selection"]["center"], request["selection"]["half_extent_m"]
        return (math.isclose(origin["latitude"], center["latitude"], abs_tol=1e-7)
                and math.isclose(origin["longitude"], center["longitude"], abs_tol=1e-7)
                and math.isclose(half["north_south"], wanted["north_south"], abs_tol=1e-6)
                and math.isclose(half["east_west"], wanted["east_west"], abs_tol=1e-6))
    except (KeyError, TypeError):
        return False


def tocho_candidates(request: dict, export_dir: Path) -> list[Path]:
    """Receipts of Tocho City Worlds under another id: registered Cities and
    jobs in Urban's export folder with the request's centre and size."""
    import urban_assets

    receipts = [asset.resolve(str(asset.data["receipt"])) for asset in urban_assets.catalog().values()
                if asset.kind == "city" and asset.id != request["id"]]
    receipts += sorted(export_dir.glob("*/build/world/city-world-receipt.json")) if export_dir.is_dir() else []
    found = []
    for receipt in receipts:
        receipt = receipt.resolve()
        if receipt not in found and receipt.is_file() and same_selection(_receipt_frame(receipt), request):
            found.append(receipt)
    return found


def check(spec: dict, adopt: bool = False, precompile: bool = True) -> int:
    """Whether the three demo Cities and the demo Compositions are in this Workspace."""
    import urban_assets
    import urban_manifest
    import yaml

    problems = 0
    worlds = [("都庁", spec["tocho"]), ("札幌駅南口ロータリー（雪）", spec["sapporo"]), ("歩道橋Aの凸包（比較用）", spec["hull"])]
    for label, world in worlds:
        asset = registered_city(world["asset_id"])
        if asset is not None:
            say(f"OK      {label}: {world['asset_id']} -> {asset.resolve(str(asset.data['receipt']))}")
            continue
        if world is spec["tocho"]:
            request = read_json(spec_file(spec, world["request"]))
            candidates = tocho_candidates(request, urban_manifest.path("assets.studio_city_jobs"))
            if candidates and adopt:
                register_city(candidates[0], world["asset_id"], world["title"], precompile)
                say(f"OK      {label}: {candidates[0]} にデモの ID {world['asset_id']} を付けました")
                continue
            if candidates:
                say(f"MISSING {label}: {world['asset_id']}（同じ範囲の City があります: {candidates[0]}。"
                    "check --adopt でデモの ID を付けます）")
                problems += 1
                continue
        say(f"MISSING {label}: {world['asset_id']}（demos/README.md の手順で作ってください）")
        problems += 1
    catalog = urban_assets.catalog()
    work = work_dir()
    source = spec_file(spec, spec["demos"]["files"])
    compositions = sorted((source / "compositions").glob("*.yaml"))
    absent, unresolved = [], []
    for path in compositions:
        target = work / "urban" / "compositions" / path.name
        if not target.is_file():
            absent.append(path.name)
            continue
        world = (yaml.safe_load(target.read_text(encoding="utf-8")) or {}).get("world")
        if world not in catalog:
            unresolved.append(f"{path.stem} -> {world}")
    if absent:
        say(f"MISSING デモの Composition {len(absent)} 個（build --demos）: {', '.join(absent[:4])} ...")
    if unresolved:
        say(f"MISSING World の無い Composition {len(unresolved)} 個: {', '.join(unresolved[:4])} ...")
    if not absent and not unresolved:
        say(f"OK      デモの Composition {len(compositions)} 個（World もそろっています）")
    problems += bool(absent) + bool(unresolved)
    say("デモの World はそろっています。" if not problems else "そろっていないものがあります。")
    return 1 if problems else 0


# --- CLI ------------------------------------------------------------------------------------------

STEPS = ("demos", "tocho", "sapporo", "hull")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = result.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="make the demo Worlds (and copy the demo Compositions)")
    for step in STEPS:
        build.add_argument(f"--{step}", action="store_true")
    build.add_argument("--all", action="store_true", help="--demos --tocho --sapporo --hull, in this order")
    build.add_argument("--force", action="store_true", help="make again what is there already")
    build.add_argument("--tocho-build", type=Path, help="use this Envsim City World build for Tocho (no download)")
    build.add_argument("--sapporo-build", type=Path,
                       help="use this Envsim City World build for sapporo-351-exact (no download)")
    build.add_argument("--no-precompile", action="store_true", help="register without the height model compile")
    checking = commands.add_parser("check", help="are the three demo Cities and the demo Compositions there")
    checking.add_argument("--adopt", action="store_true",
                          help="give the demo id to a Tocho City made under another id (same centre and size)")
    checking.add_argument("--no-precompile", action="store_true")
    return result


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    args = parser().parse_args(argv)
    spec = load_spec()
    precompile = not args.no_precompile
    if args.command == "check":
        return check(spec, args.adopt, precompile)
    steps = [step for step in STEPS if args.all or getattr(args, step)]
    if not steps:
        parser().error("build needs --all or one of --demos --tocho --sapporo --hull")
    labels = {
        "demos": "デモの Composition・ルート・飛行計画・人のシーンを work にコピーします",
        "tocho": "都庁の City World（PLATEAU、インターネット接続が必要、数十分）",
        "sapporo": "札幌駅南口ロータリー（雪）の World（PLATEAU、インターネット接続が必要）",
        "hull": "歩道橋Aを凸包にした比較用の World（都庁から作ります）",
    }
    for number, step in enumerate(steps, 1):
        say(f"[{number}/{len(steps)}] {labels[step]}")
        if step == "demos":
            install_demos(spec, work_dir(), args.force)
        elif step == "tocho":
            build_tocho(spec, args.force, args.tocho_build, precompile)
        elif step == "sapporo":
            build_sapporo(spec, args.force, args.sapporo_build, precompile)
        else:
            build_hull(spec, args.force, precompile)
    say("できました。確かめるには: python ../hakoniwa-urban-mobility/tools/urban_demo_worlds.py check")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DemoWorldError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
