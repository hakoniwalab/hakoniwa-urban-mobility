"""The root manifest (urban.manifest.yaml) and tools/urban_manifest.py: the
manifest is consistent, ports can be changed in the documented order, and the
tools take their parts and ports from it."""

from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import urban_manifest  # noqa: E402


class ManifestTest(unittest.TestCase):
    def test_the_manifest_is_consistent(self):
        self.assertEqual(urban_manifest.check(), [])
        data = urban_manifest.load()
        self.assertEqual((data["schema"], data["id"]), ("hakoniwa.urban-manifest/v1", "hakoniwa-urban-mobility"))

    def test_the_default_ports(self):
        with mock.patch.dict("os.environ", {}, clear=False), \
                mock.patch.object(urban_manifest, "_overrides", return_value=({}, None)):
            self.assertEqual({port_id: urban_manifest.port(port_id) for port_id in urban_manifest.load()["ports"]}, {
                "urban-studio": 28090, "environment-studio": 28097, "viewer-http": 28100, "web-bridge": 28865,
                "web-bridge-car": 28866, "web-bridge-fleet": 28867})

    def test_ports_are_changed_by_the_environment_then_the_overrides_file(self):
        with tempfile.TemporaryDirectory() as directory:
            overrides = Path(directory) / "ports.yaml"
            overrides.write_text("ports:\n  web-bridge: 29865\n  viewer-http: 29100\n  environment-studio: 1\n",
                                 encoding="utf-8")
            with mock.patch.object(urban_manifest, "resolve",
                                   side_effect=lambda value: overrides if "ports.yaml" in str(value)
                                   else urban_manifest.ROOT / value), \
                    mock.patch.dict("os.environ", {"HAKONIWA_URBAN_PORT_VIEWER_HTTP": "29200"}):
                self.assertEqual(urban_manifest.resolved_port("web-bridge"), (29865, f"overrides file {overrides}"))
                self.assertEqual(urban_manifest.resolved_port("viewer-http"),
                                 (29200, "environment HAKONIWA_URBAN_PORT_VIEWER_HTTP"))
                # A fixed port belongs to another component: it does not change here.
                self.assertEqual(urban_manifest.port("environment-studio"), 28097)
            with mock.patch.dict("os.environ", {"HAKONIWA_URBAN_PORT_WEB_BRIDGE_CAR": "not-a-port"}), \
                    self.assertRaises(urban_manifest.ManifestError):
                urban_manifest.port("web-bridge-car")
        with self.assertRaises(urban_manifest.ManifestError):
            urban_manifest.port("no-such-port")

    def test_a_default_that_clashes_is_reported(self):
        data = urban_manifest.load()
        broken = {**data, "ports": {**data["ports"], "extra": {"default": 28096}, "twin": {"default": 28100}}}
        with mock.patch.object(urban_manifest, "load", return_value=broken):
            found = "\n".join(urban_manifest.check())
        self.assertIn("28096 is reserved for hakoniwa-booth-studio", found)
        self.assertIn("28100 is also ports.viewer-http", found)

    def test_paths_resolve_from_the_repository_and_the_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            business_pack, work = Path(directory) / "pack", Path(directory) / "relocated-work"
            with mock.patch.dict("os.environ", {"HAKONIWA_WORKSPACE_ACTIVE": "1",
                                                "HAKONIWA_WORKSPACE_ROOT": str(business_pack),
                                                "HAKONIWA_WORK_DIR": str(work)}):
                self.assertEqual(urban_manifest.path("assets.repository"), urban_manifest.ROOT / "assets")
                self.assertEqual(urban_manifest.resolve("${workspace}/x"), urban_manifest.ROOT.parent / "x")
                self.assertEqual(urban_manifest.resolve("${business_pack}/tools"), business_pack.resolve() / "tools")
                # ${work} is $HAKONIWA_WORK_DIR, wherever it was relocated, not ${business_pack}/work.
                self.assertEqual(urban_manifest.path("assets.user"), work.resolve() / "urban/assets")
        with self.assertRaises(urban_manifest.ManifestError):
            urban_manifest.resolve("${nowhere}/x")

    def test_outside_the_workspace_the_business_pack_paths_stop_with_the_enter_command(self):
        for environment in ({"HAKONIWA_WORKSPACE_ACTIVE": ""},
                            {"HAKONIWA_WORKSPACE_ACTIVE": "1", "HAKONIWA_WORK_DIR": "", "HAKONIWA_WORKSPACE_ROOT": ""}):
            with mock.patch.dict("os.environ", environment):
                for placeholder in ("${work}/urban/assets", "${business_pack}/tools"):
                    with self.assertRaises(urban_manifest.WorkspaceError) as raised:
                        urban_manifest.resolve(placeholder)
                    self.assertIn("python tools/workspace.py enter", str(raised.exception.code))
                    self.assertNotEqual(raised.exception.code, 0)
                # Paths of this repository and its siblings do not need the Workspace.
                self.assertEqual(urban_manifest.path("assets.repository"), urban_manifest.ROOT / "assets")

    def test_no_tool_guesses_the_business_pack_next_to_this_repository(self):
        # portable_urban_car and urban_portable work in a portable package, whose
        # layout the Business Pack packager fixes (hakoniwa-business-pack next to
        # this repository); urban_portable reads $HAKONIWA_WORK_DIR when the
        # Workspace is active.
        allowed = {"tools/portable_urban_car.py", "tools/urban_portable.py"}
        guesses = [path.relative_to(ROOT).as_posix() for folder in ("tools", "apps") for path in (ROOT / folder).rglob("*.py")
                   if "hakoniwa-business-pack/work" in path.read_text(encoding="utf-8")
                   or '/ "hakoniwa-business-pack"' in path.read_text(encoding="utf-8")]
        self.assertEqual(sorted(set(guesses) - allowed), [])


class ToolsFollowTheManifestTest(unittest.TestCase):
    def test_the_tools_take_their_parts_and_ports_from_it(self):
        import urban_assets
        import urban_composition
        import urban_simulation

        port = urban_manifest.port
        self.assertEqual(urban_composition.DEFAULT_HTTP_PORT, port("viewer-http"))
        self.assertEqual(urban_composition.DEFAULT_CAR_WEB_BRIDGE_PORT, port("web-bridge-car"))
        self.assertEqual(urban_composition.DEFAULT_INTEGRATED_WEB_BRIDGE_PORT, port("web-bridge"))
        self.assertEqual(urban_composition.DEFAULT_FLEET_WEB_BRIDGE_PORT, port("web-bridge-fleet"))
        self.assertEqual(urban_assets.USER_ASSETS, urban_manifest.path("assets.user"))
        self.assertEqual(urban_assets.CITY_WORLD_JOBS, urban_manifest.path("assets.city_world_jobs"))
        self.assertEqual(urban_simulation.MANAGED_RECIPES["car"],
                         ("recipes/usecases/urban-car-rc.yaml", "urban-car-rc", "car-rc"))
        self.assertIn(urban_manifest.value("worlds.default"), urban_assets.catalog([urban_assets.REPOSITORY_ASSETS]))


if __name__ == "__main__":
    unittest.main()
