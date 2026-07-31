from dataclasses import dataclass
from typing import Any

import httpx
from django.conf import settings

from app.core.logging import get_logger


logger = get_logger(__name__)


@dataclass(frozen=True)
class ParserHealthResult:
    status: str
    checks: dict[str, Any]


class ParserHealthChecker:
    STATUS_HEALTHY = "healthy"
    STATUS_DEGRADED = "degraded"
    STATUS_UNHEALTHY = "unhealthy"

    def run(self) -> dict[str, Any]:
        go_fetcher_base_url = getattr(settings, "GO_FETCHER_BASE_URL", "")

        if not go_fetcher_base_url:
            return {
                "status": self.STATUS_UNHEALTHY,
                "checks": {
                    "go_fetcher": {
                        "status": "error",
                        "details": {
                            "error": "GO_FETCHER_BASE_URL is not configured.",
                        },
                    },
                },
            }

        parser_health_endpoint = getattr(
            settings,
            "GO_FETCHER_PARSER_HEALTH_ENDPOINT",
            "/api/v1/parser/health",
        )

        url = self._build_url(
            base_url=go_fetcher_base_url,
            endpoint=parser_health_endpoint,
        )

        try:
            response = httpx.get(
                url,
                timeout=httpx.Timeout(
                    getattr(
                        settings,
                        "GO_FETCHER_PARSER_HEALTH_TIMEOUT_SECONDS",
                        270,
                    ),
                    connect=10,
                ),
            )

        except httpx.TimeoutException as exc:
            logger.warning(
                "Parser health check timeout",
                extra={
                    "service": "parser_health",
                    "url": url,
                    "error": str(exc),
                },
            )

            return {
                "status": self.STATUS_UNHEALTHY,
                "checks": {
                    "go_fetcher": {
                        "status": "error",
                        "details": {
                            "url": url,
                            "error": "go_fetcher did not respond in time.",
                        },
                    },
                },
            }

        except httpx.HTTPStatusError as exc:
            logger.warning(
                "Parser health check returned HTTP error",
                extra={
                    "service": "parser_health",
                    "url": url,
                    "status_code": exc.response.status_code,
                    "response_text": exc.response.text[:1000],
                },
            )

            return {
                "status": self.STATUS_UNHEALTHY,
                "checks": {
                    "go_fetcher": {
                        "status": "error",
                        "details": {
                            "url": url,
                            "status_code": exc.response.status_code,
                            "response": exc.response.text[:1000],
                        },
                    },
                },
            }

        except httpx.RequestError as exc:
            logger.warning(
                "Parser health check request error",
                extra={
                    "service": "parser_health",
                    "url": url,
                    "error": str(exc),
                },
            )

            return {
                "status": self.STATUS_UNHEALTHY,
                "checks": {
                    "go_fetcher": {
                        "status": "error",
                        "details": {
                            "url": url,
                            "error": str(exc),
                        },
                    },
                },
            }

        try:
            data = response.json()

        except ValueError as exc:
            logger.warning(
                "Parser health check returned invalid JSON",
                extra={
                    "service": "parser_health",
                    "url": url,
                    "response_text": response.text[:1000],
                },
            )

            return {
                "status": self.STATUS_UNHEALTHY,
                "checks": {
                    "go_fetcher": {
                        "status": "error",
                        "details": {
                            "url": url,
                            "error": "go_fetcher returned invalid JSON.",
                        },
                    },
                },
            }

        return self._normalize_response(
            data=data,
            url=url,
        )

    def _build_url(self, *, base_url: str, endpoint: str) -> str:
        base_url = base_url.rstrip("/")

        if not endpoint.startswith("/"):
            endpoint = f"/{endpoint}"

        return f"{base_url}{endpoint}"

    def _normalize_response(self, *, data: dict[str, Any], url: str) -> dict[str, Any]:
        status = data.get("status") or self.STATUS_UNHEALTHY
        checks = data.get("checks") or {}

        if status not in {
            self.STATUS_HEALTHY,
            self.STATUS_DEGRADED,
            self.STATUS_UNHEALTHY,
        }:
            status = self.STATUS_UNHEALTHY

        normalized_checks = {
            "go_fetcher": {
                "status": "ok",
                "details": {
                    "url": url,
                },
            },
        }

        for check_name, check_value in checks.items():
            normalized_checks[check_name] = check_value

        return {
            "status": status,
            "checks": normalized_checks,
        }
