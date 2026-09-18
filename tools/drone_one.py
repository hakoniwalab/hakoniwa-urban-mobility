#!/usr/bin/env python3
"""Configure and run the one-Drone Hokkaido PLATEAU checkpoint."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
BUSINESS_PACK_ROOT = WORKSPACE / "hakoniwa-business-pack"
DRONE_SHOW_ROOT = WORKSPACE / "hakoniwa-drone-show"
DEFAULT_DRONE_ROOT = WORKSPACE / "hakoniwa-drone-core"
DEFAULT_VIEWER_ROOT = WORKSPACE / "hakoniwa-threejs-drone"
EXPERIMENT = ROOT / "recipes" / "experiments" / "drone-one-hokkaido.yaml"
MISSION = ROOT / "config" / "drone" / "city-one-mission.json"
CITY_RECEIPT = (
    BUSINESS_PACK_ROOT
    / "work/remote-operation/city-world-worker/jobs"
    / "hokkaido-01100-lat43.062-lon141.355/build/world/city-world-receipt.json"
)

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


def patch_launcher(
    path: Path,
    *,
    paths: object,
    drone_root: Path,
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
                str(MISSION),
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


def _write_urban_launcher(paths, drone_root, viewer_root, experiment, system_name):
    path = _BASE_WRITE_LAUNCHER(
        paths, drone_root, viewer_root, experiment, system_name
    )
    return patch_launcher(
        path,
        paths=paths,
        drone_root=drone_root,
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


def configure(drone_root: Path) -> int:
    if not CITY_RECEIPT.is_file():
        raise base.RecipeError(f"Hokkaido City World Receipt not found: {CITY_RECEIPT}")
    rc = base.configure(EXPERIMENT, drone_root)
    if rc != 0:
        return rc
    paths = _paths()
    marker = city.configure_single_host_fleet(
        drone_root=drone_root,
        city_world_path=CITY_RECEIPT,
        drone_count=1,
        process_count=1,
        recipe_config=paths.recipe_config,
        altitude_mode="route-clearance",
        launch_area={
            "mode": "auto",
            "offset_m": [0.0, 0.0, 0.0],
            "search_radius_m": 100.0,
        },
    )
    mission_config = json.loads(MISSION.read_text(encoding="utf-8"))
    initial_altitude_m = float(mission_config.get("initial_altitude_m", 7.0))
    set_initial_drop_altitude(marker, initial_altitude_m)
    runtime_model = select_drone_core_xml_model(marker)
    spawn = marker["flight_plan"]["spawn_points"][0]
    print("Urban one-Drone checkpoint configured")
    print(f"City World : {CITY_RECEIPT}")
    print(f"Spawn ENU  : ({spawn['x_m']:.3f}, {spawn['y_m']:.3f})")
    print(
        "Flight Z  : "
        f"{marker['flight_plan']['resolved_flight_altitude_m']:.3f} m"
    )
    print(f"Initial Z : {initial_altitude_m:.3f} m (free-fall to PLATEAU DEM)")
    print(f"Fleet      : {marker['fleet_config']}")
    print(f"Runtime XML: {runtime_model}")
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
        "--mujoco-viewer",
        action="store_true",
        help="open the native MuJoCo viewer while running doctor/start",
    )
    return result


def main() -> int:
    global _MUJOCO_VIEWER_ENABLED
    args = parser().parse_args()
    _MUJOCO_VIEWER_ENABLED = args.mujoco_viewer
    drone_root = args.drone_root.expanduser().resolve()
    viewer_root = args.viewer_root.expanduser().resolve()
    if args.command == "configure":
        return configure(drone_root)
    if args.command == "doctor":
        return base.doctor(EXPERIMENT, drone_root, viewer_root)
    if args.command == "start":
        return base.start(EXPERIMENT, drone_root, viewer_root)
    if args.command == "status":
        return base.control(EXPERIMENT, drone_root, "status")
    if args.command == "stop":
        return base.control(EXPERIMENT, drone_root, "terminate")
    return base.open_viewer(EXPERIMENT)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (base.RecipeError, city.FleetMujocoError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
