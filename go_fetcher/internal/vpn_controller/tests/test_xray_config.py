

import unittest

from vpn_controller.app.models import VPNProfile
from vpn_controller.app.xray_config import build_runtime_config


class XrayConfigTests(unittest.TestCase):
    def test_builds_local_socks_and_forces_selected_outbound(self) -> None:
        profile = VPNProfile(
            name="Profile",
            expected_exit_ip="1.1.1.1",
            config={
                "dns": {"servers": ["1.1.1.1"]},
                "routing": {
                    "domainStrategy": "AsIs",
                    "balancers": [{"tag": "b", "selector": ["proxy"]}],
                },
                "outbounds": [
                    {"tag": "proxy", "protocol": "vless"},
                    {"tag": "direct", "protocol": "freedom"},
                ],
            },
        )

        runtime, selected = build_runtime_config(
            profile=profile,
            socks_port=12345,
            access_log_path="/tmp/access.log",
            error_log_path="/tmp/error.log",
        )

        self.assertEqual(selected, "proxy")
        self.assertEqual(runtime["inbounds"][0]["listen"], "127.0.0.1")
        self.assertEqual(runtime["inbounds"][0]["port"], 12345)
        self.assertEqual(runtime["routing"]["rules"][0]["outboundTag"], "proxy")
        self.assertEqual(runtime["routing"]["domainStrategy"], "AsIs")
        self.assertIn("dns", runtime)
        self.assertIn("balancers", runtime["routing"])


if __name__ == "__main__":
    unittest.main()
