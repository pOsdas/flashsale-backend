

import json
import tempfile
import unittest
from pathlib import Path

from vpn_controller.app.profile_loader import load_selected_profiles


class ProfileLoaderTests(unittest.TestCase):
    def test_loads_only_configured_profiles_and_occurrence(self) -> None:
        subscriptions = {
            "profiles": [
                {
                    "name": "Profile A",
                    "config": {
                        "outbounds": [
                            {"tag": "proxy", "protocol": "vless"}
                        ]
                    },
                },
                {
                    "name": "Duplicate",
                    "config": {
                        "outbounds": [
                            {"tag": "proxy", "protocol": "vless"}
                        ]
                    },
                },
                {
                    "name": "Duplicate",
                    "config": {
                        "outbounds": [
                            {"tag": "second", "protocol": "trojan"}
                        ]
                    },
                },
            ]
        }
        selections = {
            "profiles": [
                {
                    "name": "Profile A",
                    "expected_exit_ip": "1.1.1.1",
                },
                {
                    "name": "Duplicate",
                    "expected_exit_ip": "2.2.2.2",
                    "occurrence": 2,
                },
            ]
        }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subscriptions_path = root / "subscriptions.json"
            profiles_path = root / "vpn_profiles.json"
            subscriptions_path.write_text(
                json.dumps(subscriptions), encoding="utf-8"
            )
            profiles_path.write_text(
                json.dumps(selections), encoding="utf-8"
            )

            profiles = load_selected_profiles(
                subscriptions_path=subscriptions_path,
                profiles_path=profiles_path,
            )

        self.assertEqual([item.name for item in profiles], ["Profile A", "Duplicate"])
        self.assertEqual(profiles[1].config["outbounds"][0]["tag"], "second")
        self.assertEqual(profiles[1].expected_exit_ip, "2.2.2.2")


if __name__ == "__main__":
    unittest.main()
