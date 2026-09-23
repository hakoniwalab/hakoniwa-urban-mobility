#!/usr/bin/env python3
"""Standard lifecycle entrypoint for Urban Mobility managed Recipes."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import importlib.util
import json
from pathlib import Path
import platform
import subprocess
import sys
import webbrowser


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
BUSINESS_PACK = WORKSPACE / "hakoniwa-business-pack"

sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(BUSINESS_PACK / "tools"))

import urban_lifecycle  # noqa: E402
from workdir import foundation_install, recipe_root  # noqa: E402
from workspace import foundation_python_layout  # noqa: E402


class UrbanMobilityError(RuntimeError):
    pass


def load_managed_recipe(path: Path) -> dict:
    """Load a managed Recipe through the Business Pack recipe.py module."""
    script = BUSINESS_PACK / "tools/recipe.py"
    spec = importlib.util.spec_from_file_location(
        "business_pack_recipe_for_urban_mobility",
        script,
    )
    if spec is None or spec.loader is None:
        raise UrbanMobilityError(f"cannot load Business Pack Recipe tool: {script}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.load_recipe(path)


@dataclass(frozen=True)
class RecipeContext:
    path: Path
    data: dict
    recipe_id: str
    use_case: str


def resolve_recipe_path(path: Path) -> Path:
    selected = path.expanduser()
    if not selected.is_absolute():
        selected = ROOT / selected
    return selected.resolve()


def load_context(path: Path) -> RecipeContext:
    recipe_path = resolve_recipe_path(path)
    data = load_managed_recipe(recipe_path)
    urban = data.get("urban_mobility")
    if not isinstance(urban, dict):
        raise UrbanMobilityError(
            f"managed Recipe has no urban_mobility contract: {recipe_path}"
        )
    use_case = urban.get("use_case")
    if not isinstance(use_case, str) or not use_case.strip():
        raise UrbanMobilityError(
            f"managed Recipe has no urban_mobility.use_case: {recipe_path}"
        )
    return RecipeContext(
        path=recipe_path,
        data=data,
        recipe_id=str(data["id"]),
        use_case=use_case.strip(),
    )


def foundation_python() -> Path:
    install = foundation_install(BUSINESS_PACK)
    path, _ = foundation_python_layout(install / "python")
    if not path.is_file():
        raise UrbanMobilityError(f"Foundation Python not found: {path}")
    return path


def root(context: RecipeContext) -> Path:
    return recipe_root(BUSINESS_PACK, context.recipe_id)


def viewer_url(context: RecipeContext) -> str:
    path = root(context) / "config/viewer-url.json"
    if not path.is_file():
        raise UrbanMobilityError(
            f"Viewer is not configured for Recipe {context.recipe_id}: "
            f"{path}; run configure first"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        url = payload["url"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise UrbanMobilityError(f"invalid Viewer URL contract: {path}: {exc}") from exc
    if not isinstance(url, str) or not url.startswith("http://127.0.0.1:"):
        raise UrbanMobilityError(f"invalid Viewer URL: {url!r}")
    return url


def spec(
    context: RecipeContext,
    *,
    require_viewer: bool = True,
) -> urban_lifecycle.LifecycleSpec:
    recipe_root_path = root(context)
    contract_path = recipe_root_path / "config/viewer-url.json"
    http_port = 8000
    websocket_port = 8765
    url = "http://127.0.0.1:8000/"
    if contract_path.is_file():
        try:
            payload = json.loads(contract_path.read_text(encoding="utf-8"))
            if isinstance(payload.get("http_port"), int):
                http_port = payload["http_port"]
            if isinstance(payload.get("websocket_port"), int):
                websocket_port = payload["websocket_port"]
            if isinstance(payload.get("url"), str):
                url = payload["url"]
        except (OSError, json.JSONDecodeError) as exc:
            raise UrbanMobilityError(
                f"invalid Viewer URL contract: {contract_path}: {exc}"
            ) from exc
    elif require_viewer:
        url = viewer_url(context)

    return urban_lifecycle.LifecycleSpec(
        recipe_id=context.recipe_id,
        recipe_root=recipe_root_path,
        launcher=recipe_root_path / "config/launcher.json",
        session=recipe_root_path / "runtime/launcher-session.json",
        viewer_url=url,
        websocket_port=websocket_port,
        ports=(http_port, websocket_port, 54111),
    )


def recipe_command(operation: str, context: RecipeContext) -> int:
    return subprocess.run(
        [
            sys.executable,
            str(BUSINESS_PACK / "tools/recipe.py"),
            operation,
            "--recipe",
            str(context.path),
        ],
        cwd=BUSINESS_PACK,
        check=False,
    ).returncode


def _urban_contract(context: RecipeContext) -> dict:
    value = context.data.get("urban_mobility")
    if not isinstance(value, dict):
        raise UrbanMobilityError(
            f"managed Recipe has no urban_mobility contract: {context.path}"
        )
    return value


def _managed_relative_path(context: RecipeContext, value: object, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise UrbanMobilityError(f"{label} must be a non-empty path")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


def composition_path(context: RecipeContext) -> Path:
    urban = _urban_contract(context)
    try:
        value = urban["composition"]["path"]
    except (KeyError, TypeError) as exc:
        raise UrbanMobilityError(
            f"managed Recipe has no urban_mobility.composition.path: {context.path}"
        ) from exc
    return _managed_relative_path(context, value, "Urban composition path")


def generated_composition_path(context: RecipeContext) -> Path:
    return root(context) / "config/urban-composition.json"


def _set_nested(mapping: dict, target: str, value: object) -> None:
    keys = [part for part in target.split(".") if part]
    if not keys:
        raise UrbanMobilityError("Urban parameter target must not be empty")
    current = mapping
    for key in keys[:-1]:
        nested = current.get(key)
        if not isinstance(nested, dict):
            nested = {}
            current[key] = nested
        current = nested
    current[keys[-1]] = value


def _parameter_values(context: RecipeContext, args: argparse.Namespace) -> dict[str, object]:
    urban = _urban_contract(context)
    parameters = urban.get("parameters", {})
    if not isinstance(parameters, dict):
        raise UrbanMobilityError("urban_mobility.parameters must be a mapping")
    values: dict[str, object] = {}
    for name, definition in parameters.items():
        if not isinstance(definition, dict):
            raise UrbanMobilityError(f"Urban parameter {name} must be a mapping")
        value = getattr(args, name, None)
        required = bool(definition.get("required", False))
        if value is None:
            if required:
                option = definition.get("cli", "--" + name.replace("_", "-"))
                raise UrbanMobilityError(
                    f"Recipe {context.recipe_id} requires {option} for configure"
                )
            continue
        if definition.get("type") == "path":
            path = Path(value).expanduser().resolve()
            if not path.is_file():
                raise UrbanMobilityError(
                    f"Urban parameter {name} file not found: {path}"
                )
            value = str(path)
        elif definition.get("type") == "int":
            value = int(value)
            if not 1 <= value <= 65535:
                raise UrbanMobilityError(
                    f"Urban parameter {name} must be within 1..65535: {value}"
                )
        values[name] = value
    return values


def materialize_template(
    context: RecipeContext,
    args: argparse.Namespace,
) -> Path:
    import multi_car

    urban = _urban_contract(context)
    try:
        template_value = urban["template"]["path"]
    except (KeyError, TypeError) as exc:
        raise UrbanMobilityError(
            f"managed Recipe has no urban_mobility.template.path: {context.path}"
        ) from exc
    template_path = _managed_relative_path(
        context, template_value, "Urban template path"
    )
    config = multi_car.load_yaml(template_path)
    config["id"] = context.recipe_id
    composition = config.setdefault("composition", {})
    if not isinstance(composition, dict):
        raise UrbanMobilityError("Urban template composition must be a mapping")
    output = composition.setdefault("output", {})
    if not isinstance(output, dict):
        raise UrbanMobilityError("Urban template composition.output must be a mapping")
    output["recipe_id"] = context.recipe_id

    values = _parameter_values(context, args)
    parameters = urban.get("parameters", {})
    for name, value in values.items():
        definition = parameters[name]
        target = definition.get("target")
        if not isinstance(target, str) or not target.strip():
            raise UrbanMobilityError(
                f"Urban parameter {name} has no target in {context.path}"
            )
        _set_nested(config, target, value)

    output_path = generated_composition_path(context)
    multi_car.write_json(output_path, config)
    multi_car.write_json(
        root(context) / "validation/urban-inputs.json",
        {
            "schema_version": 1,
            "managed_recipe": str(context.path),
            "recipe_id": context.recipe_id,
            "use_case": context.use_case,
            "template": str(template_path),
            "parameters": values,
            "generated_composition": str(output_path),
        },
    )
    return output_path


def configure_car_rc(context: RecipeContext, args: argparse.Namespace) -> int:
    import multi_car

    config_path = materialize_template(context, args)
    multi_car.build_car_asset()
    resolved = multi_car.resolve_config(config_path)
    if multi_car.configure(resolved) != 0:
        return 1

    viewer_config = root(context) / "config/threejs/viewer-config.json"
    url = multi_car.map_viewer_url(resolved, viewer_config)
    viewer_contract = {
        "url": url,
        "layout": "three-main",
        "use_case": context.use_case,
        "http_port": resolved["visualization"]["http_port"],
        "websocket_port": resolved["visualization"]["web_bridge_port"],
    }
    collider_config = root(context) / "config/threejs/viewer-config-colliders.json"
    if collider_config.is_file():
        viewer_contract["collider_url"] = multi_car.map_viewer_url(
            resolved, collider_config
        )
    multi_car.write_json(root(context) / "config/viewer-url.json", viewer_contract)
    print(f"Urban Recipe : {context.recipe_id}")
    print(f"Use case     : {context.use_case}")
    print(f"Composition  : {config_path}")
    print(f"Viewer       : {url}")
    return 0


def configure_integrated(context: RecipeContext) -> int:
    import urban_composer

    return urban_composer.configure(composition_path(context))


def configure(context: RecipeContext, args: argparse.Namespace) -> int:
    if recipe_command("configure", context) != 0:
        return 1
    if context.use_case == "car-rc":
        return configure_car_rc(context, args)
    if context.use_case == "drone-car-distributed":
        return configure_integrated(context)
    raise UrbanMobilityError(
        f"unsupported Urban use case for configure: {context.use_case}"
    )


def prepare_start(context: RecipeContext) -> None:
    if context.use_case == "car-rc":
        return
    if context.use_case != "drone-car-distributed":
        raise UrbanMobilityError(
            f"unsupported Urban use case for start: {context.use_case}"
        )

    import drone_one

    paths = drone_one._paths(context.recipe_id)
    configured = drone_one.read_selected_recipe(paths)
    runtime_recipe = drone_one.load_runtime_recipe(configured)
    drone_one.refresh_runtime_spawn(paths, runtime_recipe)
    drone_one.refresh_runtime_controller_params(
        paths,
        runtime_recipe,
        runtime_config_dir=root(context) / "config/drone/rc",
    )


def launcher_command(operation: str, context: RecipeContext) -> int:
    if operation == "start":
        if recipe_command("doctor", context) != 0:
            return 1
        prepare_start(context)
        lifecycle = spec(context)
        if not lifecycle.launcher.is_file():
            raise UrbanMobilityError(
                f"Launcher is not configured for Recipe {context.recipe_id}: "
                f"{lifecycle.launcher}"
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
        lifecycle = spec(context, require_viewer=False)
        if not lifecycle.session.is_file():
            if operation == "status":
                print(json.dumps(urban_lifecycle.status_report(lifecycle), indent=2))
                return 0
            raise UrbanMobilityError(
                f"Recipe {context.recipe_id} is already stopped: {lifecycle.session}"
            )
        session = urban_lifecycle.read_session(lifecycle) or {}
        session_state = session.get("state")
        if session_state in {"FAILED", "TERMINATED"}:
            report = urban_lifecycle.status_report(lifecycle)
            print(json.dumps(report, indent=2))
            if operation == "stop":
                urban_lifecycle.verify_stopped(lifecycle)
            return 0
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
    elif operation == "start":
        report = urban_lifecycle.wait_for_demo_ready(lifecycle)
        print(json.dumps(report, indent=2))
        if not report["demo_ready"]:
            raise UrbanMobilityError(
                f"Recipe {context.recipe_id} Launcher is RUNNING but browser "
                "readiness did not complete within 15 seconds; inspect Recipe logs"
            )
    else:
        print(json.dumps(urban_lifecycle.status_report(lifecycle), indent=2))
    return 0


def check_rc(context: RecipeContext) -> int:
    if context.use_case != "car-rc":
        raise UrbanMobilityError(
            f"check-rc is not supported by Urban use case {context.use_case}"
        )
    import multi_car

    config_path = generated_composition_path(context)
    if not config_path.is_file():
        raise UrbanMobilityError(
            f"Recipe {context.recipe_id} is not configured: {config_path}"
        )
    return multi_car.check_ps5(multi_car.resolve_config(config_path))


def open_viewer(context: RecipeContext) -> int:
    lifecycle = spec(context)
    urban_lifecycle.require_viewer_ready(lifecycle)
    print(f"Opening Three.js: {lifecycle.viewer_url}")
    return 0 if webbrowser.open(lifecycle.viewer_url) else 1


def prepare_native(context: RecipeContext) -> int:
    if context.use_case != "drone-car-distributed":
        raise UrbanMobilityError(
            f"prepare-native is not required by Urban use case {context.use_case}"
        )
    import drone_one

    paths = drone_one._paths(context.recipe_id)
    return drone_one.base.prepare_native_distribution(
        drone_one.DEFAULT_DRONE_ROOT,
        platform.system(),
        cache_root=paths.recipe_root / "downloads",
        evidence_path=paths.recipe_validation / "native-distribution.json",
    )


def print_use_case(context: RecipeContext) -> None:
    print(f"Urban use case: {context.use_case} ({context.recipe_id})")
    parameters = _urban_contract(context).get("parameters", {})
    if isinstance(parameters, dict):
        for name, definition in parameters.items():
            if not isinstance(definition, dict):
                continue
            option = definition.get("cli", "--" + name.replace("_", "-"))
            target = definition.get("target", "?")
            required = "required for configure" if definition.get("required") else "optional"
            print(f"  - {option}: {target} ({required})")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "command",
        choices=(
            "prepare-native",
            "plan",
            "doctor",
            "configure",
            "check-rc",
            "start",
            "status",
            "stop",
            "open-viewer",
        ),
    )
    result.add_argument(
        "--recipe",
        type=Path,
        required=True,
        help="managed Urban Mobility Recipe",
    )
    result.add_argument(
        "--city-receipt",
        type=Path,
        help="City World receipt used to fill a Recipe template input",
    )
    result.add_argument(
        "--web-bridge-port",
        type=int,
        help="optional WebBridge port override for configure",
    )
    return result


def main() -> int:
    args = parser().parse_args()
    context = load_context(args.recipe)
    if args.command == "prepare-native":
        return prepare_native(context)
    if args.command in {"plan", "doctor"}:
        result = recipe_command(args.command, context)
        if result == 0:
            print_use_case(context)
        return result
    if args.command == "configure":
        return configure(context, args)
    if args.command == "check-rc":
        return check_rc(context)
    if args.command == "open-viewer":
        return open_viewer(context)
    return launcher_command(args.command, context)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        OSError,
        UrbanMobilityError,
        urban_lifecycle.LifecycleError,
        subprocess.CalledProcessError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
