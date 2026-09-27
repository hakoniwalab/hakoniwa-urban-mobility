from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import urban_realtime  # noqa: E402


class LatestRtfTest(unittest.TestCase):
    def log(self, text: str) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "urban-realtime-pacer.out"
        path.write_text(text, encoding="utf-8")
        return path

    def test_the_last_report_wins_and_carries_the_headroom(self):
        report = urban_realtime.latest_rtf(self.log(
            "[pacer] manual timing control started\n"
            "[pacer] wall=2.0s sim=2.0s rtf=1.000 idle=64%\n"
            "noise\n"
            "[pacer] wall=4.0s sim=3.9s rtf=0.975 idle=3%\n"
        ))
        self.assertEqual((report["wall_sec"], report["sim_sec"], report["rtf"], report["idle_percent"]),
                         (4.0, 3.9, 0.975, 3.0))

    def test_older_reports_have_no_headroom(self):
        report = urban_realtime.latest_rtf(self.log("[pacer] wall=25.2s sim=25.2s rtf=1.000\n"))
        self.assertIsNone(report["idle_percent"])

    def test_no_report_or_no_log(self):
        self.assertIsNone(urban_realtime.latest_rtf(self.log("[pacer] registered\n")))
        self.assertIsNone(urban_realtime.latest_rtf(Path("does-not-exist.out")))


if __name__ == "__main__":
    unittest.main()
