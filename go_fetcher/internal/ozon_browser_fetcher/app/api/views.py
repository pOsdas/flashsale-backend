from flask import Blueprint, Response, jsonify, request
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from ozon_browser_fetcher.app.browser.worker import get_browser_worker


bp = Blueprint("api", __name__)


def get_request_json() -> dict:
    return request.get_json(silent=True) or {}


def get_proxy_context(data: dict) -> tuple[str, str, str]:
    return (
        str(data.get("proxy_url") or "").strip(),
        str(data.get("vpn_session_id") or "").strip(),
        str(data.get("browser_profile_id") or "").strip(),
    )


def get_int_value(
    data: dict,
    key: str,
    default: int,
    min_value: int,
    max_value: int,
) -> int:
    raw_value = data.get(key, default)

    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        value = default

    if value < min_value:
        return min_value

    if value > max_value:
        return max_value

    return value


def make_worker_response(result: dict):
    if result.get("ok"):
        return jsonify(result["data"]), 200

    if result.get("status") == "marketplace_rejected":
        return jsonify(
            {
                "status": "marketplace_rejected",
                "error_type": result.get(
                    "error_type",
                    "antibot",
                ),
                "error": result.get(
                    "error",
                    "Ozon rejected the browser session",
                ),
            }
        ), 403

    return jsonify(
        {
            "status": "error",
            "error_type": result.get(
                "error_type",
                "parser_error",
            ),
            "error": result.get(
                "error",
                "unknown parser error",
            ),
            "trace": result.get("trace", ""),
        }
    ), 500


def make_product_response(result: dict):
    if result.get("ok"):
        return jsonify(
            {
                "status": "ok",
                "product": result["data"],
            }
        ), 200

    if result.get("status") == "marketplace_rejected":
        return jsonify(
            {
                "status": "marketplace_rejected",
                "error_type": result.get(
                    "error_type",
                    "antibot",
                ),
                "error": result.get(
                    "error",
                    "Ozon rejected the browser session",
                ),
            }
        ), 403

    return jsonify(
        {
            "status": "error",
            "error_type": result.get(
                "error_type",
                "parser_error",
            ),
            "error": result.get(
                "error",
                "unknown parser error",
            ),
            "trace": result.get("trace", ""),
        }
    ), 500


@bp.route("/metrics", methods=["GET"])
def metrics():
    return Response(
        generate_latest(),
        status=200,
        content_type=CONTENT_TYPE_LATEST,
    )


@bp.route("/api/v1/product", methods=["POST"])
def product():
    data = get_request_json()

    url = str(data.get("url") or "").strip()
    timeout_seconds = get_int_value(
        data=data,
        key="timeout_seconds",
        default=90,
        min_value=5,
        max_value=180,
    )

    if not url:
        return jsonify({"error": "url is required"}), 400

    worker = get_browser_worker()
    proxy_url, vpn_session_id, browser_profile_id = get_proxy_context(data)

    try:
        result = worker.parse_product(
            url=url,
            timeout_seconds=timeout_seconds,
            proxy_url=proxy_url,
            vpn_session_id=vpn_session_id,
            browser_profile_id=browser_profile_id,
        )

        return make_product_response(result)

    except TimeoutError as exc:
        return jsonify({"error": str(exc)}), 504

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/v1/search", methods=["POST"])
def search():
    data = get_request_json()

    query = str(data.get("query") or "").strip()
    limit = get_int_value(
        data=data,
        key="limit",
        default=10,
        min_value=1,
        max_value=100,
    )
    timeout_seconds = get_int_value(
        data=data,
        key="timeout_seconds",
        default=90,
        min_value=5,
        max_value=180,
    )

    if not query:
        return jsonify({"error": "query is required"}), 400

    worker = get_browser_worker()
    proxy_url, vpn_session_id, browser_profile_id = get_proxy_context(data)

    try:
        result = worker.parse_search(
            query=query,
            limit=limit,
            timeout_seconds=timeout_seconds,
            proxy_url=proxy_url,
            vpn_session_id=vpn_session_id,
            browser_profile_id=browser_profile_id,
        )

        return make_worker_response(result)

    except TimeoutError as exc:
        return jsonify({"error": str(exc)}), 504

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/v1/category", methods=["POST"])
def category():
    data = get_request_json()

    url = str(
        data.get("url")
        or data.get("category_url")
        or ""
    ).strip()
    limit = get_int_value(
        data=data,
        key="limit",
        default=10,
        min_value=1,
        max_value=100,
    )
    timeout_seconds = get_int_value(
        data=data,
        key="timeout_seconds",
        default=90,
        min_value=5,
        max_value=180,
    )

    if not url:
        return jsonify({"error": "url is required"}), 400

    worker = get_browser_worker()
    proxy_url, vpn_session_id, browser_profile_id = get_proxy_context(data)

    try:
        result = worker.parse_category(
            url=url,
            limit=limit,
            timeout_seconds=timeout_seconds,
            proxy_url=proxy_url,
            vpn_session_id=vpn_session_id,
            browser_profile_id=browser_profile_id,
        )

        return make_worker_response(result)

    except TimeoutError as exc:
        return jsonify({"error": str(exc)}), 504

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/v1/health", methods=["GET"])
def health():
    worker = get_browser_worker()
    health_data = worker.get_health_snapshot()

    status_code = (
        200
        if health_data["status"] == "ok"
        else 503
    )

    return jsonify(health_data), status_code
