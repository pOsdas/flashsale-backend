import json
import math
import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from vpn_controller.app.config import Settings
from vpn_controller.app.metrics import (
    VPN_GROUP_PARSE_EXHAUSTED_TOTAL,
    VPN_GROUP_SUSPECTED_REJECTION,
    VPN_PARSE_ATTEMPTS_TOTAL,
    VPN_PARSE_REQUEST_DURATION_SECONDS,
    VPN_PARSE_REQUESTS_TOTAL,
    VPN_MARKETPLACE_AUTH_FAILURES_TOTAL,
)
from vpn_controller.app.models import (
    ParseAttemptResult,
    ParseAttemptStatus,
    PreflightPlan,
    VPNProfile,
)
from vpn_controller.app.parse_session import (
    ActiveSessionExitIPMismatchError,
    ActiveVPNParseSession,
)
from vpn_controller.app.profile_loader import (
    load_selected_profiles,
    normalize_name,
)
from vpn_controller.app.probe import ProxyProbeError
from vpn_controller.app.state import ControllerState
from vpn_controller.app.worker_client import (
    MarketplaceWorkerClient,
    WorkerRequestError,
    WorkerResponse,
)
from vpn_controller.app.xray_config import XrayConfigError
from vpn_controller.app.xray_process import (
    XrayConfigRejectedError,
    XrayStartError,
)


logger = logging.getLogger(__name__)


PERSISTENT_INFRASTRUCTURE_FAILURES = {
    ParseAttemptStatus.PROFILE_NOT_FOUND,
    ParseAttemptStatus.CONFIG_INVALID,
    ParseAttemptStatus.XRAY_START_FAILED,
    ParseAttemptStatus.EXIT_IP_UNAVAILABLE,
    ParseAttemptStatus.EXIT_IP_MISMATCH,
    ParseAttemptStatus.INTERNAL_ERROR,
}

PERSISTENT_MARKETPLACE_FAILURES = {
    ParseAttemptStatus.MARKETPLACE_TIMEOUT,
    ParseAttemptStatus.MARKETPLACE_CONNECTION_ERROR,
    ParseAttemptStatus.MARKETPLACE_REJECTED,
}

TERMINAL_WITHOUT_VPN_RETRY = {
    ParseAttemptStatus.WORKER_UNAVAILABLE,
    ParseAttemptStatus.REQUEST_INVALID,
    ParseAttemptStatus.MARKETPLACE_NOT_FOUND,
    ParseAttemptStatus.PARSER_ERROR,
    ParseAttemptStatus.MARKETPLACE_UNAUTHORIZED,
}


class GatewayUnavailableError(RuntimeError):
    def __init__(
        self,
        message: str,
        status_code: int = 503,
        retry_after_seconds: int = 0,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after_seconds = max(0, int(retry_after_seconds))


@dataclass(frozen=True, slots=True)
class GatewayResponse:
    status_code: int
    body: bytes
    content_type: str
    headers: dict[str, str]


class VPNParseOrchestrator:
    def __init__(
        self,
        *,
        settings: Settings,
        state: ControllerState,
        session: ActiveVPNParseSession,
        worker_client: MarketplaceWorkerClient,
    ) -> None:
        self.settings = settings
        self.state = state
        self.session = session
        self.worker_client = worker_client
        self._parse_lock = threading.Lock()
        self._runtime_lock = threading.RLock()
        self._cycle_id = ""
        self._infrastructure_failed_profiles: set[str] = set()
        self._marketplace_failed_profiles: dict[str, set[str]] = defaultdict(set)
        self._exhausted_groups: set[tuple[str, str]] = set()
        self._group_attempts: dict[
            tuple[str, str],
            list[ParseAttemptResult],
        ] = defaultdict(list)
        self._last_request_attempts: list[ParseAttemptResult] = []

    def execute(
        self,
        *,
        marketplace: str,
        worker_path: str,
        payload: dict[str, Any],
    ) -> GatewayResponse:
        started_at = time.monotonic()
        result_label = "error"
        marketplace = marketplace.strip().lower()

        if marketplace not in {"ozon", "wb"}:
            raise ValueError(f"Unsupported marketplace: {marketplace}")

        try:
            if not self.settings.gateway_enabled:
                raise GatewayUnavailableError(
                    "VPN marketplace gateway is disabled"
                )

            if not self._parse_lock.acquire(blocking=False):
                raise GatewayUnavailableError(
                    "VPN marketplace gateway is busy",
                    status_code=429,
                    retry_after_seconds=5,
                )

            try:
                self.session.stop_if_idle()
                plan = self._require_plan()
                self._ensure_cycle(plan)
                profiles = self._load_profile_map()
                response = self._execute_plan(
                    marketplace=marketplace,
                    worker_path=worker_path,
                    payload=payload,
                    plan=plan,
                    profiles=profiles,
                )
                result_label = (
                    "success" if response.status_code < 300 else "error"
                )
                return response
            finally:
                self._parse_lock.release()
        finally:
            VPN_PARSE_REQUESTS_TOTAL.labels(
                marketplace=marketplace,
                result=result_label,
            ).inc()
            VPN_PARSE_REQUEST_DURATION_SECONDS.labels(
                marketplace=marketplace,
                result=result_label,
            ).observe(time.monotonic() - started_at)

    def runtime_snapshot(self) -> dict[str, Any]:
        with self._runtime_lock:
            failed_by_marketplace = {
                marketplace: sorted(profile_names)
                for marketplace, profile_names
                in self._marketplace_failed_profiles.items()
                if profile_names
            }
            all_failed_profiles = set(self._infrastructure_failed_profiles)
            for profile_names in failed_by_marketplace.values():
                all_failed_profiles.update(profile_names)

            return {
                "cycle_id": self._cycle_id,
                "failed_profiles": sorted(all_failed_profiles),
                "infrastructure_failed_profiles": sorted(
                    self._infrastructure_failed_profiles
                ),
                "marketplace_failed_profiles": failed_by_marketplace,
                "exhausted_groups": [
                    {
                        "marketplace": marketplace,
                        "exit_ip": exit_ip,
                    }
                    for marketplace, exit_ip in sorted(self._exhausted_groups)
                ],
                "last_request_attempts": [
                    attempt.to_dict()
                    for attempt in self._last_request_attempts
                ],
                "active_session": self.session.snapshot().to_dict(),
            }

    def reset_for_preflight(self) -> None:
        with self._parse_lock:
            self.session.stop(reason="preflight_started")

    def shutdown(self) -> None:
        with self._parse_lock:
            self.session.stop(reason="controller_shutdown")

    def _require_plan(self) -> PreflightPlan:
        state_snapshot = self.state.snapshot()
        if state_snapshot["preflight_running"]:
            raise GatewayUnavailableError(
                "VPN preflight is currently running",
                retry_after_seconds=30,
            )

        plan = self.state.latest_plan()
        if plan is None:
            raise GatewayUnavailableError(
                "VPN preflight plan is not ready",
                retry_after_seconds=60,
            )
        if not plan.groups:
            raise GatewayUnavailableError(
                "VPN preflight found no available profiles",
                retry_after_seconds=300,
            )

        if self.settings.require_parse_ready:
            parse_ready_at = datetime.fromisoformat(
                plan.parse_ready_at.replace("Z", "+00:00")
            )
            now = datetime.now(timezone.utc)
            if now < parse_ready_at:
                retry_after_seconds = max(
                    1,
                    math.ceil((parse_ready_at - now).total_seconds()),
                )
                raise GatewayUnavailableError(
                    "VPN parse window is not ready yet; "
                    f"parse_ready_at={plan.parse_ready_at}",
                    status_code=425,
                    retry_after_seconds=retry_after_seconds,
                )
        return plan

    def _ensure_cycle(self, plan: PreflightPlan) -> None:
        with self._runtime_lock:
            if self._cycle_id == plan.cycle_id:
                return
            self.session.stop(reason="new_preflight_cycle")
            self._cycle_id = plan.cycle_id
            self._infrastructure_failed_profiles.clear()
            self._marketplace_failed_profiles.clear()
            self._exhausted_groups.clear()
            self._group_attempts.clear()
            self._last_request_attempts = []
            VPN_GROUP_SUSPECTED_REJECTION.clear()
            logger.info(
                "VPN parse runtime reset for cycle cycle_id=%s",
                plan.cycle_id,
            )

    def _load_profile_map(self) -> dict[str, VPNProfile]:
        selected = load_selected_profiles(
            subscriptions_path=self.settings.subscriptions_path,
            profiles_path=self.settings.profiles_path,
        )
        return {
            normalize_name(profile.name): profile
            for profile in selected
        }

    def _execute_plan(
        self,
        *,
        marketplace: str,
        worker_path: str,
        payload: dict[str, Any],
        plan: PreflightPlan,
        profiles: dict[str, VPNProfile],
    ) -> GatewayResponse:
        request_attempts: list[ParseAttemptResult] = []

        for group in plan.groups:
            group_key = (marketplace, group.exit_ip)
            if group_key in self._exhausted_groups:
                continue

            candidates = [
                item
                for item in group.selected_profiles
                if not self._is_profile_excluded(
                    marketplace=marketplace,
                    profile_name=item.profile_name,
                )
            ]
            candidates = self._prioritize_active_profile(candidates)

            for selected in candidates:
                profile = profiles.get(normalize_name(selected.profile_name))
                if profile is None:
                    attempt = ParseAttemptResult(
                        marketplace=marketplace,
                        cycle_id=plan.cycle_id,
                        group_exit_ip=group.exit_ip,
                        profile_name=selected.profile_name,
                        status=ParseAttemptStatus.PROFILE_NOT_FOUND,
                        error="Profile disappeared from subscriptions.json",
                    )
                    self._record_retryable_failure(attempt)
                    request_attempts.append(attempt)
                    continue

                attempt_started = time.monotonic()
                try:
                    runtime_profile = replace(
                        profile,
                        expected_exit_ip=(
                                selected.actual_exit_ip
                                or group.exit_ip
                        ),
                    )

                    active = self.session.activate(
                        profile=runtime_profile,
                        cycle_id=plan.cycle_id,
                    )
                except Exception as exc:
                    status = self._classify_activation_error(exc)
                    attempt = ParseAttemptResult(
                        marketplace=marketplace,
                        cycle_id=plan.cycle_id,
                        group_exit_ip=group.exit_ip,
                        profile_name=profile.name,
                        status=status,
                        duration_ms=round(
                            (time.monotonic() - attempt_started) * 1000,
                            3,
                        ),
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    self._record_retryable_failure(attempt)
                    request_attempts.append(attempt)
                    continue

                worker_payload = dict(payload)
                if marketplace == "ozon":
                    requested_timeout = worker_payload.get(
                        "timeout_seconds",
                        self.settings.parse_attempt_timeout_seconds,
                    )
                    try:
                        requested_timeout = int(requested_timeout)
                    except (TypeError, ValueError):
                        requested_timeout = (
                            self.settings.parse_attempt_timeout_seconds
                        )
                    worker_payload["timeout_seconds"] = max(
                        5,
                        min(
                            requested_timeout,
                            self.settings.parse_attempt_timeout_seconds,
                        ),
                    )
                worker_payload["proxy_url"] = active.proxy_url
                worker_payload["vpn_session_id"] = (
                    active.browser_session_id
                )
                worker_payload["browser_profile_id"] = (
                    active.browser_profile_id
                )
                worker_payload["vpn_profile_name"] = profile.name
                worker_payload["vpn_exit_ip"] = active.actual_exit_ip

                try:
                    worker_response = self.worker_client.post(
                        marketplace=marketplace,
                        path=worker_path,
                        payload=worker_payload,
                    )
                    status, effective_status_code = (
                        self._classify_worker_response(
                            marketplace=marketplace,
                            response=worker_response,
                        )
                    )
                    error = ""
                    if status != ParseAttemptStatus.SUCCESS:
                        error = worker_response.text_preview()
                except WorkerRequestError as exc:
                    worker_response = None
                    effective_status_code = 0
                    status = (
                        ParseAttemptStatus.MARKETPLACE_TIMEOUT
                        if exc.kind == "timeout"
                        else ParseAttemptStatus.WORKER_UNAVAILABLE
                    )
                    error = str(exc)

                attempt = ParseAttemptResult(
                    marketplace=marketplace,
                    cycle_id=plan.cycle_id,
                    group_exit_ip=group.exit_ip,
                    profile_name=profile.name,
                    status=status,
                    confirmed_exit_ip=active.actual_exit_ip,
                    worker_status_code=effective_status_code,
                    duration_ms=round(
                        (time.monotonic() - attempt_started) * 1000,
                        3,
                    ),
                    error=error,
                )
                request_attempts.append(attempt)

                if attempt.successful and worker_response is not None:
                    self._observe_attempt(attempt)
                    self.session.touch()
                    self._set_last_request_attempts(request_attempts)
                    logger.info(
                        "VPN marketplace parse succeeded marketplace=%s "
                        "cycle_id=%s exit_ip=%s profile=%r attempts=%s",
                        marketplace,
                        plan.cycle_id,
                        active.actual_exit_ip,
                        profile.name,
                        len(request_attempts),
                    )
                    return self._gateway_response_from_worker(
                        worker_response=worker_response,
                        plan=plan,
                        profile_name=profile.name,
                        exit_ip=active.actual_exit_ip,
                        attempts_count=len(request_attempts),
                    )

                if status in TERMINAL_WITHOUT_VPN_RETRY:
                    self._observe_attempt(attempt)
                    self.session.touch()
                    self._set_last_request_attempts(request_attempts)
                    logger.warning(
                        "VPN parse stopped without profile retry "
                        "marketplace=%s cycle_id=%s profile=%r status=%s",
                        marketplace,
                        plan.cycle_id,
                        profile.name,
                        status.value,
                    )
                    if worker_response is not None:
                        return self._gateway_response_from_worker(
                            worker_response=worker_response,
                            plan=plan,
                            profile_name=profile.name,
                            exit_ip=active.actual_exit_ip,
                            attempts_count=len(request_attempts),
                        )
                    return self._worker_unavailable_response(
                        plan=plan,
                        profile_name=profile.name,
                        exit_ip=active.actual_exit_ip,
                        attempts=request_attempts,
                        error=error,
                    )

                self._record_retryable_failure(attempt)
                self.session.stop(reason=attempt.status.value)

            self._mark_group_attempted(
                marketplace=marketplace,
                exit_ip=group.exit_ip,
                selected_profile_names=[
                    item.profile_name for item in group.selected_profiles
                ],
            )

        self._set_last_request_attempts(request_attempts)

        error_payload = {
            "status": "error",
            "error": (
                "Marketplace parsing failed through all selected VPN profiles"
            ),
            "failure_category": "all_groups_exhausted",
            "cycle_id": plan.cycle_id,
            "attempts": [attempt.to_dict() for attempt in request_attempts],
        }
        logger.error(
            "VPN marketplace parse exhausted all groups marketplace=%s "
            "cycle_id=%s attempts=%s",
            marketplace,
            plan.cycle_id,
            len(request_attempts),
        )
        return GatewayResponse(
            status_code=502,
            body=json.dumps(
                error_payload,
                ensure_ascii=False,
            ).encode("utf-8"),
            content_type="application/json",
            headers={
                "X-VPN-Cycle-Id": plan.cycle_id,
                "X-VPN-Attempts": str(len(request_attempts)),
            },
        )

    def _prioritize_active_profile(self, candidates: list) -> list:
        active = self.session.snapshot()
        if not active.running:
            return candidates
        active_name = normalize_name(active.profile_name)
        return sorted(
            candidates,
            key=lambda item: (
                0
                if normalize_name(item.profile_name) == active_name
                else 1
            ),
        )

    def _is_profile_excluded(
        self,
        *,
        marketplace: str,
        profile_name: str,
    ) -> bool:
        key = self._profile_key(profile_name)
        with self._runtime_lock:
            return bool(
                key in self._infrastructure_failed_profiles
                or key in self._marketplace_failed_profiles[marketplace]
            )

    def _record_retryable_failure(
        self,
        attempt: ParseAttemptResult,
    ) -> None:
        self._observe_attempt(attempt)
        profile_key = self._profile_key(attempt.profile_name)
        group_key = (attempt.marketplace, attempt.group_exit_ip)

        with self._runtime_lock:
            self._group_attempts[group_key].append(attempt)
            if attempt.status in PERSISTENT_INFRASTRUCTURE_FAILURES:
                self._infrastructure_failed_profiles.add(profile_key)
            elif attempt.status in PERSISTENT_MARKETPLACE_FAILURES:
                self._marketplace_failed_profiles[
                    attempt.marketplace
                ].add(profile_key)

    def _set_last_request_attempts(
        self,
        attempts: list[ParseAttemptResult],
    ) -> None:
        with self._runtime_lock:
            self._last_request_attempts = list(attempts)

    @staticmethod
    def _profile_key(profile_name: str) -> str:
        return normalize_name(profile_name)

    @staticmethod
    def _classify_activation_error(exc: Exception) -> ParseAttemptStatus:
        if isinstance(exc, (XrayConfigError, XrayConfigRejectedError)):
            return ParseAttemptStatus.CONFIG_INVALID
        if isinstance(exc, XrayStartError):
            return ParseAttemptStatus.XRAY_START_FAILED
        if isinstance(exc, ActiveSessionExitIPMismatchError):
            return ParseAttemptStatus.EXIT_IP_MISMATCH
        if isinstance(exc, ProxyProbeError):
            return ParseAttemptStatus.EXIT_IP_UNAVAILABLE
        return ParseAttemptStatus.INTERNAL_ERROR

    @staticmethod
    def _classify_worker_response(
            *,
            marketplace: str,
            response: WorkerResponse,
    ) -> tuple[ParseAttemptStatus, int]:
        effective_status_code = response.status_code
        text = response.text_preview(limit=5000)
        payload = response.json_or_none()

        if marketplace == "wb" and 200 <= response.status_code < 300:
            if isinstance(payload, dict) and "status_code" in payload:
                try:
                    effective_status_code = int(
                        payload.get("status_code") or 0
                    )
                except (TypeError, ValueError):
                    effective_status_code = 0

                nested_body = payload.get("body")

                if isinstance(nested_body, (dict, list)):
                    text = json.dumps(
                        nested_body,
                        ensure_ascii=False,
                    )
                elif nested_body is not None:
                    text = str(nested_body)

                if isinstance(nested_body, dict):
                    payload = nested_body

        if 200 <= effective_status_code < 300:
            return ParseAttemptStatus.SUCCESS, effective_status_code

        normalized_text = text.casefold()

        response_status = ""
        response_error_type = ""

        if isinstance(payload, dict):
            response_status = str(
                payload.get("status") or ""
            ).strip().casefold()
            response_error_type = str(
                payload.get("error_type") or ""
            ).strip().casefold()

        explicitly_unauthorized = bool(
            effective_status_code == 401
            or response_status == "marketplace_unauthorized"
            or response_error_type == "unauthorized"
        )

        if explicitly_unauthorized:
            return (
                ParseAttemptStatus.MARKETPLACE_UNAUTHORIZED,
                effective_status_code,
            )

        explicitly_rejected = bool(
            effective_status_code in {403, 429, 451, 498}
            or response_status == "marketplace_rejected"
            or response_error_type == "antibot"
            or any(
                marker in normalized_text
                for marker in (
                    "antibot",
                    "anti-bot",
                    "captcha",
                    "access denied",
                    "forbidden",
                    "blocked",
                    "robot check",
                    "challenge",
                )
            )
        )

        if explicitly_rejected:
            return (
                ParseAttemptStatus.MARKETPLACE_REJECTED,
                effective_status_code,
            )

        if effective_status_code == 504 or any(
                marker in normalized_text
                for marker in (
                        "timed out",
                        "timeout",
                        "err_timed_out",
                        "deadline exceeded",
                )
        ):
            return (
                ParseAttemptStatus.MARKETPLACE_TIMEOUT,
                effective_status_code,
            )

        if any(
                marker in normalized_text
                for marker in (
                        "browser worker is not ready",
                        "worker unavailable",
                        "worker thread is not running",
                        "startup_error",
                        "google chrome stable executable was not found",
                        "google chrome stopped before cdp became ready",
                        "google chrome cdp endpoint did not become ready",
                        "chrome cdp connection did not expose",
                        "xvfb stopped before its display became ready",
                        "xvfb display",
                )
        ):
            return (
                ParseAttemptStatus.WORKER_UNAVAILABLE,
                effective_status_code,
            )

        if any(
                marker in normalized_text
                for marker in (
                        "err_connection_closed",
                        "err_connection_reset",
                        "connection closed",
                        "connection reset",
                        "proxy connection",
                        "tunnel connection",
                        "net::err_",
                )
        ):
            return (
                ParseAttemptStatus.MARKETPLACE_CONNECTION_ERROR,
                effective_status_code,
            )

        if effective_status_code in {404, 410}:
            return (
                ParseAttemptStatus.MARKETPLACE_NOT_FOUND,
                effective_status_code,
            )

        if effective_status_code in {400, 405, 409, 422}:
            return (
                ParseAttemptStatus.REQUEST_INVALID,
                effective_status_code,
            )

        if any(
                marker in normalized_text
                for marker in (
                        "invalid json",
                        "invalid response",
                        "decode",
                )
        ):
            return (
                ParseAttemptStatus.INVALID_RESPONSE,
                effective_status_code,
            )

        return (
            ParseAttemptStatus.PARSER_ERROR,
            effective_status_code,
        )

    def _observe_attempt(self, attempt: ParseAttemptResult) -> None:
        VPN_PARSE_ATTEMPTS_TOTAL.labels(
            marketplace=attempt.marketplace,
            exit_ip=attempt.group_exit_ip,
            profile=attempt.profile_name,
            result=attempt.status.value,
        ).inc()

        if attempt.status == ParseAttemptStatus.MARKETPLACE_UNAUTHORIZED:
            VPN_MARKETPLACE_AUTH_FAILURES_TOTAL.labels(
                marketplace=attempt.marketplace,
            ).inc()

        logger.info(
            "VPN parse attempt completed marketplace=%s cycle_id=%s "
            "exit_ip=%s profile=%r status=%s worker_status=%s "
            "duration_ms=%s error=%r",
            attempt.marketplace,
            attempt.cycle_id,
            attempt.group_exit_ip,
            attempt.profile_name,
            attempt.status.value,
            attempt.worker_status_code,
            attempt.duration_ms,
            attempt.error[:1000],
        )

    def _mark_group_attempted(
        self,
        *,
        marketplace: str,
        exit_ip: str,
        selected_profile_names: list[str],
    ) -> None:
        group_key = (marketplace, exit_ip)
        with self._runtime_lock:
            attempts = list(self._group_attempts.get(group_key, []))

        rejected_profiles = {
            self._profile_key(attempt.profile_name)
            for attempt in attempts
            if (
                attempt.status == ParseAttemptStatus.MARKETPLACE_REJECTED
                and attempt.confirmed_exit_ip == exit_ip
            )
        }
        confirmed_attempts = [
            attempt
            for attempt in attempts
            if attempt.confirmed_exit_ip == exit_ip
        ]
        persistently_exhausted = all(
            self._is_profile_excluded(
                marketplace=marketplace,
                profile_name=profile_name,
            )
            for profile_name in selected_profile_names
        )

        if persistently_exhausted:
            with self._runtime_lock:
                self._exhausted_groups.add(group_key)

        if (
            len(rejected_profiles)
            >= self.settings.marketplace_rejection_confirmations
        ):
            reason = "suspected_marketplace_rejection"
            VPN_GROUP_SUSPECTED_REJECTION.labels(
                marketplace=marketplace,
                exit_ip=exit_ip,
            ).set(1)
        elif (
            persistently_exhausted
            and rejected_profiles
            and len(rejected_profiles) == len(confirmed_attempts)
        ):
            reason = "marketplace_rejection_unconfirmed"
        elif persistently_exhausted and not confirmed_attempts:
            reason = "vpn_group_unavailable"
        elif persistently_exhausted:
            reason = "mixed_persistent_failures"
        else:
            reason = "request_parse_failures"

        VPN_GROUP_PARSE_EXHAUSTED_TOTAL.labels(
            marketplace=marketplace,
            exit_ip=exit_ip,
            reason=reason,
        ).inc()
        logger.warning(
            "VPN IP group attempt exhausted marketplace=%s exit_ip=%s "
            "reason=%s persistent=%s attempts=%s confirmed_attempts=%s "
            "rejected_profiles=%s",
            marketplace,
            exit_ip,
            reason,
            persistently_exhausted,
            len(attempts),
            len(confirmed_attempts),
            len(rejected_profiles),
        )

    @staticmethod
    def _gateway_response_from_worker(
        *,
        worker_response: WorkerResponse,
        plan: PreflightPlan,
        profile_name: str,
        exit_ip: str,
        attempts_count: int,
    ) -> GatewayResponse:
        return GatewayResponse(
            status_code=worker_response.status_code,
            body=worker_response.body,
            content_type=worker_response.content_type,
            headers={
                "X-VPN-Cycle-Id": plan.cycle_id,
                "X-VPN-Profile": quote(profile_name, safe=""),
                "X-VPN-Exit-IP": exit_ip,
                "X-VPN-Attempts": str(attempts_count),
            },
        )

    @staticmethod
    def _worker_unavailable_response(
        *,
        plan: PreflightPlan,
        profile_name: str,
        exit_ip: str,
        attempts: list[ParseAttemptResult],
        error: str,
    ) -> GatewayResponse:
        payload = {
            "status": "error",
            "error": error or "Marketplace browser worker is unavailable",
            "failure_category": "worker_unavailable",
            "cycle_id": plan.cycle_id,
            "attempts": [attempt.to_dict() for attempt in attempts],
        }
        return GatewayResponse(
            status_code=503,
            body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            content_type="application/json",
            headers={
                "X-VPN-Cycle-Id": plan.cycle_id,
                "X-VPN-Profile": quote(profile_name, safe=""),
                "X-VPN-Exit-IP": exit_ip,
                "X-VPN-Attempts": str(len(attempts)),
            },
        )
