
from flask import Blueprint, Response, jsonify, request
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from vpn_controller.app.orchestrator import (
    GatewayUnavailableError,
    VPNParseOrchestrator,
)
from vpn_controller.app.service import (
    PreflightAlreadyRunningError,
    VPNPreflightService,
)
from vpn_controller.app.state import ControllerState


bp = Blueprint("vpn_controller", __name__)
_state: ControllerState | None = None
_service: VPNPreflightService | None = None
_orchestrator: VPNParseOrchestrator | None = None
_scheduler = None


def configure_api(state, service, scheduler, orchestrator) -> None:
    global _state, _service, _scheduler, _orchestrator
    _state = state
    _service = service
    _scheduler = scheduler
    _orchestrator = orchestrator


def require_components():
    if _state is None or _service is None or _orchestrator is None:
        raise RuntimeError("VPN controller API is not configured")
    return _state, _service, _orchestrator


@bp.get("/metrics")
def metrics():
    return Response(
        generate_latest(),
        status=200,
        content_type=CONTENT_TYPE_LATEST,
    )


@bp.get("/api/v1/health")
def health():
    state, _, orchestrator = require_components()
    snapshot = state.snapshot()
    scheduler_alive = bool(_scheduler and _scheduler.is_alive())
    has_plan = snapshot["plan"] is not None
    status = "ok" if scheduler_alive and has_plan else "degraded"

    return jsonify(
        {
            "status": status,
            "scheduler_alive": scheduler_alive,
            "preflight_running": snapshot["preflight_running"],
            "has_plan": has_plan,
            "last_error": snapshot["last_error"],
            "scheduler_error": (
                getattr(_scheduler, "last_error", "")
                if _scheduler is not None
                else "scheduler is not configured"
            ),
            "parse_runtime": orchestrator.runtime_snapshot(),
        }
    ), 200


@bp.get("/api/v1/preflight/latest")
def latest_preflight():
    state, _, _ = require_components()
    snapshot = state.snapshot()
    if snapshot["plan"] is None:
        return jsonify(
            {
                "status": "not_ready",
                "error": snapshot["last_error"] or "No preflight plan yet",
            }
        ), 503
    return jsonify(snapshot["plan"]), 200


@bp.post("/api/v1/preflight/run")
def run_preflight():
    _, service, orchestrator = require_components()
    orchestrator.reset_for_preflight()
    try:
        plan = service.run()
    except PreflightAlreadyRunningError as exc:
        return jsonify({"status": "busy", "error": str(exc)}), 409
    except Exception as exc:
        return jsonify(
            {
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
            }
        ), 500
    return jsonify(plan.to_dict()), 200


@bp.get("/api/v1/parse/runtime")
def parse_runtime():
    _, _, orchestrator = require_components()
    return jsonify(orchestrator.runtime_snapshot()), 200


def _execute_gateway(marketplace: str, worker_path: str):
    _, _, orchestrator = require_components()
    payload = request.get_json(silent=True) or {}

    try:
        result = orchestrator.execute(
            marketplace=marketplace,
            worker_path=worker_path,
            payload=payload,
        )
    except GatewayUnavailableError as exc:
        response = jsonify(
            {
                "status": "error",
                "error": str(exc),
                "failure_category": "gateway_unavailable",
                "retry_after_seconds": exc.retry_after_seconds,
            }
        )
        if exc.retry_after_seconds > 0:
            response.headers["Retry-After"] = str(exc.retry_after_seconds)
        return response, exc.status_code
    except Exception as exc:
        return jsonify(
            {
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
                "failure_category": "gateway_internal_error",
            }
        ), 500

    response = Response(
        result.body,
        status=result.status_code,
        content_type=result.content_type,
    )
    for name, value in result.headers.items():
        response.headers[name] = value
    return response


@bp.post("/api/v1/product")
def ozon_product_gateway():
    return _execute_gateway("ozon", "/api/v1/product")


@bp.post("/api/v1/search")
def ozon_search_gateway():
    return _execute_gateway("ozon", "/api/v1/search")


@bp.post("/api/v1/category")
def ozon_category_gateway():
    return _execute_gateway("ozon", "/api/v1/category")


@bp.post("/api/v1/fetch")
def wb_fetch_gateway():
    return _execute_gateway("wb", "/api/v1/fetch")
