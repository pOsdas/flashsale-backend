
import os
import stat
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from vpn_controller.app.config import Settings
from vpn_controller.app.models import PreflightStatus, VPNProfile
from vpn_controller.app.service import VPNPreflightService
from vpn_controller.app.state import ControllerState


@unittest.skipUnless(os.name == "posix", "requires POSIX process signals")
class PreflightIntegrationTests(unittest.TestCase):
    def test_checks_proxy_and_removes_runtime_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            xray_path = bin_dir / "xray"
            curl_path = bin_dir / "curl"

            xray_path.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env python3
                    import json
                    import socket
                    import sys
                    import time

                    config_path = sys.argv[sys.argv.index("-config") + 1]
                    if "-test" in sys.argv:
                        raise SystemExit(0)

                    with open(config_path, encoding="utf-8") as file:
                        config = json.load(file)
                    port = int(config["inbounds"][0]["port"])

                    with socket.socket() as server:
                        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                        server.bind(("127.0.0.1", port))
                        server.listen()
                        while True:
                            time.sleep(1)
                    """
                ),
                encoding="utf-8",
            )
            curl_path.write_text(
                "#!/bin/sh\nprintf '1.1.1.1\\n0.012\\n'\n",
                encoding="utf-8",
            )
            for executable in (xray_path, curl_path):
                executable.chmod(
                    executable.stat().st_mode
                    | stat.S_IXUSR
                    | stat.S_IXGRP
                    | stat.S_IXOTH
                )

            settings = Settings(
                subscriptions_path=root / "subscriptions.json",
                profiles_path=root / "vpn_profiles.json",
                state_path=root / "state.json",
                runs_dir=root / "runs",
                xray_binary=xray_path,
                preflight_interval_seconds=3600,
                parse_delay_seconds=600,
                preflight_attempts=3,
                preflight_min_successes=2,
                preflight_parallelism=1,
                xray_start_timeout_seconds=3.0,
                probe_timeout_seconds=2.0,
                probe_urls=("https://api.ipify.org",),
                max_profiles_per_group=3,
                retained_runs=5,
                run_preflight_on_start=True,
            )
            service = VPNPreflightService(
                settings=settings,
                state=ControllerState(settings.state_path),
            )
            profile = VPNProfile(
                name="Test profile",
                expected_exit_ip="1.1.1.1",
                config={
                    "outbounds": [
                        {"tag": "proxy", "protocol": "vless"}
                    ]
                },
            )
            profile_dir = root / "profile"
            path_value = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"

            with mock.patch.dict(os.environ, {"PATH": path_value}):
                result = service._check_profile(profile, profile_dir)

            self.assertEqual(result.status, PreflightStatus.SUCCESS)
            self.assertEqual(result.actual_exit_ip, "1.1.1.1")
            self.assertEqual(result.successful_attempts, 3)
            self.assertEqual(result.median_latency_ms, 12.0)
            self.assertFalse((profile_dir / "runtime-config.json").exists())
            self.assertTrue((profile_dir / "result.json").is_file())


if __name__ == "__main__":
    unittest.main()
