
import unittest
from collections import Counter
from pathlib import Path

from vpn_controller.app.profile_loader import load_json, load_selections


class ProfileSelectionConfigTests(unittest.TestCase):
    def test_contains_nine_primary_and_one_backup_profile(self) -> None:
        config_path = (
            Path(__file__).resolve().parents[1] / "vpn_profiles.json"
        )
        selections = [
            selection
            for selection in load_selections(load_json(config_path))
            if selection.enabled
        ]

        self.assertEqual(len(selections), 10)
        self.assertEqual(
            Counter(item.expected_exit_ip for item in selections),
            Counter(
                {
                    "88.216.223.105": 9,
                    "175.110.122.57": 1,
                }
            ),
        )
        self.assertEqual(
            [item.name for item in selections],
            [
                "🇷🇺 SMART-Россия",
                "🇸🇪 #12 LTE Универсальный",
                "🇩🇪 #43 LTE Универсальный",
                "🇳🇱 #40 LTE Универсальный",
                "🇳🇱 #39 LTE Универсальный",
                "🇸🇪 #15 LTЕ Универсальный",
                "🇸🇪 SМART-Швеция [HYS2]",
                "🇩🇪 BRIDGE-Германия",
                "🇷🇺 SМART-Россия [HYS2]",
                "🇳🇱 #24 LTE Универсальный",
            ],
        )


if __name__ == "__main__":
    unittest.main()
