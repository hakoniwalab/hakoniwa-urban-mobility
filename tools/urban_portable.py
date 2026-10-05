#!/usr/bin/env python3
"""Windows portable package tool for Urban Studio (issue #82).

hakoniwa-business-pack tools/package_portable_workspace.py drives this tool
through portable/windows-profile.json:

  collect   source Workspace: gather what only the developer's machine has
            into build/portable-runtime (the Urban Car plant and its DLLs,
            glfw3.dll for Drone Core, the demo data bundle: this Workspace's
            demo Cities and Compositions, absolute paths replaced by
            placeholders; tools/urban_demo_worlds.py makes them)
  doctor    source Workspace: fail before packaging when an input is missing;
            report ports other programs already use
  prepare   package: relocate the Foundation (receipts, mmap) and unpack the
            demo data for this extraction folder (once per folder; a moved
            folder has its paths rewritten)
  start     package: prepare, check the ports, start Urban Studio, open it
            in the browser
  status    package: Urban Studio, Environment Studio, running simulations
  stop      package: stop running simulations, Environment Studio, and
            Urban Studio

The package runs with HAKONIWA_PORTABLE_WORKSPACE=1 (set by the generated
start|status|stop-urban-studio.bat): configure there reuses the packaged
plant and Python packages instead of running Git, pip, or CMake
(urban_manifest.portable). docs/windows-portable.md explains how to build it.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
import webbrowser
import zipfile


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT.parent
BUSINESS_PACK = PACKAGE_ROOT / "hakoniwa-business-pack"
FOUNDATION = BUSINESS_PACK / "work" / "foundation"
DRONE_CORE = PACKAGE_ROOT / "hakoniwa-drone-core"
FPV_DRONE = PACKAGE_ROOT / "hakoniwa-fpv-drone"
ENVIRONMENT_STUDIO = PACKAGE_ROOT / "hakoniwa-environment-studio"
ENVSIM = PACKAGE_ROOT / "hakoniwa-envsim"
PROFILE = ROOT / "portable" / "windows-profile.json"
DEMO_SPEC = ROOT / "portable" / "demo-data.json"

# Everything collect gathers; the profile lists it in include_paths (the
# packager drops every other build/ folder).
RUNTIME = ROOT / "build" / "portable-runtime"
PLANT_BIN = RUNTIME / "bin"            # the Urban Car plant and its DLLs (multi_car.plant_bin_dirs)
DRONE_BIN = RUNTIME / "drone-bin"      # DLLs Drone Core's executables load from the developer's vcpkg
DEMO_DATA = RUNTIME / "urban-demo-data.zip"
BUNDLE_MANIFEST = "portable-bundle.json"
PLANT_NAME = "urban-car-hakoniwa-asset"
PLANT_FEATURES = f"{PLANT_NAME}.features.json"
RUNTIME_DLLS = ("glfw3.dll",)

# The placeholders the bundled text files carry instead of the source paths.
WORK_TOKEN = "__HAKONIWA_PORTABLE_WORK__"   # the Business Pack work directory
ROOT_TOKEN = "__HAKONIWA_PORTABLE_ROOT__"   # the folder holding the repositories
TEXT_SUFFIXES = frozenset({".json", ".yaml", ".yml", ".xml"})
REPO_WORK_REFERENCE = "${repo:hakoniwa-business-pack}/work"
# Where a path inside a JSON string, an XML attribute, or a YAML value ends.
_PATH_TAIL = r"[^\"'<>\r\n]*"

LAYOUT_VERSION = 1
STAMP = Path("urban") / "portable-layout.json"  # relative to the work directory
WINDOWS_MAX_PATH_CHARS = 259
STUDIO_WAIT_SEC = 60.0
CREATE_NO_WINDOW = 0x08000000
PORTABLE_MMAP_TOKEN = "__HAKONIWA_PORTABLE_MMAP__"
# Python packages the routes and Environment Studio import (Recipe configure
# installs them into Foundation Python; the package copies that environment).
REQUIRED_MODULES = ("yaml", "numpy", "mujoco", "pygame", "trimesh", "shapely", "lxml", "PIL",
                    "hakopy", "hakoniwa_pdu")


class PortableError(RuntimeError):
    pass


def _say(message: str) -> None:
    print(message, flush=True)


def _hidden(os_name: str = os.name) -> dict:
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", CREATE_NO_WINDOW)} if os_name == "nt" else {}


def _exe(name: str, windows: bool | None = None) -> str:
    return f"{name}.exe" if ((os.name == "nt") if windows is None else windows) else name


def portable_package(root: Path = ROOT) -> bool:
    """Running from an extracted package: the .bat sets the variable, and the
    packager marks every copied repository."""
    return (os.environ.get("HAKONIWA_PORTABLE_WORKSPACE") == "1"
            and (root / ".hakoniwa-repository-root").is_file())


def work_dir() -> Path:
    """The Business Pack work directory: $HAKONIWA_WORK_DIR in the Workspace,
    else hakoniwa-business-pack/work."""
    configured = os.environ.get("HAKONIWA_WORK_DIR", "").strip()
    if os.environ.get("HAKONIWA_WORKSPACE_ACTIVE") == "1" and configured:
        return Path(configured).expanduser().resolve()
    return (BUSINESS_PACK / "work").resolve()


def _business_pack_tools() -> None:
    tools = str(BUSINESS_PACK / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)


# --- Paths in text files ---------------------------------------------------------------

def path_variants(path: str | Path) -> list[str]:
    """How a path is spelled in the files tools write, longest first: POSIX
    (as_posix) and with backslashes (a Windows str), each also as JSON writes
    it (escaped backslashes; non-ASCII as \\uXXXX or not) and as XML writes it
    (&amp; and the like)."""
    text = str(path).rstrip("\\/")
    windows = bool(re.match(r"^[A-Za-z]:[\\/]", text)) or text.startswith("\\\\")
    posix = text.replace("\\", "/") if windows else text
    raw = {posix, posix.replace("/", "\\")} if windows else {posix}
    variants = set(raw)
    for value in raw:
        variants.add(json.dumps(value)[1:-1])
        variants.add(json.dumps(value, ensure_ascii=False)[1:-1])
        variants.add(_xml_escape(value))
        variants.add(_xml_escape(value).replace("&apos;", "'"))  # as ElementTree writes attributes
    return sorted(variants, key=len, reverse=True)


def _windows_like(path: str) -> bool:
    return bool(re.match(r"^[A-Za-z]:[\\/]", path)) or path.startswith("\\\\")


def _prefix_pattern(variant: str) -> re.Pattern:
    # The match must end at a path separator or where the path ends, so
    # C:/work never matches C:/work2.
    flags = re.IGNORECASE if _windows_like(variant) else 0
    return re.compile(re.escape(variant) + r"(?=[\\/\"'<>\s]|$)", flags)


def tokenize_text(text: str, roots: list[tuple[str | Path, str]]) -> str:
    """Replace each root (every spelling, path_variants) by its placeholder.
    List the more specific root first (the work directory before the folder
    holding it)."""
    for root, token in roots:
        for variant in path_variants(root):
            text = _prefix_pattern(variant).sub(lambda _match, token=token: token, text)
    return text


def _xml_escape(value: str) -> str:
    return (value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


def _replace_in_strings(value, replacements: dict[str, str]):
    if isinstance(value, str):
        for token, destination in replacements.items():
            value = value.replace(token, destination)
        return value
    if isinstance(value, list):
        return [_replace_in_strings(item, replacements) for item in value]
    if isinstance(value, dict):
        return {_replace_in_strings(key, replacements): _replace_in_strings(item, replacements)
                for key, item in value.items()}
    return value


def _is_yaml(suffix: str) -> bool:
    return suffix.lower() in {".yaml", ".yml"}


def _load_yaml(text: str):
    """The YAML data, or None when it does not parse (then it is handled as text)."""
    import yaml

    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        return None


def dump_yaml(data) -> str:
    """YAML without folded lines: PyYAML folds a long plain scalar at its
    spaces, which splits a path such as C:/Users/Taro Yamada/... in two."""
    import yaml

    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=1 << 20)


def _map_strings(value, function):
    if isinstance(value, str):
        return function(value)
    if isinstance(value, list):
        return [_map_strings(item, function) for item in value]
    if isinstance(value, dict):
        return {_map_strings(key, function): _map_strings(item, function) for key, item in value.items()}
    return value


def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    if isinstance(value, dict):
        return [text for key, item in value.items() for text in (*_strings(key), *_strings(item))]
    return []


def tokenize_file_text(text: str, suffix: str, roots: list[tuple[str | Path, str]]) -> str:
    """tokenize_text for one file; YAML through its data, since a long path
    may be folded across lines there."""
    if _is_yaml(suffix):
        data = _load_yaml(text)
        if data is not None:
            mapped = _map_strings(data, lambda value: tokenize_text(value, roots))
            return text if mapped == data else dump_yaml(mapped)
    return tokenize_text(text, roots)


def detokenize_text(text: str, suffix: str, replacements: dict[str, str]) -> str:
    """Fill the placeholders with this folder's paths, escaped for the file
    type: a folder may hold spaces, '&', or Japanese."""
    if not any(token in text for token in replacements):
        return text
    suffix = suffix.lower()
    if _is_yaml(suffix):
        data = _load_yaml(text)
        if data is not None:
            return dump_yaml(_replace_in_strings(data, replacements))
    for token, destination in replacements.items():
        if suffix == ".json":
            escaped = json.dumps(destination, ensure_ascii=False)[1:-1]
        elif suffix == ".xml":
            escaped = _xml_escape(destination)
        else:
            escaped = destination
        text = text.replace(token, escaped)
    return text


def _normalize_reference(tail: str) -> str:
    tail = tail.split(" #", 1)[0].strip()
    return tail.replace("\\\\", "/").replace("\\", "/").strip("/")


def referenced_work_files(text: str, work: Path, suffix: str = "") -> set[str]:
    """Paths relative to the work directory that text names: absolute (any
    spelling) or ${repo:hakoniwa-business-pack}/work/..."""
    if _is_yaml(suffix):
        data = _load_yaml(text)
        if data is not None:
            text = "\n".join(_strings(data))
    found = set()
    patterns = [re.compile(re.escape(REPO_WORK_REFERENCE) + "(" + _PATH_TAIL + ")")]
    for variant in path_variants(work):
        flags = re.IGNORECASE if _windows_like(variant) else 0
        patterns.append(re.compile(re.escape(variant) + r"((?:[\\/])" + _PATH_TAIL + ")", flags))
    for pattern in patterns:
        for match in pattern.finditer(text):
            relative = _normalize_reference(match.group(1))
            if relative and ".." not in relative.split("/"):
                found.add(relative)
    return found


def _excluded(relative: str, patterns: list[str]) -> bool:
    parts = relative.split("/")
    prefixes = ["/".join(parts[:index]) for index in range(1, len(parts) + 1)]
    return any(fnmatch.fnmatchcase(prefix, pattern) for prefix in prefixes for pattern in patterns)


def _read_text(path: Path) -> str | None:
    if path.suffix.lower() not in TEXT_SUFFIXES:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None


# --- collect: the demo data bundle ---------------------------------------------------------

def bundle_files(spec: dict, work: Path) -> tuple[list[str], list[str]]:
    """(the files to bundle, relative to work; references to missing files)."""
    excludes = list(spec.get("exclude", []))
    selected: set[str] = set()

    def add_file(path: Path) -> None:
        relative = path.relative_to(work).as_posix()
        if not _excluded(relative, excludes):
            selected.add(relative)

    for pattern in spec["include"]:
        matches = sorted(work.glob(pattern))
        if not matches:
            raise PortableError(f"デモデータが見つかりません: {work / pattern}")
        for match in matches:
            if match.is_file():
                add_file(match)
                continue
            for directory, names, files in os.walk(match):
                here = Path(directory)
                names[:] = [name for name in names if not (here / name).is_symlink()
                            and not _excluded((here / name).relative_to(work).as_posix(), excludes)]
                for name in files:
                    add_file(here / name)
    missing: set[str] = set()
    pending = sorted(selected)
    while pending:
        path = work / pending.pop()
        text = _read_text(path)
        if text is None:
            continue
        for relative in referenced_work_files(text, work, path.suffix):
            if relative in selected or _excluded(relative, excludes):
                continue
            candidate = work / relative
            if candidate.is_file():
                selected.add(relative)
                pending.append(relative)
            elif not candidate.exists():
                missing.add(relative)
    return sorted(selected), sorted(missing)


def write_bundle(spec: dict, work: Path, workspace_root: Path, output: Path) -> dict:
    """Write the demo data bundle: text files with placeholders for the source
    paths, every other file as it is, and a manifest."""
    files, missing = bundle_files(spec, work)
    roots = [(work, WORK_TOKEN), (workspace_root, ROOT_TOKEN)]
    tokenized: list[str] = []
    leaked: list[str] = []
    total = 0
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".partial")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for relative in files:
            source = work / relative
            total += source.stat().st_size
            text = _read_text(source)
            if text is None:
                archive.write(source, relative)
                continue
            replaced = tokenize_file_text(text, source.suffix, roots)
            if replaced != text:
                tokenized.append(relative)
            if any(_prefix_pattern(variant).search(replaced)
                   for root, _ in roots for variant in path_variants(root)):
                leaked.append(relative)
            archive.writestr(relative, replaced.encode("utf-8"))
        longest = max(files, key=len, default="")
        manifest = {
            "schema_version": 1,
            "layout_version": LAYOUT_VERSION,
            "bundle_id": uuid.uuid4().hex,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "tokens": {"work": WORK_TOKEN, "root": ROOT_TOKEN},
            "files": len(files),
            "bytes": total,
            "tokenized": tokenized,
            "longest_relative": longest,
            "cities": [item for item in files if item.startswith("urban/assets/cities/")],
            "missing_references": missing,
        }
        archive.writestr(BUNDLE_MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    if leaked:
        temporary.unlink(missing_ok=True)
        raise PortableError("作成元の絶対パスが残っています: " + ", ".join(leaked))
    os.replace(temporary, output)
    return manifest


# --- collect: native runtime -----------------------------------------------------------------

def vcpkg_bin(foundation: Path = FOUNDATION) -> Path:
    toolchain = foundation / "config" / "toolchain.json"
    try:
        root = json.loads(toolchain.read_text(encoding="utf-8")).get("vcpkg_root")
    except (OSError, json.JSONDecodeError) as exc:
        raise PortableError(
            f"Foundation の toolchain を読めません: {toolchain}: {exc}（foundation.py toolchain を先に実行してください）"
        ) from exc
    if not root:
        raise PortableError(f"{toolchain} に vcpkg_root がありません")
    return Path(root) / "installed" / "x64-windows" / "bin"


def _plant_directive_from_cache(cache: Path) -> bool | None:
    if not cache.is_file():
        return None
    for line in cache.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("HAKO_URBAN_ENABLE_MIRROR:"):
            return line.split("=", 1)[1].strip().upper() in {"ON", "1", "TRUE", "YES"}
    return False


def collect_plant(source_bin: Path | None = None, destination: Path = PLANT_BIN,
                  cmake_cache: Path | None = None, windows: bool | None = None) -> Path:
    """Copy the built Urban Car plant, the DLLs next to it, and its features
    file. Road friction (demos 3-4, 3-5) needs the Plant Directive path, so
    a plant built without it is refused."""
    source_bin = source_bin or ROOT / "build" / "bin"
    cmake_cache = cmake_cache or ROOT / "build" / "CMakeCache.txt"
    plant = source_bin / _exe(PLANT_NAME, windows)
    if not plant.is_file():
        raise PortableError(
            f"Urban Car の Plant がありません: {plant}\n"
            "  cmake -S . -B build -DHAKO_URBAN_ENABLE_MIRROR=ON で組み立ててください（docs/windows-portable.md）"
        )
    features_file = source_bin / PLANT_FEATURES
    try:
        features = json.loads(features_file.read_text(encoding="utf-8")) if features_file.is_file() else None
    except (OSError, json.JSONDecodeError):
        features = None
    if not isinstance(features, dict) or not isinstance(features.get("plant_directive"), bool):
        directive = _plant_directive_from_cache(cmake_cache)
        features = {"schema_version": 1, "plant_directive": bool(directive), "source": "CMakeCache.txt"}
    if not features["plant_directive"]:
        raise PortableError(
            "Urban Car の Plant が HAKO_URBAN_ENABLE_MIRROR=OFF で組み立てられています。"
            "摩擦のデモ（3-4・3-5）が動かないので、-DHAKO_URBAN_ENABLE_MIRROR=ON で組み立て直してください"
        )
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    shutil.copy2(plant, destination / plant.name)
    for dll in sorted(source_bin.glob("*.dll")):
        shutil.copy2(dll, destination / dll.name)
    (destination / PLANT_FEATURES).write_text(json.dumps(features, indent=2) + "\n", encoding="utf-8")
    _say(f"Collected the Urban Car plant and {len(list(source_bin.glob('*.dll')))} DLLs from {source_bin}")
    return destination / plant.name


def collect_drone_dlls(source_bin: Path | None = None, destination: Path = DRONE_BIN,
                       drone_core_bin: Path = DRONE_CORE / "win") -> list[Path]:
    """glfw3.dll for Drone Core's Windows executables, unless drone-core ships it."""
    destination.mkdir(parents=True, exist_ok=True)
    collected = []
    for name in RUNTIME_DLLS:
        if (drone_core_bin / name).is_file():
            _say(f"{name}: hakoniwa-drone-core/win already has it")
            continue
        source = (source_bin or vcpkg_bin()) / name
        if not source.is_file():
            raise PortableError(f"DLL が見つかりません: {source}（vcpkg install glfw3:x64-windows）")
        shutil.copy2(source, destination / name)
        collected.append(destination / name)
        _say(f"Collected {name} from {source.parent}")
    return collected


def collect_fpv_runtime() -> int:
    """The FPV route needs hakoniwa-fpv-drone's own runtime DLLs (its collect)."""
    tool = FPV_DRONE / "tools" / "fpv_portable.py"
    if not tool.is_file():
        raise PortableError(f"hakoniwa-fpv-drone がありません: {tool}")
    return subprocess.run([sys.executable, str(tool), "collect"], cwd=FPV_DRONE, check=False).returncode


def missing_demo_data(spec: dict, work: Path) -> list[str]:
    """The demo inputs the spec names that this Workspace does not have."""
    return [pattern for pattern in spec["include"] if not any(work.glob(pattern))]


def write_demo_data(output: Path = DEMO_DATA, spec_path: Path = DEMO_SPEC, work: Path | None = None,
                    workspace_root: Path = PACKAGE_ROOT) -> dict:
    """Write the demo data bundle from this Workspace's work directory, with
    placeholders for its absolute paths (prepare fills them in)."""
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    manifest = write_bundle(spec, work or work_dir(), workspace_root, output)
    _say(f"Demo data: {manifest['files']} files, {manifest['bytes'] / 1e6:.0f} MB -> {output}")
    _say(f"  deepest: hakoniwa-business-pack\\work\\{PureWindowsPath(manifest['longest_relative'])} "
         f"({len('hakoniwa-business-pack/work/') + len(manifest['longest_relative'])} characters)")
    for relative in manifest["missing_references"]:
        _say(f"  WARN 参照先がありません（同梱しません）: work/{relative}")
    return manifest


def collect_demo_data(output: Path = DEMO_DATA, spec_path: Path = DEMO_SPEC, work: Path | None = None) -> Path:
    """The demo data bundle for the package, made from this Workspace: the
    demo Cities made here (tools/urban_demo_worlds.py) and the demo
    Compositions. A bundle left from an earlier collect is not used."""
    work = work or work_dir()
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    missing = missing_demo_data(spec, work)
    if missing:
        output.unlink(missing_ok=True)
        raise PortableError(
            "この Workspace にデモの街と Composition がそろっていません（" + ", ".join(missing[:4])
            + (" ..." if len(missing) > 4 else "") + "）。python ../hakoniwa-urban-mobility/tools/urban_demo_worlds.py "
            "build --all で作り（都庁は Environment Studio の画面でも作れます。demos/README.md）、"
            "urban_demo_worlds.py check で確かめてください"
        )
    write_demo_data(output, spec_path, work)
    return output


def collect() -> int:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    collect_plant()
    collect_drone_dlls()
    if collect_fpv_runtime() != 0:
        raise PortableError("hakoniwa-fpv-drone の collect に失敗しました")
    collect_demo_data()
    return 0


# --- doctor -------------------------------------------------------------------------------------

def foundation_python(python_root: Path) -> Path:
    """The Foundation's python.exe: the package's embeddable Python at the root, or the
    developer Workspace's venv in Scripts (as package_portable_workspace._windows_python)."""
    portable = python_root / "python.exe"
    return portable if portable.is_file() else python_root / "Scripts" / "python.exe"


def required_inputs() -> dict[str, Path]:
    install = FOUNDATION / "install"
    return {
        "Urban Car の Plant（collect 済み）": PLANT_BIN / _exe(PLANT_NAME, True),
        "Plant の MuJoCo DLL": PLANT_BIN / "mujoco.dll",
        "Plant の features ファイル": PLANT_BIN / PLANT_FEATURES,
        "Drone Core のサービス": DRONE_CORE / "win" / "win-main_hako_drone_service.exe",
        "Drone Core の visual-state publisher": DRONE_CORE / "win" / "win-drone_visual_state_publisher.exe",
        "Drone Core の MuJoCo DLL": DRONE_CORE / "vendor" / "mujoco" / "bin" / "mujoco.dll",
        "Foundation の hako-cmd": install / "bin" / "hako-cmd.exe",
        "Foundation の WebBridge": install / "bin" / "hakoniwa-pdu-web-bridge.exe",
        "Foundation の shakoc.dll": install / "bin" / "shakoc.dll",
        "Foundation の Python": foundation_python(install / "python"),
        "FPV の runtime DLL（fpv_portable.py collect）": FPV_DRONE / "build" / "portable-runtime" / "bin" / "glfw3.dll",
        "Environment Studio": ENVIRONMENT_STUDIO / "tools" / "env_studio.py",
        "hakoniwa-envsim": ENVSIM / "src" / "city_pipeline" / "gml_lod1_extract.py",
        "デモデータ（collect 済み）": DEMO_DATA,
    }


def _glfw_available() -> bool:
    return all((DRONE_CORE / "win" / name).is_file() or (DRONE_BIN / name).is_file() for name in RUNTIME_DLLS)


def missing_modules(names: tuple[str, ...] = REQUIRED_MODULES) -> list[str]:
    import importlib.util

    missing = []
    for name in names:
        try:
            if importlib.util.find_spec(name) is None:
                missing.append(name)
        except (ImportError, ValueError):
            missing.append(name)
    return missing


def doctor() -> int:
    problems = [f"{label}: {path}" for label, path in required_inputs().items() if not path.exists()]
    features = PLANT_BIN / PLANT_FEATURES
    if features.is_file():
        try:
            directive = json.loads(features.read_text(encoding="utf-8")).get("plant_directive")
        except (OSError, json.JSONDecodeError):
            directive = None
        if directive is not True:
            problems.append(f"Plant が HAKO_URBAN_ENABLE_MIRROR=ON で組み立てられていません: {features}")
    if not _glfw_available():
        problems.append(f"glfw3.dll（Drone Core 用）: {DRONE_BIN}")
    for name in missing_modules():
        problems.append(f"Foundation Python に {name} がありません（レシピの configure で入ります）")
    for line in problems:
        _say(f"MISSING {line}")
    for conflict in port_conflicts(port_table()):
        _say(f"WARN {conflict['message']}")
    if problems:
        _say("docs/windows-portable.md の手順（レシピの configure、Plant の組み立て、collect）を実行してください。")
        return 1
    _say("Urban Studio portable inputs: OK")
    return 0


# --- ports ------------------------------------------------------------------------------------------

def port_busy(port: int, host: str = "127.0.0.1", os_name: str = os.name) -> bool:
    """Whether another program has the port: something answers, or (Windows)
    an exclusive bind fails (SO_REUSEADDR would succeed there even when busy)."""
    try:
        with socket.create_connection((host, port), timeout=0.3):
            return True
    except OSError:
        pass
    if os_name != "nt":
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", -5), 1)
            probe.bind((host, port))
        except OSError:
            return True
    return False


def parse_netstat_pid(text: str, port: int) -> int | None:
    """The pid listening on port in `netstat -ano -p TCP` output."""
    for line in text.splitlines():
        fields = line.split()
        if len(fields) >= 5 and fields[0].upper() == "TCP" and fields[3].upper() == "LISTENING":
            if fields[1].rsplit(":", 1)[-1] == str(port) and fields[4].isdigit():
                return int(fields[4])
    return None


def port_owner(port: int) -> str | None:
    """The program listening on port (Windows: name and pid), best effort."""
    if os.name != "nt":
        return None
    try:
        netstat = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True,
                                 errors="replace", check=False, timeout=10, **_hidden())
        pid = parse_netstat_pid(netstat.stdout, port)
        if pid is None:
            return None
        tasklist = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True,
                                  text=True, errors="replace", check=False, timeout=10, **_hidden())
        name = tasklist.stdout.strip().split(",")[0].strip('"') if tasklist.stdout.strip().startswith('"') else ""
        return f"{name} (pid {pid})" if name else f"pid {pid}"
    except (OSError, subprocess.SubprocessError):
        return None


def port_table() -> list[dict]:
    """Every port urban.manifest.yaml lists, with its current number."""
    import urban_manifest

    rows = []
    for port_id, entry in urban_manifest.load()["ports"].items():
        number, source = urban_manifest.resolved_port(port_id)
        rows.append({"id": port_id, "port": number, "fixed": bool(entry.get("fixed")),
                     "purpose": entry.get("purpose", ""), "source": source})
    return rows


def port_conflicts(rows: list[dict], ours: set[str] = frozenset(), busy=port_busy, owner=port_owner) -> list[dict]:
    """Each port another program already uses: its id, number, and a Japanese
    message that says who has it and what to do."""
    conflicts = []
    for row in rows:
        if row["id"] in ours or not busy(row["port"]):
            continue
        who = owner(row["port"])
        by = f"{who} が" if who else "別のプログラムが"
        if row["fixed"]:
            fix = "そのプログラムを終了してください（このポートは変えられません）"
        else:
            fix = ("そのプログラムを終了するか、hakoniwa-business-pack\\work\\urban\\ports.yaml に "
                   f"「ports: {{{row['id']}: <空いている番号>}}」と書いて番号を変えてください")
        conflicts.append({"id": row["id"], "port": row["port"],
                          "message": f"ポート {row['port']}（{row['id']}: {row['purpose']}）は {by}使っています。{fix}"})
    return conflicts


def wait_for_port(port: int, timeout_sec: float = STUDIO_WAIT_SEC) -> bool:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            probe.settimeout(0.5)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.5)
    return False


# --- prepare --------------------------------------------------------------------------------------

def relocate_core_config(foundation: Path = FOUNDATION) -> Path:
    """Point core_mmap_path at this extraction's Foundation runtime directory."""
    config = foundation / "config" / "cpp_core_config.json"
    try:
        payload = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PortableError(f"Foundation の Core 設定が読めません: {config}: {exc}") from exc
    mmap = (foundation / "runtime" / "mmap").resolve()
    mmap.mkdir(parents=True, exist_ok=True)
    if payload.get("core_mmap_path") != mmap.as_posix():
        payload["core_mmap_path"] = mmap.as_posix()
        config.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return mmap


def relocate_foundation_receipts(business_pack: Path = BUSINESS_PACK) -> int:
    """The repository-owned packaging flow leaves the Foundation receipts'
    install.prefix at the build machine; Business Pack rewrites them."""
    _business_pack_tools()
    from portable_workspace_runtime import relocate_foundation_receipts as relocate

    return relocate(business_pack)


def prepare_workspace() -> int:
    """Regenerate the Workspace Python bootstrap for this folder (it registers
    the Foundation DLL directories hakopy and the Endpoint need)."""
    return subprocess.run([sys.executable, str(BUSINESS_PACK / "tools" / "workspace.py"), "prepare"],
                          cwd=BUSINESS_PACK, check=False).returncode


def read_bundle_manifest(bundle: Path) -> dict:
    try:
        with zipfile.ZipFile(bundle) as archive:
            return json.loads(archive.read(BUNDLE_MANIFEST).decode("utf-8"))
    except (OSError, KeyError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        raise PortableError(f"デモデータが壊れています: {bundle}: {exc}") from exc


# The characters a package folder may use: MuJoCo (3.13, Windows) cannot open a file
# whose path has a character outside ASCII, and a space or a shell character
# (&, %, ^, !, ...) breaks the bat entrypoints and command lines.
PACKAGE_PATH_CHARACTERS = re.compile(r"[A-Za-z0-9_.\-:\\/]")


def check_package_path(folder: Path) -> None:
    """Refuse a package folder outside letters, digits, _ . - (and the drive's : and
    the separators): the World, the Car plant and Drone Core load MJCF/MJB by path,
    and a Windows user name in Japanese puts Downloads and Desktop on such a path."""
    text = str(folder)
    others = sorted({character for character in text if not PACKAGE_PATH_CHARACTERS.fullmatch(character)})
    if others:
        shown = "、".join("空白" if character == " " else character for character in others)
        raise PortableError(
            f"展開先のフォルダのパスに、使えない文字（{shown}）が入っています: {text}\n"
            "使えるのは英数字と _ . - だけです（日本語・空白・記号は使えません。"
            "物理エンジンがファイルを開けない、または起動の bat が崩れるため）。"
            "C:\\hako のようなフォルダに展開し直してください"
            "（ユーザー名が日本語や空白入りのときは、ダウンロードやデスクトップのフォルダも使えません）"
        )


def check_path_budget(work: Path, longest_relative: str) -> None:
    """Windows MAX_PATH: the deepest demo file must fit under this folder."""
    total = len(str(work)) + 1 + len(longest_relative)
    if total > WINDOWS_MAX_PATH_CHARS:
        raise PortableError(
            f"展開先のフォルダのパスが長すぎます（いちばん深いファイルが {total} 文字、Windows の上限は "
            f"{WINDOWS_MAX_PATH_CHARS} 文字）。あと {total - WINDOWS_MAX_PATH_CHARS} 文字以上短い場所、"
            "たとえば C:\\hako に展開し直してください"
        )


def refresh_city_versions(work: Path, receipts: set[Path] | None = None) -> list[Path]:
    """A City Asset records its receipt's mtime (urban_assets.register_city);
    after unpacking or rewriting the receipt, record the new one so Urban
    Studio does not register the City again. receipts: only these (None: every
    City)."""
    import yaml

    updated = []
    for manifest in sorted((work / "urban" / "assets" / "cities").glob("*.asset.yaml")):
        data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("receipt"), str):
            continue
        receipt = Path(data["receipt"])
        if not receipt.is_file() or (receipts is not None and receipt.resolve() not in receipts):
            continue
        version = receipt.stat().st_mtime_ns
        if data.get("version") != version:
            data["version"] = version
            manifest.write_text(dump_yaml(data), encoding="utf-8")
            updated.append(manifest)
    return updated


def unpack_bundle(bundle: Path, work: Path, package_root: Path) -> dict:
    """Unpack the demo data into work, filling the placeholders for this folder."""
    manifest = read_bundle_manifest(bundle)
    replacements = {WORK_TOKEN: work.resolve().as_posix(), ROOT_TOKEN: package_root.resolve().as_posix()}
    with zipfile.ZipFile(bundle) as archive:
        for info in archive.infolist():
            if info.is_dir() or info.filename == BUNDLE_MANIFEST:
                continue
            relative = Path(info.filename)
            if relative.is_absolute() or ".." in relative.parts:
                raise PortableError(f"デモデータに不正なパスがあります: {info.filename}")
            target = work / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            data = archive.read(info)
            if relative.suffix.lower() in TEXT_SUFFIXES and info.filename in manifest.get("tokenized", ()):
                data = detokenize_text(data.decode("utf-8"), relative.suffix, replacements).encode("utf-8")
            target.write_bytes(data)
    refresh_city_versions(work)
    return manifest


SKIP_RELOCATION = frozenset({"cache", "source", "downloads", "logs", "mmap", "__pycache__"})


def relocate_tree(roots: list[Path], old_root: str, new_root: Path) -> set[Path]:
    """Rewrite the paths of a moved package folder in the text files of the
    work directory (demo data, Cities and Compositions made here, configured
    Recipes). Returns the files changed."""
    changed = set()
    replacements = {ROOT_TOKEN: new_root.resolve().as_posix()}
    for root in roots:
        if not root.is_dir():
            continue
        for directory, names, files in os.walk(root):
            names[:] = [name for name in names if name not in SKIP_RELOCATION]
            for name in files:
                path = Path(directory) / name
                text = _read_text(path)
                if text is None:
                    continue
                replaced = tokenize_file_text(text, path.suffix, [(old_root, ROOT_TOKEN)])
                if replaced == text:
                    continue
                path.write_text(detokenize_text(replaced, path.suffix, replacements), encoding="utf-8")
                changed.add(path.resolve())
    return changed


def install_demo_data(bundle: Path, work: Path, package_root: Path, os_name: str = os.name) -> str:
    """Unpack the demo data once per extraction folder; after a move, rewrite
    the paths instead. Returns what it did: current, unpacked, relocated."""
    stamp = work / STAMP
    manifest = read_bundle_manifest(bundle)
    current = str(package_root.resolve())
    try:
        state = json.loads(stamp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = {}
    same_bundle = state.get("layout_version") == LAYOUT_VERSION and state.get("bundle_id") == manifest["bundle_id"]
    if same_bundle and state.get("package_root") == current:
        return "current"
    if os_name == "nt":
        check_path_budget(work, manifest["longest_relative"])
    if same_bundle and state.get("package_root"):
        changed = relocate_tree([work / "urban", work / "recipes"], state["package_root"], package_root)
        refresh_city_versions(work, changed)
        action = "relocated"
    else:
        unpack_bundle(bundle, work, package_root)
        action = "unpacked"
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(json.dumps({"layout_version": LAYOUT_VERSION, "bundle_id": manifest["bundle_id"],
                                 "package_root": current}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return action


def prepare() -> int:
    if not portable_package():
        raise PortableError(
            "prepare は展開したパッケージの中でだけ動きます（HAKONIWA_PORTABLE_WORKSPACE=1 と "
            ".hakoniwa-repository-root）。作成元の Workspace のデータは書き換えません"
        )
    check_package_path(PACKAGE_ROOT)
    relocate_foundation_receipts()
    relocate_core_config()
    result = prepare_workspace()
    if result != 0:
        return result
    action = install_demo_data(DEMO_DATA, BUSINESS_PACK / "work", PACKAGE_ROOT)
    _say({"current": "デモデータ: このフォルダ用に準備済みです",
          "unpacked": "デモデータ: このフォルダに展開しました",
          "relocated": "デモデータ: フォルダの移動に合わせてパスを書き換えました"}[action])
    return 0


# --- start / status / stop ------------------------------------------------------------------------

def runtime_environment(environ: dict[str, str]) -> dict[str, str]:
    """The environment Urban Studio and every simulation it starts inherit:
    Drone Core's Windows executables find glfw3.dll (collected from the
    developer's vcpkg) through PATH."""
    environment = dict(environ)
    extra = [str(DRONE_BIN)] if DRONE_BIN.is_dir() else []
    if extra:
        environment["PATH"] = os.pathsep.join([*extra, environment.get("PATH", "")])
    return environment


def _studio(command: str, environment: dict[str, str] | None = None) -> int:
    return subprocess.run([sys.executable, str(ROOT / "tools" / "urban_studio.py"), command],
                          cwd=ROOT, env=environment, check=False).returncode


def _health(port: int, app: str) -> dict | None:
    from urllib.error import URLError
    from urllib.request import urlopen

    try:
        with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1.0) as response:
            data = json.loads(response.read())
    except (OSError, URLError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("app") == app else None


def _pid_alive(pid) -> bool:
    try:
        _business_pack_tools()
        from recipe.process_liveness import pid_alive

        return bool(pid_alive(pid))
    except ImportError:
        return isinstance(pid, int) and pid > 0


def running_sessions(work: Path) -> list[Path]:
    """Launcher sessions of simulations that still run (each route writes one
    under its Recipe or FPV runtime folder)."""
    sessions = []
    for pattern in ("recipes/*/runtime/launcher-session.json", "urban/fpv/*/runtime/launcher-session.json"):
        for path in sorted(work.glob(pattern)):
            try:
                session = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if session.get("state") not in {"TERMINATED", "FAILED"} and _pid_alive(session.get("pid")):
                sessions.append(path)
    return sessions


def _session_name(path: Path) -> str:
    return path.parents[1].name


def ours_running(work: Path) -> set[str]:
    """The manifest ports this package's own programs hold now."""
    import urban_manifest

    ours = set()
    if _health(urban_manifest.port("urban-studio"), "urban-studio"):
        ours.add("urban-studio")
    if _health(urban_manifest.port("environment-studio"), "environment-studio"):
        ours.add("environment-studio")
    if running_sessions(work):
        ours.update({"viewer-http", "web-bridge", "web-bridge-car", "web-bridge-fleet"})
    return ours


def start() -> int:
    import urban_manifest

    result = prepare()
    if result != 0:
        return result
    environment = runtime_environment(dict(os.environ))
    port = urban_manifest.port("urban-studio")
    url = f"http://127.0.0.1:{port}/"
    work = BUSINESS_PACK / "work"
    if _health(port, "urban-studio"):
        _say(f"Urban Studio はもう動いています: {url}")
        webbrowser.open(url)
        return 0
    conflicts = port_conflicts(port_table(), ours_running(work))
    for conflict in conflicts:
        _say(("ERROR " if conflict["id"] == "urban-studio" else "WARN ") + conflict["message"])
    if any(conflict["id"] == "urban-studio" for conflict in conflicts):
        return 1
    if conflicts:
        _say("上のポートを使うシミュレーションは起動に失敗します。Urban Studio は起動します。")
    result = _studio("start", environment)
    if result != 0:
        _say(f"Urban Studio が起動しませんでした。ログ: {work / 'urban' / 'studio' / 'studio.log'}")
        return result
    if not wait_for_port(port):
        _say(f"Urban Studio がポート {port} で応答しません。ログ: {work / 'urban' / 'studio' / 'studio.log'}")
        return 1
    _say(f"Urban Studio: {url}")
    _say("終了するときは stop-urban-studio.bat を実行してください。")
    webbrowser.open(url)
    return 0


def status() -> int:
    import urban_manifest

    work = BUSINESS_PACK / "work"
    _studio("status")
    environment_port = urban_manifest.port("environment-studio")
    if _health(environment_port, "environment-studio"):
        _say(f"Environment Studio is running: http://127.0.0.1:{environment_port}/map.html")
    else:
        _say("Environment Studio is not running")
    sessions = running_sessions(work)
    if sessions:
        for session in sessions:
            _say(f"Simulation running: {_session_name(session)} ({session})")
    else:
        _say("No simulation is running")
    for conflict in port_conflicts(port_table(), ours_running(work)):
        _say(f"WARN {conflict['message']}")
    return 0


def stop() -> int:
    import urban_manifest

    work = BUSINESS_PACK / "work"
    failed = False
    for session in running_sessions(work):
        _say(f"Stopping simulation {_session_name(session)} ...")
        result = subprocess.run([sys.executable, "-m", "hakoniwa_pdu.apps.launcher.hako_launcher_ctl",
                                 "terminate", str(session)], cwd=ROOT, check=False).returncode
        failed |= result != 0
    if _health(urban_manifest.port("environment-studio"), "environment-studio"):
        result = subprocess.run([sys.executable, str(ROOT / "tools" / "urban_city_authoring.py"), "stop"],
                                cwd=ROOT, check=False).returncode
        failed |= result != 0
    failed |= _studio("stop") != 0
    return 1 if failed else 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    result.add_argument("command", choices=("collect", "doctor", "prepare", "start", "status", "stop"))
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    commands = {"collect": collect, "doctor": doctor, "prepare": prepare, "start": start, "status": status, "stop": stop}
    return commands[args.command]()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, KeyError, ValueError, PortableError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
