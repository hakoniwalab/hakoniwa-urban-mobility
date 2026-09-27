#!/usr/bin/env python3
"""Compose the tracked Urban Drone + multi-Car distributed simulation."""

from __future__ import annotations

import copy
from dataclasses import replace
import json
import math
from pathlib import Path
import platform
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
DEFAULT_CONFIG = ROOT / "recipes/experiments/urban-mobility-shizuoka.yaml"
DEFAULT_DRONE_ROOT = WORKSPACE / "hakoniwa-drone-core"

sys.path.insert(0, str(ROOT / "tools"))

import drone_car_rc  # noqa: E402
import drone_one  # noqa: E402
import multi_car  # noqa: E402


class UrbanComposeError(RuntimeError):
    pass


def load_composition(path: Path) -> tuple[dict, dict, dict]:
    config_path = path.expanduser().resolve()
    config = multi_car.load_yaml(config_path)
    resolved = multi_car.resolve_config(config_path)
    try:
        scenarios = config["scenarios"]
        drone_path = multi_car.resolve_path(scenarios["drone"], "Drone scenario")
        car_value = scenarios.get("car")
        # One route (a path) or one per group of Cars (a list of paths).
        car_values = car_value if isinstance(car_value, list) else [car_value] if car_value is not None else []
        car_paths = {multi_car.resolve_path(value, "Car scenario") for value in car_values}
    except (KeyError, TypeError, AttributeError) as exc:
        raise UrbanComposeError("integrated composition references are incomplete") from exc
    drone_scenario = multi_car.load_yaml(drone_path)
    if drone_scenario.get("version") != 1:
        raise UrbanComposeError("Drone rooftop scenario must use version 1")
    try:
        drone = drone_scenario["drone"]
        spawn = {
            key: float(drone["spawn_pose_enu"][key])
            for key in ("east_m", "north_m", "up_m", "yaw_deg")
        }
        rooftop = drone["rooftop"]
        surface_height = float(rooftop["surface_height_m"])
        base_clearance = float(rooftop["base_clearance_m"])
    except (KeyError, TypeError, ValueError) as exc:
        raise UrbanComposeError("Drone rooftop scenario is invalid") from exc
    if not all(math.isfinite(value) for value in spawn.values()):
        raise UrbanComposeError("Drone rooftop spawn must be finite")
    if not math.isclose(
        spawn["up_m"], surface_height + base_clearance, abs_tol=1.0e-6
    ):
        raise UrbanComposeError("Drone spawn height must equal rooftop + clearance")
    # The Car scenario is optional: explicit rc Cars need none. When present it
    # must be the scenario that multi_car.py auto-starts.
    configured_car_paths = {item["scenario"] for item in resolved.get("route_scenarios", [])}
    if configured_car_paths != car_paths:
        raise UrbanComposeError("Car scenario references disagree")
    return config, resolved, {
        "path": drone_path,
        "recipe": drone_one.load_urban_recipe(drone_path),
        "spawn": spawn,
        "rooftop": rooftop,
    }


def _drone_paths(recipe_id: str):
    return drone_one._paths(recipe_id)


def _patch_browser(resolved: dict, paths: object) -> dict[str, Path | str]:
    root = resolved["work"] / "config"
    three = root / "threejs"
    bridge = root / "web-bridge"
    embedded = (
        paths.recipe_root / "web/map-viewer/thirdparty/hakoniwa-threejs-drone"
    )
    drone_scene_path = embedded / "config/drone_config-city-fleet.json"
    drone_viewer_path = embedded / "config/viewer-config-fleets.json"
    drone_visual_pdutypes = paths.recipe_config / "pdudef/drone-visual-state-pdutypes.json"
    for required in (drone_scene_path, drone_viewer_path, drone_visual_pdutypes):
        multi_car.required(required, "generated Drone Viewer input")

    drone_scene = multi_car.load_json(drone_scene_path, "Drone scene")
    drone_viewer = multi_car.load_json(drone_viewer_path, "Drone viewer")
    source_drones = drone_scene.get("drones")
    if not isinstance(source_drones, list) or len(source_drones) != 1:
        raise UrbanComposeError("generated Drone scene must contain one template")
    drone_types_value = drone_scene.get("droneTypesPath")
    if not isinstance(drone_types_value, str) or not drone_types_value:
        raise UrbanComposeError("generated Drone scene must reference drone types")
    drone_types_source = (drone_scene_path.parent / drone_types_value).resolve()
    multi_car.required(drone_types_source, "generated Drone type definitions")
    try:
        drone_types_url = f"/{drone_types_source.relative_to(WORKSPACE).as_posix()}"
    except ValueError as exc:
        raise UrbanComposeError(
            "generated Drone type definitions must be inside the Workspace"
        ) from exc

    scene_paths = [three / "scene-config.json"]
    collider_scene = three / "scene-config-colliders.json"
    if collider_scene.is_file():
        scene_paths.append(collider_scene)
    for scene_path in scene_paths:
        scene = multi_car.load_json(scene_path, "integrated scene")
        # Keep the type definition at its generated Viewer location.  Its GLB
        # paths are relative to that directory, so copying only the JSON into
        # the integrated scene would make every Drone model path resolve below
        # config/threejs/assets instead of the embedded Viewer's assets.
        scene["droneTypesPath"] = drone_types_url
        scene["drones"] = copy.deepcopy(source_drones)
        scene["main_camera"]["target"] = "Drone"
        # The Drone camera is the subjective view. Car front cameras use the
        # same top-right window and are drawn after Drones, so they would
        # cover it; the integrated scene shows only the Drone's.
        for vehicle in scene.get("vehicles", []):
            vehicle.pop("frontCamera", None)
        multi_car.write_json(scene_path, scene)
    for viewer_path in (three / "viewer-config.json", three / "viewer-config-colliders.json"):
        if viewer_path.is_file():
            viewer = multi_car.load_json(viewer_path, "integrated viewer")
            viewer.setdefault("ui", {})["enableAttachedCameras"] = True
            multi_car.write_json(viewer_path, viewer)

    compact_path = bridge / "pdu/urban-visual-state.json"
    compact = multi_car.load_json(compact_path, "browser PDU definition")
    published_drone_types = compact_path.parent / "drone-visual-state-pdutypes.json"
    shutil.copy2(drone_visual_pdutypes, published_drone_types)
    compact["paths"] = [
        item for item in compact["paths"] if item.get("id") != "drone-visual-state"
    ]
    compact["paths"].append({
        "id": "drone-visual-state",
        "path": published_drone_types.name,
    })
    compact["robots"] = [
        item
        for item in compact["robots"]
        if item.get("name") != "DroneVisualStatePublisher"
    ]
    compact["robots"].append({
        "name": "DroneVisualStatePublisher",
        "pdutypes_id": "drone-visual-state",
    })
    multi_car.write_json(compact_path, compact)

    shm_path = bridge / "comm/urban-state-shm-callback.json"
    shm = multi_car.load_json(shm_path, "browser SHM communication")
    shm["io"]["robots"] = [
        item
        for item in shm["io"]["robots"]
        if item.get("name") != "DroneVisualStatePublisher"
    ]
    shm["io"]["robots"].append({
        "name": "DroneVisualStatePublisher",
        "pdu": [
            {"name": f"drone_visual_state_array_{index}", "notify_on_recv": False}
            for index in range(4)
        ],
    })
    multi_car.write_json(shm_path, shm)

    bridge_path = bridge / "bridge/bridge.json"
    bridge_config = multi_car.load_json(bridge_path, "browser bridge")
    bridge_config["pduKeyGroups"]["drone_visual_state"] = [
        {
            "id": f"DroneVisualStatePublisher.drone_visual_state_array_{index}",
            "robot_name": "DroneVisualStatePublisher",
            "pdu_name": f"drone_visual_state_array_{index}",
        }
        for index in range(4)
    ]
    transfers = bridge_config["connections"][0]["transferPdus"]
    transfers[:] = [
        item
        for item in transfers
        if item.get("pduKeyGroupId") != "drone_visual_state"
    ]
    transfers.append({
        "pduKeyGroupId": "drone_visual_state",
        "policyId": "ticker_20ms",
    })
    multi_car.write_json(bridge_path, bridge_config)

    viewer_paths = [three / "viewer-config.json"]
    collider_viewer = three / "viewer-config-colliders.json"
    if collider_viewer.is_file():
        viewer_paths.append(collider_viewer)
    fleet_options = copy.deepcopy(drone_viewer["stateInput"]["fleets"])
    fleet_options["dynamicSpawn"] = True
    fleet_options["templateDroneIndex"] = 0
    fleet_options["maxDynamicDrones"] = 1
    for viewer_path in viewer_paths:
        viewer = multi_car.load_json(viewer_path, "integrated viewer")
        viewer["ui"]["enableAttachedCameras"] = True
        viewer["stateInput"]["mode"] = "fleets"
        viewer["stateInput"]["fleets"] = copy.deepcopy(fleet_options)
        multi_car.write_json(viewer_path, viewer)

    viewer_config = three / "viewer-config.json"
    collider_config = three / "viewer-config-colliders.json"
    result: dict[str, Path | str] = {
        "viewer_config": viewer_config,
        "viewer_url": multi_car.map_viewer_url(resolved, viewer_config),
    }
    if collider_config.is_file():
        result.update({
            "collider_viewer_config": collider_config,
            "collider_viewer_url": multi_car.map_viewer_url(resolved, collider_config),
        })
    return result


def _add_drone_library_path(drone_service: dict) -> None:
    """Let the Drone service find Drone Core's native libraries (MuJoCo).

    The Business Pack fleet Recipe adds them to the process environment when
    it launches (drone_fleet_single_host.native_library_environment); the
    integrated Launcher starts through recipe.py launch, so the asset carries
    them itself. Without them the service exits at once on Windows (missing
    mujoco.dll).
    """
    command = drone_service.get("command")
    if not command:
        return
    drone_root = Path(command).resolve().parents[1]
    if platform.system() == "Windows":
        key, folders = "PATH", ["win", "lib", "vendor/mujoco/bin"]
    elif platform.system() == "Darwin":
        key, folders = "DYLD_LIBRARY_PATH", ["lib", "vendor/mujoco/lib"]
    else:
        key, folders = "LD_LIBRARY_PATH", ["lib", "vendor/mujoco/lib"]
    prepend = drone_service.setdefault("env", {}).setdefault("prepend", {})
    prepend[key] = [str(drone_root / folder) for folder in folders] + [
        path for path in prepend.get(key, []) if path not in {str(drone_root / folder) for folder in folders}
    ]


def _merge_launchers(
    resolved: dict,
    drone_launcher: dict,
) -> Path:
    car_launcher = multi_car.load_json(
        resolved["work"] / "config/launcher.json", "Car Launcher"
    )

    def asset(launcher: dict, name: str) -> dict:
        value = next(
            (item for item in launcher.get("assets", []) if item.get("name") == name),
            None,
        )
        if value is None:
            raise UrbanComposeError(f"source Launcher has no asset: {name}")
        return copy.deepcopy(value)

    drone_service = asset(drone_launcher, "drone-service-1")
    drone_service["args"] = [
        value for value in drone_service.get("args", []) if value != "--mujoco-viewer"
    ]
    _add_drone_library_path(drone_service)
    unified_pdu = resolved["work"] / "config/car/urban-car-pdudef.json"
    if len(drone_service["args"]) < 2:
        raise UrbanComposeError("Drone service has no PDU definition argument")
    drone_service["args"][1] = str(unified_pdu)

    car_plant = asset(car_launcher, "urban-car-fleet-plant")
    if "--external-conductor" not in car_plant["args"]:
        car_plant["args"].append("--external-conductor")

    # The Drone control is the PS4 RC client or, for a Fleet RPC Drone, the mission.
    names = {item.get("name") for item in drone_launcher.get("assets", [])}
    if "urban-drone-ps4-controller" in names:
        controller = asset(drone_launcher, "urban-drone-ps4-controller")
        controller["args"][1] = str(unified_pdu)
    else:
        controller = asset(drone_launcher, "urban-drone-mission")
    visual_publisher = asset(drone_launcher, "visual-state-publisher")
    # Car control assets exist only for the selected controls: one scenario
    # executor for api Cars and one PS5 sender per rc Car.
    car_controls = [
        copy.deepcopy(item)
        for item in car_launcher.get("assets", [])
        if item.get("name") == "urban-car-scenario-executor"
        or str(item.get("name", "")).endswith("-ps5-controller")
    ]
    web_bridge = asset(car_launcher, "urban-vehicle-web-bridge")
    web_bridge["depends_on"] = [
        "urban-car-fleet-plant", "visual-state-publisher"
    ]
    http_server = asset(car_launcher, "urban-vehicle-http-server")

    logs = resolved["work"] / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    defaults = copy.deepcopy(drone_launcher["defaults"])
    defaults["cwd"] = str(ROOT)
    defaults["stdout"] = str(logs / "${asset}.out")
    defaults["stderr"] = str(logs / "${asset}.err")
    defaults.setdefault("env", {}).setdefault("set", {}).update({
        "HAKONIWA_CORE_ROOT": str(multi_car.foundation_install()),
        "HAKONIWA_PDU_ENDPOINT_ROOT": str(multi_car.foundation_install()),
        "PYTHONUNBUFFERED": "1",
    })
    output = resolved["work"] / "config/launcher.json"
    multi_car.write_json(output, {
        "version": "0.1",
        "defaults": defaults,
        "assets": [
            drone_service,
            car_plant,
            visual_publisher,
            *car_controls,
            controller,
            web_bridge,
            http_server,
        ],
        "runtime": {"cleanup_mmap_on_start": True},
    })
    return output


def configure(
    config_path: Path = DEFAULT_CONFIG,
    drone_root: Path = DEFAULT_DRONE_ROOT,
) -> int:
    config, resolved, drone_scenario = load_composition(config_path)
    drone_car_rc.build_car_asset()
    paths = _drone_paths(resolved["recipe_id"])
    base_recipe = drone_scenario["recipe"]
    # The Drone scenario selects its control (the tracked rooftop scenario
    # selects ps4-rc).
    drone_recipe = replace(
        base_recipe,
        city_receipt=resolved["city_receipt"],
        spawn_pose_enu=drone_scenario["spawn"],
    )
    if drone_one.configure(
        drone_root,
        recipe=drone_recipe,
        workspace=paths,
        runtime_config_dir=resolved["work"] / "config/drone/rc",
    ) != 0:
        return 1
    if drone_one.base.doctor(
        drone_recipe.fleet_experiment,
        drone_root,
        drone_one.DEFAULT_VIEWER_ROOT,
        workspace=paths,
        launcher_writer=drone_one.urban_launcher_writer(drone_recipe),
    ) != 0:
        raise UrbanComposeError("Drone runtime validation failed")
    # Both composers use config/launcher.json. Keep the Drone launcher in
    # memory before the Car composer replaces that file.
    drone_launcher = multi_car.load_json(
        paths.recipe_config / "launcher.json", "Drone Launcher"
    )

    generated_body = (
        paths.recipe_config
        / "drone/mujoco-city-fleet/process-01/hexa-body.xml"
    )
    multi_car.required(generated_body, "generated Urban Hexa mirror model")
    if len(resolved["mirrors"]) > 1:
        raise UrbanComposeError("integrated demo supports at most one Drone Mirror")
    spawn = drone_scenario["spawn"]
    for mirror in resolved["mirrors"]:
        mirror["mjcf_model"] = generated_body
        mirror["initial_position_mjcf"] = (
            spawn["north_m"], -spawn["east_m"], spawn["up_m"]
        )
    if multi_car.configure(resolved) != 0:
        return 1

    browser = _patch_browser(resolved, paths)
    launcher = _merge_launchers(resolved, drone_launcher)
    viewer_contract = {
        "url": browser["viewer_url"],
        "collider_url": browser.get("collider_viewer_url"),
        "layout": "three-main",
        "attached_camera": "Drone-1 road-monitoring only",
        # tools/urban_mobility.py checks these ports before start.
        "http_port": resolved["visualization"]["http_port"],
        "websocket_port": resolved["visualization"]["web_bridge_port"],
    }
    multi_car.write_json(resolved["work"] / "config/viewer-url.json", viewer_contract)
    multi_car.write_json(resolved["work"] / "validation/integrated-composition.json", {
        "schema_version": 1,
        "configuration": str(config_path.resolve()),
        "car_scenario": (
            None
            if resolved["route_scenario"] is None
            else str(resolved["route_scenario"]["scenario"])
        ),
        "car_scenarios": [str(item["scenario"]) for item in resolved.get("route_scenarios", [])],
        "drone_scenario": str(drone_scenario["path"]),
        "drone_spawn_enu": spawn,
        "rooftop": drone_scenario["rooftop"],
        "conductor_owner": "drone-service-1",
        "car_plant_mode": "external-conductor",
        "launcher": str(launcher),
        "viewer": viewer_contract,
    })
    print("Integrated Shizuoka Urban Mobility demo configured")
    print(f"Launcher : {launcher}")
    print(f"Viewer   : {browser['viewer_url']}")
    print(
        "Drone    : rooftop ENU "
        f"({spawn['east_m']:.2f}, {spawn['north_m']:.2f}, {spawn['up_m']:.2f})"
    )
    print("Cars     : " + ", ".join(
        f"{vehicle['name']}={vehicle['control_mode']}" for vehicle in resolved["vehicles"]
    ))
    print("Time     : Drone service is the only Conductor owner")
    return 0
