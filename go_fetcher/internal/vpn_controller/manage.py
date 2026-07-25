

import atexit
import logging
import os

from flask import Flask

from vpn_controller.app.api import bp, configure_api
from vpn_controller.app.config import Settings
from vpn_controller.app.scheduler import PreflightScheduler
from vpn_controller.app.service import VPNPreflightService
from vpn_controller.app.state import ControllerState


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format=(
        "%(asctime)s %(levelname)s %(name)s %(message)s"
    ),
)

settings = Settings.from_env()
state = ControllerState(state_path=settings.state_path)
service = VPNPreflightService(settings=settings, state=state)
scheduler = PreflightScheduler(settings=settings, service=service)

app = Flask(__name__)
configure_api(state=state, service=service, scheduler=scheduler)
app.register_blueprint(bp)

scheduler.start()
atexit.register(scheduler.stop)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8097, threaded=True, debug=False)
