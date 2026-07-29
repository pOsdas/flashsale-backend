import unittest

from vpn_controller.app.models import VPNProfile
from vpn_controller.app.parse_session import build_browser_profile_id


class BrowserProfileIDTests(unittest.TestCase):
    def test_profile_id_is_stable_across_cycles(self) -> None:
        profile = VPNProfile(
            name="SMART Sweden",
            expected_exit_ip="203.0.113.10",
            config={
                "outbounds": [
                    {
                        "tag": "proxy",
                        "protocol": "vless",
                        "settings": {"vnext": []},
                    }
                ]
            },
            outbound_tag="proxy",
        )

        first = build_browser_profile_id(profile)
        second = build_browser_profile_id(profile)

        self.assertEqual(first, second)
        self.assertTrue(first.lower().startswith("smart-sweden-"))

    def test_profile_id_changes_when_config_changes(self) -> None:
        first_profile = VPNProfile(
            name="SMART Sweden",
            expected_exit_ip="203.0.113.10",
            config={"outbounds": [{"tag": "proxy", "protocol": "vless"}]},
            outbound_tag="proxy",
        )
        second_profile = VPNProfile(
            name="SMART Sweden",
            expected_exit_ip="203.0.113.10",
            config={"outbounds": [{"tag": "proxy", "protocol": "trojan"}]},
            outbound_tag="proxy",
        )

        self.assertNotEqual(
            build_browser_profile_id(first_profile),
            build_browser_profile_id(second_profile),
        )


if __name__ == "__main__":
    unittest.main()
