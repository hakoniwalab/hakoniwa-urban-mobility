#!/usr/bin/env python3
"""Materialize and operate the S1 Urban City World + Car-1 recipe.

This wrapper composes component-owned artifacts.  It does not download PLATEAU
data, generate a vehicle model, or reimplement the Generic Ackermann runtime.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
BUSINESS_PACK = WORKSPACE / "hakoniwa-business-pack"
MBODY = WORKSPACE / "hakoniwa-mbody-registry"
MUJOCO_ROBOTS = WORKSPACE / "hakoniwa-mujoco-robots"
DEFAULT_CONFIG = ROOT / "recipes/urban-car-1-viewer.yaml"
CAR_NAME = "Car-1"
COMMAND_PDU = "hako_cmd_game"


class RecipeError(RuntimeError):
    """A prerequisite or generated-artifact contract is invalid."""


def required(path: Path, label: str) -> Path:
    if not path.is_file():
        raise RecipeError(f"{label} not found: {path}")
    return path


def load_json(path: Path, label: str) -> dict:
    try:
        data = json.loads(required(path, label).read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise RecipeError(f"invalid {label}: {path}: {error}") from error
    if not isinstance(data, dict):
        raise RecipeError(f"{label} must contain a JSON object: {path}")
    return data


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def foundation_install() -> Path:
    return BUSINESS_PACK / "work/foundation/install"


def foundation_python() -> Path:
    return required(foundation_install() / "python/bin/python3", "Foundation Python")


def load_yaml(path: Path) -> dict:
    """Load YAML through the dependency-pinned Foundation Python environment."""
    required(path, "Urban Car-1 configuration")
    result = subprocess.run(
        [
            str(foundation_python()),
            "-c",
            "import json,sys,yaml; "
            "print(json.dumps(yaml.safe_load(open(sys.argv[1], encoding='utf-8'))))",
            str(path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RecipeError(f"failed to load configuration {path}: {result.stderr.strip()}")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RecipeError(f"configuration loader returned invalid JSON for {path}") from error
    if not isinstance(value, dict):
        raise RecipeError(f"configuration must contain a YAML mapping: {path}")
    return value


def resolve_path(raw: str, label: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise RecipeError(f"{label} must be a non-empty path")
    path = Path(raw.strip()).expanduser()
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def resolve_config(config_path: Path) -> dict:
    config = load_yaml(config_path.resolve())
    try:
        recipe_id = str(config["id"])
        city = config["inputs"]["business_pack_city_receipt"]
        city_receipt = resolve_path(city["path"], "City World receipt path")
        spawn = config["inputs"]["golf_cart_model"]["spawn_pose_enu"]
        east = float(spawn["east_m"])
        north = float(spawn["north_m"])
        up = float(spawn["up_m"])
        yaw_deg = float(spawn["yaw_deg"])
        work = resolve_path(config["composition"]["output"]["directory"], "output directory")
    except (KeyError, TypeError, ValueError) as error:
        raise RecipeError(f"unsupported Urban Car-1 configuration schema: {config_path}") from error
    if any(not math.isfinite(value) for value in (east, north, up, yaw_deg)):
        raise RecipeError("spawn values must be finite")

    # City World MJCF is X=North, Y=-East, Z=Up. ENU yaw is positive
    # counter-clockwise from East, while MuJoCo yaw is measured from +X (North).
    yaw_mjcf_rad = math.radians(yaw_deg - 90.0)
    return {
        "raw": config,
        "path": config_path.resolve(),
        "recipe_id": recipe_id,
        "city_receipt": city_receipt,
        "work": work,
        "spawn_enu": {
            "frame": "city_origin_local_enu",
            "east_m": east,
            "north_m": north,
            "up_m": up,
            "yaw_deg": yaw_deg,
        },
        "spawn_mjcf": (north, -east, up, 0.0, 0.0, yaw_mjcf_rad),
    }


def paths() -> dict[str, Path]:
    return {
        "compose_tool": MBODY / "tools/compose_mujoco_world.py",
        "mujoco_compiler": BUSINESS_PACK / "tools/mujoco_model_compiler.py",
        "car_model": MBODY / "bodies/generic_ackermann_golf_cart/generated/model.minimal_world.xml",
        "plant": MUJOCO_ROBOTS / "src/cmake-build/examples/actuators/generic_ackermann/generic-ackermann-hakoniwa-asset",
        "source_manifest": MUJOCO_ROBOTS / "recipes/generic_ackermann/asset-manifest.json",
        "source_runtime": MUJOCO_ROBOTS / "recipes/generic_ackermann/ackermann-runtime.json",
        "source_endpoint": MUJOCO_ROBOTS / "config/endpoint/ackermann_gamepad_endpoint.json",
        "source_cache": MUJOCO_ROBOTS / "config/endpoint/cache/buffer.json",
        "source_comm": MUJOCO_ROBOTS / "config/endpoint/comm/shm_ackermann_gamepad_comm.json",
        "source_pdu_def": MUJOCO_ROBOTS / "config/ackermann-gamepad-pdudef-compact.json",
        "ps5_sender": MUJOCO_ROBOTS / "python/ackermann_gamepad.py",
        "ps5_mapping": MUJOCO_ROBOTS / "recipes/generic_ackermann/ps5-controller-macos.json",
        "core_config": foundation_install().parent / "config/cpp_core_config.json",
    }


def mujoco_library() -> Path:
    version = required(MUJOCO_ROBOTS / "MUJOCO_VERSION.txt", "MuJoCo version").read_text(
        encoding="utf-8"
    ).strip()
    if not version:
        raise RecipeError("hakoniwa-mujoco-robots/MUJOCO_VERSION.txt is empty")
    root = MUJOCO_ROBOTS / "src/cmake-build/_deps/mujoco_precompiled-src"
    candidates = (
        root / f"lib/mujoco.framework/Versions/A/libmujoco.{version}.dylib",
        root / f"lib/libmujoco.so.{version}",
        root / "lib/libmujoco.so",
        root / "bin/mujoco.dll",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise RecipeError(
        "MuJoCo shared library used by hakoniwa-mujoco-robots was not found under "
        f"{root}; rebuild the Ackermann asset first"
    )


def city_inputs(city_receipt: Path) -> tuple[Path, Path, dict]:
    receipt = load_json(city_receipt, "City World receipt")
    try:
        mjcf = Path(receipt["mjcf"]["path"])
        glb = Path(receipt["glb"]["path"])
        coordinate_frame = receipt["coordinate_frame"]
        origin = coordinate_frame["origin"]
        latitude = float(origin["latitude"])
        longitude = float(origin["longitude"])
        altitude_offset = float(origin["altitude_offset_m"])
        half_extent = coordinate_frame["half_extent_m"]
        north_south = float(half_extent["north_south"])
        east_west = float(half_extent["east_west"])
        mjcf_frame = coordinate_frame["coordinate_systems"]["mjcf"]
    except (KeyError, TypeError, ValueError) as error:
        raise RecipeError(f"City World receipt has an unsupported schema: {city_receipt}") from error
    if any(
        not math.isfinite(value)
        for value in (latitude, longitude, altitude_offset, north_south, east_west)
    ) or north_south <= 0.0 or east_west <= 0.0:
        raise RecipeError(f"City World receipt has invalid origin or extent: {city_receipt}")
    if mjcf_frame != "X=North,Y=-East,Z=Up":
        raise RecipeError(f"unsupported City World MJCF coordinate system: {mjcf_frame}")
    return required(mjcf, "City World MJCF"), required(glb, "City World GLB"), receipt


def command(command: list[str]) -> None:
    print("+", subprocess.list2cmdline(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def materialize_runtime(runtime_model: Path, work: Path) -> dict[str, Path]:
    source = paths()
    work.mkdir(parents=True, exist_ok=True)
    runtime = load_json(source["source_runtime"], "source Ackermann runtime config")
    runtime["bindings"]["asset_name"] = CAR_NAME
    runtime["bindings"]["pdu_name"] = COMMAND_PDU
    runtime["bindings"]["endpoint_name"] = "urban_car_1_endpoint"
    runtime_path = work / "car-1-runtime.json"
    write_json(runtime_path, runtime)

    pdu_def = load_json(source["source_pdu_def"], "source Ackermann PDU definition")
    pdu_def["robots"][0]["name"] = CAR_NAME
    source_pdu_def_dir = source["source_pdu_def"].parent
    for path_entry in pdu_def["paths"]:
        path_entry["path"] = str((source_pdu_def_dir / path_entry["path"]).resolve())
    pdu_def_path = work / "car-1-pdudef.json"
    write_json(pdu_def_path, pdu_def)

    comm = load_json(source["source_comm"], "source Ackermann endpoint communication config")
    comm["name"] = "urban_car_1_shm"
    comm["io"]["robots"][0]["name"] = CAR_NAME
    comm_path = work / "car-1-comm.json"
    write_json(comm_path, comm)

    endpoint = load_json(source["source_endpoint"], "source Ackermann endpoint config")
    endpoint["name"] = "urban_car_1_endpoint"
    endpoint["pdu_def_path"] = str(pdu_def_path)
    endpoint["cache"] = str(source["source_cache"].resolve())
    endpoint["comm"] = str(comm_path)
    endpoint_path = work / "car-1-endpoint.json"
    write_json(endpoint_path, endpoint)

    manifest = load_json(source["source_manifest"], "source Ackermann manifest")
    manifest["name"] = "Urban Car-1 Golf Cart"
    manifest["model"] = str(runtime_model)
    manifest["pdu_def"] = str(pdu_def_path)
    manifest["endpoint"] = str(endpoint_path)
    manifest["runtime_config"] = str(runtime_path)
    source_manifest_dir = source["source_manifest"].parent
    for component in manifest["components"]:
        component["config"] = str((source_manifest_dir / component["config"]).resolve())
    manifest_path = work / "car-1-asset-manifest.json"
    write_json(manifest_path, manifest)
    return {
        "runtime": runtime_path,
        "pdu_def": pdu_def_path,
        "endpoint": endpoint_path,
        "comm": comm_path,
        "manifest": manifest_path,
    }


def launcher_supports_cleanup(python: Path) -> bool:
    probe = subprocess.run(
        [
            str(python),
            "-c",
            "from hakoniwa_pdu.apps.launcher.model import LauncherSpec; "
            "raise SystemExit(0 if 'runtime' in LauncherSpec.model_fields else 1)",
        ],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return probe.returncode == 0


def materialize_mjb(source_xml: Path, output_mjb: Path, receipt_path: Path) -> dict:
    source = paths()
    library = mujoco_library()
    if output_mjb.is_file() and receipt_path.is_file():
        receipt = load_json(receipt_path, "MuJoCo materialization receipt")
        expected = (
            receipt.get("source_xml_sha256") == sha256(source_xml)
            and receipt.get("output_mjb") == str(output_mjb.resolve())
            and receipt.get("output_mjb_sha256") == sha256(output_mjb)
            and receipt.get("mujoco_library") == str(library)
            and receipt.get("mujoco_library_sha256") == sha256(library)
            and receipt.get("reload_validation") == "passed"
        )
        if expected:
            print(f"Reusing validated MJB: {output_mjb}")
            return receipt

    command([
        str(foundation_python()), str(source["mujoco_compiler"]),
        "--xml", str(source_xml),
        "--mjb", str(output_mjb),
        "--mujoco-library", str(library),
        "--receipt", str(receipt_path),
    ])
    return load_json(receipt_path, "MuJoCo materialization receipt")


def materialize_launcher(runtime_files: dict[str, Path], work: Path) -> Path:
    source = paths()
    python = foundation_python()
    logs = work / "logs"
    runtime = work / "runtime"
    logs.mkdir(parents=True, exist_ok=True)
    runtime.mkdir(parents=True, exist_ok=True)
    install = foundation_install()
    launcher = {
        "version": "0.1",
        "defaults": {
            "cwd": str(MUJOCO_ROBOTS),
            "stdout": str(logs / "${asset}.out"),
            "stderr": str(logs / "${asset}.err"),
            "start_grace_sec": 2,
            "delay_sec": 1,
            "env": {
                "set": {
                    "HAKONIWA_CORE_ROOT": str(install),
                    "HAKONIWA_PDU_ENDPOINT_ROOT": str(install),
                    "HAKO_CONFIG_PATH": str(source["core_config"]),
                    "PYTHONUNBUFFERED": "1",
                },
                "prepend": {
                    "PATH": [str(python.parent), str(install / "bin")],
                    "DYLD_LIBRARY_PATH": [str(install / "lib")],
                },
            },
        },
        "assets": [
            {
                "name": "urban-car-1-plant",
                "activation_timing": "before_start",
                "command": str(source["plant"]),
                "args": ["--manifest", str(runtime_files["manifest"])],
                "delay_sec": 2,
                "readiness": {
                    "type": "hako_asset",
                    "asset_name": CAR_NAME,
                    "timeout_sec": 120,
                    "poll_interval_sec": 0.2,
                    "command_timeout_sec": 2,
                },
            },
            {
                "name": "urban-car-1-ps5-controller",
                "activation_timing": "after_start",
                "command": str(python),
                "args": [
                    str(source["ps5_sender"]),
                    "--pdu-def", str(runtime_files["pdu_def"]),
                    "--rc-config", str(source["ps5_mapping"]),
                    "--robot", CAR_NAME,
                    "--pdu", COMMAND_PDU,
                ],
                "depends_on": ["urban-car-1-plant"],
                "delay_sec": 1,
            },
        ],
    }
    if not launcher_supports_cleanup(python):
        raise RecipeError(
            "Foundation Launcher does not support runtime.cleanup_mmap_on_start; "
            "upgrade hakoniwa-pdu before running this exclusive runtime Recipe"
        )
    launcher["runtime"] = {"cleanup_mmap_on_start": True}
    launcher_path = work / "launcher.json"
    write_json(launcher_path, launcher)
    return launcher_path


def doctor(resolved: dict) -> int:
    source = paths()
    checks: list[tuple[str, Path]] = [
        ("Urban Car-1 configuration", resolved["path"]),
        ("City receipt", resolved["city_receipt"]),
    ]
    failed = False
    try:
        city_mjcf, city_glb, _ = city_inputs(resolved["city_receipt"])
        checks.extend([("City MJCF", city_mjcf), ("City GLB", city_glb)])
    except RecipeError as error:
        print(f"[NG] City receipt: {error}")
        failed = True
    checks.extend([
        ("MBody compose tool", source["compose_tool"]),
        ("MuJoCo MJB compiler", source["mujoco_compiler"]),
        ("Golf Cart model", source["car_model"]),
        ("Ackermann plant", source["plant"]),
        ("Ackermann PDU cache", source["source_cache"]),
        ("PS5 sender", source["ps5_sender"]),
        ("Foundation Python", foundation_install() / "python/bin/python3"),
        ("Foundation Core config", source["core_config"]),
    ])
    try:
        checks.append(("Ackermann MuJoCo library", mujoco_library()))
    except RecipeError as error:
        print(f"[NG] Ackermann MuJoCo library: {error}")
        failed = True
    for label, path in checks:
        ok = path.is_file()
        print(f"[{'OK' if ok else 'NG'}] {label}: {path}")
        failed = failed or not ok
    return 1 if failed else 0


def configure(resolved: dict) -> int:
    source = paths()
    work = resolved["work"]
    spawn = resolved["spawn_mjcf"]
    city_mjcf, city_glb, receipt = city_inputs(resolved["city_receipt"])
    for label, path in paths().items():
        if label != "plant":
            required(path, label)
    required(foundation_install() / "python/bin/python3", "Foundation Python")
    required(source["core_config"], "Foundation Core config")
    work.mkdir(parents=True, exist_ok=True)
    output = work / "car-1-city.xml"
    command([
        str(foundation_python()), str(source["compose_tool"]),
        str(source["car_model"]), str(city_mjcf),
        "--output", str(output),
        "--robot-pos", " ".join(str(value) for value in spawn[:3]),
    ])
    tree = ET.parse(output)
    body = tree.getroot().find("./worldbody/body[@name='vehicle']")
    if body is None:
        raise RecipeError("composed model has no top-level 'vehicle' body")
    body.set("euler", " ".join(str(value) for value in spawn[3:]))
    ET.indent(tree, space="  ")
    tree.write(output, encoding="utf-8", xml_declaration=True)

    mjb = work / "car-1-city.mjb"
    mjb_receipt = work / "mujoco-materialization.json"
    materialization = materialize_mjb(output, mjb, mjb_receipt)

    runtime_files = materialize_runtime(mjb, work)
    launcher = materialize_launcher(runtime_files, work)
    compose_receipt = {
        "schema_version": 1,
        "recipe": resolved["recipe_id"],
        "configuration": str(resolved["path"]),
        "city_receipt": str(resolved["city_receipt"]),
        "city_mjcf": {"path": str(city_mjcf), "sha256": sha256(city_mjcf)},
        "city_glb": {"path": str(city_glb), "sha256": sha256(city_glb)},
        "golf_cart_mjcf": {"path": str(source["car_model"]), "sha256": sha256(source["car_model"])},
        "output_mjcf": {"path": str(output), "sha256": sha256(output)},
        "runtime_mjb": {
            "path": str(mjb),
            "sha256": sha256(mjb),
            "mujoco_version": materialization["mujoco_version"],
            "mujoco_library": materialization["mujoco_library"],
            "reload_validation": materialization["reload_validation"],
            "materialization_receipt": str(mjb_receipt),
        },
        "spawn_pose_city_enu": resolved["spawn_enu"],
        "spawn_pose_mjcf": {
            "frame": "X=North,Y=-East,Z=Up",
            "position_m": list(spawn[:3]),
            "yaw_rad": spawn[5],
        },
        "coordinate_frame": receipt["coordinate_frame"],
        "runtime_manifest": str(runtime_files["manifest"]),
        "core_config": str(source["core_config"]),
        "runtime_ownership": "exclusive Foundation mmap; Launcher cleanup_mmap_on_start",
        "launcher": str(launcher),
    }
    write_json(work / "compose-receipt.json", compose_receipt)
    print(f"Composed MJCF : {output}")
    print(f"Runtime MJB   : {mjb}")
    print(f"Manifest      : {runtime_files['manifest']}")
    print(f"Launcher      : {launcher}")
    print("Use 'start' for Viewer + PS5 control, or 'view' for Viewer-only inspection.")
    return 0


def launcher_path(work: Path) -> Path:
    return required(work / "launcher.json", "generated Launcher; run configure first")


def session_path(work: Path) -> Path:
    return work / "runtime/launcher-session.json"


def launch(operation: str, work: Path) -> int:
    python = foundation_python()
    if operation == "start":
        command([
            str(python), "-m", "hakoniwa_pdu.apps.launcher.hako_launcher",
            str(launcher_path(work)), "--background", str(session_path(work)),
        ])
    else:
        command([
            str(python), "-m", "hakoniwa_pdu.apps.launcher.hako_launcher_ctl",
            "status" if operation == "status" else "terminate", str(session_path(work)),
        ])
    return 0


def view(work: Path) -> int:
    manifest = required(work / "car-1-asset-manifest.json", "generated manifest; run configure first")
    command([str(paths()["plant"]), "--manifest", str(manifest), "--view-model"])
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Operate a configured Urban City World + Car-1 recipe")
    result.add_argument("command", choices=("doctor", "configure", "view", "start", "status", "stop"))
    result.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG,
        help=f"configuration YAML (default: {DEFAULT_CONFIG.relative_to(ROOT)})",
    )
    return result


def main() -> int:
    args = parser().parse_args()
    resolved = resolve_config(args.config)
    if args.command == "doctor":
        return doctor(resolved)
    if args.command == "configure":
        return configure(resolved)
    if args.command == "view":
        return view(resolved["work"])
    return launch(args.command, resolved["work"])


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RecipeError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
