"""PX4 SITL on the one-Drone City route (tools/drone_px4.py, tools/px4_magnetic.py,
apps/drone/drone_link.py)."""

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "apps" / "drone"))

import drone_px4  # noqa: E402
import drone_link  # noqa: E402
import px4_magnetic  # noqa: E402


TABLES = """
static constexpr float SAMPLING_RES = 10;
static constexpr const int16_t declination_table[19][37] {
%s
};
static constexpr float WMM_DECLINATION_SCALE_TO_DEGREES = 0.01f;
static constexpr const int16_t inclination_table[19][37] {
%s
};
static constexpr float WMM_INCLINATION_SCALE_TO_DEGREES = 0.01f;
static constexpr const int16_t totalintensity_table[19][37] {
%s
};
static constexpr float WMM_TOTALINTENSITY_SCALE_TO_NANOTESLA = 10.0f;
"""


def _table(value_of) -> str:
    rows = []
    for lat_index in range(19):
        values = ", ".join(str(value_of(lat_index, lon_index)) for lon_index in range(37))
        rows.append(f"\t{{ {values}, }},")
    return "\n".join(rows)


class Px4MagneticTest(unittest.TestCase):
    def write_tables(self, root: Path) -> None:
        path = root / px4_magnetic.TABLES
        path.parent.mkdir(parents=True)
        path.write_text(TABLES % (
            _table(lambda lat, lon: 100 * lon),        # declination grows with longitude
            _table(lambda lat, lon: 100 * lat),        # inclination grows with latitude
            _table(lambda lat, lon: 5000),             # 50000 nT everywhere
        ), encoding="utf-8")

    def test_the_lookup_interpolates_the_grid_as_px4_does(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_tables(root)
            field = px4_magnetic.magnetic_field(root, 35.0, 135.0)
        # latitude 35: index 12 + 0.5, longitude 135: index 31 + 0.5 (scale 0.01 deg)
        self.assertAlmostEqual(field["declination_deg"], 31.5)
        self.assertAlmostEqual(field["inclination_deg"], 12.5)
        self.assertAlmostEqual(field["intensity_nT"], 50000.0)

    def test_the_real_table_gives_the_px4_home_field(self):
        px4_root = drone_px4.px4_root()
        if not (px4_root / px4_magnetic.TABLES).is_file():
            self.skipTest(f"no PX4-Autopilot at {px4_root}")
        field = px4_magnetic.magnetic_field(px4_root, 47.641468, -122.140165)
        # Gazebo's default home field: 15.3 deg, 69.0 deg, 53045 nT (PX4's coarse table differs a little).
        self.assertAlmostEqual(field["declination_deg"], 15.3, delta=0.5)
        self.assertAlmostEqual(field["inclination_deg"], 69.0, delta=0.5)
        self.assertAlmostEqual(field["intensity_nT"], 53045.0, delta=500.0)


class PatchLauncherTest(unittest.TestCase):
    def patched(self, drone_count: int) -> tuple[list[dict], dict]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            type_config = root / "drone_config_0.json"
            type_config.write_text(json.dumps({"simulation": {"location": {"altitude": 5.5}}}), encoding="utf-8")
            fleet = root / "api-current.json"
            fleet.write_text(json.dumps({"drones": [{"name": f"Drone-{index}"} for index in range(1, drone_count + 1)]}),
                             encoding="utf-8")
            launcher = root / "launcher.json"
            launcher.write_text(json.dumps({"assets": [
                {"name": "drone-service-1", "command": "mac-main_hako_drone_service",
                 "args": ["config/drone/fleets/api-current.json", "config/pdudef/drone-pdudef-current.json",
                          "--real-sleep-msec", "0"]},
                {"name": "visual-state-publisher", "depends_on": ["drone-service-1"]},
            ]}), encoding="utf-8")
            marker = {"city_world": {"origin": {"latitude": 35.1, "longitude": 138.9}},
                      "type_config": str(type_config), "fleet_config": str(fleet)}
            runtime = {"px4_binary": root / "px4", "data_dir": root / "etc", "work_dir": root / "rootfs",
                       "service": root / "mac-main_hako_aircraft_service_px4"}
            with mock.patch.object(drone_px4, "prepare_runtime", return_value=runtime):
                drone_px4.patch_launcher(launcher, recipe_root=root, drone_root=root, marker=marker)
            return json.loads(launcher.read_text(encoding="utf-8"))["assets"], runtime

    def test_px4_starts_first_and_the_aircraft_service_keeps_the_service_name(self):
        assets, runtime = self.patched(1)
        self.assertEqual([asset["name"] for asset in assets],
                         ["px4-sitl", "drone-service-1", "visual-state-publisher"])
        px4, service = assets[0], assets[1]
        self.assertEqual(px4["env"]["set"]["PX4_HOME_LAT"], "35.1")
        self.assertEqual(px4["env"]["set"]["PX4_HOME_ALT"], "5.5")
        self.assertEqual(px4["env"]["set"]["PX4_SIM_MODEL"], "hakoniwa_eams")
        self.assertNotIn("-i", px4["args"])
        self.assertEqual(service["command"], str(runtime["service"]))
        self.assertEqual(service["args"][:4], ["127.0.0.1", "4560", "config/drone/fleets/api-current.json",
                                               "config/pdudef/drone-pdudef-current.json"])
        self.assertEqual(service["depends_on"], ["px4-sitl"])

    def test_every_drone_gets_its_own_px4_instance_and_one_aircraft_service(self):
        assets, runtime = self.patched(3)
        self.assertEqual([asset["name"] for asset in assets],
                         ["px4-sitl", "px4-sitl-1", "px4-sitl-2", "drone-service-1", "visual-state-publisher"])
        second = assets[1]
        self.assertEqual(second["args"][:2], ["-i", "1"])
        self.assertEqual(second["cwd"], str(runtime["work_dir"].with_name("px4-rootfs-1")))
        self.assertNotEqual(second["cwd"], assets[0]["cwd"])
        self.assertEqual(assets[3]["depends_on"], ["px4-sitl", "px4-sitl-1", "px4-sitl-2"])
        self.assertTrue(all(drone_px4.is_px4_asset(asset["name"]) for asset in assets[:3]))
        self.assertFalse(drone_px4.is_px4_asset("drone-service-1"))

    def test_the_type_config_gets_the_sitl_timing_and_the_px4_field(self):
        type_config = {"simulation": {"timeStep": 0.001, "location": {"latitude": 35.1, "longitude": 138.9}}}
        hexa = {"simulation": {"timeStep": 0.003, "mavlink_tx_period_msec": {"hil_sensor": 3}}}
        field = {"intensity_nT": 1.0, "declination_deg": 2.0, "inclination_deg": 3.0}
        with mock.patch.object(drone_px4.px4_magnetic, "magnetic_field", return_value=field) as lookup:
            drone_px4.apply_type_config(type_config, hexa)
        self.assertEqual(type_config["simulation"]["timeStep"], 0.003)
        self.assertEqual(type_config["simulation"]["mavlink_tx_period_msec"], {"hil_sensor": 3})
        self.assertEqual(type_config["simulation"]["location"]["magneticField"], field)
        self.assertEqual(lookup.call_args.args[1:], (35.1, 138.9))


class FakeMavlinkClient:
    def __init__(self):
        self.calls = []

    def set_ready(self):
        self.calls.append(("set_ready",))
        return "ready"

    def takeoff(self, alt_m):
        self.calls.append(("takeoff", alt_m))
        return "took off"

    def goto(self, x, y, z, yaw_deg, **kwargs):
        self.calls.append(("goto", round(x, 6), round(y, 6), round(z, 6), yaw_deg, kwargs))
        return "there"

    def land(self, timeout_sec=0.0):
        self.calls.append(("land", timeout_sec))
        return "down"


class FakeHakopy:
    """hakopy.pdu_read / pdu_write on a dict of (robot, channel) -> bytes."""

    def __init__(self):
        self.data = {}
        self.writes = []

    def pdu_read(self, robot, channel, size):
        return self.data.get((robot, channel))

    def pdu_write(self, robot, channel, data, size):
        self.writes.append((robot, channel, bytes(data), size))
        return True


def write_pdudef(root: Path, relative_types: bool = True) -> Path:
    types = root / "drone-pdutypes.json"
    types.write_text(json.dumps([
        {"name": "pos", "channel_id": 1, "pdu_size": 72},
        {"name": "velocity", "channel_id": 5, "pdu_size": 72},
        {"name": "disturb", "channel_id": 3, "pdu_size": 256},
        {"name": "status", "channel_id": 18, "pdu_size": 64},
    ]), encoding="utf-8")
    pdudef = root / "pdudef.json"
    pdudef.write_text(json.dumps({
        "paths": [{"id": "drone", "path": "drone-pdutypes.json" if relative_types else str(types)}],
        "robots": [{"name": "Drone-1", "pdutypes_id": "drone"}],
    }), encoding="utf-8")
    return pdudef


class PduAccessTest(unittest.TestCase):
    def test_channels_come_from_the_robot_pdu_types_relative_or_absolute(self):
        for relative in (True, False):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as directory:
                pdu = drone_link.PduAccess(write_pdudef(Path(directory), relative), "Drone-1", FakeHakopy())
                self.assertEqual(pdu.channels["pos"], (1, 72))
                self.assertEqual(pdu.channels["disturb"], (3, 256))

    def test_an_unknown_robot_is_an_error(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(drone_link.DroneLinkError):
                drone_link.PduAccess(write_pdudef(Path(directory)), "Drone-9", FakeHakopy())

    def test_read_and_write_go_to_the_channel(self):
        with tempfile.TemporaryDirectory() as directory:
            hakopy = FakeHakopy()
            pdu = drone_link.PduAccess(write_pdudef(Path(directory)), "Drone-1", hakopy)
            self.assertIsNone(pdu.read("pos"))
            hakopy.data[("Drone-1", 1)] = b"x" * 72
            self.assertEqual(pdu.read("pos"), bytearray(b"x" * 72))
            self.assertTrue(pdu.write("disturb", b"abc"))
            self.assertEqual(hakopy.writes, [("Drone-1", 3, b"abc", 3)])


class ContactCountTest(unittest.TestCase):
    def test_an_unwritten_status_pdu_is_unknown(self):
        pdu = mock.Mock(channels={"status": (18, 64)})
        pdu.read.return_value = bytearray(64)  # zeros: no metadata
        self.assertIsNone(drone_link.contact_count(pdu))

    def test_no_status_pdu_is_unknown(self):
        self.assertIsNone(drone_link.contact_count(None))
        self.assertIsNone(drone_link.contact_count(mock.Mock(channels={})))


class MavlinkDroneLinkTest(unittest.TestCase):
    def make(self, pdu=None):
        link = drone_link.MavlinkDroneLink("udpin:127.0.0.1:14540", (10.0, 20.0, 3.5), pdu)
        fake = FakeMavlinkClient()
        link._client = fake
        return link, fake

    def test_world_ros_targets_are_sent_relative_to_the_spawn(self):
        link, fake = self.make()
        # World ROS (x north, y west, z up): north 45, east 15 -> (45, -15); spawn east 10, north 20, up 3.5.
        self.assertEqual(link.goto_async(45.0, -15.0, 18.5, yaw_deg=90.0, speed_m_s=3.0,
                                         tolerance_m=0.5, timeout_sec=60.0).result(timeout=5), "there")
        self.assertEqual(fake.calls[-1][:5], ("goto", 25.0, -5.0, 15.0, 90.0))
        self.assertEqual(fake.calls[-1][5], {"speed_m_s": 3.0, "tolerance_m": 0.5, "timeout_sec": 60.0})

    def test_takeoff_climbs_by_the_height_above_the_spawn(self):
        link, fake = self.make()
        self.assertEqual(link.takeoff_async(18.5).result(timeout=5), "took off")
        self.assertEqual(fake.calls, [("takeoff", 15.0)])

    def test_set_ready_and_land_go_over_mavlink(self):
        link, fake = self.make()
        link.set_ready_async().result(timeout=5)
        link.land_async(timeout_sec=0.0).result(timeout=5)
        link.poll_once()
        self.assertEqual(fake.calls, [("set_ready",), ("land", 0.0)])

    def test_the_state_comes_from_mavlink_in_the_world_frame(self):
        link, fake = self.make()
        state = mock.Mock(x=5.0, y=-2.0, z=-10.0, vx=3.0, vy=0.0, vz=0.0, yaw=0.5)
        fake.link = mock.Mock(state=state)
        fake.link.has_position.return_value = True
        with mock.patch.object(drone_link, "contact_count", return_value=4):
            result = link.state()
        # NED north 5, east -2, down -10 from the spawn (east 10, north 20, up 3.5).
        self.assertEqual(result["enu"], (8.0, 25.0, 13.5))
        self.assertEqual(result["yaw_rad"], -0.5)
        self.assertAlmostEqual(result["speed"], 3.0)
        self.assertAlmostEqual(result["heading_deg"], 90.0)  # moving north: Urban yaw 90
        self.assertEqual(result["collisions"], 4)
        fake.link.pump.assert_called_once_with(0.0)

    def test_no_state_before_the_connection_opens(self):
        link = drone_link.MavlinkDroneLink("udpin:127.0.0.1:14540", (0.0, 0.0, 0.0), None)
        with self.assertRaises(drone_link.DroneLinkError):
            link.state()

    def test_a_px4_that_never_answers_says_how_to_recover(self):
        def silent(**kwargs):
            raise TimeoutError("no heartbeat")
        link = drone_link.MavlinkDroneLink("udpin:127.0.0.1:14541", (0.0, 0.0, 0.0), None,
                                           autopilot="Drone-2: PX4 SITL (px4-sitl-1)")
        with mock.patch.object(drone_link, "_import_mavlink_client", return_value=silent):
            with self.assertRaisesRegex(drone_link.DroneLinkError,
                                        r"Drone-2: PX4 SITL \(px4-sitl-1\) did not open .*14541.*known PX4 SITL startup bug.*start it again"):
                link.set_ready_async().result(timeout=5)

    def test_the_disturbance_is_written_to_the_disturb_pdu(self):
        pdu = mock.Mock()
        link, _ = self.make(pdu)
        with mock.patch.object(drone_link, "write_disturbance") as write:
            link.send_disturbance("wind", {0: 0.0})
        write.assert_called_once_with(pdu, "wind", {0: 0.0})

    def test_the_disturbance_needs_a_pdu_definition(self):
        link, _ = self.make(None)
        with self.assertRaises(drone_link.DroneLinkError):
            link.send_disturbance(None, {0: 0.0})


class RpcDroneLinkTest(unittest.TestCase):
    def test_commands_go_to_rpc_and_the_state_comes_from_the_pdus(self):
        rpc = mock.Mock()
        pdu = mock.Mock()
        pdu.read.side_effect = lambda name: {"pos": b"p", "velocity": b"v"}.get(name)
        pose = mock.Mock(linear=mock.Mock(x=20.0, y=-10.0, z=5.0), angular=mock.Mock(z=0.3))
        velocity = mock.Mock(linear=mock.Mock(x=0.0, y=-2.0, z=0.0))
        link = drone_link.RpcDroneLink(rpc, pdu)
        link.goto_async(1.0, 2.0, 3.0, yaw_deg=4.0, speed_m_s=5.0)
        rpc.goto_async.assert_called_once_with(1.0, 2.0, 3.0, yaw_deg=4.0, speed_m_s=5.0)
        with mock.patch.object(drone_link, "_twist", side_effect=lambda raw: pose if raw == b"p" else velocity), \
                mock.patch.object(drone_link, "contact_count", return_value=None):
            state = link.state()
        self.assertEqual(state["enu"], (10.0, 20.0, 5.0))  # ROS (north, west, up) -> ENU
        self.assertEqual(state["yaw_rad"], 0.3)
        self.assertAlmostEqual(state["speed"], 2.0)
        self.assertAlmostEqual(state["heading_deg"], 0.0)  # moving east: Urban yaw 0

    def test_no_state_without_a_pos_pdu(self):
        pdu = mock.Mock()
        pdu.read.return_value = None
        with self.assertRaises(drone_link.DroneLinkError):
            drone_link.RpcDroneLink(mock.Mock(), pdu).state()


if __name__ == "__main__":
    unittest.main()
