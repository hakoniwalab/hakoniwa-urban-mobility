"""Urban Studio's lifecycle: start in the background, status, stop, the
health endpoint they check, and a clear message when the port is taken."""

import contextlib
import io
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest
from unittest import mock
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import urban_studio  # noqa: E402


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class LifecycleTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name) / "studio"

    def run_quiet(self, function, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = function(*args)
        return code, out.getvalue() + err.getvalue()

    def test_the_health_endpoint_names_this_studio(self):
        server = urban_studio.make_server(0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]
        with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=5) as response:
            health = json.loads(response.read())
        self.assertEqual((health["app"], health["port"]), ("urban-studio", port))
        self.assertEqual(urban_studio._health(port)["pid"], health["pid"])

    def test_a_port_taken_by_another_program_is_explained(self):
        with socket.socket() as other:
            other.bind(("127.0.0.1", 0))
            other.listen()
            port = other.getsockname()[1]
            code, output = self.run_quiet(urban_studio.serve, port, False)
            self.assertEqual(code, 1)
            self.assertIn(f"port {port} is in use by another program", output)
            code, output = self.run_quiet(urban_studio.start, port, False, self.state)
            self.assertEqual(code, 1)
            self.assertIn("in use by another program", output)

    def test_start_status_and_stop_in_the_background(self):
        port = free_port()
        code, output = self.run_quiet(urban_studio.start, port, False, self.state)
        self.addCleanup(lambda: self.run_quiet(urban_studio.stop, self.state))
        self.assertEqual(code, 0, output)
        self.assertIn(f"Urban Studio started: http://127.0.0.1:{port}/", output)
        state = json.loads((self.state / "studio.json").read_text(encoding="utf-8"))
        self.assertEqual(state["port"], port)
        self.assertEqual(self.run_quiet(urban_studio.status, self.state)[0], 0)
        # Started again: it says where the running one is.
        code, output = self.run_quiet(urban_studio.start, port, False, self.state)
        self.assertEqual(code, 0)
        self.assertIn("already running", output)
        # Run in a terminal on the same port: refused with how to stop the background one.
        code, output = self.run_quiet(urban_studio.serve, port, False)
        self.assertEqual(code, 1)
        self.assertIn("urban_studio.py stop", output)
        code, output = self.run_quiet(urban_studio.stop, self.state)
        self.assertEqual(code, 0)
        self.assertIsNone(urban_studio._health(port))
        self.assertFalse((self.state / "studio.json").exists())

    def test_stop_stops_an_urban_studio_started_in_a_terminal(self):
        server = urban_studio.make_server(0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        port = server.server_address[1]
        code, output = self.run_quiet(urban_studio.status, self.state, port)
        self.assertEqual(code, 0)
        self.assertIn("urban_studio.py stop", output)
        # The terminal's Urban Studio closes its port when serve_forever returns, as after Ctrl+C.
        closer = threading.Thread(target=lambda: (thread.join(), server.server_close()), daemon=True)
        closer.start()
        code, output = self.run_quiet(urban_studio.stop, self.state, port)
        self.assertEqual(code, 0, output)
        self.assertIn("Urban Studio stopped", output)
        self.assertIsNone(urban_studio._health(port))

    def test_serve_with_open_browser_opens_the_running_one(self):
        server = urban_studio.make_server(0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]
        with mock.patch.object(urban_studio.webbrowser, "open") as browser:
            code, output = self.run_quiet(urban_studio.serve, port, True)
        self.assertEqual(code, 0, output)
        browser.assert_called_once_with(f"http://127.0.0.1:{port}/")
        # open: the running one, however it was started.
        with mock.patch.object(urban_studio.webbrowser, "open") as browser:
            code, output = self.run_quiet(urban_studio.open_studio, self.state, port)
        self.assertEqual(code, 0, output)
        browser.assert_called_once_with(f"http://127.0.0.1:{port}/")

    def test_open_without_a_running_one_says_how_to_start(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        with mock.patch.object(urban_studio.webbrowser, "open") as browser:
            code, output = self.run_quiet(urban_studio.open_studio, self.state, port)
        self.assertEqual(code, 1)
        self.assertIn("start --open-browser", output)
        browser.assert_not_called()

    def test_shutdown_needs_json(self):
        from urllib.error import HTTPError
        from urllib.request import Request

        server = urban_studio.make_server(0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]
        form = Request(f"http://127.0.0.1:{port}/api/shutdown", data=b"a=1", method="POST",
                       headers={"Content-Type": "application/x-www-form-urlencoded"})
        with self.assertRaises(HTTPError) as caught:
            urlopen(form, timeout=5)
        self.assertEqual(caught.exception.code, 415)
        self.assertIsNotNone(urban_studio._health(port))


if __name__ == "__main__":
    unittest.main()
