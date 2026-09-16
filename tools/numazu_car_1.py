#!/usr/bin/env python3
"""Materialize and operate the S1 Numazu City World + Car-1 recipe.

This wrapper composes component-owned artifacts.  It does not download PLATEAU
data, generate a vehicle model, or reimplement the Generic Ackermann runtime.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
BUSINESS_PACK = WORKSPACE / "hakoniwa-business-pack"
MBODY = WORKSPACE / "hakoniwa-mbody-registry"
MUJOCO_ROBOTS = WORKSPACE / "hakoniwa-mujoco-robots"
CITY_RECEIPT = (
    BUSINESS_PACK
    / "work/remote-operation/city-world-worker/jobs"
    / "shizuoka-22203-lat35.099-lon138.859/build/world/city-world-receipt.json"
)
WORK = ROOT / "work/numazu-car-1-viewer"
CAR_NAME = "Car-1"
COMMAND_PDU = "hako_cmd_game"
SUGGESTED_SPAWN = "46.05 -8.70 6.07 0 0 -0.13"


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


def city_inputs() -> tuple[Path, Path, dict]:
    receipt = load_json(CITY_RECEIPT, "Numazu City World receipt")
    try:
        mjcf = Path(receipt["mjcf"]["path"])
        glb = Path(receipt["glb"]["path"])
        origin = receipt["coordinate_frame"]["origin"]
        latitude = float(origin["latitude"])
        longitude = float(origin["longitude"])
    except (KeyError, TypeError, ValueError) as error:
        raise RecipeError(f"City World receipt has an unsupported schema: {CITY_RECEIPT}") from error
    if (latitude, longitude) != (35.0988, 138.8587):
        raise RecipeError(
            "unexpected City World origin; expected Numazu 35.0988, 138.8587, got "
            f"{latitude}, {longitude}"
        )
    return required(mjcf, "City World MJCF"), required(glb, "City World GLB"), receipt


def parse_spawn(raw: str) -> tuple[float, float, float, float, float, float]:
    try:
        values = tuple(float(value) for value in raw.replace(",", " ").split())
    except ValueError as error:
        raise RecipeError("--spawn must contain six finite numbers") from error
    if len(values) != 6 or any(value != value or abs(value) == float("inf") for value in values):
        raise RecipeError("--spawn must contain six finite numbers: N -E U roll pitch yaw")
    return values  # type: ignore[return-value]


def command(command: list[str]) -> None:
    print("+", subprocess.list2cmdline(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def materialize_runtime(runtime_model: Path) -> dict[str, Path]:
    source = paths()
    WORK.mkdir(parents=True, exist_ok=True)
    runtime = load_json(source["source_runtime"], "source Ackermann runtime config")
    runtime["bindings"]["asset_name"] = CAR_NAME
    runtime["bindings"]["pdu_name"] = COMMAND_PDU
    runtime["bindings"]["endpoint_name"] = "numazu_car_1_endpoint"
    runtime_path = WORK / "car-1-runtime.json"
    write_json(runtime_path, runtime)

    pdu_def = load_json(source["source_pdu_def"], "source Ackermann PDU definition")
    pdu_def["robots"][0]["name"] = CAR_NAME
    source_pdu_def_dir = source["source_pdu_def"].parent
    for path_entry in pdu_def["paths"]:
        path_entry["path"] = str((source_pdu_def_dir / path_entry["path"]).resolve())
    pdu_def_path = WORK / "car-1-pdudef.json"
    write_json(pdu_def_path, pdu_def)

    comm = load_json(source["source_comm"], "source Ackermann endpoint communication config")
    comm["name"] = "numazu_car_1_shm"
    comm["io"]["robots"][0]["name"] = CAR_NAME
    comm_path = WORK / "car-1-comm.json"
    write_json(comm_path, comm)

    endpoint = load_json(source["source_endpoint"], "source Ackermann endpoint config")
    endpoint["name"] = "numazu_car_1_endpoint"
    endpoint["pdu_def_path"] = str(pdu_def_path)
    endpoint["cache"] = str(source["source_cache"].resolve())
    endpoint["comm"] = str(comm_path)
    endpoint_path = WORK / "car-1-endpoint.json"
    write_json(endpoint_path, endpoint)

    manifest = load_json(source["source_manifest"], "source Ackermann manifest")
    manifest["name"] = "Numazu Car-1 Golf Cart"
    manifest["model"] = str(runtime_model)
    manifest["pdu_def"] = str(pdu_def_path)
    manifest["endpoint"] = str(endpoint_path)
    manifest["runtime_config"] = str(runtime_path)
    source_manifest_dir = source["source_manifest"].parent
    for component in manifest["components"]:
        component["config"] = str((source_manifest_dir / component["config"]).resolve())
    manifest_path = WORK / "car-1-asset-manifest.json"
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


def materialize_launcher(runtime_files: dict[str, Path]) -> Path:
    source = paths()
    python = foundation_python()
    logs = WORK / "logs"
    runtime = WORK / "runtime"
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
                "name": "numazu-car-1-plant",
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
                "name": "numazu-car-1-ps5-controller",
                "activation_timing": "after_start",
                "command": str(python),
                "args": [
                    str(source["ps5_sender"]),
                    "--pdu-def", str(runtime_files["pdu_def"]),
                    "--rc-config", str(source["ps5_mapping"]),
                    "--robot", CAR_NAME,
                    "--pdu", COMMAND_PDU,
                ],
                "depends_on": ["numazu-car-1-plant"],
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
    launcher_path = WORK / "launcher.json"
    write_json(launcher_path, launcher)
    return launcher_path


def doctor() -> int:
    source = paths()
    checks: list[tuple[str, Path]] = [("City receipt", CITY_RECEIPT)]
    failed = False
    try:
        city_mjcf, city_glb, _ = city_inputs()
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


def configure(spawn: tuple[float, float, float, float, float, float]) -> int:
    source = paths()
    city_mjcf, city_glb, receipt = city_inputs()
    for label, path in paths().items():
        if label != "plant":
            required(path, label)
    required(foundation_install() / "python/bin/python3", "Foundation Python")
    required(source["core_config"], "Foundation Core config")
    WORK.mkdir(parents=True, exist_ok=True)
    output = WORK / "car-1-city.xml"
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

    mjb = WORK / "car-1-city.mjb"
    mjb_receipt = WORK / "mujoco-materialization.json"
    materialization = materialize_mjb(output, mjb, mjb_receipt)

    runtime_files = materialize_runtime(mjb)
    launcher = materialize_launcher(runtime_files)
    compose_receipt = {
        "schema_version": 1,
        "recipe": "numazu-car-1-viewer",
        "city_receipt": str(CITY_RECEIPT.resolve()),
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
        "spawn_pose_mjcf": list(spawn),
        "coordinate_frame": receipt["coordinate_frame"],
        "runtime_manifest": str(runtime_files["manifest"]),
        "core_config": str(source["core_config"]),
        "runtime_ownership": "exclusive Foundation mmap; Launcher cleanup_mmap_on_start",
        "launcher": str(launcher),
    }
    write_json(WORK / "compose-receipt.json", compose_receipt)
    print(f"Composed MJCF : {output}")
    print(f"Runtime MJB   : {mjb}")
    print(f"Manifest      : {runtime_files['manifest']}")
    print(f"Launcher      : {launcher}")
    print("Use 'start' for Viewer + PS5 control, or 'view' for Viewer-only inspection.")
    return 0


def launcher_path() -> Path:
    return required(WORK / "launcher.json", "generated Launcher; run configure first")


def session_path() -> Path:
    return WORK / "runtime/launcher-session.json"


def launch(operation: str) -> int:
    python = foundation_python()
    if operation == "start":
        command([
            str(python), "-m", "hakoniwa_pdu.apps.launcher.hako_launcher",
            str(launcher_path()), "--background", str(session_path()),
        ])
    else:
        command([
            str(python), "-m", "hakoniwa_pdu.apps.launcher.hako_launcher_ctl",
            "status" if operation == "status" else "terminate", str(session_path()),
        ])
    return 0


def view() -> int:
    manifest = required(WORK / "car-1-asset-manifest.json", "generated manifest; run configure first")
    command([str(paths()["plant"]), "--manifest", str(manifest), "--view-model"])
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Operate the Numazu City World + Car-1 recipe")
    result.add_argument("command", choices=("doctor", "configure", "view", "start", "status", "stop"))
    result.add_argument(
        "--spawn",
        help="required for configure: N -E U roll pitch yaw in MJCF metres/radians",
    )
    return result


def main() -> int:
    args = parser().parse_args()
    if args.command == "doctor":
        return doctor()
    if args.command == "configure":
        if args.spawn is None:
            raise RecipeError(
                "configure requires --spawn 'N -E U roll pitch yaw'; "
                f"first candidate: --spawn '{SUGGESTED_SPAWN}'"
            )
        return configure(parse_spawn(args.spawn))
    if args.command == "view":
        return view()
    return launch(args.command)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RecipeError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
