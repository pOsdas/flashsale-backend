

from flask import Blueprint, Response, jsonify
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from vpn_controller.app.service import (
    PreflightAlreadyRunningError,
    VPNPreflightService,
)
from vpn_controller.app.state import ControllerState


bp = Blueprint("vpn_controller", __name__)
_state: ControllerState | None = None
_service: VPNPreflightService | None = None
_scheduler = None


def configure_api(state, service, scheduler) -> None:
    global _state, _service, _scheduler
    _state = state
    _service = service
    _scheduler = scheduler


def require_components():
    if _state is None or _service is None:
        raise RuntimeError("VPN controller API is not configured")
    return _state, _service


@bp.get("/metrics")
def metrics():
    return Response(
        generate_latest(),
        status=200,
        content_type=CONTENT_TYPE_LATEST,
    )


@bp.get("/api/v1/health")
def health():
    state, _ = require_components()
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
        }
    ), 200


@bp.get("/api/v1/preflight/latest")
def latest_preflight():
    state, _ = require_components()
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
    _, service = require_components()
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
