

import logging
import threading
import time
from datetime import datetime, timezone

from vpn_controller.app.config import Settings
from vpn_controller.app.metrics import (
    VPN_CONTROLLER_HEARTBEAT_TIMESTAMP_SECONDS,
)
from vpn_controller.app.service import VPNPreflightService


logger = logging.getLogger(__name__)


class PreflightScheduler:
    def __init__(
        self,
        settings: Settings,
        service: VPNPreflightService,
    ) -> None:
        self.settings = settings
        self.service = service
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.last_error = ""

    def start(self) -> None:
        if self.thread is not None and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(
            target=self._run,
            name="vpn-preflight-scheduler",
            daemon=True,
        )
        self.thread.start()
        logger.info(
            "VPN preflight scheduler started interval_seconds=%s ",
            self.settings.preflight_interval_seconds,
        )

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=10)

    def is_alive(self) -> bool:
        return bool(self.thread is not None and self.thread.is_alive())

    def _run(self) -> None:
        interval = self.settings.preflight_interval_seconds
        next_run_monotonic = time.monotonic()

        if not self.settings.run_preflight_on_start:
            next_run_monotonic += interval

        while not self.stop_event.is_set():
            VPN_CONTROLLER_HEARTBEAT_TIMESTAMP_SECONDS.set(time.time())
            now = time.monotonic()
            wait_seconds = next_run_monotonic - now
            if wait_seconds > 0:
                self.stop_event.wait(min(wait_seconds, 1.0))
                continue

            cycle_started_at = datetime.now(timezone.utc)
            logger.info(
                "VPN preflight cycle started cycle_started_at=%s",
                cycle_started_at.isoformat(),
            )
            try:
                plan = self.service.run(
                    cycle_started_at=cycle_started_at
                )
                self.last_error = ""
                logger.info(
                    "VPN preflight cycle completed cycle_id=%s "
                    "available_profiles=%s groups=%s parse_ready_at=%s",
                    plan.cycle_id,
                    plan.available_profiles_count,
                    len(plan.groups),
                    plan.parse_ready_at,
                )
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                logger.exception(
                    "VPN preflight cycle failed error=%s",
                    self.last_error,
                )

            next_run_monotonic += interval
            now = time.monotonic()
            if next_run_monotonic <= now:
                skipped = int((now - next_run_monotonic) // interval) + 1
                next_run_monotonic += skipped * interval
