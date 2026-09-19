#!/usr/bin/env python3
"""Configure and run a recipe-selected one-Drone PLATEAU checkpoint."""

from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
BUSINESS_PACK_ROOT = WORKSPACE / "hakoniwa-business-pack"
DRONE_SHOW_ROOT = WORKSPACE / "hakoniwa-drone-show"
DEFAULT_DRONE_ROOT = WORKSPACE / "hakoniwa-drone-pro"
DEFAULT_VIEWER_ROOT = WORKSPACE / "hakoniwa-threejs-drone"
DEFAULT_RECIPE = ROOT / "recipes" / "experiments" / "urban-drone-one.yaml"
EAMS_TUNED_PARAMS_RELATIVE = Path(
    "tuning/vehicle/eams/tuning/hakoniwa/nominal-9kg/"
    "param-sets/final-controller-params.txt"
)

# Urban collision policy applied only to the generated City model. The Drone
# PRO golden model remains the tuning authority and is never rewritten here.
EAMS_CHASSIS_FRICTION = "0.05 0.001 0.0001"
EAMS_SKID_FRICTION = "0.2 0.005 0.0001"
EAMS_PROPELLER_FRICTION = "0.01 0.001 0.0001"
EAMS_LANDING_COLLIDER_NAME = "landing_gear_support_contact"
EAMS_LANDING_COLLIDER_POSITION = "0 0 -0.44"
EAMS_LANDING_COLLIDER_SIZE = "0.31 0.29 0.04"

for path in (
    BUSINESS_PACK_ROOT / "tools" / "recipe",
    BUSINESS_PACK_ROOT / "tools",
    DRONE_SHOW_ROOT / "tools" / "recipe",
):
    sys.path.insert(0, str(path))

import drone_fleet_single_host as base
import drone_fleet_mujoco_city as city


_BASE_WRITE_LAUNCHER = base.write_launcher
_MUJOCO_VIEWER_ENABLED = False
_ACTIVE_RECIPE: "UrbanDroneRecipe | None" = None
CONTROL_MODE_FILE = "urban-drone-control.json"
SELECTED_RECIPE_FILE = "urban-drone-one-recipe.json"


@dataclass(frozen=True)
class UrbanDroneRecipe:
    source_path: Path
    source_sha256: str
    recipe_id: str
    fleet_experiment: Path
    city_receipt: Path
    mission: Path
    drone_profile: str
    initial_altitude_m: float
    launch_area: dict[str, Any]
    control_mode: str
    map_layout: str
    collider_overlay_default: bool


def _recipe_path(recipe_path: Path, value: object, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise base.RecipeError(f"{label} must be a non-empty path")
    path = Path(value).expanduser()
    return (
        (recipe_path.parent / path).resolve()
        if not path.is_absolute()
        else path.resolve()
    )


def _recipe_mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise base.RecipeError(f"{label} must be a mapping")
    return value


def load_urban_recipe(path: Path) -> UrbanDroneRecipe:
    recipe_path = path.expanduser().resolve()
    data = base.load_simple_yaml(recipe_path)
    if data.get("version") != 1:
        raise base.RecipeError("Urban one-Drone recipe version must be 1")
    recipe_id = data.get("id")
    if not isinstance(recipe_id, str) or not recipe_id:
        raise base.RecipeError("Urban one-Drone recipe id must be a non-empty string")
    fleet = _recipe_mapping(data.get("fleet_experiment"), "fleet_experiment")
    city_world = _recipe_mapping(data.get("city_world"), "city_world")
    drone = _recipe_mapping(data.get("drone"), "drone")
    launch_area = _recipe_mapping(drone.get("launch_area"), "drone.launch_area")
    control = _recipe_mapping(data.get("control"), "control")
    mission = _recipe_mapping(data.get("mission"), "mission")
    viewer = _recipe_mapping(data.get("viewer"), "viewer")
    control_mode = control.get("mode")
    if control_mode not in {"fleet-rpc", "ps4-rc"}:
        raise base.RecipeError("control.mode must be fleet-rpc or ps4-rc")
    drone_profile = drone.get("profile")
    if drone_profile != "eams-nominal-9kg":
        raise base.RecipeError("drone.profile must be eams-nominal-9kg")
    try:
        initial_altitude_m = float(drone["initial_altitude_m"])
        search_radius_m = float(launch_area["search_radius_m"])
        offset_m = [float(value) for value in launch_area["offset_m"]]
    except (KeyError, TypeError, ValueError) as exc:
        raise base.RecipeError("drone altitude/launch_area values are invalid") from exc
    if len(offset_m) != 3 or search_radius_m <= 0:
        raise base.RecipeError(
            "drone.launch_area requires three offsets and a positive radius"
        )
    if launch_area.get("mode") != "auto":
        raise base.RecipeError("drone.launch_area.mode must be auto")
    map_layout = viewer.get("map_layout", "bottom-left")
    if map_layout not in {"bottom-left", "split"}:
        raise base.RecipeError("viewer.map_layout must be bottom-left or split")
    collider_default = viewer.get("collider_overlay_default", False)
    if not isinstance(collider_default, bool):
        raise base.RecipeError("viewer.collider_overlay_default must be boolean")
    source_bytes = recipe_path.read_bytes()
    result = UrbanDroneRecipe(
        source_path=recipe_path,
        source_sha256=hashlib.sha256(source_bytes).hexdigest(),
        recipe_id=recipe_id,
        fleet_experiment=_recipe_path(
            recipe_path, fleet.get("path"), "fleet_experiment.path"
        ),
        city_receipt=_recipe_path(
            recipe_path, city_world.get("receipt"), "city_world.receipt"
        ),
        mission=_recipe_path(recipe_path, mission.get("path"), "mission.path"),
        drone_profile=drone_profile,
        initial_altitude_m=initial_altitude_m,
        launch_area={
            "mode": "auto",
            "offset_m": offset_m,
            "search_radius_m": search_radius_m,
        },
        control_mode=control_mode,
        map_layout=map_layout,
        collider_overlay_default=collider_default,
    )
    for label, required in (
        ("fleet_experiment.path", result.fleet_experiment),
        ("city_world.receipt", result.city_receipt),
        ("mission.path", result.mission),
    ):
        if not required.is_file():
            raise base.RecipeError(f"{label} not found: {required}")
    return result


def write_selected_recipe(paths: object, recipe: UrbanDroneRecipe) -> Path:
    output = paths.recipe_config / SELECTED_RECIPE_FILE
    output.write_text(json.dumps({
        "schema_version": 1,
        "source": str(recipe.source_path),
        "source_sha256": recipe.source_sha256,
        "id": recipe.recipe_id,
        "fleet_experiment": str(recipe.fleet_experiment),
        "city_receipt": str(recipe.city_receipt),
        "mission": str(recipe.mission),
        "drone_profile": recipe.drone_profile,
        "initial_altitude_m": recipe.initial_altitude_m,
        "launch_area": recipe.launch_area,
        "control_mode": recipe.control_mode,
        "map_layout": recipe.map_layout,
        "collider_overlay_default": recipe.collider_overlay_default,
    }, indent=2) + "\n", encoding="utf-8")
    return output


def read_selected_recipe(paths: object) -> UrbanDroneRecipe:
    selected = paths.recipe_config / SELECTED_RECIPE_FILE
    if not selected.is_file():
        return load_urban_recipe(DEFAULT_RECIPE)
    try:
        data = json.loads(selected.read_text(encoding="utf-8"))
        recipe = UrbanDroneRecipe(
            source_path=Path(data["source"]),
            source_sha256=data["source_sha256"],
            recipe_id=data["id"],
            fleet_experiment=Path(data["fleet_experiment"]),
            city_receipt=Path(data["city_receipt"]),
            mission=Path(data["mission"]),
            drone_profile=data["drone_profile"],
            initial_altitude_m=float(data["initial_altitude_m"]),
            launch_area=data["launch_area"],
            control_mode=data["control_mode"],
            map_layout=data["map_layout"],
            collider_overlay_default=data["collider_overlay_default"],
        )
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise base.RecipeError(
            f"invalid configured Urban Drone recipe {selected}: {exc}"
        ) from exc
    return recipe


def patch_launcher(
    path: Path,
    *,
    paths: object,
    drone_root: Path,
    mission_path: Path,
    mujoco_viewer: bool = False,
) -> Path:
    launcher = json.loads(path.read_text(encoding="utf-8"))
    assets = launcher.get("assets", [])
    mission_asset = next(
        (asset for asset in assets if asset.get("name") == "show-runner"), None
    )
    if mission_asset is None:
        raise base.RecipeError("generated Launcher has no show-runner asset")
    old_name = mission_asset["name"]
    new_name = "urban-drone-mission"
    mission_asset.update(
        {
            "name": new_name,
            "activation_timing": "after_start",
            "args": [
                str(ROOT / "apps" / "drone" / "city_fleet_mission.py"),
                "--drone-root",
                str(drone_root.resolve()),
                "--service-config",
                str(
                    paths.recipe_config
                    / "drone/fleets/services/api-current-service.json"
                ),
                "--city-marker",
                str(paths.recipe_config / "mujoco-city-fleet.json"),
                "--mission",
                str(mission_path),
                "--summary-json",
                str(paths.recipe_validation / "urban-drone-mission.json"),
            ],
            "cwd": str(ROOT),
            "delay_sec": 1,
        }
    )
    for asset in assets:
        dependencies = asset.get("depends_on")
        if isinstance(dependencies, list):
            asset["depends_on"] = [
                new_name if dependency == old_name else dependency
                for dependency in dependencies
            ]
    # Viewer publishers must register before hako-cmd start; they only need the
    # Drone service, not the after-start mission client.
    for asset in assets:
        if asset.get("name") == "visual-state-publisher":
            asset["depends_on"] = ["drone-service-1"]
    if mujoco_viewer:
        drone_service = next(
            (asset for asset in assets if asset.get("name") == "drone-service-1"),
            None,
        )
        if drone_service is None:
            raise base.RecipeError("generated Launcher has no drone-service-1 asset")
        service_args = drone_service.setdefault("args", [])
        if "--mujoco-viewer" not in service_args:
            service_args.append("--mujoco-viewer")
    path.write_text(json.dumps(launcher, indent=2) + "\n", encoding="utf-8")
    return path


def patch_rc_launcher(path: Path, *, paths: object, drone_root: Path) -> Path:
    """Replace the automatic mission with the proven PS4 RadioController client."""
    launcher = json.loads(path.read_text(encoding="utf-8"))
    assets = launcher.get("assets", [])
    controller = next(
        (asset for asset in assets if asset.get("name") == "show-runner"), None
    )
    if controller is None:
        raise base.RecipeError("generated Launcher has no show-runner asset")
    controller.update(
        {
            "name": "urban-drone-ps4-controller",
            "args": [
                str(drone_root.resolve() / "drone_api/rc/rc-custom.py"),
                str(paths.recipe_config / "pdudef/drone-pdudef-current.json"),
                str(
                    drone_root.resolve()
                    / "drone_api/rc/rc_config/ps4-control.json"
                ),
                "--name",
                "Drone-1",
                "--index",
                "0",
            ],
            "cwd": str(drone_root.resolve() / "drone_api"),
            "depends_on": ["drone-service-1"],
            "activation_timing": "after_start",
            "delay_sec": 1,
        }
    )
    for asset in assets:
        if asset.get("name") == "visual-state-publisher":
            asset["depends_on"] = ["drone-service-1"]
    path.write_text(json.dumps(launcher, indent=2) + "\n", encoding="utf-8")
    return path


def write_control_mode(paths: object, mode: str) -> Path:
    if mode not in {"fleet-rpc", "ps4-rc"}:
        raise base.RecipeError(f"unsupported Urban Drone control mode: {mode}")
    path = paths.recipe_config / CONTROL_MODE_FILE
    path.write_text(json.dumps({"mode": mode}, indent=2) + "\n", encoding="utf-8")
    return path


def read_control_mode(paths: object) -> str:
    path = paths.recipe_config / CONTROL_MODE_FILE
    if not path.is_file():
        return "fleet-rpc"
    try:
        mode = json.loads(path.read_text(encoding="utf-8"))["mode"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise base.RecipeError(f"invalid Urban Drone control mode {path}: {exc}") from exc
    if mode not in {"fleet-rpc", "ps4-rc"}:
        raise base.RecipeError(f"unsupported Urban Drone control mode: {mode}")
    return mode


def patch_eams_city_viewer(paths: object) -> tuple[Path, Path]:
    """Prepare normal and optional Collider-overlay EAMS City viewers."""
    embedded = (
        paths.recipe_root
        / "web/map-viewer/thirdparty/hakoniwa-threejs-drone"
    )
    scene_path = embedded / "config/drone_config-city-fleet.json"
    viewer_path = embedded / "config/viewer-config-fleets.json"
    type_path = embedded / "config/drone_types-hexa-eams.json"
    model_path = embedded / "assets/models/eams-hexa-frame.glb"
    for required in (scene_path, viewer_path, type_path, model_path):
        if not required.is_file():
            raise base.RecipeError(
                f"EAMS Three.js viewer resource not found: {required}"
            )

    marker_path = paths.recipe_config / "mujoco-city-fleet.json"
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        receipt_path = Path(marker["city_world"]["receipt"]).resolve()
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise base.RecipeError(
            f"invalid MuJoCo City viewer contract {marker_path}: {exc}"
        ) from exc
    world_dir = receipt_path.parent
    if world_dir.name != "world" or world_dir.parent.name != "build":
        raise base.RecipeError(
            f"City World Receipt is outside a Worker job: {receipt_path}"
        )
    collider_source = world_dir.parent.parent / "viewer/city-world-colliders.glb"
    if not collider_source.is_file():
        raise base.RecipeError(
            f"City World Collider GLB not found: {collider_source}"
        )
    collider_destination = embedded / "assets/local_models/city-world-colliders.glb"
    collider_destination.parent.mkdir(parents=True, exist_ok=True)
    collider_destination.unlink(missing_ok=True)
    try:
        os.link(collider_source, collider_destination)
    except OSError:
        shutil.copy2(collider_source, collider_destination)

    scene = json.loads(scene_path.read_text(encoding="utf-8"))
    drones = scene.get("drones")
    if not isinstance(drones, list) or not drones:
        raise base.RecipeError("generated Three.js City scene has no Drone template")
    scene["droneTypesPath"] = "./drone_types-hexa-eams.json"
    for drone in drones:
        drone["type"] = "hexa_eams"
    scene["environments"] = [
        environment
        for environment in scene.get("environments", [])
        if environment.get("name") != "city-world-colliders"
    ]
    scene_path.write_text(json.dumps(scene, indent=2) + "\n", encoding="utf-8")

    collider_scene = copy.deepcopy(scene)
    collider_scene["environments"].append({
        "name": "city-world-colliders",
        "model": "../assets/local_models/city-world-colliders.glb",
        "pos": [0, 0, 0],
        "hpr": [0, 0, 0],
        "render": {
            "mode": "wireframe",
            "color": "#22c55e",
            "opacity": 0.72,
            "depthTest": True,
            "depthWrite": False,
        },
    })
    collider_scene_path = scene_path.with_name(
        "drone_config-city-fleet-colliders.json"
    )
    collider_scene_path.write_text(
        json.dumps(collider_scene, indent=2) + "\n", encoding="utf-8"
    )

    viewer = json.loads(viewer_path.read_text(encoding="utf-8"))
    viewer.setdefault("ui", {})["enableAttachedCameras"] = True
    fleets = viewer.setdefault("stateInput", {}).setdefault("fleets", {})
    fleets["motorChannels"] = [0, 1, 2, 3, 4, 5]
    fleets["rotorScale"] = 200.0
    viewer_path.write_text(json.dumps(viewer, indent=2) + "\n", encoding="utf-8")
    collider_viewer = copy.deepcopy(viewer)
    collider_viewer.setdefault("three", {})["sceneConfigPath"] = (
        "./drone_config-city-fleet-colliders.json"
    )
    collider_viewer_path = viewer_path.with_name(
        "viewer-config-fleets-colliders.json"
    )
    collider_viewer_path.write_text(
        json.dumps(collider_viewer, indent=2) + "\n", encoding="utf-8"
    )
    return scene_path, viewer_path


def open_viewer(
    *, show_colliders: bool = False, map_layout: str = "bottom-left"
) -> int:
    paths = _paths()
    collider_config = paths.recipe_root / (
        "web/map-viewer/thirdparty/hakoniwa-threejs-drone/"
        "config/viewer-config-fleets-colliders.json"
    )
    if show_colliders and not collider_config.is_file():
        raise base.RecipeError(
            "Collider viewer is not configured; run configure before open-viewer"
        )
    url = base.viewer_url(1, map_viewer=True)
    if map_layout == "bottom-left":
        url += "&layout=three-main"
    if show_colliders:
        url = url.replace(
            "viewerConfigName=viewer-config-fleets.json",
            "viewerConfigName=viewer-config-fleets-colliders.json",
        )
    return 0 if base.open_browser(url) else 1


def _write_urban_launcher(paths, drone_root, viewer_root, experiment, system_name):
    if _ACTIVE_RECIPE is None:
        raise base.RecipeError("Urban Drone recipe is not active during configure")
    path = _BASE_WRITE_LAUNCHER(
        paths, drone_root, viewer_root, experiment, system_name
    )
    patch_eams_city_viewer(paths)
    if _ACTIVE_RECIPE.control_mode == "ps4-rc":
        return patch_rc_launcher(path, paths=paths, drone_root=drone_root)
    return patch_launcher(
        path,
        paths=paths,
        drone_root=drone_root,
        mission_path=_ACTIVE_RECIPE.mission,
        mujoco_viewer=_MUJOCO_VIEWER_ENABLED,
    )


base.write_launcher = _write_urban_launcher


def _paths():
    foundation = base.load_foundation_module()
    return foundation.resolve_workspace(base.ROOT, base.RECIPE_ID)


def select_drone_core_xml_model(marker: dict) -> Path:
    """Point the public Drone Core v4 runtime at XML while retaining MJB QA.

    The City helper compiles and reload-validates an MJB, while the public
    v4.0.0 service binary still opens its configured model through mj_loadXML.
    """
    type_path = Path(marker["type_config"])
    type_config = json.loads(type_path.read_text(encoding="utf-8"))
    model_path = Path(marker["process_models"][0]["mjb"]).with_suffix(".xml")
    if not model_path.is_file():
        raise base.RecipeError(f"generated MuJoCo City XML not found: {model_path}")
    type_config["components"]["droneDynamics"]["mujoco"]["modelPath"] = str(
        model_path
    )
    type_path.write_text(json.dumps(type_config, indent=2) + "\n", encoding="utf-8")
    marker["runtime_model"] = {
        "format": "xml",
        "path": str(model_path),
        "reason": "hakoniwa-drone-core v4.0.0 public service uses mj_loadXML",
    }
    marker_path = _paths().recipe_config / "mujoco-city-fleet.json"
    marker_path.write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")
    return model_path


def _parameter_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, value = line.split(None, 1)
        values[key] = value
    return values


def materialize_eams_controller_params(
    drone_root: Path, output: Path, *, rc_mode: bool = False
) -> Path:
    """Overlay the completed EAMS tuning result on the selected controller baseline."""
    base_path = (
        drone_root / "tuning/vehicle/eams/config/controller-params.txt"
        if rc_mode
        else drone_root / "config/controller/param-api-mixer-mujoco.txt"
    )
    tuned_path = drone_root / EAMS_TUNED_PARAMS_RELATIVE
    if not base_path.is_file() or not tuned_path.is_file():
        raise base.RecipeError("EAMS controller parameter inputs are incomplete")
    tuned = _parameter_values(tuned_path)
    if rc_mode:
        # RC supplies angle and throttle commands directly. Keep the completed
        # EAMS gains, but select the control stages and explicit ground-start
        # state machine required by RadioController. Without the landing
        # thresholds, their zero defaults make RC-safe input enter Landing.
        tuned.update({
            # RadioOperation starts in GPS mode. ANGLE_CONTROL_ENABLE=1 with
            # GPS resolves to the Unknown profile and drops thrust after the
            # takeoff phase completes.
            "ANGLE_CONTROL_ENABLE": "0",
            "ANGLE_RATE_CONTROL_ENABLE": "0",
            "ALT_SPD_CONTROL_ENABLE": "0",
            "CTRLMODE_START_IN_HOVERING": "0",
            "CTRLMODE_STARTUP_SETTLE_TIME_SEC": "0.5",
            "CTRLMODE_TAKEOFF_IDLE_THROTTLE_RATE": "0.2",
            "CTRLMODE_TAKEOFF_TRIGGER_THROTTLE_VALUE": "0.1",
            "CTRLMODE_TAKEOFF_TRIGGER_HOLD_SEC": "0.1",
            "CTRLMODE_TAKEOFF_ACTION_TARGET_ALTITUDE_M": "0.23",
            "CTRLMODE_TAKEOFF_ACTION_MAX_SPEED_M_S": "0.1",
            "CTRLMODE_TAKEOFF_COMPLETION_ALTITUDE_ERROR_M": "0.03",
            "CTRLMODE_TAKEOFF_COMPLETION_STABLE_DURATION_SEC": "1.0",
            "CTRLMODE_TAKEOFF_COMPLETION_STABLE_ANGLE_DEG": "1.0",
            "CTRLMODE_LANDING_TRIGGER_ALTITUDE_M": "0.2",
            "CTRLMODE_LANDING_TRIGGER_THROTTLE_VALUE": "0.3",
            "CTRLMODE_LANDING_TRIGGER_HOLD_SEC": "0.1",
            "CTRLMODE_LANDING_ACTION_TARGET_SPEED_M_S": "0.1",
            "CTRLMODE_LANDING_COMPLETION_ALTITUDE_M": "0.05",
            "CTRLMODE_LANDING_COMPLETION_STABLE_ANGLE_DEG": "1.0",
            "CTRLMODE_LANDING_COMPLETION_STABLE_DURATION_SEC": "1.0",
        })
    baseline = "EAMS RC" if rc_mode else "Fleet RPC"
    lines: list[str] = [
        f"# {baseline} baseline with completed nominal 9 kg tuning overlay."
    ]
    seen: set[str] = set()
    for raw_line in base_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            lines.append(raw_line)
            continue
        key = line.split(None, 1)[0]
        # Some legacy parameter files contain the same mode selector twice.
        # Emit each key once so the generated runtime contract is unambiguous.
        if key in seen:
            continue
        if key in tuned:
            lines.append(f"{key} {tuned[key]}")
        else:
            lines.append(raw_line)
        seen.add(key)
    for key, value in tuned.items():
        if key not in seen:
            lines.append(f"{key} {value}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


def apply_eams_city_contact_policy(hexa_body: ET.Element) -> dict:
    """Make the generated Hexa model slide off walls and collide at its rotors."""
    contact_friction = {
        "frame_chassis_contact": EAMS_CHASSIS_FRICTION,
        "landing_gear_left_skid_contact": EAMS_SKID_FRICTION,
        "landing_gear_right_skid_contact": EAMS_SKID_FRICTION,
    }
    for name, friction in contact_friction.items():
        geom = hexa_body.find(f".//geom[@name='{name}']")
        if geom is None:
            raise base.RecipeError(f"EAMS model has no required contact geom: {name}")
        geom.set("friction", friction)

    propeller_names = [f"prop{index}_geom" for index in range(1, 7)]
    for name in propeller_names:
        geom = hexa_body.find(f".//geom[@name='{name}']")
        if geom is None:
            raise base.RecipeError(f"EAMS model has no required propeller geom: {name}")
        # PLATEAU City geoms use contype=1. The source visual masks (2/4)
        # intentionally do not meet that mask, so enable the swept propeller
        # discs as low-friction collision envelopes in the generated copy.
        geom.set("contype", "1")
        geom.set("conaffinity", "1")
        geom.set("condim", "1")
        geom.set("friction", EAMS_PROPELLER_FRICTION)

    if hexa_body.find(
        f".//geom[@name='{EAMS_LANDING_COLLIDER_NAME}']"
    ) is not None:
        raise base.RecipeError(
            f"EAMS model already has landing collider: {EAMS_LANDING_COLLIDER_NAME}"
        )
    ET.SubElement(hexa_body, "geom", {
        "name": EAMS_LANDING_COLLIDER_NAME,
        "type": "box",
        "pos": EAMS_LANDING_COLLIDER_POSITION,
        "size": EAMS_LANDING_COLLIDER_SIZE,
        "rgba": "0 0 0 0",
        "mass": "0",
        "group": "2",
        "contype": "1",
        "conaffinity": "1",
        "condim": "3",
        "friction": EAMS_SKID_FRICTION,
        "margin": "0.005",
    })

    return {
        "chassis_friction": EAMS_CHASSIS_FRICTION,
        "skid_friction": EAMS_SKID_FRICTION,
        "propeller_friction": EAMS_PROPELLER_FRICTION,
        "propeller_collision_geoms": len(propeller_names),
        "propeller_contact_dimension": 1,
        "landing_collider": {
            "name": EAMS_LANDING_COLLIDER_NAME,
            "position": EAMS_LANDING_COLLIDER_POSITION,
            "size": EAMS_LANDING_COLLIDER_SIZE,
        },
    }


def materialize_eams_city_model(
    marker: dict,
    drone_root: Path,
    *,
    rc_mode: bool = False,
    runtime_config_dir: Path | None = None,
) -> Path:
    """Compose the tuned EAMS Hexa body with the configured PLATEAU City."""
    eams_root = drone_root / "tuning/vehicle/eams"
    model_source = eams_root / "generated/nominal-9kg/drone.xml"
    config_source = eams_root / "config/drone_config_0.json"
    if not model_source.is_file() or not config_source.is_file():
        raise base.RecipeError(f"EAMS nominal 9 kg golden package is missing: {eams_root}")

    process_dir = _paths().recipe_config / "drone/mujoco-city-fleet/process-01"
    body_only = process_dir / "eams-hexa-body.xml"
    runtime_xml = process_dir / "eams-hexa-city.xml"
    runtime_mjb = runtime_xml.with_suffix(".mjb")

    tree = ET.parse(model_source)
    root = tree.getroot()
    worldbody = root.find("worldbody")
    if worldbody is None:
        raise base.RecipeError(f"EAMS model has no worldbody: {model_source}")
    hexa_body = next(
        (
            item for item in list(worldbody)
            if item.tag == "body" and item.get("name") == "drone_base"
        ),
        None,
    )
    if hexa_body is None:
        raise base.RecipeError(f"EAMS model has no drone_base body: {model_source}")
    for item in list(worldbody):
        is_composition_ground = item.tag == "geom" and item.get("name") == "ground"
        if item is not hexa_body and not is_composition_ground:
            worldbody.remove(item)
    hexa_body.set("pos", "0 0 0")
    contact_policy = apply_eams_city_contact_policy(hexa_body)
    ET.indent(tree, space="  ")
    tree.write(body_only, encoding="utf-8", xml_declaration=True)

    city_mjcf = Path(marker["city_world"]["mjcf"])
    compose_tool = WORKSPACE / "hakoniwa-mbody-registry/tools/compose_mujoco_world.py"
    subprocess.run(
        [
            sys.executable,
            str(compose_tool),
            str(body_only),
            str(city_mjcf),
            "--output",
            str(runtime_xml),
        ],
        cwd=ROOT,
        check=True,
    )
    compiled = city.compile_mujoco_xml(
        runtime_xml, runtime_mjb, city.find_mujoco_library(drone_root)
    )

    eams_config = json.loads(config_source.read_text(encoding="utf-8"))
    type_path = Path(marker["type_config"])
    type_config = json.loads(type_path.read_text(encoding="utf-8"))
    for name in ("droneDynamics", "battery", "rotor", "thruster", "sensors"):
        type_config["components"][name] = copy.deepcopy(
            eams_config["components"][name]
        )
    dynamics = type_config["components"]["droneDynamics"]
    dynamics["mujoco"]["modelPath"] = str(runtime_xml)
    dynamics["collision_detection"] = True
    type_config["simulation"]["timeStep"] = eams_config["simulation"]["timeStep"]
    if rc_mode and runtime_config_dir is not None:
        runtime_config_dir.mkdir(parents=True, exist_ok=True)
        parameter_path = runtime_config_dir / "controller-params.txt"
        runtime_type_path = runtime_config_dir / "drone_config_0.json"
    else:
        parameter_name = (
            "eams-rc-controller-params.txt" if rc_mode
            else "eams-controller-params.txt"
        )
        parameter_path = process_dir / parameter_name
        runtime_type_path = type_path
    parameter_path = materialize_eams_controller_params(
        drone_root,
        parameter_path,
        rc_mode=rc_mode,
    )
    if rc_mode:
        # Preserve the EAMS controller contract (including mixer.enable) and
        # change only the implementation/mode needed for gamepad operation.
        type_config["controller"] = copy.deepcopy(eams_config["controller"])
        if runtime_config_dir is not None:
            log_directory = runtime_config_dir / "logs/drone"
            log_directory.mkdir(parents=True, exist_ok=True)
            type_config["simulation"].setdefault("logging", {})["mode"] = "csv"
            type_config["simulation"]["logOutputDirectory"] = str(log_directory)
    controller = type_config["controller"]
    controller["paramFilePath"] = (
        "controller-params.txt"
        if runtime_type_path.parent == parameter_path.parent
        else str(parameter_path)
    )
    if rc_mode:
        type_config.pop("serviceConfigPath", None)
        controller.pop("apiServiceMode", None)
        controller.update({
            "serviceMode": "rc",
            "moduleDirectory": "../drone_control/cmake-build/workspace/RadioController",
            "moduleName": "RadioController",
            "backendType": "adapter-hakoniwa",
        })
    runtime_type_path.write_text(
        json.dumps(type_config, indent=2) + "\n", encoding="utf-8"
    )

    fleet_path = Path(marker["fleet_config"])
    fleet_config = json.loads(fleet_path.read_text(encoding="utf-8"))
    drones = fleet_config.get("drones")
    if not isinstance(drones, list) or len(drones) != 1:
        raise base.RecipeError("EAMS Hexa checkpoint requires exactly one Drone")
    drones[0]["mujoco"] = {
        "modelName": "drone_base",
        "propNames": [f"prop{index}" for index in range(1, 7)],
    }
    fleet_config["types"][drones[0]["type"]] = str(runtime_type_path)
    fleet_path.write_text(json.dumps(fleet_config, indent=2) + "\n", encoding="utf-8")

    marker["runtime_model"] = {
        "format": "xml",
        "path": str(runtime_xml),
        "vehicle": "EAMS E6106FLMP2-equivalent nominal 9 kg Hexa-X",
        "rotor_count": 6,
        "contact_policy": contact_policy,
        "compiled_validation": compiled,
    }
    marker["type_config"] = str(runtime_type_path)
    marker_path = _paths().recipe_config / "mujoco-city-fleet.json"
    marker_path.write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")
    return runtime_xml


def set_initial_drop_altitude(marker: dict, initial_altitude_m: float) -> Path:
    """Start above the City surface so MuJoCo settles the Drone on the DEM."""
    spawn = marker["flight_plan"]["spawn_points"][0]
    surface_height_m = float(spawn["surface_height_m"])
    if initial_altitude_m <= surface_height_m + 1.0:
        raise base.RecipeError(
            "initial_altitude_m must be at least 1 m above the selected City surface"
        )
    fleet_path = Path(marker["fleet_config"])
    fleet_config = json.loads(fleet_path.read_text(encoding="utf-8"))
    drones = fleet_config.get("drones")
    if not isinstance(drones, list) or len(drones) != 1:
        raise base.RecipeError("one-Drone checkpoint Fleet config is invalid")
    position = drones[0].get("position_meter")
    if not isinstance(position, list) or len(position) != 3:
        raise base.RecipeError("Drone-1 has no valid initial position_meter")
    # Drone Core's Fleet config uses NED, hence an altitude above the local
    # origin is represented by a negative Z value.
    position[2] = -initial_altitude_m
    fleet_path.write_text(json.dumps(fleet_config, indent=2) + "\n", encoding="utf-8")
    marker["flight_plan"]["initial_drop"] = {
        "initial_altitude_m": initial_altitude_m,
        "surface_height_m": surface_height_m,
        "drop_distance_m": initial_altitude_m - surface_height_m,
        "ground": "plateau-dem",
    }
    return fleet_path


def configure(
    drone_root: Path,
    *,
    recipe: UrbanDroneRecipe,
    runtime_config_dir: Path | None = None,
) -> int:
    global _ACTIVE_RECIPE
    _ACTIVE_RECIPE = recipe
    rc = base.configure(recipe.fleet_experiment, drone_root)
    if rc != 0:
        return rc
    paths = _paths()
    if recipe.control_mode == "ps4-rc" and runtime_config_dir is None:
        runtime_config_dir = paths.recipe_root / "rc"
    marker = city.configure_single_host_fleet(
        drone_root=drone_root,
        city_world_path=recipe.city_receipt,
        drone_count=1,
        process_count=1,
        recipe_config=paths.recipe_config,
        altitude_mode="route-clearance",
        launch_area=recipe.launch_area,
    )
    set_initial_drop_altitude(marker, recipe.initial_altitude_m)
    runtime_model = materialize_eams_city_model(
        marker,
        drone_root,
        rc_mode=recipe.control_mode == "ps4-rc",
        runtime_config_dir=runtime_config_dir,
    )
    write_control_mode(paths, recipe.control_mode)
    write_selected_recipe(paths, recipe)
    spawn = marker["flight_plan"]["spawn_points"][0]
    print("Urban one-Drone checkpoint configured")
    print(f"Recipe     : {recipe.source_path}")
    print(f"City World : {recipe.city_receipt}")
    print(f"Spawn ENU  : ({spawn['x_m']:.3f}, {spawn['y_m']:.3f})")
    print(
        "Flight Z  : "
        f"{marker['flight_plan']['resolved_flight_altitude_m']:.3f} m"
    )
    print(
        f"Initial Z : {recipe.initial_altitude_m:.3f} m "
        "(free-fall to PLATEAU DEM)"
    )
    print(f"Fleet      : {marker['fleet_config']}")
    print(f"Runtime XML: {runtime_model}")
    print("Vehicle    : EAMS nominal 9 kg Hexa-X (6 rotors, Hakoniwa tuned)")
    print(
        f"Controller : "
        f"{'PS4 RC' if recipe.control_mode == 'ps4-rc' else 'Fleet RPC'}"
    )
    print("Browser    : EAMS Hexa GLB (6 animated propellers)")
    print("Next       : python tools/drone_one.py doctor")
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "command",
        choices=["configure", "doctor", "start", "status", "open-viewer", "stop"],
    )
    result.add_argument("--drone-root", type=Path, default=DEFAULT_DRONE_ROOT)
    result.add_argument("--viewer-root", type=Path, default=DEFAULT_VIEWER_ROOT)
    result.add_argument(
        "--recipe",
        type=Path,
        help="configure recipe; defaults to urban-drone-one.yaml",
    )
    result.add_argument(
        "--mujoco-viewer",
        action="store_true",
        help="open the native MuJoCo viewer while running doctor/start",
    )
    result.add_argument(
        "--rc",
        action="store_true",
        help="compatibility override: configure with PS4 RadioController",
    )
    collider_group = result.add_mutually_exclusive_group()
    collider_group.add_argument(
        "--colliders",
        dest="colliders",
        action="store_true",
        help="show green MJCF Collider wireframes with open-viewer",
    )
    collider_group.add_argument(
        "--no-colliders",
        dest="colliders",
        action="store_false",
        help="hide MJCF Collider wireframes with open-viewer",
    )
    result.set_defaults(colliders=None)
    return result


def main() -> int:
    global _MUJOCO_VIEWER_ENABLED, _ACTIVE_RECIPE
    argument_parser = parser()
    args = argument_parser.parse_args()
    if args.colliders is not None and args.command != "open-viewer":
        argument_parser.error(
            "--colliders/--no-colliders are available only with open-viewer"
        )
    if args.rc and args.command != "configure":
        argument_parser.error("--rc is available only with configure")
    if args.recipe is not None and args.command != "configure":
        argument_parser.error("--recipe is available only with configure")
    _MUJOCO_VIEWER_ENABLED = args.mujoco_viewer
    drone_root = args.drone_root.expanduser().resolve()
    viewer_root = args.viewer_root.expanduser().resolve()
    if args.command == "configure":
        recipe = load_urban_recipe(args.recipe or DEFAULT_RECIPE)
        if args.rc:
            recipe = replace(recipe, control_mode="ps4-rc")
        _ACTIVE_RECIPE = recipe
        return configure(drone_root, recipe=recipe)
    paths = _paths()
    recipe = read_selected_recipe(paths)
    _ACTIVE_RECIPE = recipe
    if args.command == "doctor":
        return base.doctor(recipe.fleet_experiment, drone_root, viewer_root)
    if args.command == "start":
        return base.start(recipe.fleet_experiment, drone_root, viewer_root)
    if args.command == "status":
        return base.control(recipe.fleet_experiment, drone_root, "status")
    if args.command == "stop":
        return base.control(recipe.fleet_experiment, drone_root, "terminate")
    show_colliders = (
        recipe.collider_overlay_default
        if args.colliders is None
        else args.colliders
    )
    return open_viewer(
        show_colliders=show_colliders,
        map_layout=recipe.map_layout,
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (base.RecipeError, city.FleetMujocoError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
