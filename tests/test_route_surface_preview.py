import math
import unittest

from tools.route_surface_preview import (
    ClosedRoute,
    RoutePoint,
    minimum_polyline_turn_radius,
)


class RouteSurfacePreviewTest(unittest.TestCase):
    def ellipse(self, count=24):
        return [
            RoutePoint(
                6.0 + 21.0 * math.cos(2.0 * math.pi * index / count),
                -14.0 + 8.0 * math.sin(2.0 * math.pi * index / count),
            )
            for index in range(count)
        ]

    def sampled_length(self, route, lateral_m):
        points = [
            route.formation_sample(route.length * index / 768, 0.0, lateral_m)[0]
            for index in range(768)
        ]
        return sum(
            math.hypot(end.east_m - start.east_m, end.north_m - start.north_m)
            for start, end in zip(points, points[1:] + points[:1])
        )

    def test_candidate_route_satisfies_turn_radius_contract(self):
        self.assertGreaterEqual(minimum_polyline_turn_radius(self.ellipse()), 3.0)

    def test_lateral_paths_are_continuous_and_ordered(self):
        route = ClosedRoute(self.ellipse())
        inner = self.sampled_length(route, 1.8)
        leader = self.sampled_length(route, 0.0)
        outer = self.sampled_length(route, -1.8)
        self.assertLess(inner, leader)
        self.assertLess(leader, outer)
        self.assertLess(abs(inner - 84.3), 0.5)
        self.assertLess(abs(outer - 106.7), 0.5)

    def test_longitudinal_offset_preserves_pair_spacing(self):
        route = ClosedRoute(self.ellipse())
        first, _ = route.formation_sample(0.0, -4.5, 1.8)
        second, _ = route.formation_sample(0.0, -9.0, 1.8)
        separation = math.hypot(
            first.east_m - second.east_m,
            first.north_m - second.north_m,
        )
        # The configured spacing is measured along the route. Its Euclidean
        # chord is slightly shorter on the east turn.
        self.assertGreater(separation, 3.8)
        self.assertLess(separation, 4.5)


if __name__ == "__main__":
    unittest.main()
