#!/usr/bin/env python3
"""Run Urban Compositions: the one entrypoint for the CLI and the browser backend.

plan() decides how a Composition runs (asset-contract 7.1):

  route        Composition                      tool                  managed Recipe
  car          City + Cars                      tools/multi_car.py    urban-car-rc
  drone        City + one Drone                 tools/drone_one.py    - (own workspace)
  integrated   City + Cars + one Drone          tools/urban_composer  urban-mobility-rc
  fpv          City or plain World + one FPV     hakoniwa-fpv-drone/tools/fpv.py
               Drone

run() executes a lifecycle command for a plan. Every route re-reads the
Composition at start, so placement, control param, and program edits need no
configure (section 5.5). The Car and integrated routes run through
tools/urban_mobility.py, which owns the managed Recipe lifecycle and calls the
managed-route functions here with a ManagedTarget.

Tool-specific inputs the contract does not expose (the Drone launch area,
the RC mission file, the rooftop field name) stay inside this module and
tools/urban_composition.py.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
BUSINESS_PACK = WORKSPACE / "hakoniwa-business-pack"

for _path in (ROOT / "tools", BUSINESS_PACK / "tools"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

URBAN_MOBILITY = ROOT / "tools/urban_mobility.py"
DRONE_ONE = ROOT / "tools/drone_one.py"
DRONE_FLEET = ROOT / "tools/drone_fleet.py"
FLEET_WORKSPACE_ID = "urban-drone-fleet"
FLEET_RECIPE_FILE = "urban-composition-fleet.json"
FPV_TOOL = WORKSPACE / "hakoniwa-fpv-drone/tools/fpv.py"
FPV_OUTPUT_ROOT = BUSINESS_PACK / "work/urban/fpv"
FPV_SELECTION_FILE = "urban-composition.json"
# The open ground an FPV vehicle is generated on before it is composed into a City.
FPV_CITY_GROUND = ROOT / "worlds/plain-ground.yaml"
MBODY_COMPOSE = WORKSPACE / "hakoniwa-mbody-registry/tools/compose_mujoco_world.py"
DRONE_CORE = WORKSPACE / "hakoniwa-drone-core"
DRONE_RECIPE_FILE = "urban-composition-drone.yaml"
DRONE_WORKSPACE_ID = "urban-drone-one"

CAR = frozenset({"ackermann-mujoco"})
DRONE = frozenset({"drone-core"})
CAR_AND_DRONE = CAR | DRONE
# Managed Recipes that prepare the environment of a route, and their ids.
MANAGED_RECIPES = {
    "car": ("recipes/usecases/urban-car-rc.yaml", "urban-car-rc", "car-rc"),
    "integrated": ("recipes/experiments/urban-mobility-rc.yaml", "urban-mobility-rc", "drone-car-distributed"),
}
COMMANDS = ("plan", "configure", "start", "status", "stop", "open-viewer")


class SimulationError(RuntimeError):
    pass


def load_composition(composition_path: Path):
    import urban_composition

    try:
        return urban_composition.load(composition_path)
    except urban_composition.CompositionError as exc:
        raise SimulationError(str(exc)) from exc


def _recipe_root(recipe_id: str) -> Path:
    from workdir import recipe_root

    return recipe_root(BUSINESS_PACK, recipe_id)


# --- Plan ----------------------------------------------------------------------

@dataclass(frozen=True)
class Plan:
    composition: object
    route: str
    managed_recipe: Path | None
    workspace: Path

    def to_json(self) -> dict:
        composition = self.composition
        return {
            "composition": composition.id,
            "path": str(composition.path),
            "route": self.route,
            "world": {"id": composition.world.id, "kind": composition.world.kind},
            "vehicles": [
                {"name": vehicle.name, "asset": vehicle.asset.id, "control": vehicle.control}
                for vehicle in composition.vehicles
            ],
            "fleets": [
                {"name": fleet.name, "asset": fleet.asset.id, "control": fleet.control, "count": fleet.count}
                for fleet in composition.fleets
            ],
            "simulators": sorted(composition.simulators()),
            "managed_recipe": None if self.managed_recipe is None else str(self.managed_recipe),
            "workspace": str(self.workspace),
        }


def plan(composition_path: Path) -> Plan:
    """Validate a Composition and decide the route that runs it."""
    import urban_composition

    composition = load_composition(composition_path)
    simulators = frozenset(composition.simulators())
    if composition.fleets:
        try:
            urban_composition.to_fleet_recipe(composition)
        except urban_composition.CompositionError as exc:
            raise SimulationError(str(exc)) from exc
        return Plan(composition, "fleet", None, _recipe_root(FLEET_WORKSPACE_ID))
    fpv = [vehicle for vehicle in composition.vehicles if urban_composition.is_fpv(vehicle)]
    if fpv:
        try:
            urban_composition.fpv_vehicle(composition)
        except urban_composition.CompositionError as exc:
            raise SimulationError(str(exc)) from exc
        return Plan(composition, "fpv", None, FPV_OUTPUT_ROOT / composition.id)
    # Other vehicles run on a plain World through its City World job
    # (tools/plain_world.py) on the same routes as on a City.
    if simulators == DRONE:
        return Plan(composition, "drone", None, _recipe_root(DRONE_WORKSPACE_ID))
    route = {CAR: "car", CAR_AND_DRONE: "integrated"}.get(simulators)
    if route is None:
        raise SimulationError(f"no route runs a Composition with simulators {sorted(simulators)}")
    recipe, recipe_id, _ = MANAGED_RECIPES[route]
    return Plan(composition, route, ROOT / recipe, _recipe_root(recipe_id))


def viewer_url(selected: Plan) -> str | None:
    """Return the browser Viewer URL of a configured plan, or None before configure."""
    if selected.route in {"car", "integrated"}:
        contract = selected.workspace / "config/viewer-url.json"
        if not contract.is_file():
            return None
        return json.loads(contract.read_text(encoding="utf-8")).get("url")
    if selected.route == "fpv":
        threejs = selected.workspace / "runtime/threejs"
        if not (threejs / "viewer-config.json").is_file():
            return None
        ports = json.loads((threejs / "ports.json").read_text(encoding="utf-8"))
        config = (threejs / "viewer-config.json").resolve().relative_to(WORKSPACE.resolve())
        return (
            f"http://127.0.0.1:{int(ports['http'])}/hakoniwa-threejs-drone/index.html"
            f"?viewerConfigPath=/{config.as_posix()}"
        )
    if selected.route == "fleet":
        configured = selected.workspace / "config" / FLEET_RECIPE_FILE
        if not configured.is_file():
            return None
        import drone_fleet

        return drone_fleet.viewer_url(json.loads(configured.read_text(encoding="utf-8"))["drone_count"])
    if not (selected.workspace / "config" / DRONE_RECIPE_FILE).is_file():
        return None
    import drone_one

    # As tools/drone_one.py open-viewer with its default bottom-left map layout.
    return drone_one.base.viewer_url(1, map_viewer=True) + "&layout=three-main"


def run(command: str, composition_path: Path) -> int:
    """Run a lifecycle command for a Composition."""
    selected = plan(composition_path)
    if command == "plan":
        print(json.dumps(selected.to_json(), indent=2))
        return 0
    if selected.route == "fpv":
        return fpv_command(command, composition_path)
    if selected.route == "drone":
        return drone_command(command, composition_path)
    if selected.route == "fleet":
        return fleet_command(command, composition_path, selected.workspace)
    # The managed Recipe lifecycle lives in tools/urban_mobility.py.
    return subprocess.run(
        [sys.executable, str(URBAN_MOBILITY), command,
         "--recipe", str(selected.managed_recipe), "--composition", str(Path(composition_path).resolve())],
        cwd=ROOT, check=False,
    ).returncode


# --- Control runtimes -------------------------------------------------------------

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


def fpv_runtime():
    """Controls runtime for the FPV Drone service that tools/fpv.py configures."""
    import multi_car
    import urban_controls

    return urban_controls.Runtime(
        values={"pdu_def": WORKSPACE / "hakoniwa-drone-core/config/pdudef/drone-pdudef-1.json"},
        service_asset="fpv-drone-service",
        python=str(multi_car.foundation_python()),
    )


def control_processes(composition_path: Path, runtimes: dict) -> list[dict]:
    import urban_controls

    try:
        return urban_controls.control_processes(load_composition(composition_path), runtimes)
    except urban_controls.ControlError as exc:
        raise SimulationError(str(exc)) from exc


# --- Managed routes: car and integrated (called by tools/urban_mobility.py) ----------

@dataclass(frozen=True)
class ManagedTarget:
    """The managed Recipe workspace a Car or integrated Composition configures."""

    managed_recipe: Path
    recipe_id: str
    use_case: str
    work: Path

    @property
    def tool_config(self) -> Path:
        return self.work / "config/urban-composition.json"

    @property
    def drone_recipe(self) -> Path:
        return self.work / "config" / DRONE_RECIPE_FILE

    @property
    def inputs(self) -> Path:
        return self.work / "validation/urban-inputs.json"


def composition_outputs(target: ManagedTarget, composition_path: Path) -> tuple[dict, str | None]:
    """Return the tool configuration and, with a Drone, its drone_one.py recipe text."""
    import urban_composition

    composition = load_composition(composition_path)
    try:
        if target.use_case == "car-rc":
            return urban_composition.to_car_config(composition, target.recipe_id), None
        if target.use_case == "drone-car-distributed":
            config, drone = urban_composition.to_integrated(composition, target.recipe_id, target.drone_recipe)
            return config, urban_composition.render_simple_yaml(drone) + "\n"
    except urban_composition.CompositionError as exc:
        raise SimulationError(str(exc)) from exc
    raise SimulationError(f"Urban use case {target.use_case} does not take a Composition")


def write_composition_outputs(target: ManagedTarget, config: dict, drone: str | None) -> Path:
    import multi_car

    multi_car.write_json(target.tool_config, config)
    if drone is not None:
        target.drone_recipe.parent.mkdir(parents=True, exist_ok=True)
        target.drone_recipe.write_text(drone, encoding="utf-8")
    return target.tool_config


def materialize(target: ManagedTarget, composition_path: Path) -> Path:
    """Write the tool inputs for configure and record which Composition produced them."""
    import multi_car

    config, drone = composition_outputs(target, composition_path)
    config_path = write_composition_outputs(target, config, drone)
    multi_car.write_json(target.inputs, {
        "schema_version": 1,
        "managed_recipe": str(target.managed_recipe),
        "recipe_id": target.recipe_id,
        "use_case": target.use_case,
        "composition": str(Path(composition_path).resolve()),
        "composition_id": config["composition_source"]["id"],
        "generated_composition": str(config_path),
    })
    return config_path


def refresh_placement(target: ManagedTarget, composition_path: Path) -> Path:
    """Rewrite the tool inputs for a placement-only Composition edit.

    Returns the Car configuration path. Any edit beyond vehicle placement is
    rejected before anything is written.
    """
    import urban_composition

    try:
        configured = json.loads(target.inputs.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SimulationError(f"Recipe {target.recipe_id} is not configured: {target.inputs}") from exc
    if "composition" not in configured:
        raise SimulationError(
            f"Recipe {target.recipe_id} was not configured from a Composition; run configure --composition"
        )
    previous = json.loads(target.tool_config.read_text(encoding="utf-8"))
    current, drone = composition_outputs(target, composition_path)
    if not urban_composition.placement_only_change(previous, current):
        raise SimulationError("the Composition changed more than vehicle placement; run configure --composition")
    # A Drone placement edit lands in the drone_one.py recipe, which that tool
    # accepts as a pose-only edit at start.
    return write_composition_outputs(target, current, drone)


def refresh_car_poses(config_path: Path) -> None:
    import multi_car

    multi_car.refresh_runtime_initial_body_poses(multi_car.resolve_config(config_path))


def apply_managed_runtime(target: ManagedTarget, composition_path: Path) -> None:
    """Put the manifest controls and the real-time pacer into the configured Launcher.

    The Car route's plant owns the Conductor; in the integrated route the
    Drone service owns it and the plant joins it (--external-conductor).
    """
    import drone_one
    import multi_car
    import urban_controls
    import urban_realtime

    runtimes = {"ackermann-mujoco": car_runtime(target.work)}
    conductor, drone_services = "urban-car-fleet-plant", ()
    if target.use_case == "drone-car-distributed":
        runtimes["drone-core"] = drone_runtime(
            drone_one._paths(target.recipe_id), target.work / "config/car/urban-car-pdudef.json"
        )
        conductor, drone_services = "drone-service-1", ("drone-service-1",)
    launcher_path = target.work / "config/launcher.json"
    launcher = multi_car.load_json(launcher_path, "configured Launcher")
    urban_controls.apply_controls(launcher, control_processes(composition_path, runtimes))
    pacer = urban_realtime.pacer_asset(str(multi_car.foundation_python()), conductor)
    urban_realtime.apply_pacer(launcher, pacer, drone_services=drone_services)
    multi_car.write_json(launcher_path, launcher)


# --- Drone route (tools/drone_one.py) --------------------------------------------------

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
        raise SimulationError(str(exc)) from exc
    path = drone_recipe_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_drone_controls(composition_path: Path) -> Path:
    """Write the controls and pacer that tools/drone_one.py applies whenever it writes its Launcher."""
    import drone_one
    import multi_car
    import urban_controls
    import urban_realtime

    paths = drone_one._paths()
    processes = control_processes(
        composition_path,
        {"drone-core": drone_runtime(paths, paths.recipe_config / "pdudef/drone-pdudef-current.json")},
    )
    path = paths.recipe_config / urban_controls.CONTROLS_FILE
    multi_car.write_json(path, {
        "processes": processes,
        "pacer": urban_realtime.pacer_asset(str(multi_car.foundation_python()), "drone-service-1"),
        "drone_services": ["drone-service-1"],
    })
    return path


def drone_command(command: str, composition_path: Path) -> int:
    """Run a City + one Drone Composition through tools/drone_one.py.

    configure and start rewrite the tool recipe and control processes from
    the Composition (drone_one.py accepts pose-only recipe edits at start and
    rejects the rest until configure).
    """
    tool = [sys.executable, str(DRONE_ONE)]
    if command == "configure":
        recipe = materialize_drone_recipe(composition_path)
        result = subprocess.run([*tool, "configure", "--recipe", str(recipe)], cwd=ROOT, check=False).returncode
        if result == 0:
            write_drone_controls(composition_path)
        return result
    if command == "start":
        if not drone_recipe_path().is_file():
            raise SimulationError("the Drone Composition is not configured; run configure --composition first")
        materialize_drone_recipe(composition_path)
        write_drone_controls(composition_path)
    if command not in {"start", "status", "stop", "open-viewer", "doctor", "prepare-native"}:
        raise SimulationError(f"{command} is not supported for a City + Drone Composition")
    return subprocess.run([*tool, command], cwd=ROOT, check=False).returncode


# --- Fleet route (tools/drone_fleet.py) ---------------------------------------------------

def write_fleet_recipe(composition_path: Path, workspace: Path) -> Path:
    import urban_composition

    try:
        recipe = urban_composition.to_fleet_recipe(load_composition(composition_path))
    except urban_composition.CompositionError as exc:
        raise SimulationError(str(exc)) from exc
    path = workspace / "config" / FLEET_RECIPE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(recipe, indent=2) + "\n", encoding="utf-8")
    return path


def write_fleet_controls(recipe_path: Path) -> Path:
    """The pacer for tools/drone_fleet.py; the show runner control is generated with the Launcher."""
    import multi_car
    import urban_controls
    import urban_realtime

    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    services = [f"drone-service-{index}" for index in range(1, int(recipe["process_count"]) + 1)]
    path = recipe_path.parent / urban_controls.CONTROLS_FILE
    multi_car.write_json(path, {
        "processes": [],
        "pacer": urban_realtime.pacer_asset(str(multi_car.foundation_python()), services[0]),
        "drone_services": services,
    })
    return path


def fleet_command(command: str, composition_path: Path, workspace: Path) -> int:
    """Run a World + Drone fleet Composition through tools/drone_fleet.py."""
    tool = [sys.executable, str(DRONE_FLEET)]
    if command in {"configure", "start"}:
        recipe = write_fleet_recipe(composition_path, workspace)
        result = subprocess.run([*tool, command, "--recipe", str(recipe)], cwd=ROOT, check=False).returncode
        if command == "configure" and result == 0:
            write_fleet_controls(recipe)
        return result
    if command not in {"status", "stop", "doctor"}:
        raise SimulationError(f"{command} is not supported for a Drone fleet Composition")
    return subprocess.run([*tool, command], cwd=ROOT, check=False).returncode


# --- FPV route (hakoniwa-fpv-drone tools/fpv.py) ------------------------------------------

def fpv_selection(composition_path: Path) -> tuple[object, Path, dict]:
    """Return (composition, tools/fpv.py output, the inputs that need configure)."""
    import urban_composition

    composition = load_composition(composition_path)
    try:
        vehicle = urban_composition.fpv_vehicle(composition)
        if composition.world.kind == "plain":
            world, city = urban_composition.plain_world_yaml(composition), None
        else:
            # In a City the vehicle is generated on open ground, then composed
            # with the City World (compose_fpv_city).
            world, city = FPV_CITY_GROUND, urban_composition.city_receipt(composition)
    except urban_composition.CompositionError as exc:
        raise SimulationError(str(exc)) from exc
    generator = vehicle.asset.data["source"]["generator"]
    assembly = (WORKSPACE / vehicle.asset.data["source"]["repository"] / generator["assembly"]).resolve()
    selection = {
        "world": str(world),
        "city_receipt": None if city is None else str(city),
        "asset": vehicle.asset.id,
        "assembly": str(assembly),
    }
    return composition, FPV_OUTPUT_ROOT / composition.id, selection


def compose_fpv_city(output: Path, receipt: Path) -> Path:
    """Put the generated FPV vehicle into a City World.

    The vehicle body is composed with the City MJCF (hakoniwa-mbody-registry
    compose_mujoco_world.py, as for the Urban Hexa), compiled once into an MJB
    that Drone Core loads, and the Three.js view switches from the course to
    the City GLB. The MJB is reused while the composed model is unchanged.
    """
    import hashlib
    import shutil
    import xml.etree.ElementTree as ET

    import multi_car
    from mujoco_model_compiler import compile_mujoco_xml, find_mujoco_library

    vehicle = output / "runtime/vehicle"
    city_mjcf, city_glb, _ = multi_car.city_inputs(receipt)
    tree = ET.parse(vehicle / "drone.xml")
    worldbody = tree.getroot().find("worldbody")
    body = worldbody.find("./body[@name='drone_base']") if worldbody is not None else None
    if body is None:
        raise SimulationError(f"generated FPV vehicle has no drone_base: {vehicle / 'drone.xml'}")
    for item in list(worldbody):
        if item is not body:
            worldbody.remove(item)  # the open ground and lights of the generation World
    body.set("pos", "0 0 0")  # Drone Core places the body from droneDynamics.position_meter
    ET.indent(tree, space="  ")
    body_only = vehicle / "fpv-body.xml"
    tree.write(body_only, encoding="utf-8", xml_declaration=True)
    composed = vehicle / "fpv-city.xml"
    subprocess.run(
        [sys.executable, str(MBODY_COMPOSE), str(body_only), str(city_mjcf), "--output", str(composed), "--no-validate"],
        cwd=ROOT, check=True,
    )
    mjb = composed.with_suffix(".mjb")
    stamp = composed.with_suffix(".mjb.json")
    digest = hashlib.sha256(composed.read_bytes()).hexdigest()
    try:
        reusable = mjb.is_file() and json.loads(stamp.read_text(encoding="utf-8")).get("xml_sha256") == digest
    except (OSError, json.JSONDecodeError):
        reusable = False
    if reusable:
        print(f"Reusing compiled FPV City model: {mjb}")
    else:
        compile_mujoco_xml(composed, mjb, find_mujoco_library(DRONE_CORE))
        multi_car.write_json(stamp, {"xml_sha256": digest})
    config_path = vehicle / "drone_config_0.json"
    config = multi_car.load_json(config_path, "FPV Drone config")
    config["components"]["droneDynamics"]["mujoco"]["modelPath"] = str(mjb)
    multi_car.write_json(config_path, config)

    threejs = output / "runtime/threejs"
    shutil.copy2(city_glb, threejs / "assets/city-world.glb")
    scene_path = threejs / "scene-config.json"
    scene = multi_car.load_json(scene_path, "FPV Three.js scene")
    scene["environments"] = [{"name": "city", "model": "./assets/city-world.glb"}]
    scene.setdefault("main_camera", {})["far"] = 20000
    multi_car.write_json(scene_path, scene)
    return mjb


def apply_fpv_composition(composition_path: Path, output: Path) -> None:
    """Apply the spawn and manifest controls to a configured tools/fpv.py runtime."""
    import multi_car
    import urban_composition
    import urban_controls

    composition = load_composition(composition_path)
    vehicle = urban_composition.fpv_vehicle(composition)
    runtime = output / "runtime"
    report = multi_car.load_json(runtime / "vehicle/report.json", "FPV generated report")
    generated = float(report["initial_pose"]["mujoco_z_m"])
    declared = float(vehicle.asset.data["spawn"]["ground_clearance_m"])
    if abs(generated - declared) > 1.0e-6:
        raise SimulationError(
            f"Asset {vehicle.asset.id} declares ground_clearance_m {declared}, but the generated "
            f"vehicle starts {generated} m above the ground; update {vehicle.asset.path}"
        )
    try:
        spawn = urban_composition.to_fpv_spawn(composition)
    except urban_composition.CompositionError as exc:
        raise SimulationError(str(exc)) from exc
    config_path = runtime / "vehicle/drone_config_0.json"
    config = multi_car.load_json(config_path, "FPV Drone config")
    dynamics = config["components"]["droneDynamics"]
    dynamics["position_meter"] = spawn["position_meter"]
    dynamics["angle_degree"] = spawn["angle_degree"]
    multi_car.write_json(config_path, config)
    launcher_path = runtime / "launcher.json"
    launcher = multi_car.load_json(launcher_path, "FPV Launcher")
    urban_controls.apply_controls(launcher, control_processes(composition_path, {"drone-core": fpv_runtime()}))
    # The Urban pacer replaces tools/fpv.py's own (the same pacing), so every
    # route runs one pacer implementation.
    import urban_realtime

    pacer = urban_realtime.pacer_asset(str(multi_car.foundation_python()), "fpv-drone-service")
    urban_realtime.apply_pacer(launcher, pacer, drone_services=("fpv-drone-service",))
    multi_car.write_json(launcher_path, launcher)


def fpv_command(command: str, composition_path: Path) -> int:
    """Run a World + FPV Drone Composition through tools/fpv.py.

    configure generates the vehicle on the plain World (in a City: on open
    ground, then composed with the City, compose_fpv_city); configure and
    start apply the Composition spawn and controls. A different World or
    Asset needs configure.
    """
    import multi_car

    composition, output, selection = fpv_selection(composition_path)
    tool = [sys.executable, str(FPV_TOOL), command, "--output", str(output)]
    selected = output / FPV_SELECTION_FILE
    if command == "configure":
        result = subprocess.run(
            [*tool, "--world", selection["world"], "--threejs", "--assembly", selection["assembly"]],
            cwd=FPV_TOOL.parents[1], check=False,
        ).returncode
        if result != 0:
            return result
        if selection["city_receipt"] is not None:
            compose_fpv_city(output, Path(selection["city_receipt"]))
        multi_car.write_json(selected, selection)
        apply_fpv_composition(composition_path, output)
        return 0
    if command == "start":
        if not selected.is_file():
            raise SimulationError("the FPV Composition is not configured; run configure --composition first")
        if json.loads(selected.read_text(encoding="utf-8")) != selection:
            raise SimulationError("the Composition changed its World or FPV Asset; run configure --composition")
        apply_fpv_composition(composition_path, output)
    if command not in {"start", "status", "stop", "open-viewer"}:
        raise SimulationError(f"{command} is not supported for an FPV Drone Composition")
    return subprocess.run(tool, cwd=FPV_TOOL.parents[1], check=False).returncode


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    result.add_argument("command", choices=COMMANDS)
    result.add_argument("--composition", type=Path, required=True)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    return run(args.command, args.composition)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, SimulationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
