#!/usr/bin/env python3
"""Report and prune Urban's derived caches under work/urban/cache (asset-contract 5.4).

world-height/<fingerprint>-mujoco-<version>/ holds the compiled World chunks of
tools/world_height.py. An entry is kept only while all of these hold:

- it was written by the MuJoCo version this interpreter imports (an MJB loads
  only in the version that wrote it). An entry of another version is removed
  by default only once the same World has an entry for the current version, so
  running this from an interpreter with a different MuJoCo (for example outside
  the Workspace shell) never discards the caches the Workspace uses. Pass
  other_versions=True (--other-mujoco-versions) to remove them regardless;
- the MJCF recorded in its manifest.json still exists and still has that
  fingerprint (a regenerated World gets a new entry);
- when that MJCF belongs to a City World Web UI job, the City is registered.

A <key>.partial/ staging directory is a compile in progress; it is removed only
once it is older than PARTIAL_GRACE_SEC. plain-world/*.xml are regenerated on
demand and are removed only on request (--plain-world).

Nothing is deleted without apply=True (the CLI's --apply).
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import shutil
import time

import urban_assets
import urban_manifest
import world_height


CACHE_ROOT = urban_manifest.work_dir() / "urban/cache"
PARTIAL_GRACE_SEC = 3600.0
ENTRY_NAME = re.compile(r"^(?P<fingerprint>[0-9a-f]{64})-mujoco-(?P<version>.+)$")


@dataclass
class Entry:
    path: Path
    size_bytes: int
    remove: bool
    reason: str
    mjcf: str | None = None

    def as_json(self) -> dict:
        return {
            "path": str(self.path),
            "size_bytes": self.size_bytes,
            "remove": self.remove,
            "reason": self.reason,
            "mjcf": self.mjcf,
        }


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file() and not item.is_symlink())


def current_mujoco_version() -> str | None:
    try:
        import mujoco
    except ImportError:
        return None
    return getattr(mujoco, "__version__", "unknown")


def registered_city_jobs(jobs_root: Path, assets: dict | None = None) -> set[Path]:
    """City World job folders (jobs_root/<id>) that back a registered City Asset."""
    jobs_root = jobs_root.resolve()
    folders = set()
    for asset in (urban_assets.catalog() if assets is None else assets).values():
        if asset.kind != "city":
            continue
        receipt = asset.resolve(str(asset.data["receipt"]))
        try:
            relative = receipt.relative_to(jobs_root)
        except ValueError:
            continue
        folders.add(jobs_root / relative.parts[0])
    return folders


def _height_entry(
    path: Path,
    *,
    mujoco_version: str | None,
    jobs_root: Path,
    registered_jobs: set[Path],
    fingerprints: dict[Path, str],
    current_worlds: set[str],
    other_versions: bool,
    now: float,
) -> Entry:
    size = _size(path)
    if path.name.endswith(".partial"):
        age = now - path.stat().st_mtime
        if age < PARTIAL_GRACE_SEC:
            return Entry(path, size, False, "compile in progress")
        return Entry(path, size, True, "abandoned compile")
    match = ENTRY_NAME.fullmatch(path.name)
    if match is None or not path.is_dir():
        return Entry(path, size, False, "not a World height entry; left untouched")
    try:
        mjcf = Path(json.loads((path / "manifest.json").read_text(encoding="utf-8"))["mjcf"])
    except (OSError, KeyError, TypeError, ValueError):
        return Entry(path, size, True, "manifest missing or unreadable")
    if mujoco_version is not None and match["version"] != mujoco_version:
        label = f"MuJoCo {match['version']} (current {mujoco_version})"
        if other_versions:
            return Entry(path, size, True, label, str(mjcf))
        if match["fingerprint"] in current_worlds:
            return Entry(path, size, True, f"{label}, superseded", str(mjcf))
        keep_label = f"{label}; kept, no current-version entry for this World"
    else:
        keep_label = None
    try:
        job = jobs_root / mjcf.resolve().relative_to(jobs_root).parts[0]
    except ValueError:
        job = None
    if job is not None and job not in registered_jobs:
        reason = "City World job deleted" if not job.exists() else "City not registered"
        return Entry(path, size, True, reason, str(mjcf))
    if not mjcf.is_file():
        return Entry(path, size, True, "World MJCF deleted", str(mjcf))
    if mjcf not in fingerprints:
        fingerprints[mjcf] = world_height.fingerprint(mjcf)
    if fingerprints[mjcf] != match["fingerprint"]:
        return Entry(path, size, True, "World regenerated since", str(mjcf))
    reason = keep_label or ("registered City" if job is not None else "current World")
    if mujoco_version is None:
        reason += " (MuJoCo not importable: version not checked)"
    return Entry(path, size, False, reason, str(mjcf))


def plan(
    *,
    cache_root: Path | None = None,
    jobs_root: Path | None = None,
    plain_world: bool = False,
    mujoco_version: str | None = None,
    other_versions: bool = False,
    assets: dict | None = None,
    now: float | None = None,
) -> dict:
    """Classify every cache entry without modifying anything."""
    cache_root = CACHE_ROOT if cache_root is None else cache_root
    jobs_root = (urban_assets.CITY_WORLD_JOBS if jobs_root is None else jobs_root).resolve()
    now = time.time() if now is None else now
    registered = registered_city_jobs(jobs_root, assets)
    fingerprints: dict[Path, str] = {}
    height_root = cache_root / "world-height"
    paths = sorted(height_root.iterdir()) if height_root.is_dir() else []
    current_worlds = {
        match["fingerprint"] for match in map(ENTRY_NAME.fullmatch, (path.name for path in paths))
        if match is not None and match["version"] == mujoco_version
    }
    height = [
        _height_entry(
            path, mujoco_version=mujoco_version, jobs_root=jobs_root,
            registered_jobs=registered, fingerprints=fingerprints,
            current_worlds=current_worlds, other_versions=other_versions, now=now,
        )
        for path in paths
    ]
    plain_root = cache_root / "plain-world"
    plain = []
    for path in sorted(plain_root.iterdir()) if plain_root.is_dir() else []:
        if path.name.endswith(".partial.xml") and now - path.stat().st_mtime < PARTIAL_GRACE_SEC:
            plain.append(Entry(path, _size(path), False, "generation in progress"))
        elif plain_world:
            plain.append(Entry(path, _size(path), True, "regenerated on demand"))
        else:
            plain.append(Entry(path, _size(path), False, "kept (pass --plain-world to remove)"))
    entries = height + plain
    return {
        "cache_root": str(cache_root),
        "mujoco_version": mujoco_version,
        "world_height": [entry.as_json() for entry in height],
        "plain_world": [entry.as_json() for entry in plain],
        "total_bytes": sum(entry.size_bytes for entry in entries),
        "reclaimable_bytes": sum(entry.size_bytes for entry in entries if entry.remove),
    }


def prune(apply: bool = False, **options) -> dict:
    report = plan(**options)
    cache_root = Path(report["cache_root"]).resolve()
    removed = []
    for entry in report["world_height"] + report["plain_world"]:
        if not entry["remove"]:
            continue
        path = Path(entry["path"])
        if path.is_symlink() or path.resolve().parent.parent != cache_root:
            raise urban_assets.AssetError(f"refusing to remove unexpected path: {path}")
        if apply:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        removed.append(str(path))
    report.update({"applied": apply, "removed": removed})
    return report


def format_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GiB"


def render(report: dict) -> str:
    lines = [
        f"Urban cache: {report['cache_root']} ({format_bytes(report['total_bytes'])})",
        f"MuJoCo: {report['mujoco_version'] or 'not importable'}",
    ]
    for section in ("world_height", "plain_world"):
        lines.append(f"{section.replace('_', '-')}:")
        for entry in report[section]:
            action = "remove" if entry["remove"] else "keep  "
            lines.append(
                f"  {action} {format_bytes(entry['size_bytes']):>10}  "
                f"{Path(entry['path']).name}  {entry['reason']}"
            )
        if not report[section]:
            lines.append("  empty")
    verb = "Removed" if report.get("applied") else "Reclaimable"
    lines.append(f"{verb}: {format_bytes(report['reclaimable_bytes'])}")
    if "applied" in report and not report["applied"]:
        lines.append("Dry run only. Re-run with --apply to delete.")
    return "\n".join(lines)
