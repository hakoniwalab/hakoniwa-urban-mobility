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
  GET  /api/jobs/<job>?since=<line>      a command's state, output, and progress
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


def list_compositions() -> list[dict]:
    import yaml

    result = {}
    for directory, editable in ((EXAMPLE_COMPOSITIONS, False), (USER_COMPOSITIONS, True)):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.yaml")):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                continue
            # A saved Composition hides the example of the same id.
            result[path.stem] = {
                "id": path.stem,
                "world": data.get("world"),
                "vehicles": len(data.get("vehicles") or []),
                "editable": editable,
                "path": str(path),
            }
    return sorted(result.values(), key=lambda item: item["id"])


def read_composition(composition_id: str) -> dict:
    import yaml

    path = composition_path(composition_id)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {"id": composition_id, "editable": path.parent == USER_COMPOSITIONS, "composition": data}


def save_composition(composition_id: str, composition: dict) -> dict:
    """Validate and save a Composition under the user directory; return its plan."""
    import urban_simulation
    import yaml

    _check_id(composition_id)
    if not isinstance(composition, dict):
        raise StudioError("the request body must be a Composition object")
    composition = {**composition, "schema": "hakoniwa.composition/v1", "id": composition_id}
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
    composition: str
    command: str
    arguments: list[str]
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
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def start(self, composition_id: str, command: str) -> Job:
        if command not in COMMANDS:
            raise StudioError(f"unknown command {command!r}; use one of {', '.join(COMMANDS)}")
        path = composition_path(composition_id)
        with self._lock:
            for job in self.jobs.values():
                if job.composition == composition_id and job.state == "running":
                    raise StudioError(
                        f"{job.command} is still running for {composition_id} (job {job.id})",
                        HTTPStatus.CONFLICT,
                    )
            job = Job(
                id=str(next(self._ids)), composition=composition_id, command=command,
                arguments=[self.python, "-u", str(self.simulation), command, "--composition", str(path)],
            )
            self.jobs[job.id] = job
        threading.Thread(target=self._run, args=(job,), name=f"studio-job-{job.id}", daemon=True).start()
        return job

    def _run(self, job: Job) -> None:
        environment = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
        try:
            process = subprocess.Popen(
                job.arguments, cwd=ROOT, env=environment, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
            )
        except OSError as exc:
            with job.lock:
                job.lines.append(f"error: {exc}")
                job.state, job.exit_code, job.finished = "failed", -1, time.time()
            return
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
        with job.lock:
            job.exit_code = code
            job.state = "succeeded" if code == 0 else "failed"
            job.finished = time.time()

    def get(self, job_id: str) -> Job:
        job = self.jobs.get(job_id)
        if job is None:
            raise StudioError(f"job {job_id} not found", HTTPStatus.NOT_FOUND)
        return job


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
            if len(parts) == 2 and parts[0] == "compositions":
                if method == "GET":
                    return self._json(read_composition(parts[1]))
                if method == "PUT":
                    return self._json(save_composition(parts[1], self._body()))
            if len(parts) == 3 and parts[0] == "compositions":
                if method == "GET" and parts[2] == "viewer":
                    return self._json(viewer(parts[1]))
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
