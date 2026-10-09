"""PX4 SITL on the one-Drone City route (tools/drone_px4.py, tools/px4_magnetic.py,
apps/drone/mavlink_schedule_client.py)."""

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
import mavlink_schedule_client  # noqa: E402
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
    def test_px4_starts_first_and_the_aircraft_service_keeps_the_service_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            type_config = root / "drone_config_0.json"
            type_config.write_text(json.dumps({"simulation": {"location": {"altitude": 5.5}}}), encoding="utf-8")
            launcher = root / "launcher.json"
            launcher.write_text(json.dumps({"assets": [
                {"name": "drone-service-1", "command": "mac-main_hako_drone_service",
                 "args": ["config/drone/fleets/api-current.json", "config/pdudef/drone-pdudef-current.json",
                          "--real-sleep-msec", "0"]},
                {"name": "visual-state-publisher", "depends_on": ["drone-service-1"]},
            ]}), encoding="utf-8")
            marker = {"city_world": {"origin": {"latitude": 35.1, "longitude": 138.9}},
                      "type_config": str(type_config)}
            runtime = {"px4_binary": root / "px4", "data_dir": root / "etc", "work_dir": root / "rootfs",
                       "service": root / "mac-main_hako_aircraft_service_px4"}
            with mock.patch.object(drone_px4, "prepare_runtime", return_value=runtime):
                drone_px4.patch_launcher(launcher, recipe_root=root, drone_root=root, marker=marker)
            assets = json.loads(launcher.read_text(encoding="utf-8"))["assets"]
        self.assertEqual([asset["name"] for asset in assets],
                         ["px4-sitl", "drone-service-1", "visual-state-publisher"])
        px4, service = assets[0], assets[1]
        self.assertEqual(px4["env"]["set"]["PX4_HOME_LAT"], "35.1")
        self.assertEqual(px4["env"]["set"]["PX4_HOME_ALT"], "5.5")
        self.assertEqual(px4["env"]["set"]["PX4_SIM_MODEL"], "hakoniwa_eams")
        self.assertEqual(service["command"], str(runtime["service"]))
        self.assertEqual(service["args"][:4], ["127.0.0.1", "4560", "config/drone/fleets/api-current.json",
                                               "config/pdudef/drone-pdudef-current.json"])
        self.assertEqual(service["depends_on"], ["px4-sitl"])

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


class MavlinkScheduleClientTest(unittest.TestCase):
    def make(self):
        pdu = mock.Mock()
        pdu._runtime = object()
        client = mavlink_schedule_client.MavlinkScheduleClient("udpin:127.0.0.1:14540", (10.0, 20.0, 3.5), pdu)
        fake = FakeMavlinkClient()
        client._client = fake
        return client, fake, pdu

    def test_world_ros_targets_are_sent_relative_to_the_spawn(self):
        client, fake, _ = self.make()
        # World ROS (x north, y west, z up): north 45, east 15 -> (45, -15); spawn east 10, north 20, up 3.5.
        self.assertEqual(client.goto_async(45.0, -15.0, 18.5, yaw_deg=90.0, speed_m_s=3.0,
                                           tolerance_m=0.5, timeout_sec=60.0).result(timeout=5), "there")
        self.assertEqual(fake.calls[-1][:5], ("goto", 25.0, -5.0, 15.0, 90.0))
        self.assertEqual(fake.calls[-1][5], {"speed_m_s": 3.0, "tolerance_m": 0.5, "timeout_sec": 60.0})

    def test_takeoff_climbs_by_the_height_above_the_spawn(self):
        client, fake, _ = self.make()
        self.assertEqual(client.takeoff_async(18.5).result(timeout=5), "took off")
        self.assertEqual(fake.calls, [("takeoff", 15.0)])

    def test_set_ready_and_land_go_over_mavlink_and_pdus_stay_hakoniwa(self):
        client, fake, pdu = self.make()
        client.set_ready_async().result(timeout=5)
        client.land_async(timeout_sec=0.0).result(timeout=5)
        client.get_raw_pdu("pos")
        client.poll_once()
        self.assertEqual(fake.calls, [("set_ready",), ("land", 0.0)])
        pdu.get_raw_pdu.assert_called_once_with("pos")
        self.assertIs(client._runtime, pdu._runtime)


if __name__ == "__main__":
    unittest.main()
