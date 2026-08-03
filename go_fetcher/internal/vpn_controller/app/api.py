import math
from datetime import datetime, timezone

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


@bp.get("/api/v1/readiness")
def readiness():
    state, _, orchestrator = require_components()
    snapshot = state.snapshot()
    runtime = orchestrator.runtime_snapshot()
    scheduler_alive = bool(_scheduler and _scheduler.is_alive())
    plan = snapshot["plan"]
    preflight_running = bool(snapshot["preflight_running"])
    active_parse_session = bool(
        runtime.get("active_session", {}).get("running")
    )
    available_profiles = (
        int(plan.get("available_profiles_count", 0))
        if plan is not None
        else 0
    )

    ready = True
    reason = "ready"
    retry_after_seconds = None

    if not scheduler_alive:
        ready = False
        reason = "scheduler_not_running"
        retry_after_seconds = 30
    elif preflight_running:
        ready = False
        reason = "preflight_running"
        retry_after_seconds = 30
    elif plan is None:
        ready = False
        reason = "preflight_plan_not_ready"
        retry_after_seconds = 60
    elif available_profiles < 1:
        ready = False
        reason = "no_available_profiles"
        retry_after_seconds = 300
    else:
        parse_ready_at = datetime.fromisoformat(
            plan["parse_ready_at"].replace("Z", "+00:00")
        )
        now = datetime.now(timezone.utc)
        if now < parse_ready_at:
            ready = False
            reason = "parse_window_not_ready"
            retry_after_seconds = max(
                1,
                math.ceil((parse_ready_at - now).total_seconds()),
            )
        elif active_parse_session:
            ready = False
            reason = "active_parse_session"
            retry_after_seconds = 5

    payload = {
        "ready": ready,
        "reason": reason,
        "scheduler_alive": scheduler_alive,
        "preflight_running": preflight_running,
        "active_parse_session": active_parse_session,
        "available_profiles": available_profiles,
        "cycle_id": plan.get("cycle_id") if plan is not None else None,
        "last_preflight_completed_at": (
            plan.get("completed_at") if plan is not None else None
        ),
        "parse_ready_at": (
            plan.get("parse_ready_at") if plan is not None else None
        ),
        "next_preflight_at": (
            plan.get("next_preflight_at") if plan is not None else None
        ),
        "last_error": snapshot["last_error"] or None,
        "scheduler_error": (
            getattr(_scheduler, "last_error", "") or None
            if _scheduler is not None
            else "scheduler is not configured"
        ),
        "retry_after_seconds": retry_after_seconds,
    }

    response = jsonify(payload)
    status_code = 200 if ready else 503
    if retry_after_seconds is not None:
        response.headers["Retry-After"] = str(retry_after_seconds)
    return response, status_code


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
