#!/usr/bin/env python3
"""Load Urban Asset manifests (docs/asset-contract.md) and register City Assets."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import sys

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
BUSINESS_PACK = WORKSPACE / "hakoniwa-business-pack"

ASSET_SCHEMA = "hakoniwa.asset/v1"
MANIFEST_SUFFIX = ".asset.yaml"
# Tracked vehicle and plain-World manifests, then user-generated City Assets.
REPOSITORY_ASSETS = ROOT / "assets"
USER_ASSETS = BUSINESS_PACK / "work/urban/assets"

KINDS = {"vehicle", "city", "plain"}
CATEGORIES = {"car", "drone"}
SIMULATORS = {"ackermann-mujoco", "drone-core"}
CONTROLS = {"rc", "api"}
SCOPES = {"vehicle", "composition"}
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
        raise AssetError(f"vehicle Asset {asset.id} category must be car or drone")
    if asset.simulator not in SIMULATORS:
        raise AssetError(
            f"vehicle Asset {asset.id} simulator must be one of {sorted(SIMULATORS)}"
        )
    clearance = data.get("spawn", {}).get("ground_clearance_m")
    if not isinstance(clearance, (int, float)) or clearance < 0:
        raise AssetError(f"vehicle Asset {asset.id} needs spawn.ground_clearance_m >= 0")
    controls = asset.controls()
    if not isinstance(controls, dict) or not controls or set(controls) - CONTROLS:
        raise AssetError(f"vehicle Asset {asset.id} controls must declare rc and/or api")
    for name, control in controls.items():
        if not isinstance(control, dict) or not isinstance(control.get("program"), str):
            raise AssetError(f"Asset {asset.id} control {name} has no program")
        if control.get("scope", "vehicle") not in SCOPES:
            raise AssetError(f"Asset {asset.id} control {name} scope must be vehicle or composition")
        if not isinstance(control.get("args", []), list):
            raise AssetError(f"Asset {asset.id} control {name} args must be a list")


def asset_dirs() -> list[Path]:
    return [REPOSITORY_ASSETS, USER_ASSETS]


def catalog(directories: list[Path] | None = None) -> dict[str, Asset]:
    assets: dict[str, Asset] = {}
    for directory in directories if directories is not None else asset_dirs():
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob(f"*{MANIFEST_SUFFIX}")):
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


def register_city(receipt: Path, asset_id: str | None = None, directory: Path = USER_ASSETS) -> Path:
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
        "version": receipt.stat().st_mtime_ns,
        "receipt": receipt.as_posix(),
    }
    path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    return path


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="list the Asset catalog")
    register = commands.add_parser("register-city", help="register a City World receipt")
    register.add_argument("--receipt", type=Path, required=True)
    register.add_argument("--id", help="City Asset id (default: the City World job name)")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "register-city":
        print(f"Registered City Asset: {register_city(args.receipt, args.id)}")
        return 0
    for asset in catalog().values():
        detail = asset.category or asset.kind
        print(f"{asset.id:40} {asset.kind:8} {detail:6} {asset.path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
