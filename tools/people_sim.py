#!/usr/bin/env python3
"""Run 箱庭人間 (Hakoniwa People) that external programs move by name.

    python tools/people_sim.py configure --people recipes/people/people-one.yaml
    python tools/people_sim.py start     --people recipes/people/people-one.yaml
    python tools/people_sim.py open-viewer --people recipes/people/people-one.yaml
    python tools/people_sim.py stop      --people recipes/people/people-one.yaml

A recipe may name an `environment` (an Environment Studio Recipe, written as
a City World under world/ first) or a `world` (a City World receipt); the
people walk in it, bump into it and step up onto what is lower than 0.32 m.

configure writes, under $HAKONIWA_WORK_DIR/people/<id>/:
- config/people-world.xml: one MuJoCo world with every person (the bodies of
  hakoniwa-mbody-registry/bodies/hakoniwa_person, names prefixed "<name>/")
- config/people-pdudef.json: per person <name>/cmd_vel (Twist) and
  <name>/animation (String); the people's states on UrbanPeople
  (joint_states, vehicle_states, as the car fleet's)
- config/people-plant.json, config/launcher.json, and the web bridge and
  Three.js configs (tools/multi_car.py materialize_browser_visualization)

Then apps/people/hakoniwa_people.py (the Hakoniwa People API) moves them.
Run it in the Business Pack Workspace.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
import webbrowser
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import multi_car  # noqa: E402
import urban_lifecycle  # noqa: E402
import urban_manifest  # noqa: E402

WORKSPACE = ROOT.parent
PERSON_BODY = WORKSPACE / "hakoniwa-mbody-registry/bodies/hakoniwa_person/generated"
PLANT = ROOT / "apps/people/people_plant.py"
PLANT_ASSET = "HakoniwaPeople"
STATE_ROBOT = "UrbanPeople"
SCHEMA = "hakoniwa.people/v1"
LOOKS = {"visitor": 1.0, "staff": 1.0, "passerby": 1.0, "child": 0.7}
DEFAULT_HTTP_PORT = 28100
DEFAULT_BRIDGE_PORT = 28870
PDU_HEADER = 24  # pdudef sizes = the message's base size + the PDU header
ENVIRONMENT_STUDIO = WORKSPACE / "hakoniwa-environment-studio"


class PeopleError(RuntimeError):
    pass


# --- Recipe ---------------------------------------------------------------------------

def load(path: Path) -> dict:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise PeopleError(f"{path}: schema must be {SCHEMA}")
    people = data.get("people")
    if not isinstance(people, list) or not people:
        raise PeopleError(f"{path}: people must be a non-empty list")
    names = set()
    resolved = []
    for index, person in enumerate(people):
        where = f"{path}: people[{index}]"
        name = str(person.get("name", "")).strip()
        if not name or "/" in name or name in names or name == STATE_ROBOT:
            raise PeopleError(f"{where}: a unique name without '/' is needed")
        names.add(name)
        look = person.get("look", "visitor")
        if look not in LOOKS:
            raise PeopleError(f"{where}: look must be one of {', '.join(LOOKS)}")
        spawn = person.get("spawn", {}) or {}
        values = {key: float(spawn.get(key, 0.0)) for key in ("east_m", "north_m", "yaw_deg")}
        if not all(math.isfinite(value) for value in values.values()):
            raise PeopleError(f"{where}: spawn must be finite")
        resolved.append({"name": name, "look": look, "scale": LOOKS[look], "spawn": values})
    viewer = data.get("viewer", {}) or {}
    base = Path(path).resolve().parent
    where = {}
    for key in ("environment", "world"):
        if data.get(key):
            text = str(data[key]).replace("{workspace}", str(WORKSPACE))
            where[key] = (base / text).resolve() if not Path(text).is_absolute() else Path(text)
            if not where[key].is_file():
                raise PeopleError(f"{path}: {key} not found: {where[key]}")
    if len(where) > 1:
        raise PeopleError(f"{path}: give an environment or a world, not both")
    return {
        **where,
        "id": str(data.get("id") or Path(path).stem),
        "people": resolved,
        "http_port": int(viewer.get("http_port", DEFAULT_HTTP_PORT)),
        "web_bridge_port": int(viewer.get("web_bridge_port", DEFAULT_BRIDGE_PORT)),
        "ground_m": float(data.get("ground_m", 40.0)),
        "work": urban_manifest.work_dir() / "people" / str(data.get("id") or Path(path).stem),
    }


# --- MuJoCo world ------------------------------------------------------------------------

def person_model(look: str) -> Path:
    path = PERSON_BODY / look / "model.xml"
    if not path.is_file():
        raise PeopleError(f"Hakoniwa Person body not found: {path} (hakoniwa-mbody-registry)")
    return path


def city_world(receipt_path: Path) -> dict:
    """A City World receipt's MJCF (X north, Y west, as the people's) and GLB."""
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    try:
        mjcf, glb = Path(receipt["mjcf"]["path"]), Path(receipt["glb"]["path"])
        half = receipt["coordinate_frame"]["half_extent_m"]
    except (KeyError, TypeError) as exc:
        raise PeopleError(f"not a City World receipt: {receipt_path}") from exc
    if receipt["coordinate_frame"].get("coordinate_systems", {}).get("mjcf", "X=North,Y=-East,Z=Up") != "X=North,Y=-East,Z=Up":
        raise PeopleError(f"{receipt_path}: the people need an MJCF with X=North, Y=-East")
    colliders = receipt_path.parents[2] / "viewer/city-world-colliders.glb"
    return {"mjcf": mjcf, "glb": glb, "colliders": colliders if colliders.is_file() else None,
            "size_m": 2 * max(float(half["north_south"]), float(half["east_west"]))}


def export_environment(recipe: Path, job: Path) -> Path:
    """An Environment Studio Recipe written as a City World job; its receipt."""
    tool = ENVIRONMENT_STUDIO / "tools/env_urban.py"
    if not tool.is_file():
        raise PeopleError(f"Environment Studio not found: {tool}")
    subprocess.run([str(multi_car.foundation_python()), str(tool), "export", str(recipe), "--out", str(job)],
                   cwd=ENVIRONMENT_STUDIO, check=True, stdout=subprocess.DEVNULL)
    return job / "build/world/city-world-receipt.json"


VEHICLE_HEIGHT_M = 1.7  # a car's stand-in reaches this high above the ground


def vehicle_box(asset_data: dict) -> dict:
    """A car's stand-in in the people's world: a box of its Asset's outer
    size, from the ground to VEHICLE_HEIGHT_M, in its body frame (its body
    stands ground_clearance_m above the ground)."""
    size = asset_data["dimensions"]
    clearance = float(asset_data.get("spawn", {}).get("ground_clearance_m", 0.0))
    return {"half": [size["length_m"] / 2, size["width_m"] / 2, VEHICLE_HEIGHT_M / 2],
            "pos": [0.0, 0.0, VEHICLE_HEIGHT_M / 2 - clearance]}


def compile_world(world: Path) -> Path:
    """The people world compiled once: <world>.mjb next to the XML, reused while
    the XML (and MuJoCo) stay the same. Compiling a city's people world from XML
    takes 35 s or more (Sapporo: 4679 meshes), longer than the Launcher waits for
    the People plant to register; loading the MJB takes a moment. Raises if the
    world does not load."""
    import mujoco

    mjb = world.with_suffix(".mjb")
    stamp = world.with_suffix(".mjb.json")
    key = {"xml_sha256": hashlib.sha256(world.read_bytes()).hexdigest(), "mujoco": mujoco.__version__}
    try:
        if mjb.is_file() and json.loads(stamp.read_text(encoding="utf-8")) == key:
            return mjb
    except (OSError, json.JSONDecodeError):
        pass
    model = mujoco.MjModel.from_xml_path(str(world))
    mujoco.mj_saveModel(model, str(mjb), None)
    multi_car.write_json(stamp, key)
    return mjb


def world_xml(resolved: dict, city: dict | None = None, vehicles: dict | None = None) -> str:
    """Every person in one world (the City World's, if any); each one's names
    prefixed "<name>/", its root body standing at its spawn (MuJoCo X north,
    Y west)."""
    if city is None:
        root = ET.Element("mujoco", {"model": f"hakoniwa_people_{resolved['id']}"})
        worldbody = ET.SubElement(root, "worldbody")
        half = resolved["ground_m"] / 2
        ET.SubElement(worldbody, "geom", {"name": "ground", "type": "plane", "size": f"{half:g} {half:g} 0.1",
                                          "rgba": "0.8 0.8 0.8 1"})
    else:
        root = ET.parse(city["mjcf"]).getroot()
        for element in root.iter():  # its files (the terrain's hfield) from wherever this world is written
            if "file" in element.attrib and not Path(element.attrib["file"]).is_absolute():
                element.attrib["file"] = str((city["mjcf"].parent / element.attrib["file"]).resolve())
        worldbody = root.find("worldbody")
    for tag in ("compiler", "option"):
        for old in root.findall(tag):
            root.remove(old)
    root.insert(0, ET.Element("option", {"timestep": "0.002", "gravity": "0 0 -9.81"}))
    root.insert(0, ET.Element("compiler", {"angle": "degree"}))
    # The cars as boxes the plant moves with them (mocap): people bump into
    # them and their contacts are reported; the cars do not feel the people.
    for name, vehicle in sorted((vehicles or {}).items()):
        if "box" not in vehicle:
            continue
        body = ET.SubElement(worldbody, "body", {"name": f"vehicle:{name}", "mocap": "true", "pos": "0 0 -100"})
        box = vehicle["box"]
        ET.SubElement(body, "geom", {"name": f"vehicle:{name}", "type": "box", "size": " ".join(f"{v:g}" for v in box["half"]),
                                     "pos": " ".join(f"{v:g}" for v in box["pos"]), "rgba": "1 0.4 0.1 0.25",
                                     "group": "3"})
    actuators = root.find("actuator")
    if actuators is None:
        actuators = ET.SubElement(root, "actuator")
    for person in resolved["people"]:
        source = ET.parse(person.get("model") or person_model(person["look"])).getroot()
        prefix = f"{person['name']}/"
        for element in source.iter():
            for key in ("name", "joint"):
                if key in element.attrib:
                    element.attrib[key] = prefix + element.attrib[key]
        body = source.find("worldbody/body")
        spawn = person["spawn"]
        body.attrib["pos"] = f"{spawn['north_m']:g} {-spawn['east_m']:g} 0"
        worldbody.append(body)
        actuated = source.find("actuator")
        for actuator in [] if actuated is None else list(actuated):
            actuators.append(actuator)
    ET.indent(root)
    return ET.tostring(root, encoding="unicode") + "\n"


# --- PDUs -----------------------------------------------------------------------------------

def pdu_files(resolved: dict, config: Path, vehicle_states: tuple[str, Path] | None = None) -> dict[str, Path]:
    """The people's PDU definition; with vehicle_states (the Car fleet's state
    robot and its pdutypes), the plant also reads the cars' poses (riding)."""
    count = len(resolved["people"])
    state_size = max(4096, 1 << (1024 + 1024 * count - 1).bit_length())
    command_types = [
        {"channel_id": 0, "pdu_size": 48 + PDU_HEADER, "name": "cmd_vel", "type": "geometry_msgs/Twist"},
        {"channel_id": 1, "pdu_size": 256, "name": "animation", "type": "std_msgs/String"},
        {"channel_id": 2, "pdu_size": 256, "name": "ride", "type": "std_msgs/String"},
        # The person's latest contact (written by the plant).
        {"channel_id": 3, "pdu_size": 1024, "name": "contact", "type": "hako_msgs/ContactEvent"},
    ]
    state_types = [
        {"channel_id": 0, "pdu_size": state_size, "name": "joint_states", "type": "sensor_msgs/JointState"},
        {"channel_id": 1, "pdu_size": state_size, "name": "vehicle_states", "type": "sensor_msgs/MultiDOFJointState"},
        # The latest contact events of everyone (the plant keeps a window of them).
        {"channel_id": 2, "pdu_size": 16384, "name": "contact_events", "type": "hako_msgs/ContactEventArray"},
    ]
    command_path = config / "people-command-pdutypes.json"
    state_path = config / "people-state-pdutypes.json"
    multi_car.write_json(command_path, command_types)
    multi_car.write_json(state_path, state_types)
    pdu_def = config / "people-pdudef.json"
    paths = [{"id": "people-command", "path": str(command_path)}, {"id": "people-state", "path": str(state_path)}]
    robots = [*[{"name": person["name"], "pdutypes_id": "people-command"} for person in resolved["people"]],
              {"name": STATE_ROBOT, "pdutypes_id": "people-state"}]
    if vehicle_states is not None:
        paths.append({"id": "vehicle-state", "path": str(vehicle_states[1])})
        robots.append({"name": vehicle_states[0], "pdutypes_id": "vehicle-state"})
    multi_car.write_json(pdu_def, {"paths": paths, "robots": robots})
    return {"pdu_def": pdu_def, "state_pdu_types": state_path}


def ground_glb(path: Path, size_m: float) -> Path:
    """A flat grey ground for the viewer (glTF: y up)."""
    import trimesh

    mesh = trimesh.creation.box(extents=(size_m, 0.02, size_m))
    mesh.apply_translation((0, -0.01, 0))
    mesh.visual = trimesh.visual.TextureVisuals(
        material=trimesh.visual.material.PBRMaterial(baseColorFactor=[200, 202, 205, 255], roughnessFactor=0.9))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(trimesh.Scene(mesh).export(file_type="glb"))
    return path


# --- Configure / lifecycle ------------------------------------------------------------------

def spec(resolved: dict) -> urban_lifecycle.LifecycleSpec:
    work = resolved["work"]
    return urban_lifecycle.LifecycleSpec(
        recipe_id=f"people-{resolved['id']}", recipe_root=work, launcher=work / "config/launcher.json",
        session=work / "runtime/launcher-session.json", viewer_url=viewer_url(resolved),
        websocket_port=resolved["web_bridge_port"], ports=(resolved["http_port"], resolved["web_bridge_port"]))


def viewer_url(resolved: dict) -> str:
    config = resolved["work"] / "config/threejs/viewer-config.json"
    return (f"http://127.0.0.1:{resolved['http_port']}"
            + multi_car.workspace_url(WORKSPACE / "hakoniwa-threejs-drone/index.html")
            + "?viewerConfigPath=" + multi_car.workspace_url(config))


def configure(resolved: dict) -> int:
    work = resolved["work"]
    config = work / "config"
    config.mkdir(parents=True, exist_ok=True)
    (work / "logs").mkdir(exist_ok=True)
    (work / "runtime").mkdir(exist_ok=True)
    receipt = resolved.get("world")
    if resolved.get("environment"):
        receipt = export_environment(resolved["environment"], work / "world")
    city = city_world(receipt) if receipt else None
    world = config / "people-world.xml"
    world.write_text(world_xml(resolved, city), encoding="utf-8")
    world_mjb = compile_world(world)  # stops here if the world does not load
    pdus = pdu_files(resolved, config)
    multi_car.write_json(config / "people-plant.json", {
        "asset_name": PLANT_ASSET, "state_robot": STATE_ROBOT, "world_xml": str(world),
        "world_mjb": str(world_mjb),
        "pdu_def": str(pdus["pdu_def"]), "delta_usec": 10000, "state_period_usec": 20000,
        "owns_conductor": True, "realtime": True, "people": resolved["people"],
        "contact_log": str(work / "logs/people-contacts.jsonl"),
    })
    vehicles = [{
        "name": person["name"], "type": f"hakoniwa-person-{person['look']}",
        "type_definition": {"view_model": PERSON_BODY / person["look"] / "view-model.json"},
    } for person in resolved["people"]]
    environment_glb = city["glb"] if city else ground_glb(work / "assets/ground.glb", resolved["ground_m"])
    browser = multi_car.materialize_browser_visualization(
        pdus, config, environment_glb, vehicles,
        {"web_bridge_port": resolved["web_bridge_port"], "http_port": resolved["http_port"],
         "threejs_root": WORKSPACE / "hakoniwa-threejs-drone"},
        collider_glb=city["colliders"] if city else None, state_robot=STATE_ROBOT)
    # Look at the people from a few metres away rather than from a car's distance.
    scene = json.loads(Path(browser["scene_config"]).read_text(encoding="utf-8"))
    scene["main_camera"]["position"] = [-6.0, -6.0, 4.0]
    multi_car.write_json(Path(browser["scene_config"]), scene)
    multi_car.write_json(config / "launcher.json", launcher(resolved, config, browser))
    print(f"Configured {len(resolved['people'])} people: {work}")
    print(f"PDU definition (for the API): {pdus['pdu_def']}")
    return 0


def launcher(resolved: dict, config: Path, browser: dict) -> dict:
    source = multi_car.paths()
    python = multi_car.foundation_python()
    install = multi_car.foundation_install()
    logs = resolved["work"] / "logs"
    return {
        "version": "0.1",
        "defaults": {
            "cwd": str(ROOT), "stdout": str(logs / "${asset}.out"), "stderr": str(logs / "${asset}.err"),
            "start_grace_sec": 2, "delay_sec": 1,
            "env": {
                "set": {"HAKONIWA_CORE_ROOT": str(install), "HAKONIWA_PDU_ENDPOINT_ROOT": str(install),
                        "HAKO_CONFIG_PATH": str(source["core_config"]), "PYTHONUNBUFFERED": "1"},
                "prepend": {"PATH": [str(python.parent), str(install / "bin")],
                            "DYLD_LIBRARY_PATH": [str(install / "lib")]},
            },
        },
        "assets": [
            {"name": "people-plant", "activation_timing": "before_start", "command": str(python),
             "args": [str(PLANT), str(config / "people-plant.json")], "delay_sec": 2,
             "readiness": {"type": "hako_asset", "asset_name": PLANT_ASSET, "timeout_sec": 60,
                           "poll_interval_sec": 0.2, "command_timeout_sec": multi_car.READINESS_PROBE_SEC}},
            {"name": "people-web-bridge", "activation_timing": "before_start", "command": str(source["web_bridge"]),
             "args": ["--config-root", str(browser["bridge_root"]), "--node-name", "urban_vehicle_viewer_node1",
                      "--delta-time-step-usec", "20000"],
             "depends_on": ["people-plant"], "delay_sec": 1},
            {"name": "people-http-server", "activation_timing": "after_start", "command": str(python),
             "args": [str(source["http_server"]), "--port", str(resolved["http_port"]), "--bind", multi_car.VIEWER_HTTP_BIND,
                      "--directory", str(WORKSPACE)],
             "cwd": str(WORKSPACE), "depends_on": ["people-web-bridge"], "delay_sec": 1},
        ],
        "runtime": {"cleanup_mmap_on_start": True},
    }


def lifecycle(operation: str, resolved: dict) -> int:
    python = str(multi_car.foundation_python())
    life = spec(resolved)
    if operation == "start":
        if not life.launcher.is_file():
            raise PeopleError("not configured; run configure first")
        urban_lifecycle.preflight_start(life)
        subprocess.run([python, "-m", "hakoniwa_pdu.apps.launcher.hako_launcher", str(life.launcher),
                        "--background", str(life.session)], cwd=ROOT, check=True)
        report = urban_lifecycle.wait_for_demo_ready(life)
        print(json.dumps(report, indent=2))
        print(f"Viewer: {life.viewer_url}")
        return 0 if report["demo_ready"] else 1
    if operation == "status":
        print(json.dumps(urban_lifecycle.status_report(life), indent=2))
        return 0
    if operation == "stop":
        if urban_lifecycle.read_session(life) is None:
            print("not running")
            return 0
        subprocess.run([python, "-m", "hakoniwa_pdu.apps.launcher.hako_launcher_ctl", "terminate",
                        str(life.session)], cwd=ROOT, check=False)
        urban_lifecycle.verify_stopped(life)
        return 0
    urban_lifecycle.require_viewer_ready(life)
    print(f"Opening: {life.viewer_url}")
    return 0 if webbrowser.open(life.viewer_url) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("configure", "start", "status", "open-viewer", "stop"))
    parser.add_argument("--people", type=Path, required=True, help="a hakoniwa.people/v1 recipe")
    args = parser.parse_args()
    try:
        resolved = load(args.people)
        if args.command == "configure":
            return configure(resolved)
        return lifecycle(args.command, resolved)
    except (PeopleError, urban_lifecycle.LifecycleError, multi_car.RecipeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
