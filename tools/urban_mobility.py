#!/usr/bin/env python3
"""Standard lifecycle entrypoint for Urban Mobility managed Recipes."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import importlib.util
import json
import os
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


# Composition simulators -> the managed Recipe that prepares their environment
# (asset-contract 7.1 step 1; one standard managed Recipe replaces this later).
COMPOSITION_RECIPES = {
    frozenset({"ackermann-mujoco"}): "recipes/usecases/urban-car-rc.yaml",
    frozenset({"ackermann-mujoco", "drone-core"}): "recipes/experiments/urban-mobility-rc.yaml",
}


def load_composition(composition_path: Path):
    import urban_composition

    try:
        return urban_composition.load(composition_path)
    except urban_composition.CompositionError as exc:
        raise UrbanMobilityError(str(exc)) from exc


# A Drone-only Composition runs through tools/drone_one.py, which owns its
# Recipe workspace; the adapter writes that tool's recipe from the Composition.
DRONE_ONLY = frozenset({"drone-core"})
DRONE_RECIPE_FILE = "urban-composition-drone.yaml"


def composition_recipe(composition_path: Path) -> Path:
    composition = load_composition(composition_path)
    recipe = COMPOSITION_RECIPES.get(frozenset(composition.simulators()))
    if recipe is None:
        raise UrbanMobilityError(
            "no managed Recipe adapts a Composition with simulators "
            f"{sorted(composition.simulators())} yet"
        )
    return ROOT / recipe


def drone_recipe_path() -> Path:
    import drone_one

    return drone_one._paths().recipe_config / DRONE_RECIPE_FILE


def materialize_drone_recipe(composition_path: Path) -> Path:
    import urban_composition

    composition = load_composition(composition_path)
    try:
        recipe = urban_composition.to_drone_recipe(composition)
        text = urban_composition.render_simple_yaml(recipe) + "\n"
    except urban_composition.CompositionError as exc:
        raise UrbanMobilityError(str(exc)) from exc
    path = drone_recipe_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def car_runtime(work: Path):
    """Controls runtime for the Car simulator configured under a Recipe root."""
    import multi_car
    import urban_controls

    return urban_controls.Runtime(
        values={
            "pdu_def": work / "config/car/urban-car-pdudef.json",
            "rc_config": ROOT / "config/car/dualsense-controller.json",
        },
        service_asset="urban-car-fleet-plant",
        python=str(multi_car.foundation_python()),
    )


def drone_runtime(paths: object, pdu_def: Path):
    """Controls runtime for the Drone simulator configured in drone_one paths."""
    import drone_one
    import multi_car
    import urban_controls

    return urban_controls.Runtime(
        values={
            "pdu_def": pdu_def,
            "drone_root": drone_one.DEFAULT_DRONE_ROOT.resolve(),
            "service_config": paths.recipe_config / "drone/fleets/services/api-current-service.json",
            "city_marker": paths.recipe_config / "mujoco-city-fleet.json",
            "summary_json": paths.recipe_validation / "urban-drone-mission.json",
        },
        service_asset="drone-service-1",
        python=str(multi_car.foundation_python()),
    )


def control_processes(composition_path: Path, runtimes: dict) -> list[dict]:
    import urban_controls

    try:
        return urban_controls.control_processes(load_composition(composition_path), runtimes)
    except urban_controls.ControlError as exc:
        raise UrbanMobilityError(str(exc)) from exc


def write_drone_controls(composition_path: Path) -> Path:
    """Write the processes that tools/drone_one.py applies whenever it writes its Launcher."""
    import drone_one
    import multi_car
    import urban_controls

    paths = drone_one._paths()
    processes = control_processes(
        composition_path,
        {"drone-core": drone_runtime(paths, paths.recipe_config / "pdudef/drone-pdudef-current.json")},
    )
    path = paths.recipe_config / urban_controls.CONTROLS_FILE
    multi_car.write_json(path, processes)
    return path


def apply_composition_controls(context: RecipeContext, composition_path: Path) -> None:
    """Replace the configured Launcher's control assets with the manifest controls."""
    import drone_one
    import multi_car
    import urban_controls

    runtimes = {"ackermann-mujoco": car_runtime(root(context))}
    if context.use_case == "drone-car-distributed":
        runtimes["drone-core"] = drone_runtime(
            drone_one._paths(context.recipe_id), root(context) / "config/car/urban-car-pdudef.json"
        )
    launcher_path = root(context) / "config/launcher.json"
    launcher = multi_car.load_json(launcher_path, "configured Launcher")
    urban_controls.apply_controls(launcher, control_processes(composition_path, runtimes))
    multi_car.write_json(launcher_path, launcher)


def drone_composition_command(command: str, composition_path: Path) -> int:
    """Run a Drone-only Composition through tools/drone_one.py.

    configure and start rewrite the tool recipe and control processes from
    the Composition, so placement, param, and program edits take effect at
    the next start (drone_one.py accepts pose-only recipe edits and rejects
    the rest until configure).
    """
    tool = [sys.executable, str(ROOT / "tools/drone_one.py")]
    if command == "configure":
        recipe = materialize_drone_recipe(composition_path)
        result = subprocess.run([*tool, "configure", "--recipe", str(recipe)], cwd=ROOT, check=False).returncode
        if result == 0:
            write_drone_controls(composition_path)
        return result
    if command == "start":
        if not drone_recipe_path().is_file():
            raise UrbanMobilityError("the Drone Composition is not configured; run configure --composition first")
        materialize_drone_recipe(composition_path)
        write_drone_controls(composition_path)
    if command in {"plan", "check-rc"}:
        raise UrbanMobilityError(f"{command} is not supported for a Drone-only Composition")
    return subprocess.run([*tool, command], cwd=ROOT, check=False).returncode


def materialize_composition(context: RecipeContext, composition_path: Path) -> Path:
    import multi_car
    import urban_composition

    composition = load_composition(composition_path)
    try:
        config = urban_composition.to_car_config(composition, context.recipe_id)
    except urban_composition.CompositionError as exc:
        raise UrbanMobilityError(str(exc)) from exc
    output_path = generated_composition_path(context)
    multi_car.write_json(output_path, config)
    multi_car.write_json(
        root(context) / "validation/urban-inputs.json",
        {
            "schema_version": 1,
            "managed_recipe": str(context.path),
            "recipe_id": context.recipe_id,
            "use_case": context.use_case,
            "composition": str(composition.path),
            "composition_id": composition.id,
            "generated_composition": str(output_path),
        },
    )
    return output_path


def configure_car_rc(context: RecipeContext, args: argparse.Namespace) -> int:
    import multi_car

    if getattr(args, "composition", None) is not None:
        if getattr(args, "city_receipt", None) is not None:
            raise UrbanMobilityError(
                "--city-receipt conflicts with --composition; the Composition world selects the City"
            )
        config_path = materialize_composition(context, args.composition)
    else:
        config_path = materialize_template(context, args)
    if not getattr(args, "reuse_built_asset", False):
        multi_car.build_car_asset()
    elif not (ROOT / "build/bin/urban-car-hakoniwa-asset.exe").is_file():
        raise UrbanMobilityError(
            "--reuse-built-asset requires build/bin/urban-car-hakoniwa-asset.exe"
        )
    resolved = multi_car.resolve_config(config_path)
    if multi_car.configure(resolved) != 0:
        return 1
    if getattr(args, "composition", None) is not None:
        apply_composition_controls(context, args.composition)

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


def composition_outputs(context: RecipeContext, composition_path: Path) -> tuple[dict, str | None]:
    """Return the tool configuration and, with a Drone, its drone_one.py recipe text."""
    import urban_composition

    composition = load_composition(composition_path)
    try:
        if context.use_case == "car-rc":
            return urban_composition.to_car_config(composition, context.recipe_id), None
        if context.use_case == "drone-car-distributed":
            config, drone = urban_composition.to_integrated(
                composition, context.recipe_id, root(context) / "config" / DRONE_RECIPE_FILE
            )
            return config, urban_composition.render_simple_yaml(drone) + "\n"
    except urban_composition.CompositionError as exc:
        raise UrbanMobilityError(str(exc)) from exc
    raise UrbanMobilityError(f"Urban use case {context.use_case} does not take a Composition")


def write_composition_outputs(context: RecipeContext, config: dict, drone: str | None) -> Path:
    import multi_car

    config_path = generated_composition_path(context)
    multi_car.write_json(config_path, config)
    if drone is not None:
        drone_path = root(context) / "config" / DRONE_RECIPE_FILE
        drone_path.parent.mkdir(parents=True, exist_ok=True)
        drone_path.write_text(drone, encoding="utf-8")
    return config_path


def configure_integrated(context: RecipeContext, args: argparse.Namespace | None = None) -> int:
    import multi_car
    import urban_composer

    selected = getattr(args, "composition", None)
    if selected is None:
        return urban_composer.configure(composition_path(context))
    config, drone = composition_outputs(context, selected)
    config_path = write_composition_outputs(context, config, drone)
    multi_car.write_json(
        root(context) / "validation/urban-inputs.json",
        {
            "schema_version": 1,
            "managed_recipe": str(context.path),
            "recipe_id": context.recipe_id,
            "use_case": context.use_case,
            "composition": str(Path(selected).resolve()),
            "composition_id": config["composition_source"]["id"],
            "generated_composition": str(config_path),
        },
    )
    if urban_composer.configure(config_path) != 0:
        return 1
    apply_composition_controls(context, selected)
    return 0


def configure(context: RecipeContext, args: argparse.Namespace) -> int:
    portable_reconfigure = getattr(args, "portable_reconfigure", False)
    if not portable_reconfigure and recipe_command("configure", context) != 0:
        return 1
    if context.use_case == "car-rc":
        return configure_car_rc(context, args)
    if context.use_case == "drone-car-distributed":
        return configure_integrated(context, args)
    raise UrbanMobilityError(
        f"unsupported Urban use case for configure: {context.use_case}"
    )


def refresh_composition_placement(context: RecipeContext, composition_path: Path) -> Path:
    """Rewrite the tool inputs for a placement-only Composition edit.

    Returns the Car configuration path. Any edit beyond vehicle placement is
    rejected before anything is written.
    """
    import urban_composition

    inputs = root(context) / "validation/urban-inputs.json"
    try:
        configured = json.loads(inputs.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UrbanMobilityError(f"Recipe {context.recipe_id} is not configured: {inputs}") from exc
    if "composition" not in configured:
        raise UrbanMobilityError(
            f"Recipe {context.recipe_id} was not configured from a Composition; run configure --composition"
        )
    previous = json.loads(generated_composition_path(context).read_text(encoding="utf-8"))
    current, drone = composition_outputs(context, composition_path)
    if not urban_composition.placement_only_change(previous, current):
        raise UrbanMobilityError(
            "the Composition changed more than vehicle placement; run configure --composition"
        )
    # A Drone placement edit lands in the drone_one.py recipe, which that tool
    # accepts as a pose-only edit at start.
    return write_composition_outputs(context, current, drone)


def refresh_car_poses(config_path: Path) -> None:
    import multi_car

    multi_car.refresh_runtime_initial_body_poses(multi_car.resolve_config(config_path))


def prepare_start(context: RecipeContext, composition_path: Path | None = None) -> None:
    if context.use_case == "car-rc":
        if composition_path is not None:
            refresh_car_poses(refresh_composition_placement(context, composition_path))
            apply_composition_controls(context, composition_path)
        return
    if context.use_case != "drone-car-distributed":
        raise UrbanMobilityError(
            f"unsupported Urban use case for start: {context.use_case}"
        )

    import drone_one

    car_config = (
        refresh_composition_placement(context, composition_path)
        if composition_path is not None
        else None
    )

    paths = drone_one._paths(context.recipe_id)
    configured = drone_one.read_selected_recipe(paths)
    runtime_recipe = drone_one.load_runtime_recipe(configured)
    drone_one.refresh_runtime_spawn(paths, runtime_recipe)
    drone_one.refresh_runtime_controller_params(
        paths,
        runtime_recipe,
        runtime_config_dir=root(context) / "config/drone/rc",
    )
    if car_config is not None:
        refresh_car_poses(car_config)
        apply_composition_controls(context, composition_path)


def launcher_command(operation: str, context: RecipeContext, args: argparse.Namespace | None = None) -> int:
    if operation == "start":
        portable = os.environ.get("HAKONIWA_PORTABLE_WORKSPACE") == "1"
        if not portable and recipe_command("doctor", context) != 0:
            return 1
        prepare_start(context, getattr(args, "composition", None))
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
        help="managed Urban Mobility Recipe (derived from --composition when omitted)",
    )
    result.add_argument(
        "--composition",
        type=Path,
        help="Urban Composition (docs/asset-contract.md) used by configure",
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
    result.add_argument(
        "--reuse-built-asset",
        action="store_true",
        help=(
            "reuse the packaged Urban Car executable during configure; intended "
            "for a portable workspace without a C++ build toolchain"
        ),
    )
    result.add_argument(
        "--portable-reconfigure",
        action="store_true",
        help=(
            "regenerate relocatable runtime configuration from packaged, "
            "prevalidated artifacts without resolving or building sources"
        ),
    )
    return result


def main() -> int:
    args = parser().parse_args()
    if args.recipe is None:
        if args.composition is None:
            raise UrbanMobilityError("--recipe or --composition is required")
        if frozenset(load_composition(args.composition).simulators()) == DRONE_ONLY:
            return drone_composition_command(args.command, args.composition)
        args.recipe = composition_recipe(args.composition)
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
    return launcher_command(args.command, context, args)


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
