from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import route_check  # noqa: E402

# A 10 m x 10 m building at east 20..30, north 0..10, with a courtyard 23..27 x 3..7.
BUILDING = {
    "id": "bldg_a",
    "vertices": [[20, 0], [30, 0], [30, 10], [20, 10]],
    "holes": [[[23, 3], [27, 3], [27, 7], [23, 7]]],
}


def route(*points):
    return [{"name": f"p{index + 1}", "east_m": east, "north_m": north} for index, (east, north) in enumerate(points)]


class RouteCheckTest(unittest.TestCase):
    def test_a_segment_crossing_a_building_is_reported_once(self):
        conflicts = route_check.route_conflicts(route((0, 5), (40, 5), (40, 30), (0, 30)), [BUILDING])
        self.assertEqual([(item["from"], item["to"], item["building"]) for item in conflicts], [(1, 2, "bldg_a")])
        self.assertIn(conflicts[0]["reason"], {"inside", "near_wall"})

    def test_a_segment_brushing_a_wall_is_reported_as_near_wall(self):
        # Runs 0.5 m north of the north wall: outside, but closer than half a Car.
        conflicts = route_check.route_conflicts(route((0, 10.5), (40, 10.5), (40, 30), (0, 30)), [BUILDING])
        self.assertEqual([(item["from"], item["reason"]) for item in conflicts], [(1, "near_wall")])

    def test_a_route_clear_of_buildings_has_no_conflicts(self):
        self.assertEqual(route_check.route_conflicts(route((0, 20), (40, 20), (40, 40), (0, 40)), [BUILDING]), [])

    def test_the_courtyard_is_open_ground(self):
        # A small loop inside the courtyard, over 0.8 m from every wall.
        loop = route((24, 4), (26, 4), (26, 6), (24, 6))
        self.assertEqual(route_check.route_conflicts(loop, [BUILDING]), [])

    def test_the_closing_segment_is_checked(self):
        conflicts = route_check.route_conflicts(route((0, 5), (0, 30), (40, 30), (40, 5)), [BUILDING])
        self.assertEqual([(item["from"], item["to"]) for item in conflicts], [(4, 1)])

    def test_a_wider_car_needs_more_room(self):
        # 1.0 m north of the wall: a 1.2 m wide Car fits, a 2.0 m wide one does not.
        loop = route((0, 11.0), (40, 11.0), (40, 30), (0, 30))
        self.assertEqual(route_check.route_conflicts(loop, [BUILDING], route_check.clearance_for_width(1.2)), [])
        wide = route_check.route_conflicts(loop, [BUILDING], route_check.clearance_for_width(2.0))
        self.assertEqual([(item["from"], item["reason"]) for item in wide], [(1, "near_wall")])

    def test_fewer_than_three_points_are_not_checked(self):
        self.assertEqual(route_check.route_conflicts(route((0, 5), (40, 5)), [BUILDING]), [])


if __name__ == "__main__":
    unittest.main()
