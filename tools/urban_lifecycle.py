#!/usr/bin/env python3
"""Recipe-local Launcher and browser lifecycle checks for Urban demos."""

from __future__ import annotations

from dataclasses import dataclass
import http.client
import json
import os
from pathlib import Path
import socket
from urllib.parse import urlsplit


class LifecycleError(RuntimeError):
    """The selected Recipe cannot safely perform the requested operation."""


@dataclass(frozen=True)
class LifecycleSpec:
    recipe_id: str
    recipe_root: Path
    launcher: Path
    session: Path
    viewer_url: str
    websocket_port: int = 8765
    ports: tuple[int, ...] = (8000, 8765, 54111)


def read_session(spec: LifecycleSpec) -> dict | None:
    if not spec.session.is_file():
        return None
    try:
        payload = json.loads(spec.session.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LifecycleError(f"invalid Launcher session: {spec.session}: {exc}") from exc
    if not isinstance(payload, dict):
        raise LifecycleError(f"Launcher session must contain an object: {spec.session}")
    launch_file = payload.get("launch_file")
    if not isinstance(launch_file, str) or Path(launch_file).resolve() != spec.launcher.resolve():
        raise LifecycleError(
            f"Launcher session does not belong to Recipe {spec.recipe_id}: "
            f"session={spec.session}, launch_file={launch_file!r}, expected={spec.launcher}"
        )
    return payload


def process_alive(pid: object) -> bool:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True


def launcher_running(spec: LifecycleSpec) -> bool:
    session = read_session(spec)
    return bool(
        session
        and session.get("state") == "RUNNING"
        and process_alive(session.get("pid"))
    )


def listening(port: int, *, host: str = "127.0.0.1", timeout: float = 0.2) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def http_ready(url: str, *, timeout: float = 1.0) -> bool:
    """Probe the local HTTP Viewer without initializing HTTPS/certificate state."""
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
            return False
        port = parsed.port or 80
        target = parsed.path or "/"
        if parsed.query:
            target += "?" + parsed.query
        connection = http.client.HTTPConnection(
            parsed.hostname,
            port,
            timeout=timeout,
        )
        try:
            connection.request("GET", target)
            response = connection.getresponse()
            return 200 <= int(response.status) < 400
        finally:
            connection.close()
    except (OSError, ValueError, http.client.HTTPException):
        return False


def preflight_start(spec: LifecycleSpec) -> None:
    if launcher_running(spec):
        session = read_session(spec) or {}
        raise LifecycleError(
            f"Recipe {spec.recipe_id} is already RUNNING "
            f"(pid={session.get('pid')}, session={spec.session})"
        )
    occupied = [port for port in spec.ports if listening(port)]
    if occupied:
        raise LifecycleError(
            "required port is already owned by another process or Recipe: "
            + ", ".join(str(port) for port in occupied)
        )


def require_viewer_ready(spec: LifecycleSpec) -> None:
    if not launcher_running(spec):
        raise LifecycleError(
            f"Recipe {spec.recipe_id} is not RUNNING: {spec.session}"
        )
    if not http_ready(spec.viewer_url):
        raise LifecycleError(
            f"Recipe {spec.recipe_id} HTTP viewer is not ready: {spec.viewer_url}"
        )


def status_report(spec: LifecycleSpec) -> dict:
    session = read_session(spec)
    running = launcher_running(spec) if session is not None else False
    http = http_ready(spec.viewer_url) if running else False
    websocket = listening(spec.websocket_port) if running else False
    return {
        "recipe_id": spec.recipe_id,
        "session": str(spec.session),
        "session_state": "NOT_CONFIGURED" if session is None else session.get("state"),
        "launcher_running": running,
        "http_ready": http,
        "websocket_listening": websocket,
        "demo_ready": running and http and websocket,
    }


def verify_stopped(spec: LifecycleSpec) -> None:
    session = read_session(spec)
    if session is not None and session.get("state") == "RUNNING" and process_alive(
        session.get("pid")
    ):
        raise LifecycleError(
            f"Recipe {spec.recipe_id} Launcher is still running: {spec.session}"
        )
    occupied = [port for port in spec.ports if listening(port)]
    if occupied:
        raise LifecycleError(
            "Recipe stopped but required ports still have listeners: "
            + ", ".join(str(port) for port in occupied)
        )
