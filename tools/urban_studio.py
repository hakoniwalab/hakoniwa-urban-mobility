#!/usr/bin/env python3
"""Urban Studio: the local browser UI for Urban Compositions (issue #5).

Serves web/ and a JSON API over tools/urban_assets.py and
tools/urban_simulation.py. Long commands (configure, start, ...) run as
child processes; their output and [HAKO_PROGRESS] events are exposed as jobs
the browser polls.

Run it from the Business Pack Workspace shell so the simulations it starts
inherit the Workspace environment:

  python tools/urban_studio.py [--port 8090] [--open-browser]

API (all JSON):
  GET  /api/assets                       Asset catalog
  GET  /api/compositions                 saved and example Compositions
  GET  /api/compositions/<id>            one Composition
  PUT  /api/compositions/<id>            save a Composition; returns its plan
  GET  /api/compositions/<id>/plan       its route (tools/urban_simulation.py plan)
  POST /api/compositions/<id>/<command>  run plan|configure|start|stop|status
  GET  /api/compositions/<id>/viewer     the configured Viewer URL
  GET  /api/compositions/<id>/rtf        the latest real-time factor from the pacer log
  GET  /api/jobs/<job>?since=<line>      a command's state, output, and progress
  GET  /api/worlds/<id>                  a World's extent, map origin, and GLB URL
  GET  /api/worlds/<id>/glb              the World's display GLB
  GET  /api/worlds/<id>/height?east=&north=  ground height (terrain, roofs, obstacles)
  GET  /api/cities                       City World Web UI state and its jobs; a finished,
                                         unregistered job starts its registration
  POST /api/cities/web-ui/start          start the City World Web UI (configured once)
  POST /api/cities/web-ui/stop           stop it
  GET  /api/cache                        Urban cache (world-height, plain-world) and what
                                         prune-cache would remove; City World download
                                         sizes (read-only, from the Business Pack tool)
  POST /api/cache/prune                  run urban_assets.py prune-cache --apply
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import itertools
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlparse
import webbrowser


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
BUSINESS_PACK = WORKSPACE / "hakoniwa-business-pack"
WEB_ROOT = ROOT / "web"
USER_COMPOSITIONS = BUSINESS_PACK / "work/urban/compositions"
EXAMPLE_COMPOSITIONS = ROOT / "recipes/compositions"
SIMULATION = ROOT / "tools/urban_simulation.py"
COMMANDS = ("plan", "configure", "start", "stop", "status")
COMPOSITION_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
PROGRESS_MARKER = "[HAKO_PROGRESS] "
DEFAULT_PORT = 8090
# The Business Pack City World Web UI (tools/recipe/city_world_web_ui.py).
CITY_WEB_UI = BUSINESS_PACK / "tools/recipe/city_world_web_ui.py"
CITY_RECIPE_ROOT = BUSINESS_PACK / "work/recipes/city-world-web-ui"
CITY_WEB_PORT = 8008
URBAN_ASSETS = ROOT / "tools/urban_assets.py"
CACHE_KEY = "cache:urban"

for _path in (ROOT / "tools", BUSINESS_PACK / "tools"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))


class StudioError(RuntimeError):
    def __init__(self, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST):
        super().__init__(message)
        self.status = status


# --- Assets and Compositions ------------------------------------------------------

def asset_catalog() -> list[dict]:
    import urban_assets

    assets = []
    for asset in urban_assets.catalog().values():
        data = asset.data
        entry = {
            "id": asset.id,
            "kind": asset.kind,
            "title": data.get("title", asset.id),
            "path": str(asset.path),
        }
        if asset.kind == "city":
            # A City whose receipt is gone (its City World job was deleted
            # elsewhere) stays listed but cannot be used as a World.
            entry["available"] = urban_assets.city_receipt_available(asset)
        if asset.kind == "vehicle":
            entry.update({
                "category": asset.category,
                "simulator": asset.simulator,
                "ground_clearance_m": data.get("spawn", {}).get("ground_clearance_m"),
                "controls": {
                    name: {
                        "scope": control.get("scope", "vehicle"),
                        "params": control.get("params", {}),
                    }
                    for name, control in asset.controls().items()
                },
                "interactions": sorted(data.get("interactions", {})),
                "fleet": data.get("fleet"),
            })
        assets.append(entry)
    order = {"city": 0, "plain": 1, "vehicle": 2}
    return sorted(assets, key=lambda item: (order.get(item["kind"], 3), item["id"]))


def _check_id(composition_id: str) -> str:
    if not COMPOSITION_ID.match(composition_id):
        raise StudioError(f"a Composition id uses lowercase letters, digits, and '-': {composition_id!r}")
    return composition_id


def composition_path(composition_id: str) -> Path:
    """A saved Composition, else a tracked example of that id."""
    _check_id(composition_id)
    for directory in (USER_COMPOSITIONS, EXAMPLE_COMPOSITIONS):
        path = directory / f"{composition_id}.yaml"
        if path.is_file():
            return path
    raise StudioError(f"Composition {composition_id} not found", HTTPStatus.NOT_FOUND)


def _catalog_or_empty() -> dict:
    import urban_assets

    try:
        return urban_assets.catalog()
    except urban_assets.AssetError:
        return {}


def composition_summary(data: dict, catalog: dict) -> dict:
    """World and vehicle make-up of a Composition, for labels and the run summary.

    Unknown Asset ids are kept (their id is the title) so a broken Composition
    is still recognisable.
    """
    world_id = data.get("world")
    world = catalog.get(world_id)
    vehicles = []
    for entry in data.get("vehicles") or []:
        if not isinstance(entry, dict):
            continue
        asset = catalog.get(entry.get("asset"))
        vehicles.append({
            "name": entry.get("name"),
            "asset": entry.get("asset"),
            "title": asset.data.get("title", asset.id) if asset else entry.get("asset"),
            "control": entry.get("control"),
        })
    fleets = []
    for entry in data.get("fleets") or []:
        if not isinstance(entry, dict):
            continue
        asset = catalog.get(entry.get("asset"))
        fleets.append({
            "name": entry.get("name"),
            "asset": entry.get("asset"),
            "title": asset.data.get("title", asset.id) if asset else entry.get("asset"),
            "control": entry.get("control"),
            "count": entry.get("count"),
        })
    return {
        "world": world_id,
        "world_title": world.data.get("title", world.id) if world else world_id,
        "world_kind": world.kind if world else None,
        "vehicle_list": vehicles,
        "fleets": fleets,
    }


def list_compositions() -> list[dict]:
    import yaml

    catalog = _catalog_or_empty()
    result = {}
    for directory, editable in ((EXAMPLE_COMPOSITIONS, False), (USER_COMPOSITIONS, True)):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.yaml")):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                continue
            if not isinstance(data, dict):
                continue
            # A saved Composition hides the example of the same id.
            result[path.stem] = {
                "id": path.stem,
                "vehicles": len(data.get("vehicles") or []),
                "editable": editable,
                "path": str(path),
                "updated_at": path.stat().st_mtime,
                **composition_summary(data, catalog),
            }
    return sorted(result.values(), key=lambda item: item["id"])


def read_composition(composition_id: str) -> dict:
    import yaml

    path = composition_path(composition_id)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {"id": composition_id, "editable": path.parent == USER_COMPOSITIONS, "composition": data}


def _repo_reference(path: Path) -> str:
    """${repo:NAME}/... for a file inside a workspace repository, else the absolute path."""
    import urban_assets

    try:
        relative = path.relative_to(urban_assets.WORKSPACE.resolve())
    except ValueError:
        return path.as_posix()
    return f"${{repo:{relative.parts[0]}}}/{Path(*relative.parts[1:]).as_posix()}"


def relocate_path_params(composition: dict, catalog: dict, target_dir: Path = USER_COMPOSITIONS) -> dict:
    """Keep path params valid once the Composition is saved under target_dir.

    Relative paths resolve against the Composition file, so an example copied
    from recipes/compositions/ would point elsewhere. A relative path that
    only exists next to the examples becomes a ${repo:...} reference; a path
    that exists nowhere is rejected so the save fails instead of configure.
    """
    import urban_assets

    for group in ("vehicles", "fleets"):
        for entry in composition.get(group) or []:
            if not isinstance(entry, dict) or not isinstance(entry.get("params"), dict):
                continue
            asset = catalog.get(entry.get("asset"))
            declared = asset.controls().get(entry.get("control"), {}).get("params", {}) if asset else {}
            for name, definition in declared.items():
                value = entry["params"].get(name)
                if definition.get("type") != "path" or not isinstance(value, str) or not value:
                    continue
                if urban_assets.resolve_reference(value, target_dir).is_file():
                    continue
                example = urban_assets.resolve_reference(value, EXAMPLE_COMPOSITIONS)
                if not value.startswith("${") and not Path(value).is_absolute() and example.is_file():
                    entry["params"][name] = _repo_reference(example)
                    continue
                raise StudioError(f"{entry.get('name')} の {name} のファイルが見つかりません: {value}")
    return composition


def save_composition(composition_id: str, composition: dict) -> dict:
    """Validate and save a Composition under the user directory; return its plan."""
    import urban_simulation
    import yaml

    _check_id(composition_id)
    if not isinstance(composition, dict):
        raise StudioError("the request body must be a Composition object")
    composition = {**composition, "schema": "hakoniwa.composition/v1", "id": composition_id}
    import urban_assets

    try:
        relocate_path_params(composition, _catalog_or_empty())
    except urban_assets.AssetError as exc:  # a malformed reference such as ${runtime.*}
        raise StudioError(str(exc)) from exc
    USER_COMPOSITIONS.mkdir(parents=True, exist_ok=True)
    path = USER_COMPOSITIONS / f"{composition_id}.yaml"
    staging = path.with_suffix(".partial.yaml")
    staging.write_text(yaml.safe_dump(composition, sort_keys=False, allow_unicode=True), encoding="utf-8")
    try:
        selected = urban_simulation.plan(staging)
    except urban_simulation.SimulationError as exc:
        staging.unlink()
        raise StudioError(str(exc)) from exc
    staging.replace(path)
    # plan() read the staging file; report the saved path.
    return {**selected.to_json(), "path": str(path)}


# --- Worlds (placement view) ------------------------------------------------------------

_grounds: dict[str, tuple[object, threading.Lock, bool]] = {}
_grounds_lock = threading.Lock()


def world_receipt(world_id: str) -> tuple[object, Path]:
    """Return (World Asset, City World receipt); a plain World gets its City World job."""
    import urban_assets

    asset = urban_assets.catalog().get(world_id)
    if asset is None or asset.kind not in {"city", "plain"}:
        raise StudioError(f"World {world_id} not found", HTTPStatus.NOT_FOUND)
    if asset.kind == "city":
        if not urban_assets.city_receipt_available(asset):
            raise StudioError(
                f"City {world_id} の City World receipt がありません（ジョブが削除された可能性があります）",
                HTTPStatus.NOT_FOUND,
            )
        return asset, asset.resolve(asset.data["receipt"])
    import plain_world

    return asset, plain_world.materialize(asset.resolve(asset.data["world"]))


def world_info(world_id: str) -> dict:
    asset, receipt_path = world_receipt(world_id)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    frame = receipt["coordinate_frame"]
    info = {
        "id": world_id,
        "kind": asset.kind,
        "title": asset.data.get("title", world_id),
        "half_extent_m": frame["half_extent_m"],
        "glb": f"/api/worlds/{world_id}/glb",
        "map": asset.kind == "city",
    }
    if asset.kind == "city":
        info["origin"] = {key: frame["origin"][key] for key in ("latitude", "longitude")}
    return info


def world_glb(world_id: str) -> Path:
    _, receipt_path = world_receipt(world_id)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    path = Path(receipt["glb"]["path"])
    if not path.is_absolute():
        path = receipt_path.parent / path
    if not path.is_file():
        raise StudioError(f"World {world_id} has no GLB: {path}", HTTPStatus.NOT_FOUND)
    return path


def world_height(world_id: str, east_m: float, north_m: float) -> dict:
    """Ground height (terrain, buildings, obstacles) under a point; the World model loads once."""
    import urban_composition

    with _grounds_lock:
        entry = _grounds.get(world_id)
        if entry is None:
            _, receipt_path = world_receipt(world_id)
            try:
                import mujoco  # noqa: F401 - only to report whether rooftops count
                rooftops = True
            except ImportError:
                rooftops = False
            entry = (urban_composition.city_ground(receipt_path), threading.Lock(), rooftops)
            _grounds[world_id] = entry
    ground, lock, rooftops = entry
    with lock:  # one MuJoCo query at a time per World model
        try:
            height = ground(east_m, north_m)
        except Exception as exc:  # noqa: BLE001 - e.g. outside the World
            raise StudioError(f"no ground at east={east_m}, north={north_m}: {exc}") from exc
    return {"ground_m": round(float(height), 4), "rooftops": rooftops}


def plan_json(composition_id: str) -> dict:
    import urban_simulation

    try:
        return urban_simulation.plan(composition_path(composition_id)).to_json()
    except urban_simulation.SimulationError as exc:
        raise StudioError(str(exc)) from exc


def viewer(composition_id: str) -> dict:
    import urban_simulation

    try:
        selected = urban_simulation.plan(composition_path(composition_id))
    except urban_simulation.SimulationError as exc:
        raise StudioError(str(exc)) from exc
    return {"url": urban_simulation.viewer_url(selected)}


def realtime_factor(composition_id: str) -> dict:
    """The pacer's latest report (wall, sim, rtf), or null before one exists."""
    import urban_realtime
    import urban_simulation

    try:
        selected = urban_simulation.plan(composition_path(composition_id))
    except urban_simulation.SimulationError as exc:
        raise StudioError(str(exc)) from exc
    log = urban_simulation.pacer_log(selected)
    report = urban_realtime.latest_rtf(log)
    if report is not None:
        report["age_sec"] = round(max(0.0, time.time() - report.pop("updated")), 1)
    return {"log": str(log), "report": report}


# --- Jobs -----------------------------------------------------------------------------

def parse_progress(line: str) -> dict | None:
    """Return a [HAKO_PROGRESS] event with a percent when current/total say so."""
    if not line.startswith(PROGRESS_MARKER):
        return None
    try:
        event = json.loads(line[len(PROGRESS_MARKER):])
    except json.JSONDecodeError:
        return None
    if not isinstance(event, dict):
        return None
    current, total = event.get("current"), event.get("total")
    if isinstance(current, (int, float)) and isinstance(total, (int, float)) and total > 0:
        event["percent"] = round(100.0 * current / total, 1)
    return event


@dataclass
class Job:
    id: str
    composition: str  # the Composition id, or "city:<task>" for City tasks
    command: str
    steps: list[list[str]]  # commands run in order; the first failure stops the job
    state: str = "running"
    exit_code: int | None = None
    lines: list[str] = field(default_factory=list)
    progress: dict | None = None
    started: float = field(default_factory=time.time)
    finished: float | None = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def snapshot(self, since: int = 0) -> dict:
        with self.lock:
            return {
                "id": self.id,
                "composition": self.composition,
                "command": self.command,
                "state": self.state,
                "exit_code": self.exit_code,
                "progress": self.progress,
                "line_count": len(self.lines),
                "lines": self.lines[since:],
                "started": self.started,
                "finished": self.finished,
            }


class JobRunner:
    """Run one command at a time per Composition and keep its output."""

    def __init__(self, python: str = sys.executable, simulation: Path = SIMULATION):
        self.python = python
        self.simulation = simulation
        self.jobs: dict[str, Job] = {}
        # City registration key -> receipt mtime_ns when that registration started.
        self.city_receipt_versions: dict[str, int] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def start(self, composition_id: str, command: str) -> Job:
        if command not in COMMANDS:
            raise StudioError(f"unknown command {command!r}; use one of {', '.join(COMMANDS)}")
        path = composition_path(composition_id)
        return self.launch(composition_id, command,
                           [[self.python, "-u", str(self.simulation), command, "--composition", str(path)]])

    def launch(self, key: str, command: str, steps: list[list[str]]) -> Job:
        """Run steps as one job; one job at a time per key."""
        with self._lock:
            running = self.running(key)
            if running is not None:
                raise StudioError(
                    f"{running.command} is still running for {key} (job {running.id})",
                    HTTPStatus.CONFLICT,
                )
            job = Job(id=str(next(self._ids)), composition=key, command=command, steps=steps)
            self.jobs[job.id] = job
        threading.Thread(target=self._run, args=(job,), name=f"studio-job-{job.id}", daemon=True).start()
        return job

    def running(self, key: str) -> Job | None:
        return next((job for job in self.jobs.values() if job.composition == key and job.state == "running"), None)

    def latest(self, key: str) -> Job | None:
        return next((job for job in reversed(self.jobs.values()) if job.composition == key), None)

    def _run(self, job: Job) -> None:
        environment = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
        code = 0
        for arguments in job.steps:
            try:
                process = subprocess.Popen(
                    arguments, cwd=ROOT, env=environment, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                )
            except OSError as exc:
                with job.lock:
                    job.lines.append(f"error: {exc}")
                code = -1
                break
            assert process.stdout is not None
            for raw in process.stdout:
                line = raw.rstrip("\r\n")
                event = parse_progress(line)
                with job.lock:
                    if event is not None:
                        job.progress = event
                    else:
                        job.lines.append(line)
            code = process.wait()
            process.stdout.close()
            if code != 0:
                break
        with job.lock:
            job.exit_code = code
            job.state = "succeeded" if code == 0 else "failed"
            job.finished = time.time()

    def get(self, job_id: str) -> Job:
        job = self.jobs.get(job_id)
        if job is None:
            raise StudioError(f"job {job_id} not found", HTTPStatus.NOT_FOUND)
        return job


# --- Cities (City World Web UI) ------------------------------------------------------

def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False


def city_jobs() -> list[dict]:
    """City World Web UI jobs, newest first, and whether each is registered.

    A job is finished once its artifacts/result-manifest.json exists: the
    Worker writes it last, and a regenerated job starts from an empty folder.
    A registered City whose receipt changed since (regenerated) counts as
    unregistered, so the new World is picked up.
    """
    import urban_assets

    registered = {
        Path(asset.data["receipt"]).resolve(): asset.data.get("version")
        for asset in urban_assets.catalog().values() if asset.kind == "city"
    }
    root = CITY_RECIPE_ROOT / "runtime/jobs"
    folders = sorted(root.glob("*"), key=lambda path: path.stat().st_mtime, reverse=True) if root.is_dir() else []
    jobs = []
    for folder in folders:
        receipt = (folder / "build/world/city-world-receipt.json").resolve()
        finished = (folder / "artifacts/result-manifest.json").is_file() and receipt.is_file()
        jobs.append({
            "id": folder.name,
            "finished": finished,
            "registered": finished and registered.get(receipt) == receipt.stat().st_mtime_ns,
            "receipt": str(receipt),
        })
    return jobs


def city_state(runner: "JobRunner") -> dict:
    """The City page state; starts registering any finished, unregistered job.

    Cities registered from a City World job that was deleted in the Web UI
    are unregistered first, so the registered list follows the job list.
    """
    import urban_assets

    unregistered = urban_assets.prune_missing_cities(CITY_RECIPE_ROOT / "runtime/jobs")
    jobs = city_jobs()
    for job in jobs:
        key = f"city:{job['id']}"
        previous = runner.latest(key)
        version = Path(job["receipt"]).stat().st_mtime_ns if job["finished"] else None
        # A failed registration is shown, not retried. A succeeded one is
        # redone only for a new World: the receipt changed since (regenerated,
        # or deleted and recreated under the same id) or it was just pruned.
        retry = (
            previous is not None
            and previous.state == "succeeded"
            and (job["id"] in unregistered or runner.city_receipt_versions.get(key) != version)
        )
        if job["finished"] and not job["registered"] and (previous is None or retry):
            previous = runner.launch(key, "register", [[
                runner.python, "-u", str(URBAN_ASSETS), "register-city", "--receipt", job["receipt"],
            ]])
            runner.city_receipt_versions[key] = version
        job["registration"] = previous.snapshot() if previous else None
    web = runner.latest("city:web-ui")
    return {
        "web_ui": {
            "running": _port_open(CITY_WEB_PORT),
            "url": f"http://127.0.0.1:{CITY_WEB_PORT}/",
            "job": web.snapshot() if web else None,
        },
        "jobs": jobs,
        "unregistered": unregistered,
    }


def city_web_ui(runner: "JobRunner", command: str) -> "Job":
    if command not in {"start", "stop"}:
        raise StudioError(f"unknown City World Web UI command {command!r}", HTTPStatus.NOT_FOUND)
    steps = []
    if command == "start" and not CITY_RECIPE_ROOT.is_dir():
        steps.append([runner.python, "-u", str(CITY_WEB_UI), "configure"])  # first use only
    steps.append([runner.python, "-u", str(CITY_WEB_UI), command])
    return runner.launch("city:web-ui", command, steps)


# --- Caches ---------------------------------------------------------------------------

def city_world_cache() -> dict:
    """City World download sizes from the Business Pack tool that owns that data.

    Studio only reports them; removing them stays a Business Pack command.
    """
    command = "python tools/recipe/city_world_web_ui.py cache-clean --job-sources --source-cache"
    try:
        result = subprocess.run(
            [sys.executable, "-m", "tools.remote_operation.city_world.cache_cleanup", "status",
             "--json", "--runtime-dir", str(CITY_RECIPE_ROOT / "runtime")],
            cwd=BUSINESS_PACK, capture_output=True, text=True, encoding="utf-8", timeout=120,
        )
        report = json.loads(result.stdout) if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        report = None
    if report is None:
        return {"available": False, "command": command}
    return {
        "available": True,
        "command": command,
        "shared_cache_bytes": report["shared_cache"]["apparent_bytes"],
        "reclaimable_bytes": report["reclaimable_bytes"],
        "busy_reasons": report["busy_reasons"],
    }


def cache_state(runner: "JobRunner") -> dict:
    import urban_cache

    job = runner.latest(CACHE_KEY)
    return {
        "urban": urban_cache.plan(
            jobs_root=CITY_RECIPE_ROOT / "runtime/jobs",
            mujoco_version=urban_cache.current_mujoco_version(),
        ),
        "prune_job": job.snapshot() if job else None,
        "city_world": city_world_cache(),
    }


def prune_cache(runner: "JobRunner") -> "Job":
    """Prune the Urban cache while no Studio command (configure, registration) runs."""
    busy = [job for job in runner.jobs.values() if job.state == "running" and job.composition != CACHE_KEY]
    if busy:
        raise StudioError(
            f"wait for {busy[0].command} of {busy[0].composition} to finish before pruning the cache",
            HTTPStatus.CONFLICT,
        )
    return runner.launch(CACHE_KEY, "prune", [[runner.python, "-u", str(URBAN_ASSETS), "prune-cache", "--apply"]])


# --- HTTP -----------------------------------------------------------------------------

class StudioHandler(SimpleHTTPRequestHandler):
    runner: JobRunner

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - http.server signature
        if not self.path.startswith("/api/jobs/"):
            super().log_message(format, *args)

    def _json(self, value, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path, content_type: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(path.stat().st_size))
        self.end_headers()
        with path.open("rb") as source:
            while chunk := source.read(1 << 20):
                self.wfile.write(chunk)

    def _body(self) -> object:
        length = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(length) or b"null")
        except json.JSONDecodeError as exc:
            raise StudioError(f"invalid JSON body: {exc}") from exc

    def _api(self, method: str) -> None:
        url = urlparse(self.path)
        parts = [part for part in url.path.split("/") if part][1:]  # drop "api"
        query = parse_qs(url.query)
        try:
            if method == "GET" and parts == ["assets"]:
                return self._json(asset_catalog())
            if method == "GET" and parts == ["compositions"]:
                return self._json(list_compositions())
            if method == "GET" and len(parts) == 2 and parts[0] == "worlds":
                return self._json(world_info(parts[1]))
            if method == "GET" and len(parts) == 3 and parts[0] == "worlds" and parts[2] == "glb":
                return self._file(world_glb(parts[1]), "model/gltf-binary")
            if method == "GET" and len(parts) == 3 and parts[0] == "worlds" and parts[2] == "height":
                try:
                    east = float(query["east"][0])
                    north = float(query["north"][0])
                except (KeyError, IndexError, ValueError) as exc:
                    raise StudioError("height needs numeric east and north") from exc
                return self._json(world_height(parts[1], east, north))
            if method == "GET" and parts == ["cities"]:
                return self._json(city_state(self.runner))
            if method == "POST" and len(parts) == 3 and parts[:2] == ["cities", "web-ui"]:
                return self._json(city_web_ui(self.runner, parts[2]).snapshot(), HTTPStatus.ACCEPTED)
            if method == "GET" and parts == ["cache"]:
                return self._json(cache_state(self.runner))
            if method == "POST" and parts == ["cache", "prune"]:
                return self._json(prune_cache(self.runner).snapshot(), HTTPStatus.ACCEPTED)
            if len(parts) == 2 and parts[0] == "compositions":
                if method == "GET":
                    return self._json(read_composition(parts[1]))
                if method == "PUT":
                    return self._json(save_composition(parts[1], self._body()))
            if len(parts) == 3 and parts[0] == "compositions":
                if method == "GET" and parts[2] == "viewer":
                    return self._json(viewer(parts[1]))
                if method == "GET" and parts[2] == "rtf":
                    return self._json(realtime_factor(parts[1]))
                if method == "GET" and parts[2] == "plan":
                    return self._json(plan_json(parts[1]))
                if method == "POST":
                    job = self.runner.start(parts[1], parts[2])
                    return self._json(job.snapshot(), HTTPStatus.ACCEPTED)
            if method == "GET" and len(parts) == 2 and parts[0] == "jobs":
                since = int(query.get("since", ["0"])[0])
                return self._json(self.runner.get(parts[1]).snapshot(since))
            raise StudioError(f"no API {method} {url.path}", HTTPStatus.NOT_FOUND)
        except StudioError as exc:
            return self._json({"error": str(exc)}, exc.status)
        except Exception as exc:  # noqa: BLE001 - report instead of dropping the connection
            return self._json({"error": f"{type(exc).__name__}: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_GET(self) -> None:  # noqa: N802 - http.server naming
        if self.path.startswith("/api/"):
            return self._api("GET")
        return super().do_GET()

    def do_PUT(self) -> None:  # noqa: N802
        return self._api("PUT")

    def do_POST(self) -> None:  # noqa: N802
        return self._api("POST")


def make_server(port: int = DEFAULT_PORT, runner: JobRunner | None = None) -> ThreadingHTTPServer:
    handler = type("BoundStudioHandler", (StudioHandler,), {"runner": runner or JobRunner()})
    return ThreadingHTTPServer(("127.0.0.1", port), handler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args(argv)
    server = make_server(args.port)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Urban Studio: {url}", flush=True)
    if args.open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
