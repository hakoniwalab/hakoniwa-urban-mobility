#!/usr/bin/env python3
"""Serve the workspace viewer assets without stale browser caches."""

from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import urban_manifest


class ViewerHTTPServer(ThreadingHTTPServer):
    """The viewer's pages load their scripts as dozens of ES modules at once,
    from every viewer open (the one in Urban Studio, Viewer, Viewer with
    colliders). The standard listen backlog of 5 resets the connections
    beyond it (ERR_CONNECTION_RESET, the viewer stops with "error"): a long
    backlog lets them wait their turn."""

    request_queue_size = 128
    daemon_threads = True


class NoCacheHandler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=urban_manifest.port("viewer-http"))
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    handler = partial(NoCacheHandler, directory=str(args.directory.resolve()))
    server = ViewerHTTPServer((args.bind, args.port), handler)
    print(f"Serving {args.directory.resolve()} on {args.bind}:{args.port} (no-cache)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
