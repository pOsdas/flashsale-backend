
import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class WorkerResponse:
    status_code: int
    body: bytes
    content_type: str

    def json_or_none(self) -> Any | None:
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

    def text_preview(self, limit: int = 2000) -> str:
        return self.body.decode("utf-8", errors="replace")[:limit]


class WorkerRequestError(RuntimeError):
    def __init__(self, message: str, *, kind: str) -> None:
        super().__init__(message)
        self.kind = kind


class MarketplaceWorkerClient:
    def __init__(
        self,
        *,
        ozon_base_url: str,
        wb_base_url: str,
        timeout_seconds: float,
    ) -> None:
        self.ozon_base_url = ozon_base_url.rstrip("/")
        self.wb_base_url = wb_base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def post(
        self,
        *,
        marketplace: str,
        path: str,
        payload: dict[str, Any],
    ) -> WorkerResponse:
        if marketplace == "ozon":
            base_url = self.ozon_base_url
        elif marketplace == "wb":
            base_url = self.wb_base_url
        else:
            raise ValueError(f"Unsupported marketplace: {marketplace}")

        request_body = json.dumps(
            payload,
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            url=f"{base_url}{path}",
            data=request_body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(
                request,
                timeout=self.timeout_seconds,
            ) as response:
                return WorkerResponse(
                    status_code=int(response.status),
                    body=response.read(),
                    content_type=(
                        response.headers.get("Content-Type")
                        or "application/json"
                    ),
                )
        except urllib.error.HTTPError as exc:
            return WorkerResponse(
                status_code=int(exc.code),
                body=exc.read(),
                content_type=(
                    exc.headers.get("Content-Type")
                    if exc.headers is not None
                    else "application/json"
                ) or "application/json",
            )
        except (TimeoutError, socket.timeout) as exc:
            raise WorkerRequestError(
                "Marketplace worker request timed out: "
                f"{type(exc).__name__}: {exc}",
                kind="timeout",
            ) from exc
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, (TimeoutError, socket.timeout)):
                kind = "timeout"
                message = "Marketplace worker request timed out"
            else:
                kind = "unavailable"
                message = "Marketplace worker request failed"
            raise WorkerRequestError(
                f"{message}: {type(exc).__name__}: {exc}",
                kind=kind,
            ) from exc
