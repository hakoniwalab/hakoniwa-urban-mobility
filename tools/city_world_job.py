#!/usr/bin/env python3
"""Check a City World job against schemas/city-world-job.yaml.

A City World job is the folder a World is handed to Urban Mobility in: its
receipt, World MJCF and GLB, terrain hfield and collider view (see the schema
for every rule). Any producer can check its output before registering it:

    tools/city_world_job.py check <job folder | city-world-receipt.json> [--json]

The exit status is 0 when there is no error (warnings are printed but pass).
`tools/urban_assets.py register-city` runs the same check.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import xml.etree.ElementTree as ET

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "city-world-job.yaml"
WORKSPACE = ROOT.parent


@dataclass(frozen=True)
class Problem:
    severity: str  # error | warning
    where: str
    message: str

    def as_json(self) -> dict:
        return {"severity": self.severity, "where": self.where, "message": self.message}


def load_schema(path: Path = SCHEMA_PATH) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def job_root(target: Path, schema: dict) -> Path:
    """The job folder of a job folder or of its receipt."""
    target = target.expanduser().resolve()
    receipt = Path(schema["layout"]["receipt"])
    if target.is_file() and target.name == receipt.name:
        parts = target.parts
        if tuple(parts[-len(receipt.parts):]) != receipt.parts:
            raise ValueError(f"a receipt must sit at <job>/{receipt.as_posix()}: {target}")
        return Path(*parts[:-len(receipt.parts)])
    return target


def _get(data, dotted: str):
    for key in dotted.split("."):
        if not isinstance(data, dict) or key not in data:
            raise KeyError(dotted)
        data = data[key]
    return data


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fields(data: dict, rules: dict, required: bool, where: str, schema: dict, problems: list[Problem],
            known: dict[str, Path] | None = None) -> dict:
    """Check fields of a JSON document against schema rules; returns the paths
    found (with `known`, the paths already found, for hashes to refer to)."""
    found_paths: dict[str, Path] = dict(known or {})
    pending_hashes = []
    for name, rule in rules.items():
        try:
            value = _get(data, name)
        except KeyError:
            if required:
                problems.append(Problem("error", f"{where}: {name}", "missing"))
            continue
        kind = rule.get("type")
        label = f"{where}: {name}"
        if kind == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
            problems.append(Problem("error", label, f"must be an integer, got {value!r}"))
            continue
        if kind == "number" and (isinstance(value, bool) or not isinstance(value, (int, float))):
            problems.append(Problem("error", label, f"must be a number, got {value!r}"))
            continue
        if kind in ("string", "path", "sha256") and not isinstance(value, str):
            problems.append(Problem("error", label, f"must be text, got {value!r}"))
            continue
        if kind == "list" and not isinstance(value, list):
            problems.append(Problem("error", label, f"must be a list, got {value!r}"))
            continue
        if "equals" in rule and value != rule["equals"]:
            problems.append(Problem("error", label, f"must be {rule['equals']!r}, got {value!r}"))
        if "equals_frame" in rule and value != schema["frames"][rule["equals_frame"]]:
            problems.append(Problem("error", label, f"must be exactly {schema['frames'][rule['equals_frame']]!r}, got {value!r}"))
        if "values" in rule and value not in rule["values"]:
            problems.append(Problem("error", label, f"must be one of {rule['values']}, got {value!r}"))
        if "minimum_exclusive" in rule and isinstance(value, (int, float)) and value <= rule["minimum_exclusive"]:
            problems.append(Problem("error", label, f"must be greater than {rule['minimum_exclusive']}, got {value!r}"))
        if kind == "path":
            path = Path(value)
            if not path.is_absolute():
                problems.append(Problem("error", label, f"must be an absolute path (tools resolve relative ones differently): {value}"))
                continue
            if rule.get("file") and not path.is_file():
                problems.append(Problem("error", label, f"no such file: {value}"))
                continue
            found_paths[name] = path
        if kind == "sha256":
            pending_hashes.append((name, rule, value))
    for name, rule, value in pending_hashes:
        path = found_paths.get(rule["of"])
        if path is not None and _sha256(path) != value:
            problems.append(Problem("error", f"{where}: {name}", f"does not match {rule['of']} ({path})"))
    return found_paths


def _check_hfield(path: Path, where: str, problems: list[Problem]) -> None:
    data = path.read_bytes()
    if len(data) < 8:
        problems.append(Problem("error", where, f"hfield is shorter than its header: {path}"))
        return
    nrow, ncol = struct.unpack_from("<ii", data, 0)
    if nrow < 2 or ncol < 2:
        problems.append(Problem("error", where, f"hfield must be at least 2 x 2 samples, got {nrow} x {ncol}: {path}"))
        return
    if len(data) != 8 + 4 * nrow * ncol:
        problems.append(Problem("error", where, f"hfield of {nrow} x {ncol} float32 samples should be "
                                                f"{8 + 4 * nrow * ncol} bytes, got {len(data)}: {path}"))


def _check_mjcf(path: Path, schema: dict, problems: list[Problem]) -> None:
    rules = schema["mjcf"]
    where = f"World MJCF ({path.name})"
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        problems.append(Problem("error", where, f"not XML: {exc}"))
        return
    if root.tag != rules["root"]:
        problems.append(Problem("error", where, f"root element must be <{rules['root']}>, got <{root.tag}>"))
    allowed = set(rules["top_level_elements"])
    for element in root:
        if element.tag not in allowed:
            problems.append(Problem("error", where, f"<{element.tag}> is not allowed at the top level "
                                                    f"(only {', '.join(sorted(allowed))})"))
    worldbody = root.find("worldbody")
    if rules.get("requires_hfield_geom") and (
            worldbody is None or not any(geom.get("type") == "hfield" for geom in worldbody.iter("geom"))):
        problems.append(Problem("error", where, "the World brings its own ground: an hfield geom in worldbody"))
    for tag in rules.get("forbidden_elements", []):
        found = sorted({element.get("name") or f"<{tag}>" for element in root.iter(tag)})
        if found:
            problems.append(Problem("error", where, f"no <{tag}> in a World (the vehicles bring it): {', '.join(found[:5])}"))
    reserved = [re.compile(pattern) for pattern in rules.get("reserved_names", [])]
    clashes = sorted({element.get("name") for element in root.iter() if element.get("name")
                      and any(pattern.search(element.get("name")) for pattern in reserved)})
    if clashes:
        problems.append(Problem("error", where, f"names reserved for vehicles: {', '.join(clashes[:5])}"
                                                + (f" and {len(clashes) - 5} more" if len(clashes) > 5 else "")))


def check(target: Path, schema: dict | None = None, workspace: Path | None = WORKSPACE) -> tuple[Path, list[Problem]]:
    """(job folder, problems) of a job folder or receipt."""
    schema = schema or load_schema()
    problems: list[Problem] = []
    try:
        job = job_root(target, schema)
    except ValueError as exc:
        return target, [Problem("error", str(target), str(exc))]
    for entry in schema["layout"]["files"]:
        if entry.get("required") and not (job / entry["path"]).is_file():
            problems.append(Problem("error", entry["path"], f"missing ({entry['purpose'].split(';')[0].strip()})"))
    receipt_path = job / schema["layout"]["receipt"]
    if not receipt_path.is_file():
        return job, problems
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return job, problems + [Problem("error", "receipt", f"not JSON: {exc}")]
    rules = schema["receipt"]
    paths = _fields(receipt, rules["required"], True, "receipt", schema, problems)
    paths = _fields(receipt, rules["optional"], False, "receipt", schema, problems, paths)

    terrain_xml = paths.get("components.terrain_xml")
    if terrain_xml is not None:
        terrain_receipt = terrain_xml.with_name(schema["terrain"]["receipt"])
        if not terrain_receipt.is_file():
            problems.append(Problem("error", "terrain", f"no {terrain_receipt.name} beside {terrain_xml}"))
        else:
            try:
                data = json.loads(terrain_receipt.read_text(encoding="utf-8"))
            except ValueError as exc:
                problems.append(Problem("error", "terrain", f"{terrain_receipt} is not JSON: {exc}"))
            else:
                terrain_paths = _fields(data, schema["terrain"]["required"], True, "terrain receipt", schema, problems)
                _fields(data, schema["terrain"]["optional"], False, "terrain receipt", schema, problems, terrain_paths)
                if "hfield.path" in terrain_paths:
                    _check_hfield(terrain_paths["hfield.path"], "terrain hfield", problems)
    if "mjcf.path" in paths:
        _check_mjcf(paths["mjcf.path"], schema, problems)
    buildings = paths.get("components.buildings_xml")
    if buildings is not None:
        glb_receipt = buildings.with_name("buildings-glb-receipt.json")
        try:
            float(json.loads(glb_receipt.read_text(encoding="utf-8"))["bounds"]["max"][1])
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            problems.append(Problem("warning", "receipt: components.buildings_xml",
                                    f"no usable {glb_receipt.name} (bounds.max) beside it: the Drone "
                                    "city-max-clearance launch height is unavailable"))
    if workspace is not None:
        workspace = workspace.resolve()
        outside = []
        for name, path in [("job", job), *paths.items()]:
            try:
                path.resolve().relative_to(workspace)
            except ValueError:
                outside.append(name)
        if outside:
            problems.append(Problem("warning", "serving", f"{', '.join(outside)} lie outside the workspace the viewers "
                                                          f"serve ({workspace})"))
    return job, problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("check", help="check a City World job (folder or receipt)")
    command.add_argument("target", type=Path)
    command.add_argument("--json", action="store_true", help="print a JSON report")
    command.add_argument("--workspace", type=Path, default=WORKSPACE,
                         help=f"the workspace root the viewers serve (default {WORKSPACE})")
    args = parser.parse_args(argv)
    job, problems = check(args.target, workspace=args.workspace)
    errors = [problem for problem in problems if problem.severity == "error"]
    if args.json:
        print(json.dumps({"ok": not errors, "job": str(job), "problems": [p.as_json() for p in problems]},
                         ensure_ascii=False, indent=2))
    else:
        for problem in problems:
            print(f"{problem.severity.upper():7} {problem.where}: {problem.message}")
        print(f"{'OK' if not errors else 'NG'}  {job} ({len(errors)} errors, {len(problems) - len(errors)} warnings)")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
