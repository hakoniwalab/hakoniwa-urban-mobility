#!/usr/bin/env python3
"""Load Urban Asset manifests (docs/asset-contract.md) and register City Assets."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import sys

import yaml

import urban_manifest


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
BUSINESS_PACK = urban_manifest.business_pack()  # $HAKONIWA_WORKSPACE_ROOT

ASSET_SCHEMA = "hakoniwa.asset/v1"
MANIFEST_SUFFIX = urban_manifest.value("assets.suffix")
# Tracked vehicle and plain-World manifests, then user-generated City Assets.
REPOSITORY_ASSETS = urban_manifest.path("assets.repository")
USER_ASSETS = urban_manifest.path("assets.user")
# Jobs of the Business Pack City World Web UI; Urban Studio registers these.
CITY_WORLD_JOBS = urban_manifest.path("assets.city_world_jobs")

KINDS = {"vehicle", "city", "plain"}
CATEGORIES = {"car", "drone", "person"}
SIMULATORS = {"ackermann-mujoco", "drone-core", "hakoniwa-people"}
CONTROLS = {"rc", "api", "schedule", "external"}
SCOPES = {"vehicle", "composition", "shared"}
REPO_REFERENCE = re.compile(r"\$\{repo:([A-Za-z0-9_.-]+)\}")


class AssetError(RuntimeError):
    pass


@dataclass(frozen=True)
class Asset:
    id: str
    kind: str
    path: Path
    data: dict

    @property
    def category(self) -> str | None:
        return self.data.get("category")

    @property
    def simulator(self) -> str | None:
        return self.data.get("simulator")

    def controls(self) -> dict:
        return self.data.get("controls", {})

    def resolve(self, value: str) -> Path:
        """Resolve a manifest path: ${repo:NAME} or relative to the manifest."""
        return resolve_reference(value, self.path.parent)


def resolve_reference(value: str, base: Path) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise AssetError(f"asset path must be a non-empty string: {value!r}")
    if "${runtime." in value:
        raise AssetError(f"runtime placeholder cannot be resolved at load time: {value}")
    match = REPO_REFERENCE.match(value)
    if match:
        return (WORKSPACE / match.group(1) / value[match.end():].lstrip("/")).resolve()
    path = Path(value).expanduser()
    return (path if path.is_absolute() else base / path).resolve()


def load_manifest(path: Path) -> Asset:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise AssetError(f"invalid Asset manifest: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise AssetError(f"Asset manifest must be a mapping: {path}")
    if data.get("schema") != ASSET_SCHEMA:
        raise AssetError(f"Asset manifest schema must be {ASSET_SCHEMA}: {path}")
    asset_id = data.get("id")
    kind = data.get("kind")
    if not isinstance(asset_id, str) or not asset_id.strip():
        raise AssetError(f"Asset manifest has no id: {path}")
    if kind not in KINDS:
        raise AssetError(f"Asset {asset_id} kind must be one of {sorted(KINDS)}: {path}")
    asset = Asset(id=asset_id, kind=kind, path=path.resolve(), data=data)
    validate(asset)
    return asset


def validate(asset: Asset) -> None:
    data = asset.data
    if asset.kind == "city":
        if not isinstance(data.get("receipt"), str):
            raise AssetError(f"City Asset {asset.id} has no receipt")
        return
    if asset.kind == "plain":
        if not isinstance(data.get("world"), str):
            raise AssetError(f"plain World Asset {asset.id} has no world")
        return
    if asset.category not in CATEGORIES:
        raise AssetError(f"vehicle Asset {asset.id} category must be one of {sorted(CATEGORIES)}")
    if asset.simulator not in SIMULATORS:
        raise AssetError(
            f"vehicle Asset {asset.id} simulator must be one of {sorted(SIMULATORS)}"
        )
    clearance = data.get("spawn", {}).get("ground_clearance_m")
    if not isinstance(clearance, (int, float)) or clearance < 0:
        raise AssetError(f"vehicle Asset {asset.id} needs spawn.ground_clearance_m >= 0")
    controls = asset.controls()
    if not isinstance(controls, dict) or not controls or set(controls) - CONTROLS:
        raise AssetError(f"vehicle Asset {asset.id} controls must declare rc, api, schedule and/or external")
    for name, control in controls.items():
        # external: an outside program (an agent, a script) drives it through
        # its API; the Launcher starts nothing for it.
        if isinstance(control, dict) and control.get("external") is True:
            continue
        if not isinstance(control, dict) or not isinstance(control.get("program"), str):
            raise AssetError(f"Asset {asset.id} control {name} has no program")
        if control.get("scope", "vehicle") not in SCOPES:
            raise AssetError(f"Asset {asset.id} control {name} scope must be vehicle, composition or shared")
        for key in ("args", "vehicle_args"):
            if not isinstance(control.get(key, []), list):
                raise AssetError(f"Asset {asset.id} control {name} {key} must be a list")
    dimensions = data.get("dimensions")
    if dimensions is not None:
        # The Car's outer size [m]; route checks keep half its width off walls.
        if not isinstance(dimensions, dict) or not all(
            isinstance(dimensions.get(key), (int, float)) and not isinstance(dimensions.get(key), bool)
            and dimensions[key] > 0 for key in ("width_m", "length_m")
        ):
            raise AssetError(f"Asset {asset.id} dimensions need positive width_m and length_m")
    import asset_preview

    try:
        asset_preview.validate(data.get("preview"), asset.id)
    except asset_preview.PreviewError as exc:
        raise AssetError(str(exc)) from exc
    fleet = data.get("fleet")
    if fleet is not None:
        # A fleet-capable Drone (Composition `fleets`, asset-contract 5.8).
        if asset.category != "drone" or not isinstance(fleet, dict):
            raise AssetError(f"Asset {asset.id} fleet must be a mapping on a Drone Asset")
        max_count, spacing = fleet.get("max_count"), fleet.get("default_spacing_m")
        if not isinstance(max_count, int) or max_count < 1:
            raise AssetError(f"Asset {asset.id} fleet.max_count must be a positive integer")
        if not isinstance(spacing, (int, float)) or spacing <= 0:
            raise AssetError(f"Asset {asset.id} fleet.default_spacing_m must be positive")


def asset_dirs() -> list[Path]:
    return [REPOSITORY_ASSETS, USER_ASSETS]


def source_repository_manifests() -> list[Path]:
    """Manifests owned by other workspace repositories (their top-level assets/).

    Only the top level is read: other repositories' assets/ directories also
    hold models and textures.
    """
    return sorted(
        path for path in WORKSPACE.glob(
            f"{urban_manifest.path('assets.workspace').relative_to(WORKSPACE).as_posix()}/*{MANIFEST_SUFFIX}")
        if path.parent.resolve() != REPOSITORY_ASSETS.resolve()
    )


def catalog(directories: list[Path] | None = None) -> dict[str, Asset]:
    """Load every manifest: this repository's, source repositories', and user Assets.

    With directories, only manifests under those directories are read.
    """
    if directories is None:
        paths = [
            *sorted(REPOSITORY_ASSETS.rglob(f"*{MANIFEST_SUFFIX}")),
            *source_repository_manifests(),
            *(sorted(USER_ASSETS.rglob(f"*{MANIFEST_SUFFIX}")) if USER_ASSETS.is_dir() else []),
        ]
    else:
        paths = [
            path for directory in directories if directory.is_dir()
            for path in sorted(directory.rglob(f"*{MANIFEST_SUFFIX}"))
        ]
    assets: dict[str, Asset] = {}
    for path in paths:
        asset = load_manifest(path)
        if asset.id in assets:
            raise AssetError(
                f"duplicate Asset id {asset.id}: {assets[asset.id].path} and {path}"
            )
        assets[asset.id] = asset
    return assets


def city_id_from_receipt(receipt: Path) -> str:
    """City World jobs live at jobs/<city id>/build/world/city-world-receipt.json."""
    parts = receipt.resolve().parts
    if len(parts) >= 4 and parts[-2:] == ("world", "city-world-receipt.json") and parts[-3] == "build":
        return parts[-4]
    raise AssetError(f"cannot derive a City id from receipt path; pass --id: {receipt}")


def register_city(receipt: Path, asset_id: str | None = None, directory: Path = USER_ASSETS,
                  title: str | None = None) -> Path:
    receipt = receipt.expanduser().resolve()
    if not receipt.is_file():
        raise AssetError(f"City World receipt not found: {receipt}")
    asset_id = asset_id or city_id_from_receipt(receipt)
    path = directory / "cities" / f"{asset_id}{MANIFEST_SUFFIX}"
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema": ASSET_SCHEMA,
        "id": asset_id,
        "kind": "city",
        **({"title": title} if title else {}),
        "version": receipt.stat().st_mtime_ns,
        "receipt": receipt.as_posix(),
    }
    path.write_text(yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def city_receipt_available(asset: Asset) -> bool:
    """A City Asset is usable only while its City World receipt still exists."""
    return asset.kind == "city" and asset.resolve(str(asset.data["receipt"])).is_file()


def _user_city_manifests(directory: Path | None) -> list[Asset]:
    cities = (directory or USER_ASSETS) / "cities"
    if not cities.is_dir():
        return []
    return [load_manifest(path) for path in sorted(cities.glob(f"*{MANIFEST_SUFFIX}"))]


def unregister_city(asset_id: str, directory: Path | None = None) -> Path:
    """Remove a registered City Asset manifest; the City World job is left untouched."""
    for asset in _user_city_manifests(directory):
        if asset.id == asset_id:
            asset.path.unlink()
            return asset.path
    raise AssetError(
        f"registered City Asset {asset_id!r} not found under {(directory or USER_ASSETS) / 'cities'}"
    )


def prune_missing_cities(jobs_root: Path, directory: Path | None = None) -> list[str]:
    """Unregister Cities whose City World job under jobs_root was deleted.

    Only manifests registered from a job under jobs_root (the City World Web
    UI's own jobs) are removed. A City registered from another location is
    kept even when its receipt is missing, so it is never removed silently.
    """
    jobs_root = jobs_root.expanduser().resolve()
    removed = []
    for asset in _user_city_manifests(directory):
        receipt = asset.resolve(str(asset.data["receipt"]))
        try:
            receipt.relative_to(jobs_root)
        except ValueError:
            continue
        if not receipt.is_file():
            asset.path.unlink()
            removed.append(asset.id)
    return removed


def register_world(world: Path, asset_id: str | None = None, directory: Path = USER_ASSETS) -> Path:
    """Register a prepared environment (World YAML) as a plain World Asset."""
    world = world.expanduser().resolve()
    if not world.is_file():
        raise AssetError(f"World YAML not found: {world}")
    asset_id = asset_id or world.stem
    path = directory / "worlds" / f"{asset_id}{MANIFEST_SUFFIX}"
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema": ASSET_SCHEMA,
        "id": asset_id,
        "kind": "plain",
        "version": world.stat().st_mtime_ns,
        "world": world.as_posix(),
    }
    path.write_text(yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list", help="list the Asset catalog")
    listing.add_argument("--json", action="store_true",
                         help="print JSON (id, kind, category, title, manifest, and a City's receipt) for tools")
    register = commands.add_parser("register-city", help="register a City World receipt")
    register.add_argument("--receipt", type=Path, required=True)
    register.add_argument("--id", help="City Asset id (default: the City World job name)")
    register.add_argument("--title", help="the name shown for the City (default: its id)")
    register.add_argument(
        "--no-precompile",
        action="store_true",
        help="skip compiling the City model for spawn heights (otherwise done once here)",
    )
    register.add_argument(
        "--no-check",
        action="store_true",
        help="register without checking the job against schemas/city-world-job.yaml",
    )
    unregister = commands.add_parser(
        "unregister-city", help="remove a registered City Asset (its City World job is kept)"
    )
    unregister.add_argument("--id", required=True, help="City Asset id")
    commands.add_parser(
        "prune-cities",
        help="unregister Cities whose City World Web UI job was deleted",
    )
    cache = commands.add_parser(
        "prune-cache",
        help="remove World height caches of other MuJoCo versions, unregistered Cities, "
        "and deleted or regenerated Worlds (dry run unless --apply)",
    )
    cache.add_argument("--apply", action="store_true", help="delete instead of a dry run")
    cache.add_argument(
        "--plain-world", action="store_true",
        help="also remove the plain-world MJCF cache (regenerated on demand)",
    )
    cache.add_argument(
        "--other-mujoco-versions", action="store_true",
        help="remove every World height entry of another MuJoCo version, even when the "
        "running version has no replacement yet",
    )
    cache.add_argument("--json", action="store_true", help="print a JSON report")
    world = commands.add_parser("register-world", help="register a prepared environment (World YAML)")
    world.add_argument("--world", type=Path, required=True)
    world.add_argument("--id", help="plain World Asset id (default: the YAML file name)")
    world.add_argument(
        "--no-precompile",
        action="store_true",
        help="skip generating the World job and its height model (otherwise done once here)",
    )
    return result


def precompile_height(receipt: Path) -> None:
    """Compile and cache the City model used for spawn heights (tools/world_height.py).

    The first compile of a City takes minutes; doing it at registration keeps
    the placement loop fast.
    """
    import json

    data = json.loads(receipt.read_text(encoding="utf-8"))
    mjcf = Path(data["mjcf"]["path"])
    if not mjcf.is_absolute():
        mjcf = receipt.parent / mjcf
    try:
        import world_height

        world_height.load_models(mjcf)
    except ImportError:
        print("MuJoCo Python is not installed; the City model compiles at the first configure instead.")
        return
    print(f"Compiled City model for spawn heights: {mjcf}")


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "register-city":
        if not args.no_check:
            import city_world_job

            _job, problems = city_world_job.check(args.receipt)
            for problem in problems:
                print(f"{problem.severity.upper():7} {problem.where}: {problem.message}")
            if any(problem.severity == "error" for problem in problems):
                print("Not registered: the City World job does not follow schemas/city-world-job.yaml "
                      "(tools/city_world_job.py check; --no-check registers anyway).", file=sys.stderr)
                return 1
        print(f"Registered City Asset: {register_city(args.receipt, args.id, title=args.title)}")
        if not args.no_precompile:
            precompile_height(args.receipt.expanduser().resolve())
        return 0
    if args.command == "unregister-city":
        print(f"Unregistered City Asset: {unregister_city(args.id)}")
        return 0
    if args.command == "prune-cities":
        removed = prune_missing_cities(CITY_WORLD_JOBS)
        print(f"Unregistered Cities with a deleted City World job: {', '.join(removed) or 'none'}")
        return 0
    if args.command == "prune-cache":
        import json

        import urban_cache

        report = urban_cache.prune(
            apply=args.apply,
            plain_world=args.plain_world,
            other_versions=args.other_mujoco_versions,
            mujoco_version=urban_cache.current_mujoco_version(),
        )
        print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else urban_cache.render(report))
        return 0
    if args.command == "register-world":
        print(f"Registered plain World Asset: {register_world(args.world, args.id)}")
        if not args.no_precompile:
            import plain_world

            receipt = plain_world.materialize(args.world)
            print(f"Generated plain World job: {receipt.parents[2]}")
            precompile_height(receipt)
        return 0
    if args.json:
        import json

        print(json.dumps([listing_entry(asset) for asset in catalog().values()], ensure_ascii=False, indent=2))
        return 0
    for asset in catalog().values():
        detail = asset.category or asset.kind
        print(f"{asset.id:40} {asset.kind:8} {detail:6} {asset.path}")
    return 0


def listing_entry(asset: Asset) -> dict:
    """What `list --json` tells another tool about an Asset (Environment
    Studio reads it to show which of its City Worlds are registered)."""
    entry = {"id": asset.id, "kind": asset.kind, "category": asset.category,
             "title": asset.data.get("title") or asset.id, "manifest": str(asset.path)}
    if asset.kind == "city":
        entry["receipt"] = str(Path(str(asset.data["receipt"])).expanduser().resolve())
    return entry


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
