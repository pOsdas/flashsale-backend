import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from browser_runtime.chrome_cdp import ChromeCDPConfiguration, ChromeCDPSession


class ChromeCDPConfigurationTests(unittest.TestCase):
    def test_builds_google_chrome_command_with_cdp_profile_and_proxy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(
                "os.environ",
                {
                    "BROWSER_EXECUTABLE": "/usr/bin/google-chrome-stable",
                    "BROWSER_CDP_HOST": "127.0.0.1",
                    "BROWSER_CDP_PORT": "9222",
                    "BROWSER_RUNTIME_DIR": str(Path(temp_dir) / "runtime"),
                    "BROWSER_PROFILE_DIR": str(Path(temp_dir) / "profiles"),
                    "BROWSER_REQUIRE_PROXY": "true",
                    "BROWSER_HEADLESS": "false",
                    "OZON_BROWSER_CDP_URL": "",
                },
                clear=False,
            ):
                config = ChromeCDPConfiguration.from_environment("ozon")
                session = ChromeCDPSession(config)
                session.proxy_url = "socks5://vpn_controller:10808"
                session.session_id = "cycle-profile"
                session.profile_dir = config.profile_root / "cycle-profile"

                command = session.build_chrome_command(
                    executable="/usr/bin/google-chrome-stable"
                )

        self.assertIn(
            "--remote-debugging-address=127.0.0.1",
            command,
        )
        self.assertIn("--remote-debugging-port=9222", command)
        self.assertTrue(
            any(argument.startswith("--user-data-dir=") for argument in command)
        )
        self.assertIn(
            "--proxy-server=socks5://vpn_controller:10808",
            command,
        )
        self.assertNotIn("--headless=new", command)

    def test_proxy_is_required_before_any_browser_process_is_started(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(
                "os.environ",
                {
                    "BROWSER_RUNTIME_DIR": str(Path(temp_dir) / "runtime"),
                    "BROWSER_PROFILE_DIR": str(Path(temp_dir) / "profiles"),
                    "BROWSER_REQUIRE_PROXY": "true",
                    "BROWSER_PROXY_SERVER": "",
                    "WB_BROWSER_CDP_URL": "",
                },
                clear=False,
            ):
                session = ChromeCDPSession(
                    ChromeCDPConfiguration.from_environment("wb")
                )
                with self.assertRaisesRegex(
                    RuntimeError,
                    "Browser proxy is required",
                ):
                    session.start(proxy_url="", session_id="test")

        self.assertIsNone(session.chrome_process)
        self.assertIsNone(session.xvfb_process)


if __name__ == "__main__":
    unittest.main()
