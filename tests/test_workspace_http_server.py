"""The viewer's HTTP server (tools/workspace_http_server.py): the viewer
pages load dozens of ES modules at once, from every viewer open together
(the one in Urban Studio, Viewer, Viewer with colliders); none of them may
be reset (the standard listen backlog of 5 reset most: the viewer stopped
with "error")."""

import concurrent.futures
from functools import partial
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import workspace_http_server  # noqa: E402

MODULES = 150  # about what two viewers ask for at once


class ViewerHttpServerTest(unittest.TestCase):
    def test_a_burst_of_module_requests_is_all_served(self):
        with tempfile.TemporaryDirectory() as directory:
            for number in range(MODULES):
                Path(directory, f"module{number}.js").write_text("export {};\n" * 100, encoding="utf-8")
            server = workspace_http_server.ViewerHTTPServer(
                ("127.0.0.1", 0), partial(workspace_http_server.NoCacheHandler, directory=directory))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            port = server.server_address[1]

            def get(number):
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/module{number}.js", timeout=10) as response:
                        return response.status, response.headers.get("Cache-Control")
                except OSError as exc:
                    return type(exc).__name__, None

            with concurrent.futures.ThreadPoolExecutor(MODULES) as pool:
                answers = list(pool.map(get, range(MODULES)))
        self.assertEqual([status for status, _ in answers], [200] * MODULES)
        self.assertTrue(all("no-store" in (cache or "") for _, cache in answers))


if __name__ == "__main__":
    unittest.main()
