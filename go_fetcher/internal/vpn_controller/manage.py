
import atexit
import logging
import os

from flask import Flask

from vpn_controller.app.api import bp, configure_api
from vpn_controller.app.config import Settings
from vpn_controller.app.orchestrator import VPNParseOrchestrator
from vpn_controller.app.parse_session import ActiveVPNParseSession
from vpn_controller.app.scheduler import PreflightScheduler
from vpn_controller.app.service import VPNPreflightService
from vpn_controller.app.state import ControllerState
from vpn_controller.app.worker_client import MarketplaceWorkerClient


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

settings = Settings.from_env()
state = ControllerState(state_path=settings.state_path)
service = VPNPreflightService(settings=settings, state=state)
parse_session = ActiveVPNParseSession(settings=settings)
worker_client = MarketplaceWorkerClient(
    ozon_base_url=settings.ozon_browser_url,
    wb_base_url=settings.wb_browser_url,
    timeout_seconds=settings.parse_worker_timeout_seconds,
)
orchestrator = VPNParseOrchestrator(
    settings=settings,
    state=state,
    session=parse_session,
    worker_client=worker_client,
)
scheduler = PreflightScheduler(
    settings=settings,
    service=service,
    before_cycle=orchestrator.reset_for_preflight,
)

app = Flask(__name__)
configure_api(
    state=state,
    service=service,
    scheduler=scheduler,
    orchestrator=orchestrator,
)
app.register_blueprint(bp)

scheduler.start()
atexit.register(orchestrator.shutdown)
atexit.register(scheduler.stop)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8097, threaded=True, debug=False)
