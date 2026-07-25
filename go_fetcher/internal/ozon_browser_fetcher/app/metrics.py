import time
from typing import Any

from prometheus_client import Counter, Gauge, Histogram


OZON_BROWSER_HTTP_REQUESTS_TOTAL = Counter(
    "ozon_browser_http_requests_total",
    "Total number of Ozon browser fetcher HTTP requests",
    ["route", "method", "status_class"],
)

OZON_BROWSER_HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "ozon_browser_http_request_duration_seconds",
    "Ozon browser fetcher HTTP request duration in seconds",
    ["route", "method"],
)

OZON_BROWSER_HTTP_REQUESTS_IN_PROGRESS = Gauge(
    "ozon_browser_http_requests_in_progress",
    "Current number of Ozon browser fetcher HTTP requests in progress",
    ["route", "method"],
)

OZON_BROWSER_WORKER_RUNNING = Gauge(
    "ozon_browser_worker_running",
    "Whether the Ozon browser worker loop is running",
)

OZON_BROWSER_WORKER_READY = Gauge(
    "ozon_browser_worker_ready",
    "Whether the Ozon browser worker and browser context are ready",
)

OZON_BROWSER_WORKER_HEARTBEAT_TIMESTAMP_SECONDS = Gauge(
    "ozon_browser_worker_heartbeat_timestamp_seconds",
    "Unix timestamp of the latest Ozon browser worker heartbeat",
)

OZON_BROWSER_QUEUE_SIZE = Gauge(
    "ozon_browser_queue_size",
    "Current number of tasks waiting in the Ozon browser worker queue",
)

OZON_BROWSER_TASK_REQUESTS_TOTAL = Counter(
    "ozon_browser_task_requests_total",
    "Total number of caller-visible Ozon browser task results",
    ["task_type", "result"],
)

OZON_BROWSER_TASK_REQUEST_DURATION_SECONDS = Histogram(
    "ozon_browser_task_request_duration_seconds",
    "Ozon browser task duration as observed by the HTTP caller",
    ["task_type"],
)

OZON_BROWSER_TASK_EXECUTIONS_TOTAL = Counter(
    "ozon_browser_task_executions_total",
    "Total number of Ozon browser task executions",
    ["task_type", "result", "error_type"],
)

OZON_BROWSER_TASK_EXECUTION_DURATION_SECONDS = Histogram(
    "ozon_browser_task_execution_duration_seconds",
    "Ozon browser task execution duration in seconds",
    ["task_type"],
)

OZON_BROWSER_TASK_QUEUE_WAIT_SECONDS = Histogram(
    "ozon_browser_task_queue_wait_seconds",
    "Time spent by an Ozon browser task waiting in the worker queue",
    ["task_type"],
)

OZON_BROWSER_TASKS_IN_PROGRESS = Gauge(
    "ozon_browser_tasks_in_progress",
    "Current number of Ozon browser tasks being executed",
    ["task_type"],
)

OZON_BROWSER_PRODUCTS_RETURNED_TOTAL = Counter(
    "ozon_browser_products_returned_total",
    "Total number of products returned by Ozon browser tasks",
    ["task_type"],
)

OZON_BROWSER_LAST_SUCCESS_TIMESTAMP_SECONDS = Gauge(
    "ozon_browser_last_success_timestamp_seconds",
    "Unix timestamp of the latest successful Ozon browser task",
    ["task_type"],
)

OZON_BROWSER_PAGES_ACTIVE = Gauge(
    "ozon_browser_pages_active",
    "Current number of active Playwright pages",
)

OZON_BROWSER_PAGE_EVENTS_TOTAL = Counter(
    "ozon_browser_page_events_total",
    "Total number of Playwright page lifecycle events",
    ["event"],
)

OZON_BROWSER_LIFECYCLE_TOTAL = Counter(
    "ozon_browser_lifecycle_total",
    "Total number of Ozon browser lifecycle events",
    ["event"],
)

OZON_BROWSER_START_DURATION_SECONDS = Histogram(
    "ozon_browser_start_duration_seconds",
    "Ozon browser startup duration in seconds",
)

OZON_BROWSER_LAST_SUCCESSFUL_START_TIMESTAMP_SECONDS = Gauge(
    "ozon_browser_last_successful_start_timestamp_seconds",
    "Unix timestamp of the latest successful Ozon browser startup",
)


def normalize_route(path: str) -> str:
    known_routes = {
        "/metrics",
        "/api/v1/health",
        "/api/v1/product",
        "/api/v1/search",
        "/api/v1/category",
    }

    if path in known_routes:
        return path

    return "unknown"


def status_class(status_code: int) -> str:
    if 100 <= status_code <= 599:
        return f"{status_code // 100}xx"

    return "other"


def normalize_task_type(value: Any) -> str:
    task_type = str(value or "").strip().lower()

    if task_type in {"product", "search", "category"}:
        return task_type

    return "unknown"


def classify_error(error: BaseException | str) -> str:
    if isinstance(error, TimeoutError):
        return "timeout"

    text = str(error).lower()

    if "timeout" in text or "timed out" in text:
        return "timeout"

    antibot_markers = {
        "challenge.html",
        "block.html",
        "incidentid",
        "доступ ограничен",
        "проверяем ваш браузер",
        "security check",
        "antibot",
    }

    if any(marker in text for marker in antibot_markers):
        return "antibot"

    validation_markers = {
        "is required",
        "invalid ozon",
        "invalid url",
        "invalid category",
        "search query is required",
    }

    if any(marker in text for marker in validation_markers):
        return "validation_error"

    browser_markers = {
        "browser context is not started",
        "browser has been closed",
        "target page, context or browser has been closed",
        "playwright",
        "chromium",
        "new_page",
    }

    if any(marker in text for marker in browser_markers):
        return "browser_error"

    return "parser_error"


def observe_worker_heartbeat() -> None:
    OZON_BROWSER_WORKER_HEARTBEAT_TIMESTAMP_SECONDS.set(
        time.time()
    )
