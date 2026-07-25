

import unittest

from vpn_controller.app.models import PreflightResult, PreflightStatus
from vpn_controller.app.ranking import build_group_plan


def result(name: str, ip: str, latency: float, failures: int = 0):
    return PreflightResult(
        profile_name=name,
        expected_exit_ip=ip,
        actual_exit_ip=ip,
        status=PreflightStatus.SUCCESS,
        median_latency_ms=latency,
        jitter_ms=1.0,
        successful_attempts=3 - failures,
        failed_attempts=failures,
    )


class RankingTests(unittest.TestCase):
    def test_selects_only_three_best_profiles_per_group(self) -> None:
        results = [
            result("p4", "88.216.223.105", 40),
            result("p2", "88.216.223.105", 20),
            result("p1", "88.216.223.105", 10),
            result("p3", "88.216.223.105", 30),
            result("backup", "175.110.122.57", 15),
        ]

        groups, unavailable = build_group_plan(
            results=results,
            max_profiles_per_group=3,
        )

        self.assertFalse(unavailable)
        self.assertEqual(groups[0].exit_ip, "88.216.223.105")
        self.assertEqual(
            [item.profile_name for item in groups[0].selected_profiles],
            ["p1", "p2", "p3"],
        )
        self.assertEqual(
            [item.profile_name for item in groups[1].selected_profiles],
            ["backup"],
        )

    def test_failures_are_penalized_before_latency(self) -> None:
        results = [
            result("unstable-fast", "1.1.1.1", 5, failures=1),
            result("stable-slower", "1.1.1.1", 50, failures=0),
        ]
        groups, _ = build_group_plan(results, 3)
        self.assertEqual(
            groups[0].selected_profiles[0].profile_name,
            "stable-slower",
        )


if __name__ == "__main__":
    unittest.main()
