import socket
import unittest
from unittest.mock import patch

from vpn_controller.app.models import VPNProfile
from vpn_controller.app.xray_config import build_runtime_config


class XrayConfigTests(unittest.TestCase):
    @patch("vpn_controller.app.xray_config.socket.getaddrinfo")
    def test_builds_local_socks_and_forces_selected_outbound(
        self,
        getaddrinfo_mock,
    ) -> None:
        getaddrinfo_mock.return_value = [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("188.130.209.7", 20443),
            )
        ]

        profile = VPNProfile(
            name="Profile",
            expected_exit_ip="1.1.1.1",
            config={
                "dns": {"servers": ["1.1.1.1"]},
                "routing": {
                    "domainStrategy": "AsIs",
                    "balancers": [
                        {
                            "tag": "b",
                            "selector": ["proxy"],
                        }
                    ],
                },
                "outbounds": [
                    {
                        "tag": "proxy",
                        "protocol": "vless",
                        "settings": {
                            "vnext": [
                                {
                                    "address": (
                                        "media-bs22-f53l.mx.yndx-cdn.com"
                                    ),
                                    "port": 20443,
                                    "users": [],
                                }
                            ]
                        },
                        "streamSettings": {
                            "network": "tcp",
                            "security": "reality",
                            "realitySettings": {
                                "serverName": "player.mx.yndx-cdn.com",
                            },
                        },
                    },
                    {
                        "tag": "direct",
                        "protocol": "freedom",
                    },
                ],
            },
        )

        runtime, selected = build_runtime_config(
            profile=profile,
            socks_port=12345,
            access_log_path="/tmp/access.log",
            error_log_path="/tmp/error.log",
        )

        proxy = runtime["outbounds"][0]
        server = proxy["settings"]["vnext"][0]
        reality = proxy["streamSettings"]["realitySettings"]

        self.assertEqual(selected, "proxy")
        self.assertEqual(
            runtime["inbounds"][0]["listen"],
            "127.0.0.1",
        )
        self.assertEqual(
            runtime["inbounds"][0]["port"],
            12345,
        )
        self.assertEqual(
            runtime["routing"]["rules"][0]["outboundTag"],
            "proxy",
        )
        self.assertEqual(
            runtime["routing"]["domainStrategy"],
            "AsIs",
        )
        self.assertNotIn("dns", runtime)
        self.assertIn("balancers", runtime["routing"])

        self.assertEqual(
            server["address"],
            "188.130.209.7",
        )
        self.assertEqual(
            reality["serverName"],
            "player.mx.yndx-cdn.com",
        )

        getaddrinfo_mock.assert_called_once_with(
            "media-bs22-f53l.mx.yndx-cdn.com",
            20443,
            family=socket.AF_INET,
            type=socket.SOCK_STREAM,
        )

    @patch("vpn_controller.app.xray_config.socket.getaddrinfo")
    def test_does_not_resolve_existing_ip_address(
        self,
        getaddrinfo_mock,
    ) -> None:
        profile = VPNProfile(
            name="IP profile",
            expected_exit_ip="1.1.1.1",
            config={
                "outbounds": [
                    {
                        "tag": "proxy",
                        "protocol": "vless",
                        "settings": {
                            "vnext": [
                                {
                                    "address": "178.248.238.133",
                                    "port": 10065,
                                    "users": [],
                                }
                            ]
                        },
                    }
                ],
            },
        )

        runtime, _ = build_runtime_config(
            profile=profile,
            socks_port=12345,
            access_log_path="/tmp/access.log",
            error_log_path="/tmp/error.log",
        )

        server = runtime["outbounds"][0]["settings"]["vnext"][0]

        self.assertEqual(
            server["address"],
            "178.248.238.133",
        )
        getaddrinfo_mock.assert_not_called()

    @patch("vpn_controller.app.xray_config.socket.getaddrinfo")
    def test_resolves_server_from_servers_collection(
        self,
        getaddrinfo_mock,
    ) -> None:
        getaddrinfo_mock.return_value = [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("192.0.2.10", 443),
            )
        ]

        profile = VPNProfile(
            name="Servers profile",
            expected_exit_ip="1.1.1.1",
            config={
                "outbounds": [
                    {
                        "tag": "proxy",
                        "protocol": "hysteria2",
                        "settings": {
                            "servers": [
                                {
                                    "address": "vpn.example.com",
                                    "port": 443,
                                }
                            ]
                        },
                    }
                ],
            },
        )

        runtime, _ = build_runtime_config(
            profile=profile,
            socks_port=12345,
            access_log_path="/tmp/access.log",
            error_log_path="/tmp/error.log",
        )

        server = runtime["outbounds"][0]["settings"]["servers"][0]

        self.assertEqual(
            server["address"],
            "192.0.2.10",
        )


if __name__ == "__main__":
    unittest.main()
