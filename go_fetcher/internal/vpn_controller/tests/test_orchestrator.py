
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from vpn_controller.app.config import Settings
from vpn_controller.app.models import (
    ParseAttemptStatus,
    PreflightPlan,
    PreflightResult,
    PreflightStatus,
    ProfileGroupPlan,
    VPNProfile,
)
from vpn_controller.app.orchestrator import (
    GatewayUnavailableError,
    VPNParseOrchestrator,
)
from vpn_controller.app.parse_session import ActiveSessionSnapshot
from vpn_controller.app.state import ControllerState
from vpn_controller.app.worker_client import (
    WorkerRequestError,
    WorkerResponse,
)


class FakeSession:
    def __init__(self) -> None:
        self.profile_name = ""
        self.cycle_id = ""
        self.expected_exit_ip = ""
        self.stop_reasons: list[str] = []

    def activate(self, *, profile, cycle_id):
        self.profile_name = profile.name
        self.cycle_id = cycle_id
        self.expected_exit_ip = profile.expected_exit_ip
        return ActiveSessionSnapshot(
            cycle_id=cycle_id,
            profile_name=profile.name,
            expected_exit_ip=profile.expected_exit_ip,
            actual_exit_ip=profile.expected_exit_ip,
            proxy_url="socks5://vpn_controller:10808",
            browser_session_id=f"session-{profile.name}",
            running=True,
            last_used_at=1.0,
        )

    def snapshot(self):
        return ActiveSessionSnapshot(
            cycle_id=self.cycle_id,
            profile_name=self.profile_name,
            expected_exit_ip=(
                self.expected_exit_ip if self.profile_name else ""
            ),
            actual_exit_ip=(
                self.expected_exit_ip if self.profile_name else ""
            ),
            proxy_url=(
                "socks5://vpn_controller:10808"
                if self.profile_name
                else ""
            ),
            browser_session_id=(
                f"session-{self.profile_name}"
                if self.profile_name
                else ""
            ),
            running=bool(self.profile_name),
            last_used_at=1.0 if self.profile_name else 0.0,
        )

    def touch(self):
        return None

    def stop(self, reason="manual"):
        self.stop_reasons.append(reason)
        self.profile_name = ""
        self.cycle_id = ""
        self.expected_exit_ip = ""

    def stop_if_idle(self):
        return False


class FakeWorkerClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.payloads = []

    def post(self, *, marketplace, path, payload):
        self.payloads.append((marketplace, path, payload))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class OrchestratorTests(unittest.TestCase):
    def make_settings(self, root: Path) -> Settings:
        return Settings(
            subscriptions_path=root / "subscriptions.json",
            profiles_path=root / "profiles.json",
            state_path=root / "state.json",
            runs_dir=root / "runs",
            xray_binary=root / "xray",
            preflight_interval_seconds=3600,
            parse_delay_seconds=600,
            preflight_attempts=3,
            preflight_min_successes=2,
            preflight_parallelism=2,
            xray_start_timeout_seconds=20.0,
            probe_timeout_seconds=10.0,
            probe_urls=("https://api.ipify.org",),
            max_profiles_per_group=3,
            retained_runs=5,
            run_preflight_on_start=True,
            require_parse_ready=True,
        )

    @staticmethod
    def make_plan() -> PreflightPlan:
        now = datetime.now(timezone.utc)
        selected = [
            PreflightResult(
                profile_name=name,
                expected_exit_ip="88.216.223.105",
                actual_exit_ip="88.216.223.105",
                status=PreflightStatus.SUCCESS,
                median_latency_ms=float(index),
            )
            for index, name in enumerate(
                ("profile-a", "profile-b", "profile-c"),
                start=1,
            )
        ]
        return PreflightPlan(
            cycle_id="cycle-1",
            cycle_started_at=(now - timedelta(minutes=11)).isoformat(),
            completed_at=(now - timedelta(minutes=10)).isoformat(),
            parse_ready_at=(now - timedelta(minutes=1)).isoformat(),
            next_preflight_at=(now + timedelta(minutes=49)).isoformat(),
            groups=[
                ProfileGroupPlan(
                    exit_ip="88.216.223.105",
                    ranked_profiles=selected,
                    selected_profiles=selected,
                )
            ],
            unavailable_profiles=[],
        )

    @staticmethod
    def make_profiles() -> dict[str, VPNProfile]:
        return {
            name: VPNProfile(
                name=name,
                expected_exit_ip="88.216.223.105",
                config={"outbounds": [{"protocol": "vless"}]},
            )
            for name in ("profile-a", "profile-b", "profile-c")
        }

    def make_orchestrator(self, root: Path, responses):
        settings = self.make_settings(root)
        state = ControllerState(settings.state_path)
        state.set_plan(self.make_plan())
        session = FakeSession()
        worker = FakeWorkerClient(responses)
        orchestrator = VPNParseOrchestrator(
            settings=settings,
            state=state,
            session=session,
            worker_client=worker,
        )
        profiles = self.make_profiles()
        orchestrator._load_profile_map = lambda: profiles
        return orchestrator, state, session, worker

    def test_chrome_cdp_startup_failure_is_worker_unavailable(self):
        response = WorkerResponse(
            status_code=500,
            body=(
                b'{"error":"Google Chrome CDP endpoint did not become ready"}'
            ),
            content_type="application/json",
        )

        status, effective = VPNParseOrchestrator._classify_worker_response(
            marketplace="ozon",
            response=response,
        )

        self.assertEqual(status, ParseAttemptStatus.WORKER_UNAVAILABLE)
        self.assertEqual(effective, 500)

    def test_wb_nested_status_is_classified(self):
        response = WorkerResponse(
            status_code=200,
            body=b'{"status_code":403,"body":"forbidden"}',
            content_type="application/json",
        )
        status, effective = VPNParseOrchestrator._classify_worker_response(
            marketplace="wb",
            response=response,
        )
        self.assertEqual(status, ParseAttemptStatus.MARKETPLACE_REJECTED)
        self.assertEqual(effective, 403)

    def test_retries_next_profile_and_keeps_successful_session(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            orchestrator, _, session, worker = self.make_orchestrator(
                root,
                [
                    WorkerResponse(
                        status_code=500,
                        body=b'{"error":"antibot page"}',
                        content_type="application/json",
                    ),
                    WorkerResponse(
                        status_code=200,
                        body=(
                            b'{"status":"ok","product":'
                            b'{"external_id":"1"}}'
                        ),
                        content_type="application/json",
                    ),
                ],
            )

            response = orchestrator.execute(
                marketplace="ozon",
                worker_path="/api/v1/product",
                payload={"url": "https://www.ozon.ru/product/1"},
            )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["X-VPN-Profile"], "profile-b")
            self.assertEqual(len(worker.payloads), 2)
            self.assertIn("marketplace_rejected", session.stop_reasons)

            runtime = orchestrator.runtime_snapshot()
            self.assertIn("profile-a", runtime["failed_profiles"])
            self.assertEqual(
                runtime["marketplace_failed_profiles"],
                {"ozon": ["profile-a"]},
            )
            self.assertEqual(
                runtime["active_session"]["profile_name"],
                "profile-b",
            )
            self.assertEqual(
                [item["status"] for item in runtime["last_request_attempts"]],
                ["marketplace_rejected", "success"],
            )

    def test_marketplace_rejection_does_not_disable_profile_for_other_marketplace(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            orchestrator, _, session, worker = self.make_orchestrator(
                root,
                [
                    WorkerResponse(
                        status_code=403,
                        body=b'{"error":"forbidden"}',
                        content_type="application/json",
                    ),
                    WorkerResponse(
                        status_code=200,
                        body=b'{"status":"ok"}',
                        content_type="application/json",
                    ),
                    WorkerResponse(
                        status_code=200,
                        body=b'{"status_code":200,"body":{}}',
                        content_type="application/json",
                    ),
                ],
            )

            ozon_response = orchestrator.execute(
                marketplace="ozon",
                worker_path="/api/v1/product",
                payload={"url": "https://www.ozon.ru/product/1"},
            )
            self.assertEqual(ozon_response.headers["X-VPN-Profile"], "profile-b")

            session.stop(reason="test_reset_active_session")
            wb_response = orchestrator.execute(
                marketplace="wb",
                worker_path="/api/v1/fetch",
                payload={"url": "https://search.wb.ru/test"},
            )

            self.assertEqual(wb_response.status_code, 200)
            self.assertEqual(wb_response.headers["X-VPN-Profile"], "profile-a")
            runtime = orchestrator.runtime_snapshot()
            self.assertEqual(
                runtime["marketplace_failed_profiles"],
                {"ozon": ["profile-a"]},
            )

    def test_worker_unavailable_does_not_retry_or_disable_vpn_profile(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            orchestrator, _, session, worker = self.make_orchestrator(
                root,
                [
                    WorkerRequestError(
                        "connection refused",
                        kind="unavailable",
                    ),
                    WorkerResponse(
                        status_code=200,
                        body=b'{"status":"ok"}',
                        content_type="application/json",
                    ),
                ],
            )

            response = orchestrator.execute(
                marketplace="ozon",
                worker_path="/api/v1/product",
                payload={"url": "https://www.ozon.ru/product/1"},
            )

            self.assertEqual(response.status_code, 503)
            self.assertEqual(len(worker.payloads), 1)
            runtime = orchestrator.runtime_snapshot()
            self.assertEqual(runtime["failed_profiles"], [])
            self.assertEqual(
                runtime["active_session"]["profile_name"],
                "profile-a",
            )
            self.assertNotIn("worker_unavailable", session.stop_reasons)

    def test_parser_error_is_returned_without_vpn_retry(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            orchestrator, _, _, worker = self.make_orchestrator(
                root,
                [
                    WorkerResponse(
                        status_code=500,
                        body=b'{"error":"unexpected parser failure"}',
                        content_type="application/json",
                    ),
                    WorkerResponse(
                        status_code=200,
                        body=b'{"status":"ok"}',
                        content_type="application/json",
                    ),
                ],
            )

            first_response = orchestrator.execute(
                marketplace="ozon",
                worker_path="/api/v1/product",
                payload={"url": "https://www.ozon.ru/product/1"},
            )
            self.assertEqual(first_response.status_code, 500)
            self.assertEqual(len(worker.payloads), 1)
            self.assertEqual(
                first_response.headers["X-VPN-Profile"],
                "profile-a",
            )

            runtime = orchestrator.runtime_snapshot()
            self.assertEqual(runtime["failed_profiles"], [])
            self.assertEqual(runtime["exhausted_groups"], [])

            second_response = orchestrator.execute(
                marketplace="ozon",
                worker_path="/api/v1/product",
                payload={"url": "https://www.ozon.ru/product/2"},
            )
            self.assertEqual(second_response.status_code, 200)
            self.assertEqual(
                second_response.headers["X-VPN-Profile"],
                "profile-a",
            )

    def test_invalid_request_is_returned_without_vpn_retry(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            orchestrator, _, _, worker = self.make_orchestrator(
                root,
                [
                    WorkerResponse(
                        status_code=400,
                        body=b'{"error":"url is required"}',
                        content_type="application/json",
                    ),
                    WorkerResponse(
                        status_code=200,
                        body=b'{"status":"ok"}',
                        content_type="application/json",
                    ),
                ],
            )

            response = orchestrator.execute(
                marketplace="ozon",
                worker_path="/api/v1/product",
                payload={},
            )

            self.assertEqual(response.status_code, 400)
            self.assertEqual(len(worker.payloads), 1)
            self.assertEqual(
                orchestrator.runtime_snapshot()["failed_profiles"],
                [],
            )

    def test_parse_wait_window_exposes_retry_after(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            orchestrator, state, _, _ = self.make_orchestrator(
                root,
                [],
            )
            plan = self.make_plan()
            plan.parse_ready_at = (
                datetime.now(timezone.utc) + timedelta(seconds=125)
            ).isoformat()
            state.set_plan(plan)

            with self.assertRaises(GatewayUnavailableError) as context:
                orchestrator.execute(
                    marketplace="ozon",
                    worker_path="/api/v1/product",
                    payload={"url": "https://www.ozon.ru/product/1"},
                )

            self.assertEqual(context.exception.status_code, 425)
            self.assertGreaterEqual(
                context.exception.retry_after_seconds,
                124,
            )
            self.assertLessEqual(
                context.exception.retry_after_seconds,
                125,
            )

    def test_parse_is_blocked_while_preflight_is_running(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            orchestrator, state, _, _ = self.make_orchestrator(
                root,
                [],
            )
            state.set_running(True)

            with self.assertRaises(GatewayUnavailableError) as context:
                orchestrator.execute(
                    marketplace="ozon",
                    worker_path="/api/v1/product",
                    payload={"url": "https://www.ozon.ru/product/1"},
                )

            self.assertEqual(context.exception.status_code, 503)
            self.assertIn("preflight", str(context.exception).lower())


if __name__ == "__main__":
    unittest.main()
