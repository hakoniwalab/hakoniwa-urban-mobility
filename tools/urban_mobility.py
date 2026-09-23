#!/usr/bin/env python3
"""Standard lifecycle entrypoint for the city-independent Urban Recipe."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import platform
import subprocess
import sys
import webbrowser


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
BUSINESS_PACK = WORKSPACE / "hakoniwa-business-pack"
RECIPE_ID = "urban-mobility-rc"
MANAGED_RECIPE = ROOT / "recipes/experiments/urban-mobility-rc.yaml"
COMPOSITION = ROOT / "recipes/experiments/urban-mobility-shizuoka.yaml"

sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(BUSINESS_PACK / "tools"))

import urban_lifecycle  # noqa: E402
import drone_one  # noqa: E402
import urban_composer  # noqa: E402
from workdir import foundation_install, recipe_root  # noqa: E402
from workspace import foundation_python_layout  # noqa: E402


class UrbanMobilityError(RuntimeError):
    pass


def foundation_python() -> Path:
    install = foundation_install(BUSINESS_PACK)
    path, _ = foundation_python_layout(install / "python")
    if not path.is_file():
        raise UrbanMobilityError(f"Foundation Python not found: {path}")
    return path


def root() -> Path:
    return recipe_root(BUSINESS_PACK, RECIPE_ID)


def viewer_url() -> str:
    path = root() / "config/viewer-url.json"
    if not path.is_file():
        raise UrbanMobilityError(
            f"integrated Viewer is not configured: {path}; run configure first"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        url = payload["url"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise UrbanMobilityError(f"invalid integrated Viewer URL: {path}: {exc}") from exc
    if not isinstance(url, str) or not url.startswith("http://127.0.0.1:"):
        raise UrbanMobilityError(f"invalid integrated Viewer URL: {url!r}")
    return url


def spec(*, require_viewer: bool = True) -> urban_lifecycle.LifecycleSpec:
    recipe_root_path = root()
    url = viewer_url() if require_viewer else "http://127.0.0.1:8000/"
    return urban_lifecycle.LifecycleSpec(
        recipe_id=RECIPE_ID,
        recipe_root=recipe_root_path,
        launcher=recipe_root_path / "config/launcher.json",
        session=recipe_root_path / "runtime/launcher-session.json",
        viewer_url=url,
    )


def recipe_command(operation: str) -> int:
    return subprocess.run(
        [
            sys.executable,
            str(BUSINESS_PACK / "tools/recipe.py"),
            operation,
            "--recipe",
            str(MANAGED_RECIPE),
        ],
        cwd=BUSINESS_PACK,
        check=False,
    ).returncode


def launcher_command(operation: str) -> int:
    if operation == "start":
        if recipe_command("doctor") != 0:
            return 1
        paths = drone_one._paths(RECIPE_ID)
        configured = drone_one.read_selected_recipe(paths)
        runtime_recipe = drone_one.load_runtime_recipe(configured)
        drone_one.refresh_runtime_spawn(paths, runtime_recipe)
        drone_one.refresh_runtime_controller_params(
            paths,
            runtime_recipe,
            runtime_config_dir=root() / "config/drone/rc",
        )
        lifecycle = spec()
        if not lifecycle.launcher.is_file():
            raise UrbanMobilityError(
                f"integrated Launcher is not configured: {lifecycle.launcher}"
            )
        urban_lifecycle.preflight_start(lifecycle)
        command = [
            str(foundation_python()),
            "-m",
            "hakoniwa_pdu.apps.launcher.hako_launcher",
            str(lifecycle.launcher),
            "--background",
            str(lifecycle.session),
        ]
    else:
        lifecycle = spec(require_viewer=False)
        if not lifecycle.session.is_file():
            if operation == "status":
                print(json.dumps(urban_lifecycle.status_report(lifecycle), indent=2))
                return 0
            raise UrbanMobilityError(f"Recipe is already stopped: {lifecycle.session}")
        # Refuse to send a control command through a stale or foreign session.
        urban_lifecycle.read_session(lifecycle)
        command = [
            str(foundation_python()),
            "-m",
            "hakoniwa_pdu.apps.launcher.hako_launcher_ctl",
            "status" if operation == "status" else "terminate",
            str(lifecycle.session),
        ]
    result = subprocess.run(command, cwd=ROOT, check=False)
    if result.returncode != 0:
        return result.returncode
    if operation == "stop":
        urban_lifecycle.verify_stopped(lifecycle)
    else:
        print(json.dumps(urban_lifecycle.status_report(lifecycle), indent=2))
    return 0


def open_viewer() -> int:
    lifecycle = spec()
    urban_lifecycle.require_viewer_ready(lifecycle)
    print(f"Opening Three.js: {lifecycle.viewer_url}")
    return 0 if webbrowser.open(lifecycle.viewer_url) else 1


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "command",
        choices=(
            "prepare-native", "plan", "doctor", "configure", "start",
            "status", "stop", "open-viewer",
        ),
    )
    return result


def main() -> int:
    command = parser().parse_args().command
    if command == "prepare-native":
        paths = drone_one._paths(RECIPE_ID)
        return drone_one.base.prepare_native_distribution(
            drone_one.DEFAULT_DRONE_ROOT,
            platform.system(),
            cache_root=paths.recipe_root / "downloads",
            evidence_path=paths.recipe_validation / "native-distribution.json",
        )
    if command in {"plan", "doctor"}:
        return recipe_command(command)
    if command == "configure":
        if recipe_command("configure") != 0:
            return 1
        return urban_composer.configure(COMPOSITION)
    if command == "open-viewer":
        return open_viewer()
    return launcher_command(command)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, UrbanMobilityError, urban_lifecycle.LifecycleError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
