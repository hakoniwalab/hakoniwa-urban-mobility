#!/usr/bin/env python3
"""Make Cities with Environment Studio: configure it and start or stop it
with Urban's export folder (recipes/usecases/urban-city-authoring.yaml).

Environment Studio does not know Urban. It writes the Cities it makes as City
World jobs (schemas/city-world-job.yaml) to the folder it is started with;
Urban Studio registers each job that appears in that folder
(urban.manifest.yaml assets.studio_city_jobs) and unregisters it when it goes.

From hakoniwa-business-pack, in the Workspace (python tools/workspace.py enter):

    python ../hakoniwa-urban-mobility/tools/urban_city_authoring.py start [--open-browser] [--no-configure]
    python ../hakoniwa-urban-mobility/tools/urban_city_authoring.py status | open | stop

start runs the Business Pack `recipe.py configure` on this Recipe (it clones
Environment Studio when missing) and on Environment Studio's own Recipe (its
Python packages and hakoniwa-envsim), then `env_studio.py start --export-dir`.
In a Windows portable package (HAKONIWA_PORTABLE_WORKSPACE=1) it skips the
configure steps, as --no-configure does.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import urban_manifest  # noqa: E402

RECIPE = urban_manifest.ROOT / urban_manifest.value("recipes.city.path")
EXPORT_DIR = urban_manifest.path("assets.studio_city_jobs")
PORT = urban_manifest.port("environment-studio")
STUDIO_RECIPE = Path("recipes/business-pack/environment-studio.yaml")


class AuthoringError(RuntimeError):
    pass


def studio_root(environ: dict[str, str] | None = None) -> Path:
    """Environment Studio's checkout, as recipe.py resolves this Recipe's
    recipe_local_requirements: the override variable, else default_path from
    this repository."""
    environ = os.environ if environ is None else environ
    recipe = yaml.safe_load(RECIPE.read_text(encoding="utf-8"))
    root = recipe["recipe_local_requirements"]["hakoniwa-environment-studio"]["root"]
    override = environ.get(root["override_env"], "").strip()
    selected = Path(override).expanduser() if override else Path(root["default_path"])
    return (selected if selected.is_absolute() else ROOT / selected).resolve()


def _run(command: list[str]) -> int:
    print("+ " + " ".join(command), flush=True)
    return subprocess.run(command, check=False, stdin=subprocess.DEVNULL).returncode


def configure() -> int:
    """This Recipe first (Environment Studio's checkout), then the Studio's own.

    A portable package (urban_manifest.portable) carries Environment Studio,
    hakoniwa-envsim, and their Python packages, and has no Git or pip: there
    is nothing to configure."""
    if urban_manifest.portable():
        print("Portable workspace: Environment Studio configure skipped (the package carries it)", flush=True)
        return 0
    recipe_tool = urban_manifest.business_pack() / "tools/recipe.py"
    for recipe in (RECIPE, None):
        path = recipe if recipe is not None else studio_root() / STUDIO_RECIPE
        code = _run([sys.executable, str(recipe_tool), "configure", "--recipe", str(path)])
        if code:
            print(f"ERROR: configure of {path} failed (exit {code})", file=sys.stderr)
            return code
    return 0


def studio(command: str, *extra: str) -> int:
    tool = studio_root() / "tools/env_studio.py"
    if not tool.is_file():
        raise AuthoringError(f"Environment Studio is not there ({tool}); run start to configure it")
    return _run([sys.executable, str(tool), command, "--port", str(PORT), *extra])


def start(open_browser: bool = False, configure_first: bool = True) -> int:
    if configure_first:
        code = configure()
        if code:
            return code
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    code = studio("start", "--export-dir", str(EXPORT_DIR), *(["--open-browser"] if open_browser else []))
    if code == 0:
        print(f"Cities written by Environment Studio go to {EXPORT_DIR}; Urban Studio registers them.")
        print(f"  map page: http://127.0.0.1:{PORT}/map.html")
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("start", "status", "open", "stop", "configure"))
    parser.add_argument("--open-browser", action="store_true", help="start: open the Studio in the browser")
    parser.add_argument("--no-configure", action="store_true", help="start: skip the configure steps")
    args = parser.parse_args(argv)
    try:
        if args.command == "configure":
            return configure()
        if args.command == "start":
            return start(args.open_browser, not args.no_configure)
        return studio(args.command)
    except AuthoringError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
