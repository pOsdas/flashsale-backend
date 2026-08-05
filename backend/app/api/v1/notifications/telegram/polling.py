import signal
import time
from typing import Any
from app.api.v1.notifications.telegram.client import (
    TelegramApiError,
    TelegramBotClient,
)
from app.api.v1.notifications.telegram.router import TelegramUpdateRouter
from app.api.v1.notifications.telegram.telegram_metrics import (
    TELEGRAM_BOT_RUNNING,
    TELEGRAM_HANDLER_ERRORS_TOTAL,
    TELEGRAM_LAST_UPDATE_TIMESTAMP_SECONDS,
    TELEGRAM_POLLING_HEARTBEAT_TIMESTAMP_SECONDS,
    TELEGRAM_POLLING_REQUEST_DURATION_SECONDS,
    TELEGRAM_POLLING_REQUESTS_TOTAL,
    TELEGRAM_UPDATES_IN_PROGRESS,
    TELEGRAM_UPDATES_TOTAL,
)
from app.core.logging import get_logger


logger = get_logger(__name__)


class TelegramPollingRunner:
    def __init__(
        self,
        *,
        client: TelegramBotClient,
        router: TelegramUpdateRouter,
        drop_pending_updates_on_start: bool,
        error_sleep_seconds: int = 2,
        max_error_sleep_seconds: int = 60,
    ) -> None:
        self.client = client
        self.router = router
        self.drop_pending_updates_on_start = (
            drop_pending_updates_on_start
        )
        self.error_sleep_seconds = error_sleep_seconds
        self.max_error_sleep_seconds = max_error_sleep_seconds
        self.should_stop = False

    def run(self) -> None:
        self._register_signal_handlers()
        offset: int | None = None
        consecutive_errors = 0

        TELEGRAM_BOT_RUNNING.set(1)

        logger.info(
            "Telegram bot polling started",
            extra={
                "service": "telegram_bot",
                "drop_pending_updates_on_start": (
                    self.drop_pending_updates_on_start
                ),
            },
        )

        try:
            if self.drop_pending_updates_on_start:
                self.client.drop_pending_updates()

                logger.info(
                    "Telegram pending updates dropped",
                    extra={
                        "service": "telegram_bot",
                    },
                )

            while not self.should_stop:
                polling_started_at = time.monotonic()

                try:
                    updates = self.client.get_updates(
                        offset=offset,
                    )

                except TelegramApiError as exc:
                    consecutive_errors += 1

                    if exc.status_code == 429:
                        request_status = "rate_limited"
                    else:
                        request_status = "http_error"

                    TELEGRAM_POLLING_REQUESTS_TOTAL.labels(
                        status=request_status,
                    ).inc()
                    TELEGRAM_HANDLER_ERRORS_TOTAL.labels(
                        stage="polling_http",
                    ).inc()

                    sleep_seconds = self._get_error_sleep_seconds(
                        consecutive_errors=consecutive_errors,
                        retry_after_seconds=exc.retry_after_seconds,
                    )

                    logger.warning(
                        "Telegram bot polling request failed",
                        extra={
                            "service": "telegram_bot",
                            "status_code": exc.status_code,
                            "retry_after_seconds": (
                                exc.retry_after_seconds
                            ),
                            "sleep_seconds": sleep_seconds,
                            "consecutive_errors": consecutive_errors,
                            "error": str(exc),
                        },
                    )

                    self._sleep_interruptibly(
                        seconds=sleep_seconds,
                    )

                except Exception as exc:
                    consecutive_errors += 1

                    TELEGRAM_POLLING_REQUESTS_TOTAL.labels(
                        status="error",
                    ).inc()
                    TELEGRAM_HANDLER_ERRORS_TOTAL.labels(
                        stage="polling",
                    ).inc()

                    sleep_seconds = self._get_error_sleep_seconds(
                        consecutive_errors=consecutive_errors,
                    )

                    logger.exception(
                        "Telegram bot polling error",
                        extra={
                            "service": "telegram_bot",
                            "sleep_seconds": sleep_seconds,
                            "consecutive_errors": consecutive_errors,
                            "error": str(exc),
                        },
                    )

                    self._sleep_interruptibly(
                        seconds=sleep_seconds,
                    )

                else:
                    consecutive_errors = 0

                    TELEGRAM_POLLING_REQUESTS_TOTAL.labels(
                        status="success",
                    ).inc()
                    TELEGRAM_POLLING_HEARTBEAT_TIMESTAMP_SECONDS.set(
                        time.time()
                    )

                    offset = self._process_updates(
                        updates=updates,
                        current_offset=offset,
                    )

                finally:
                    TELEGRAM_POLLING_REQUEST_DURATION_SECONDS.observe(
                        time.monotonic() - polling_started_at
                    )

        finally:
            TELEGRAM_BOT_RUNNING.set(0)
            self.client.close()

            logger.info(
                "Telegram bot polling stopped",
                extra={
                    "service": "telegram_bot",
                },
            )

    def stop(self) -> None:
        self.should_stop = True

    def _process_updates(
        self,
        *,
        updates: list[dict[str, Any]],
        current_offset: int | None,
    ) -> int | None:
        offset = current_offset

        for update in updates:
            if self.should_stop:
                break

            update_type = self._get_update_type(
                update=update,
            )
            update_id = update.get("update_id")

            if not isinstance(update_id, int):
                TELEGRAM_UPDATES_TOTAL.labels(
                    update_type=update_type,
                    status="ignored",
                ).inc()

                logger.warning(
                    "Telegram update without valid update_id ignored",
                    extra={
                        "service": "telegram_bot",
                        "update": update,
                    },
                )
                continue

            next_offset = update_id + 1
            TELEGRAM_UPDATES_IN_PROGRESS.inc()

            try:
                self.router.handle_update(
                    update=update,
                )
                TELEGRAM_UPDATES_TOTAL.labels(
                    update_type=update_type,
                    status="processed",
                ).inc()

            except Exception as exc:
                TELEGRAM_UPDATES_TOTAL.labels(
                    update_type=update_type,
                    status="error",
                ).inc()
                TELEGRAM_HANDLER_ERRORS_TOTAL.labels(
                    stage="update",
                ).inc()

                logger.exception(
                    "Telegram update handling failed",
                    extra={
                        "service": "telegram_bot",
                        "update_id": update_id,
                        "error": str(exc),
                    },
                )

            finally:
                TELEGRAM_UPDATES_IN_PROGRESS.dec()
                TELEGRAM_LAST_UPDATE_TIMESTAMP_SECONDS.set(
                    time.time()
                )
                offset = next_offset

        return offset

    def _get_error_sleep_seconds(
        self,
        *,
        consecutive_errors: int,
        retry_after_seconds: int | None = None,
    ) -> int:
        exponential_delay = min(
            self.error_sleep_seconds
            * (2 ** max(consecutive_errors - 1, 0)),
            self.max_error_sleep_seconds,
        )

        if retry_after_seconds is None:
            return exponential_delay

        return min(
            max(
                retry_after_seconds,
                exponential_delay,
            ),
            self.max_error_sleep_seconds,
        )

    def _sleep_interruptibly(
        self,
        *,
        seconds: int,
    ) -> None:
        sleep_until = time.monotonic() + seconds

        while not self.should_stop:
            remaining_seconds = sleep_until - time.monotonic()

            if remaining_seconds <= 0:
                return

            time.sleep(
                min(
                    remaining_seconds,
                    1,
                )
            )

    def _register_signal_handlers(self) -> None:
        signal.signal(
            signal.SIGINT,
            self._handle_stop_signal,
        )
        signal.signal(
            signal.SIGTERM,
            self._handle_stop_signal,
        )

    def _handle_stop_signal(
        self,
        signum,
        frame,
    ) -> None:
        self.stop()

        logger.info(
            "Telegram bot polling stopping",
            extra={
                "service": "telegram_bot",
                "signal": signum,
            },
        )

    @staticmethod
    def _get_update_type(
        *,
        update: dict[str, Any],
    ) -> str:
        if update.get("callback_query"):
            return "callback_query"

        if update.get("message"):
            return "message"

        return "other"
