import atexit
import json
import os
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, jsonify, request

from wb_browser_fetcher.app.browser import WBBrowser


def resolve_cookie_path() -> str:
    env_path = os.getenv("WB_COOKIE_PATH")
    if env_path:
        return str(Path(env_path).expanduser().resolve())

    docker_path = Path("/app/secrets/wb_cookie.txt")
    if docker_path.exists():
        return str(docker_path)

    return str(Path(__file__).resolve().parents[2] / "secrets" / "wb_cookie.txt")


def is_allowed_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname in {
        "wildberries.ru",
        "www.wildberries.ru",
        "search.wb.ru",
    }


app = Flask(__name__)
browser = WBBrowser(resolve_cookie_path())
browser.start()
atexit.register(browser.stop)


@app.post("/api/v1/fetch")
def fetch():
    data = request.get_json(silent=True) or {}
    url = str(data.get("url") or "").strip()
    if not is_allowed_url(url):
        return jsonify({"error": "url must be an allowed Wildberries HTTPS URL"}), 400

    try:
        print(f"WB browser fetch requested: url={url}", flush=True)
        result = browser.fetch(url)
        body_text = str(result.get("body") or "")
        try:
            body = json.loads(body_text)
        except json.JSONDecodeError:
            body = body_text

        return jsonify(
            {
                "status_code": int(result.get("status_code") or 0),
                "body": body,
            }
        )
    except Exception as exc:
        print(f"WB browser fetch failed: error={exc}", flush=True)
        return jsonify({"error": str(exc)}), 500


@app.get("/api/v1/health")
def health():
    ready = browser.is_ready()
    return jsonify({"status": "ok" if ready else "error"}), 200 if ready else 503


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8096, threaded=False, debug=False)
