import unittest
from datetime import datetime, timedelta, timezone

from flask import Flask

from vpn_controller.app.api import bp, configure_api


class FakeState:
    def __init__(self, snapshot):
        self._snapshot = snapshot

    def snapshot(self):
        return self._snapshot


class FakeScheduler:
    def __init__(self, alive=True, last_error=""):
        self._alive = alive
        self.last_error = last_error

    def is_alive(self):
        return self._alive


class FakeOrchestrator:
    def __init__(self, active=False):
        self.active = active

    def runtime_snapshot(self):
        return {
            "active_session": {
                "running": self.active,
            }
        }


class ReadinessApiTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.register_blueprint(bp)
        self.client = self.app.test_client()

    def configure(
        self,
        *,
        preflight_running=False,
        available_profiles=2,
        parse_ready_delta_seconds=-60,
        active_session=False,
        scheduler_alive=True,
        include_plan=True,
    ):
        now = datetime.now(timezone.utc)
        plan = None
        if include_plan:
            plan = {
                "cycle_id": "20260803T000000Z",
                "completed_at": (now - timedelta(minutes=2)).isoformat(),
                "parse_ready_at": (
                    now + timedelta(seconds=parse_ready_delta_seconds)
                ).isoformat(),
                "next_preflight_at": (now + timedelta(hours=1)).isoformat(),
                "available_profiles_count": available_profiles,
            }

        state = FakeState(
            {
                "preflight_running": preflight_running,
                "last_error": "",
                "plan": plan,
            }
        )
        scheduler = FakeScheduler(alive=scheduler_alive)
        orchestrator = FakeOrchestrator(active=active_session)
        configure_api(
            state=state,
            service=object(),
            scheduler=scheduler,
            orchestrator=orchestrator,
        )

    def test_returns_ready_when_controller_can_accept_parse(self):
        self.configure()

        response = self.client.get("/api/v1/readiness")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ready"])
        self.assertEqual(payload["reason"], "ready")
        self.assertEqual(payload["available_profiles"], 2)
        self.assertIsNone(payload["retry_after_seconds"])

    def test_returns_503_while_preflight_is_running(self):
        self.configure(preflight_running=True)

        response = self.client.get("/api/v1/readiness")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["Retry-After"], "30")
        payload = response.get_json()
        self.assertFalse(payload["ready"])
        self.assertEqual(payload["reason"], "preflight_running")

    def test_returns_503_before_parse_window_is_ready(self):
        self.configure(parse_ready_delta_seconds=45)

        response = self.client.get("/api/v1/readiness")

        self.assertEqual(response.status_code, 503)
        payload = response.get_json()
        self.assertFalse(payload["ready"])
        self.assertEqual(payload["reason"], "parse_window_not_ready")
        self.assertGreaterEqual(payload["retry_after_seconds"], 44)

    def test_returns_503_when_parse_session_is_active(self):
        self.configure(active_session=True)

        response = self.client.get("/api/v1/readiness")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["Retry-After"], "5")
        payload = response.get_json()
        self.assertFalse(payload["ready"])
        self.assertEqual(payload["reason"], "active_parse_session")

    def test_returns_503_when_no_plan_exists(self):
        self.configure(include_plan=False)

        response = self.client.get("/api/v1/readiness")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["Retry-After"], "60")
        payload = response.get_json()
        self.assertFalse(payload["ready"])
        self.assertEqual(payload["reason"], "preflight_plan_not_ready")


if __name__ == "__main__":
    unittest.main()
