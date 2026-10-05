#!/usr/bin/env python3
"""The root manifest of this repository (urban.manifest.yaml, docs/urban-manifest.md).

Tools read the repository's parts (contracts, Asset locations, Worlds, managed
Recipes) and the ports through this module instead of spelling them out:

    import urban_manifest
    urban_manifest.port("viewer-http")          # 28100, or its override
    urban_manifest.path("assets.user")          # $HAKONIWA_WORK_DIR/urban/assets

    tools/urban_manifest.py ports [--json]      # every port, where its value comes from
    tools/urban_manifest.py check               # the manifest is consistent

The tools run in the Hakoniwa Business Pack Workspace (hakoniwa-business-pack
docs/hakoniwa-workspace-environment-ja.md): `python tools/workspace.py enter`, or
`python tools/workspace.py run -- <command>`. ${business_pack} is its
$HAKONIWA_WORKSPACE_ROOT and ${work} its $HAKONIWA_WORK_DIR; outside the
Workspace they stop with the command that enters it.
"""

from __future__ import annotations

import argparse
import functools
import json
import os
from pathlib import Path
import re
import sys

import yaml


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "urban.manifest.yaml"
SCHEMA = "hakoniwa.urban-manifest/v1"
PLACEHOLDER = re.compile(r"\$\{([a-z_]+)\}")


ENTER = "python tools/workspace.py enter"


class ManifestError(RuntimeError):
    pass


class WorkspaceError(SystemExit):
    """Run outside the Workspace: a message and a non-zero exit, not a traceback."""

    def __init__(self, message: str):
        super().__init__(f"{message}\nIn hakoniwa-business-pack: {ENTER} "
                         "(or python tools/workspace.py run -- <command>)")


@functools.lru_cache(maxsize=1)
def load(path: Path = MANIFEST_PATH) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise ManifestError(f"{path} is not a {SCHEMA} manifest")
    return data


def _workspace_variable(name: str) -> Path:
    if os.environ.get("HAKONIWA_WORKSPACE_ACTIVE") != "1":
        raise WorkspaceError("Urban Mobility runs in the Hakoniwa Business Pack Workspace, which is not active.")
    configured = os.environ.get(name, "").strip()
    if not configured:
        raise WorkspaceError(f"The Hakoniwa Business Pack Workspace is active but ${name} is not set.")
    return Path(configured).expanduser().resolve()


# Set by the start / status / stop .bat files of a Windows portable package
# (hakoniwa-business-pack tools/package_portable_workspace.py). There is no
# pip, Git, or C++ toolchain there: configure reuses what the package carries.
PORTABLE_ENV = "HAKONIWA_PORTABLE_WORKSPACE"


def portable() -> bool:
    """Whether this runs in a portable package (portable/windows-profile.json)."""
    return os.environ.get(PORTABLE_ENV) == "1"


def business_pack() -> Path:
    """The Business Pack root ($HAKONIWA_WORKSPACE_ROOT)."""
    return _workspace_variable("HAKONIWA_WORKSPACE_ROOT")


def work_dir() -> Path:
    """The Business Pack work directory ($HAKONIWA_WORK_DIR, which can be relocated)."""
    return _workspace_variable("HAKONIWA_WORK_DIR")


def placeholders() -> dict:
    """Each placeholder and how to find it (the Workspace ones only when a path uses them)."""
    return {"repo": lambda: ROOT, "workspace": lambda: ROOT.parent, "business_pack": business_pack, "work": work_dir}


def resolve(value: str) -> Path:
    """A manifest path: relative to this repository, or starting with a placeholder."""
    known = placeholders()

    def replace(match):
        if match.group(1) not in known:
            raise ManifestError(f"unknown placeholder ${{{match.group(1)}}} in {value!r}")
        return str(known[match.group(1)]())

    text = PLACEHOLDER.sub(replace, str(value))
    candidate = Path(text)
    return candidate if candidate.is_absolute() else ROOT / candidate


def value(dotted: str):
    """A manifest value by its dotted name (assets.user, recipes.car.path, ...)."""
    data = load()
    for key in dotted.split("."):
        if not isinstance(data, dict) or key not in data:
            raise ManifestError(f"urban.manifest.yaml has no {dotted}")
        data = data[key]
    return data


def path(dotted: str) -> Path:
    return resolve(value(dotted))


# --- Ports -----------------------------------------------------------------------------

def env_name(port_id: str) -> str:
    return "HAKONIWA_URBAN_PORT_" + port_id.upper().replace("-", "_")


def _overrides() -> tuple[dict, Path | None]:
    target = load().get("port_overrides")
    if not target:
        return {}, None
    file = resolve(target)
    if not file.is_file():
        return {}, file
    data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    ports = data.get("ports", {}) if isinstance(data, dict) else {}
    if not isinstance(ports, dict):
        raise ManifestError(f"{file}: ports must be a mapping of port id to number")
    return ports, file


def _valid(number, where: str) -> int:
    if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= 65535:
        try:
            number = int(str(number))
        except ValueError:
            raise ManifestError(f"{where}: a port is a whole number 1-65535, got {number!r}") from None
        if not 1 <= number <= 65535:
            raise ManifestError(f"{where}: a port is a whole number 1-65535, got {number!r}")
    return number


def resolved_port(port_id: str) -> tuple[int, str]:
    """(port, where it came from: default | environment | overrides file)."""
    ports = load()["ports"]
    if port_id not in ports:
        raise ManifestError(f"urban.manifest.yaml has no port {port_id!r} (known: {', '.join(sorted(ports))})")
    entry = ports[port_id]
    if entry.get("fixed"):
        return int(entry["default"]), "default (fixed)"
    variable = env_name(port_id)
    if os.environ.get(variable):
        return _valid(os.environ[variable], variable), f"environment {variable}"
    overrides, file = _overrides()
    if port_id in overrides:
        return _valid(overrides[port_id], f"{file}: ports.{port_id}"), f"overrides file {file}"
    return int(entry["default"]), "default"


def port(port_id: str) -> int:
    """The port of an id (its default unless the environment or the overrides file changes it)."""
    return resolved_port(port_id)[0]


def check() -> list[str]:
    """What is wrong with the manifest (its paths, ports, and reserved ports)."""
    problems = []
    data = load()
    for name in ("contracts", "recipes"):
        for key, entry in data.get(name, {}).items():
            if not resolve(entry["path"]).is_file():
                problems.append(f"{name}.{key}.path: no such file: {entry['path']}")
    if not path("compositions.repository").is_dir():
        problems.append(f"compositions.repository: no such folder: {value('compositions.repository')}")
    if not path("assets.repository").is_dir():
        problems.append(f"assets.repository: no such folder: {value('assets.repository')}")
    if not path("worlds.plain_ground").is_file():
        problems.append(f"worlds.plain_ground: no such file: {value('worlds.plain_ground')}")
    default_world = path("assets.repository") / f"{value('worlds.default')}{value('assets.suffix')}"
    if not default_world.is_file():
        problems.append(f"worlds.default: no Asset {value('worlds.default')!r} in {value('assets.repository')}")
    seen: dict[int, str] = {}
    for port_id, entry in data["ports"].items():
        try:
            number = _valid(entry.get("default"), f"ports.{port_id}.default")
        except ManifestError as exc:
            problems.append(str(exc))
            continue
        if number in seen:
            problems.append(f"ports.{port_id}: {number} is also ports.{seen[number]}")
        seen[number] = port_id
    for entry in data.get("reserved", []):
        if entry["port"] in seen:
            problems.append(f"ports.{seen[entry['port']]}: {entry['port']} is reserved for {entry['owner']}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    ports = commands.add_parser("ports", help="print every port and where its value comes from")
    ports.add_argument("--json", action="store_true")
    commands.add_parser("check", help="check the manifest's paths and ports")
    args = parser.parse_args(argv)
    if args.command == "ports":
        rows = []
        for port_id, entry in load()["ports"].items():
            number, source = resolved_port(port_id)
            rows.append({"id": port_id, "port": number, "source": source, "protocol": entry.get("protocol"),
                         "purpose": entry.get("purpose"), "env": None if entry.get("fixed") else env_name(port_id)})
        if args.json:
            print(json.dumps({"ports": rows, "reserved": load().get("reserved", [])}, ensure_ascii=False, indent=2))
        else:
            for row in rows:
                print(f"{row['id']:20} {row['port']:6}  {row['source']:28} {row['purpose']}")
            for entry in load().get("reserved", []):
                print(f"{'(reserved)':20} {entry['port']:6}  {entry['owner']}")
        return 0
    problems = check()
    for problem in problems:
        print(f"NG  {problem}", file=sys.stderr)
    print("OK" if not problems else f"{len(problems)} problems")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
