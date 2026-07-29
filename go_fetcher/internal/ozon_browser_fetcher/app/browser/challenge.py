import os
import re
import time
from typing import Dict, Optional

from playwright.sync_api import Page

from ozon_browser_fetcher.app.browser.errors import OzonAntibotRejectedError


CHALLENGE_TITLE_MARKERS = {
    "antibot challenge page",
    "antibot page",
    "antibot captcha",
    "captcha",
}

REJECTED_TITLE_MARKERS = {
    "похоже, нет соединения",
    "access denied",
}

CHALLENGE_BODY_MARKERS = {
    "проверяем ваш браузер",
    "antibot challenge",
    "oops, something went wrong",
    "please refresh the page",
}

REJECTED_BODY_MARKERS = {
    "похоже, нет соединения",
    "access denied",
}


class OzonChallengeTimeoutError(TimeoutError):
    def __init__(self, message: str, last_state: Dict[str, object]) -> None:
        super().__init__(message)
        self.last_state = last_state


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        return default

    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return default

    return max(minimum, value)


def get_challenge_timeout_ms() -> int:
    return _env_int(
        "OZON_BROWSER_CHALLENGE_TIMEOUT_MS",
        25_000,
        minimum=1,
    )


def get_refresh_attempts() -> int:
    return _env_int(
        "OZON_BROWSER_CHALLENGE_REFRESH_ATTEMPTS",
        1,
        minimum=0,
    )


def get_page_state(page: Page) -> Dict[str, object]:
    try:
        return page.evaluate(
            r"""
            () => ({
                title: document.title || "",
                ready_state: document.readyState || "",
                body_length: document.body
                    ? (document.body.innerText || "").length
                    : 0,
                body_prefix: document.body
                    ? (document.body.innerText || "").slice(0, 500)
                    : ""
            })
            """
        )
    except Exception:
        return {
            "title": "",
            "ready_state": "",
            "body_length": 0,
            "body_prefix": "",
        }


def _poll_page_ready(
    page: Page,
    *,
    timeout_ms: int,
    phase: str,
) -> Dict[str, object]:
    deadline = time.monotonic() + timeout_ms / 1000
    started_at = time.monotonic()
    challenge_seen = False
    last_state: Dict[str, object] = {}
    next_log_at = 0.0

    while time.monotonic() < deadline:
        current_url = page.url
        state = get_page_state(page)
        title = str(state.get("title") or "").strip()
        lowered_title = title.lower()
        body_prefix = str(state.get("body_prefix") or "").lower()
        body_length = int(state.get("body_length") or 0)
        ready_state = str(state.get("ready_state") or "")
        elapsed_seconds = time.monotonic() - started_at

        last_state = {
            "url": current_url,
            "title": title,
            "ready_state": ready_state,
            "body_length": body_length,
            "challenge_seen": challenge_seen,
            "phase": phase,
            "elapsed_seconds": round(elapsed_seconds, 3),
        }

        if (
            any(marker in lowered_title for marker in REJECTED_TITLE_MARKERS)
            or any(marker in body_prefix for marker in REJECTED_BODY_MARKERS)
        ):
            raise OzonAntibotRejectedError(
                "Ozon rejected browser session: "
                f"url={current_url}, title={title!r}, phase={phase}"
            )

        pending_challenge_url = bool(
            "__rr=" in current_url
            and "abt_att=1" not in current_url
            and (
                not title
                or lowered_title.startswith("loading ")
                or body_length < 200
            )
        )
        is_challenge = bool(
            any(marker in lowered_title for marker in CHALLENGE_TITLE_MARKERS)
            or any(marker in body_prefix for marker in CHALLENGE_BODY_MARKERS)
            or pending_challenge_url
        )

        if is_challenge:
            challenge_seen = True
            if elapsed_seconds >= next_log_at:
                print(
                    "Ozon antibot challenge pending: "
                    f"phase={phase}, elapsed={elapsed_seconds:.1f}s, "
                    f"url={current_url}, title={title!r}, "
                    f"body_length={body_length}",
                    flush=True,
                )
                next_log_at += 2.0
            time.sleep(0.15)
            continue

        is_loading_title = not title or lowered_title.startswith("loading ")
        document_ready = ready_state in {"interactive", "complete"}
        accepted_challenge = "abt_att=1" in current_url
        meaningful_document = body_length >= 200

        if not is_loading_title and document_ready and meaningful_document:
            if challenge_seen:
                print(
                    "Ozon antibot challenge passed: "
                    f"phase={phase}, url={current_url}, title={title!r}",
                    flush=True,
                )
            return last_state

        if accepted_challenge and not is_loading_title:
            return last_state

        time.sleep(0.15)

    raise OzonChallengeTimeoutError(
        "Ozon page did not become ready after browser challenge: "
        f"timeout_ms={timeout_ms}, phase={phase}, last_state={last_state}",
        last_state=last_state,
    )


def _refresh_challenge_page(page: Page) -> str:
    try:
        button = page.get_by_role(
            "button",
            name=re.compile(r"^(refresh|обновить)$", re.IGNORECASE),
        )
        if button.count() > 0:
            button.first.click(timeout=3_000)
            return "button"
    except Exception:
        pass

    try:
        page.reload(
            wait_until="domcontentloaded",
            timeout=45_000,
        )
        return "reload"
    except Exception as exc:
        raise OzonAntibotRejectedError(
            "Ozon antibot page could not be refreshed: "
            f"url={page.url}, error={type(exc).__name__}: {exc}"
        ) from exc


def wait_for_ozon_page_ready(
    page: Page,
    *,
    timeout_ms: Optional[int] = None,
    refresh_attempts: Optional[int] = None,
) -> Dict[str, object]:
    safe_timeout_ms = (
        timeout_ms
        if timeout_ms is not None and timeout_ms > 0
        else get_challenge_timeout_ms()
    )
    safe_refresh_attempts = (
        refresh_attempts
        if refresh_attempts is not None and refresh_attempts >= 0
        else get_refresh_attempts()
    )

    last_timeout: Optional[OzonChallengeTimeoutError] = None

    for attempt in range(safe_refresh_attempts + 1):
        phase = "initial" if attempt == 0 else f"refresh-{attempt}"

        try:
            return _poll_page_ready(
                page,
                timeout_ms=safe_timeout_ms,
                phase=phase,
            )
        except OzonChallengeTimeoutError as exc:
            last_timeout = exc
            challenge_seen = bool(exc.last_state.get("challenge_seen"))

            if not challenge_seen:
                raise

            if attempt >= safe_refresh_attempts:
                break

            refresh_method = _refresh_challenge_page(page)
            print(
                "Ozon antibot challenge refresh requested: "
                f"attempt={attempt + 1}, method={refresh_method}, url={page.url}",
                flush=True,
            )
            page.wait_for_timeout(1_000)

    last_state = last_timeout.last_state if last_timeout is not None else {}
    raise OzonAntibotRejectedError(
        "Ozon antibot challenge did not pass: "
        f"timeout_ms={safe_timeout_ms}, "
        f"refresh_attempts={safe_refresh_attempts}, "
        f"last_state={last_state}"
    )
