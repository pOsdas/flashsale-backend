
import json
import threading
from pathlib import Path
from typing import Any

from vpn_controller.app.models import PreflightPlan


class ControllerState:
    def __init__(self, state_path: Path) -> None:
        self.state_path = state_path
        self._lock = threading.RLock()
        self._plan: PreflightPlan | None = None
        self._last_error = ""
        self._running = False

    def set_running(self, running: bool) -> None:
        with self._lock:
            self._running = running

    def set_error(self, error: str) -> None:
        with self._lock:
            self._last_error = error

    def set_plan(self, plan: PreflightPlan) -> None:
        payload = plan.to_dict()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.state_path.with_suffix(
            self.state_path.suffix + ".tmp"
        )
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary_path.replace(self.state_path)

        with self._lock:
            self._plan = plan
            self._last_error = ""

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            plan = self._plan
            return {
                "preflight_running": self._running,
                "last_error": self._last_error,
                "plan": plan.to_dict() if plan is not None else None,
            }

    def latest_plan(self) -> PreflightPlan | None:
        with self._lock:
            return self._plan
