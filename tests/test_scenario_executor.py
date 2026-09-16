from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/car"))
MODULE_PATH = ROOT / "apps/car/scenario_executor.py"
SPEC = importlib.util.spec_from_file_location("scenario_executor", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
scenario_executor = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = scenario_executor
SPEC.loader.exec_module(scenario_executor)


class ScenarioExecutorTest(unittest.TestCase):
    def test_checked_in_convoy_is_staggered_and_valid(self):
        scenario = scenario_executor.load_scenario(
            ROOT / "recipes/scenarios/two-car-convoy.yaml"
        )
        self.assertEqual(tuple(scenario.schedules), ("Car-1", "Car-2"))
        self.assertEqual(scenario.schedules["Car-1"][0].at_sec, 0.0)
        self.assertEqual(scenario.schedules["Car-2"][0].at_sec, 0.8)
        self.assertAlmostEqual(scenario.duration_sec, 12.8)

    def test_active_command_returns_stop_during_gaps(self):
        scenario = scenario_executor.load_scenario(
            ROOT / "recipes/scenarios/two-car-convoy.yaml"
        )
        commands = scenario.schedules["Car-2"]
        self.assertIsNone(scenario_executor.active_command(commands, 0.5))
        self.assertEqual(
            scenario_executor.active_command(commands, 1.0).label,
            "follow-straight",
        )

    def test_overlapping_commands_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.yaml"
            path.write_text(
                """schema_version: 1
name: invalid
vehicles:
  - name: Car-1
    commands:
      - {at_sec: 0, duration_sec: 2, speed_m_s: 1}
      - {at_sec: 1, duration_sec: 2, speed_m_s: 1}
""",
                encoding="utf-8",
            )
            with self.assertRaises(scenario_executor.ScenarioError):
                scenario_executor.load_scenario(path)


if __name__ == "__main__":
    unittest.main()
