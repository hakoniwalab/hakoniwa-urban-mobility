#!/usr/bin/env python3
"""Build Launcher control processes from Asset manifest controls.

Implements asset-contract section 4: each vehicle's selected control (`rc` or
`api`) is a program with an argument template. Placeholders:

  ${runtime.<name>}  generated paths supplied by the builder for the simulator
  ${param.<name>}    Composition params, defaulted and typed by the manifest
  ${vehicle.name}    the vehicle instance name
  ${vehicle.index}   the vehicle's 0-based index among its simulator's vehicles
  ${repo:<name>}     a workspace repository

A Composition may replace the program and args (section 5.2); replacements
keep access to the same placeholders.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # tools/drone_one.py imports this module without PyYAML.
    from urban_composition import Composition, Vehicle


WORKSPACE = Path(__file__).resolve().parents[2]


PLACEHOLDER = re.compile(
    r"\$\{(?:(runtime|param|vehicle)\.([A-Za-z0-9_]+)|repo:([A-Za-z0-9_.-]+))\}"
)
# Control assets written by the current tools; they are replaced by the
# manifest-derived processes.
LEGACY_CONTROL_ASSETS = {
    "urban-car-scenario-executor",
    "urban-drone-ps4-controller",
    "urban-drone-mission",
    "fpv-remote-controller",
}
CONTROL_PREFIX = "control-"
# Written next to a tool's generated config when a Composition selects the
# controls; tools/drone_one.py applies it whenever it writes its Launcher.
CONTROLS_FILE = "urban-composition-controls.json"


class ControlError(RuntimeError):
    pass


@dataclass(frozen=True)
class Runtime:
    """What a simulator builder supplies to its vehicles' controls."""

    values: dict[str, Path | str]
    service_asset: str
    python: str


def is_control_asset(asset: dict) -> bool:
    name = str(asset.get("name", ""))
    return (
        name in LEGACY_CONTROL_ASSETS
        or name.endswith("-ps5-controller")
        or name.startswith(CONTROL_PREFIX)
    )


def _param_values(composition: Composition, vehicle: Vehicle, declared: dict) -> dict[str, str]:
    values = {}
    for name, definition in declared.items():
        if name in vehicle.params:
            value = vehicle.params[name]
        elif "default" in definition:
            value = definition["default"]
        else:
            continue
        kind = definition.get("type", "string")
        if kind == "path":
            path = Path(str(value)).expanduser()
            value = str((path if path.is_absolute() else composition.path.parent / path).resolve())
        elif kind == "number":
            try:
                value = float(value)
            except (TypeError, ValueError) as exc:
                raise ControlError(f"vehicle {vehicle.name} param {name} must be a number: {value!r}") from exc
            value = repr(value)
        values[name] = str(value)
    return values


def _expander(vehicle: Vehicle, index: int, params: dict[str, str], runtime: Runtime, *, allow_vehicle: bool):
    def substitute(match: re.Match) -> str:
        namespace, key, repo = match.groups()
        if repo is not None:
            return str((WORKSPACE / repo).resolve())
        if namespace == "runtime":
            if key not in runtime.values:
                raise ControlError(
                    f"vehicle {vehicle.name}: this simulator provides no ${{runtime.{key}}} "
                    f"(available: {sorted(runtime.values)})"
                )
            return str(runtime.values[key])
        if namespace == "param":
            if key not in params:
                raise ControlError(f"vehicle {vehicle.name}: param {key} has no value")
            return params[key]
        if not allow_vehicle:
            raise ControlError(
                f"vehicle {vehicle.name}: a composition-scoped control cannot use ${{vehicle.{key}}}"
            )
        if key == "name":
            return vehicle.name
        if key == "index":
            return str(index)
        raise ControlError(f"vehicle {vehicle.name}: unknown placeholder ${{vehicle.{key}}}")

    def expand(text: str) -> str:
        expanded = PLACEHOLDER.sub(substitute, str(text))
        if "${" in expanded:
            raise ControlError(f"vehicle {vehicle.name}: unknown placeholder in {text!r}")
        return expanded

    return expand


def _path(value: str, base: Path) -> str:
    path = Path(value).expanduser()
    return str((path if path.is_absolute() else base / path).resolve())


def control_process(
    composition: Composition,
    vehicle: Vehicle,
    index: int,
    runtime: Runtime,
) -> dict:
    """Return the Launcher asset for one vehicle's selected control."""
    control = vehicle.asset.controls()[vehicle.control]
    scope = control.get("scope", "vehicle")
    params = _param_values(composition, vehicle, control.get("params", {}))
    expand = _expander(vehicle, index, params, runtime, allow_vehicle=scope == "vehicle")

    def expand_arg(text: str, base: Path) -> str:
        # Repository-relative arguments are paths; normalize them like the tools do.
        value = expand(text)
        return _path(value, base) if str(text).startswith("${repo:") else value

    if vehicle.program is not None:
        program = _path(expand(vehicle.program), composition.path.parent)
        args = [expand_arg(arg, composition.path.parent) for arg in vehicle.args]
        cwd = str(composition.path.parent)
    else:
        program = _path(expand(control["program"]), vehicle.asset.path.parent)
        args = [expand_arg(arg, vehicle.asset.path.parent) for arg in control.get("args", [])]
        cwd = control.get("cwd")
        cwd = _path(expand(cwd), vehicle.asset.path.parent) if cwd else None
    runner = control.get("runner", "python")
    if runner not in {"python", "executable"}:
        raise ControlError(f"Asset {vehicle.asset.id} control {vehicle.control} runner must be python or executable")
    asset = {
        "name": (
            f"{CONTROL_PREFIX}{vehicle.name}-{vehicle.control}"
            if scope == "vehicle"
            else f"{CONTROL_PREFIX}{vehicle.asset.id}-{vehicle.control}"
        ).lower(),
        "activation_timing": "after_start",
        "command": runtime.python if runner == "python" else program,
        "args": (
            [*map(str, control.get("interpreter_args", [])), program, *args]
            if runner == "python" else args
        ),
        "depends_on": [runtime.service_asset],
        "delay_sec": 1,
    }
    if cwd is not None:
        asset["cwd"] = cwd
    return asset


def control_processes(
    composition: Composition,
    runtimes: dict[str, Runtime],
) -> list[dict]:
    """Return one process per vehicle-scoped control and per distinct composition-scoped one."""
    processes: list[dict] = []
    seen_composition: dict[str, dict] = {}
    indexes: dict[str, int] = {}
    for vehicle in composition.vehicles:
        simulator = vehicle.asset.simulator
        if simulator not in runtimes:
            raise ControlError(f"no runtime supplied for simulator {simulator}")
        index = indexes.get(simulator, 0)
        indexes[simulator] = index + 1
        process = control_process(composition, vehicle, index, runtimes[simulator])
        if vehicle.asset.controls()[vehicle.control].get("scope", "vehicle") == "composition":
            previous = seen_composition.get(process["name"])
            if previous is not None:
                if previous != process:
                    raise ControlError(
                        f"vehicles sharing the composition-scoped {vehicle.asset.id} "
                        f"{vehicle.control} control must run the same program and args"
                    )
                continue
            seen_composition[process["name"]] = process
        processes.append(process)
    return processes


def apply_controls(launcher: dict, processes: list[dict]) -> dict:
    """Replace the Launcher's control assets with the given processes, in place.

    The processes take the position of the first replaced control asset so
    the relative activation order of the other assets is unchanged.
    """
    assets = launcher.get("assets", [])
    position = next((index for index, asset in enumerate(assets) if is_control_asset(asset)), len(assets))
    kept = [asset for asset in assets if not is_control_asset(asset)]
    position -= sum(1 for asset in assets[:position] if is_control_asset(asset))
    launcher["assets"] = kept[:position] + list(processes) + kept[position:]
    return launcher
