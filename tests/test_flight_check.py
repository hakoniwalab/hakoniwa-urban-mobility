from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import flight_check  # noqa: E402


def wall_at_east(wall_east: float, top_m: float):
    """first_hit for a wall across the World at east = wall_east, up to top_m."""
    def first_hit(start, end):
        if (start[0] - wall_east) * (end[0] - wall_east) > 0 or start[0] == end[0]:
            return None
        t = (wall_east - start[0]) / (end[0] - start[0])
        up = start[2] + (end[2] - start[2]) * t
        if up > top_m:
            return None
        length = sum((end[i] - start[i]) ** 2 for i in range(3)) ** 0.5
        return t * length, "wall"
    return first_hit


class FlightCheckTest(unittest.TestCase):
    def line(self, *points):
        return [{"east_m": e, "north_m": n, "up_m": u} for e, n, u in points]

    def test_a_leg_through_a_wall_is_reported_where_it_meets_it(self):
        conflicts = flight_check.leg_conflicts(wall_at_east(10.0, 20.0), self.line((0, 0, 15), (20, 0, 15), (20, 10, 15)))
        self.assertEqual(len(conflicts), 1)
        self.assertEqual((conflicts[0]["from"], conflicts[0]["to"], conflicts[0]["geom"]), (1, 2, "wall"))
        self.assertEqual(conflicts[0]["at"], [10.0, 0.0, 15.0])

    def test_a_leg_just_over_a_wall_is_reported_by_the_copy_below(self):
        # 1 m over the top: the centre line is clear, the copy 1.5 m below is not.
        conflicts = flight_check.leg_conflicts(wall_at_east(10.0, 20.0), self.line((0, 0, 21), (20, 0, 21)))
        self.assertEqual(conflicts[0]["side"], "below")
        clear = flight_check.leg_conflicts(wall_at_east(10.0, 20.0), self.line((0, 0, 22), (20, 0, 22)))
        self.assertEqual(clear, [])

    def test_a_stand_on_the_ground_leaves_out_the_copy_below(self):
        ground = lambda start, end: (0.1, "ground") if min(start[2], end[2]) < 0 else None  # noqa: E731
        points = [{"east_m": 0, "north_m": 0, "up_m": 0.5, "stand": True}, {"east_m": 0, "north_m": 0, "up_m": 6.0}]
        self.assertEqual(flight_check.leg_conflicts(ground, points), [])
        points[0]["stand"] = False
        self.assertEqual(flight_check.leg_conflicts(ground, points)[0]["side"], "below")


if __name__ == "__main__":
    unittest.main()
