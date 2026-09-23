#!/usr/bin/env python3
"""Relocate bundled Urban Car inputs and materialize runtime configuration."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT.parent
METADATA = PACKAGE_ROOT / "portable-data/city-world.json"


class PortableUrbanCarError(RuntimeError):
    pass


def relocate_foundation_config() -> Path:
    config = (
        PACKAGE_ROOT
        / "hakoniwa-business-pack/work/foundation/config/cpp_core_config.json"
    )
    try:
        payload = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PortableUrbanCarError(f"invalid Foundation Core config: {config}: {exc}") from exc
    mmap = (
        PACKAGE_ROOT
        / "hakoniwa-business-pack/work/foundation/runtime/mmap"
    ).resolve()
    mmap.mkdir(parents=True, exist_ok=True)
    payload["core_mmap_path"] = mmap.as_posix()
    config.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return config


def _replace_root(value: object, source: str, destination: str) -> object:
    if isinstance(value, str):
        source_path = source.rstrip("\\/")
        if value.lower() == source_path.lower():
            return destination
        for separator in ("\\", "/"):
            prefix = source_path + separator
            if value.lower().startswith(prefix.lower()):
                relative = value[len(prefix) :].replace("\\", "/")
                return str(Path(destination) / Path(relative))
        return value
    if isinstance(value, list):
        return [_replace_root(item, source, destination) for item in value]
    if isinstance(value, dict):
        return {
            str(_replace_root(key, source, destination)): _replace_root(
                item, source, destination
            )
            for key, item in value.items()
        }
    return value


def relocate_receipt() -> Path:
    if not METADATA.is_file():
        raise PortableUrbanCarError(f"portable City World metadata not found: {METADATA}")
    metadata = json.loads(METADATA.read_text(encoding="utf-8"))
    source_root = str(
        metadata.get("path_token", metadata.get("source_build_root", ""))
    )
    if not source_root:
        raise PortableUrbanCarError(
            f"portable City World metadata has no path token: {METADATA}"
        )
    build_root = (PACKAGE_ROOT / "portable-data/city-world/build").resolve()
    template = build_root / str(metadata["receipt_relative"])
    if not template.is_file():
        raise PortableUrbanCarError(f"bundled City World receipt not found: {template}")
    for path in build_root.rglob("*.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PortableUrbanCarError(f"invalid bundled JSON: {path}: {exc}") from exc
        relocated_value = _replace_root(value, source_root, str(build_root))
        path.write_text(
            json.dumps(relocated_value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    receipt = json.loads(template.read_text(encoding="utf-8"))
    relocated = _replace_root(receipt, source_root, str(build_root))
    # Keep the receipt in its original <job>/build/world position because
    # Urban derives the sibling viewer/collider directory from that contract.
    output = template
    output.write_text(
        json.dumps(relocated, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output


def prepare() -> int:
    relocate_foundation_config()
    runtime_root = (
        PACKAGE_ROOT
        / "hakoniwa-business-pack/work/recipes/urban-car-rc/runtime"
    )
    stamp = runtime_root / "portable-layout.json"
    launcher = (
        PACKAGE_ROOT
        / "hakoniwa-business-pack/work/recipes/urban-car-rc/config/launcher.json"
    )
    if stamp.is_file() and launcher.is_file():
        try:
            state = json.loads(stamp.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            state = {}
        if state.get("package_root") == str(PACKAGE_ROOT.resolve()):
            print(f"Portable Urban workspace is current: {PACKAGE_ROOT}")
            return 0
    receipt = relocate_receipt()
    command = [
        sys.executable,
        str(ROOT / "tools/urban_mobility.py"),
        "configure",
        "--recipe",
        str(ROOT / "recipes/usecases/urban-car-rc.yaml"),
        "--city-receipt",
        str(receipt),
        "--reuse-built-asset",
        "--portable-reconfigure",
    ]
    environment = os.environ.copy()
    for variable, repository in (
        ("HAKONIWA_URBAN_MOBILITY_ROOT", "hakoniwa-urban-mobility"),
        ("HAKONIWA_PDU_REGISTRY_ROOT", "hakoniwa-pdu-registry"),
        ("HAKONIWA_ROBOT_RUNTIME_ROOT", "hakoniwa-robot-runtime"),
        ("HAKONIWA_MUJOCO_ROBOTS_ROOT", "hakoniwa-mujoco-robots"),
        ("HAKONIWA_MBODY_REGISTRY_ROOT", "hakoniwa-mbody-registry"),
        ("HAKONIWA_THREEJS_DRONE_ROOT", "hakoniwa-threejs-drone"),
        ("HAKONIWA_MAP_VIEWER_ROOT", "hakoniwa-map-viewer"),
    ):
        environment[variable] = str(PACKAGE_ROOT / repository)
    business_pack = PACKAGE_ROOT / "hakoniwa-business-pack"
    foundation = business_pack / "work/foundation"
    environment.update(
        {
            "HAKONIWA_WORKSPACE_ROOT": str(business_pack),
            "HAKONIWA_WORK_DIR": str(business_pack / "work"),
            "HAKONIWA_HOME": str(foundation / "install"),
            "HAKO_CONFIG_PATH": str(foundation / "config/cpp_core_config.json"),
            "VIRTUAL_ENV": str(foundation / "install/python"),
            "PYTHONNOUSERSITE": "1",
            "HAKONIWA_PORTABLE_WORKSPACE": "1",
        }
    )
    result = subprocess.run(
        command, cwd=ROOT, env=environment, check=False
    )
    if result.returncode == 0:
        runtime_root.mkdir(parents=True, exist_ok=True)
        stamp.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "package_root": str(PACKAGE_ROOT.resolve()),
                    "city_receipt": str(receipt),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return result.returncode


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("command", choices=("prepare", "relocate-receipt"))
    return result


def main() -> int:
    args = parser().parse_args()
    if args.command == "relocate-receipt":
        print(relocate_receipt())
        return 0
    return prepare()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, KeyError, json.JSONDecodeError, PortableUrbanCarError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
