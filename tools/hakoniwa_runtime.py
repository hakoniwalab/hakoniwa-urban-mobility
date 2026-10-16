"""The Hakoniwa runtime directory (shared memory and its locks) as Urban owns it.

Up to hakoniwa-pdu's fail-stop Launcher (hakoniwa-core-cpp#62) Urban asked the
Launcher to remove the runtime files at every start (runtime.cleanup_mmap_on_start).
A process killed while it held the master lock left the counter at 0 and the next
run hung; removing the files at start hid that. The fail-stop Launcher refuses the
option: it checks the runtime (hako-cmd check) before startup and while active, stops
everything on ABORT (a dead lock owner) and keeps the files as evidence. Recovery is
an explicit operator action: `urban_mobility.py recover`.

This module picks the Launcher runtime options for the installed Launcher, and
recovers the runtime directory on request.
"""
from __future__ import annotations

import filecmp
import functools
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# The fail-stop Launcher's defaults (1.0 s / 0.8 s) leave little room for a process
# start on Windows (DLL loading, antivirus scans): a timed-out check is UNKNOWN, which
# the Launcher only reports. A slower interval and a longer timeout keep the check
# meaningful there; a dead owner still stops the run within a few seconds.
HEALTH_CHECK_INTERVAL_SEC = 2.0
HEALTH_CHECK_TIMEOUT_SEC = 5.0

# The files the Launcher's former cleanup removed (and `hako-cmd recover` removes).
RUNTIME_FILE = re.compile(r"^(mmap-0x[0-9a-fA-F]+\.bin|flock\.bin|pdu_init\.lock|pro_init\.lock)$")


class HakoniwaRuntimeError(Exception):
    """A runtime operation that cannot proceed."""


def _probe(python: Path, field: str) -> bool:
    probe = subprocess.run(
        [
            str(python),
            "-c",
            "from hakoniwa_pdu.apps.launcher.model import RuntimeOptions; "
            f"raise SystemExit(0 if {field!r} in RuntimeOptions.model_fields else 1)",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return probe.returncode == 0


@functools.lru_cache(maxsize=None)
def launcher_kind(python: str) -> str:
    """"fail-stop" (checks the runtime, refuses the cleanup), "cleanup" (the former
    Launcher with runtime.cleanup_mmap_on_start) or "none" (too old for either)."""
    path = Path(python)
    if _probe(path, "health_check_interval_sec"):
        return "fail-stop"
    if _probe(path, "cleanup_mmap_on_start"):
        return "cleanup"
    return "none"


def launcher_runtime(python: Path | None = None) -> dict:
    """The Launcher's `runtime` section for an Urban run (Urban owns the runtime
    directory exclusively)."""
    if python is None:
        import multi_car

        try:
            python = multi_car.foundation_python()
        except Exception:  # no Foundation here (a test, a portable package): this Python
            python = Path(sys.executable)
    kind = launcher_kind(str(python))
    if kind == "fail-stop":
        return {
            "health_check_interval_sec": HEALTH_CHECK_INTERVAL_SEC,
            "health_check_timeout_sec": HEALTH_CHECK_TIMEOUT_SEC,
        }
    if kind == "cleanup":
        return {"cleanup_mmap_on_start": True}
    raise HakoniwaRuntimeError(
        "the Foundation Launcher supports neither runtime checks nor "
        "runtime.cleanup_mmap_on_start; upgrade hakoniwa-pdu"
    )


def runtime_directory(config_path: Path | None = None) -> Path:
    """core_mmap_path of the Foundation's Hakoniwa config (HAKO_CONFIG_PATH)."""
    config_path = config_path or (Path(os.environ["HAKO_CONFIG_PATH"])
                                  if os.environ.get("HAKO_CONFIG_PATH") else None)
    if config_path is None or not config_path.is_file():
        raise HakoniwaRuntimeError(f"Hakoniwa config not found (HAKO_CONFIG_PATH): {config_path}")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    directory = config.get("core_mmap_path")
    if not directory:
        raise HakoniwaRuntimeError(f"{config_path} has no core_mmap_path")
    path = Path(directory)
    if not path.is_absolute():
        path = (config_path.parent / path).resolve()
    return path


def hako_cmd() -> str | None:
    return shutil.which("hako-cmd")


def hako_cmd_version(command: str) -> tuple[int, ...]:
    """hako-cmd's version ("hako-cmd version 1.0.1"); (0,) when unreadable. `check` and
    `recover` came with 1.1.0: an older hako-cmd prints its usage for any subcommand's
    --help, so asking it is no test."""
    try:
        result = subprocess.run([command, "--version"], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return (0,)
    match = re.search(r"version\s+(\d+(?:\.\d+)*)", result.stdout or "")
    return tuple(int(part) for part in match.group(1).split(".")) if match else (0,)


def _supports(command: str, subcommand: str) -> bool:
    return hako_cmd_version(command) >= (1, 1, 0)


def check(timeout_s: float = 10.0) -> dict:
    """`hako-cmd check` as a dict ({"status": "OK" | "ABORT" | "UNKNOWN", ...}); UNKNOWN
    when hako-cmd is missing or too old (no check) or its answer cannot be read."""
    command = hako_cmd()
    if command is None:
        return {"status": "UNKNOWN", "reason": "hako-cmd not found"}
    if not _supports(command, "check"):
        return {"status": "UNKNOWN", "reason": "hako-cmd is older than 1.1.0 (no check)"}
    try:
        result = subprocess.run([command, "check"], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=timeout_s, check=False)
    except subprocess.TimeoutExpired:
        return {"status": "UNKNOWN", "reason": f"hako-cmd check timed out after {timeout_s} s"}
    try:
        report = json.loads(result.stdout)
    except ValueError:
        return {"status": "UNKNOWN", "reason": "hako-cmd check gave no report (an older hako-cmd?)",
                "output": (result.stdout + result.stderr)[-500:]}
    if not isinstance(report, dict) or report.get("status") not in {"OK", "ABORT", "UNKNOWN"}:
        return {"status": "UNKNOWN", "reason": "unexpected hako-cmd check report", "report": report}
    return report


def recover(directory: Path | None = None, *, dry_run: bool = False) -> list[str]:
    """Clear the runtime directory for a new session. Only when nothing uses it: the
    caller has stopped every Urban Recipe. With a hako-cmd that has `recover` (1.1.0,
    hakoniwa-core-cpp#62) that command does it and checks the recorded processes;
    otherwise the files the former Launcher cleanup removed are removed here. Returns
    what was (or would be) removed or the command run."""
    command = hako_cmd()
    if command is not None and _supports(command, "recover"):
        if dry_run:
            return [f"{command} recover"]
        result = subprocess.run([command, "recover"], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", check=False)
        if result.returncode != 0:
            raise HakoniwaRuntimeError("hako-cmd recover refused or failed:\n"
                                + (result.stdout + result.stderr).strip())
        return [f"{command} recover"]
    directory = directory or runtime_directory()
    if not directory.is_dir():
        return []
    found = sorted((p for p in directory.iterdir()
                    if RUNTIME_FILE.match(p.name) and p.is_file() and not p.is_symlink()),
                   # The lock last: a failure on the way leaves the mmap files guarded.
                   key=lambda p: (p.name == "flock.bin", p.name))
    if not dry_run:
        for path in found:
            path.unlink()
    return [str(p) for p in found]


def stale_core_copies(launcher: Path, foundation_bin: Path) -> list[Path]:
    """DLLs next to a Launcher asset's executable that differ from the Foundation's
    file of the same name. Windows loads a DLL from the executable's directory first,
    and the Urban Car plant's build copies the Foundation DLLs there (CMakeLists.txt):
    after a Core upgrade it would run the old Core beside processes of the new one
    (mixed Core versions on one runtime directory are unsupported) until
    `urban_mobility.py configure` copies them again. Only meaningful on Windows."""
    try:
        config = json.loads(launcher.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    foundation = {p.name.lower(): p for p in foundation_bin.glob("*.dll")} if foundation_bin.is_dir() else {}
    stale = []
    directories = {Path(a["command"]).parent for a in config.get("assets", [])
                   if isinstance(a, dict) and isinstance(a.get("command"), str)}
    for directory in sorted(directories):
        if not directory.is_dir() or directory.resolve() == foundation_bin.resolve():
            continue
        for copy in sorted(directory.glob("*.dll")):
            source = foundation.get(copy.name.lower())
            if source is not None and not filecmp.cmp(copy, source, shallow=False):
                stale.append(copy)
    return stale


if __name__ == "__main__":  # a quick look: python tools/hakoniwa_runtime.py
    print(json.dumps(check(), indent=2, ensure_ascii=False))
    sys.exit(0)
