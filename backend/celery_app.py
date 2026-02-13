import os
from celery import Celery
import logging

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "app_project.settings")

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
handler = logging.StreamHandler()
formatter = logging.Formatter('[%(asctime)s: %(levelname)s/%(processName)s] %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)


app = Celery("backend")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks(["app.api.v1"])

app.conf.update(
    worker_hijack_root_logger=False,
    worker_log_format='[%(asctime)s: %(levelname)s/%(processName)s] %(message)s',
    worker_task_log_format='[%(asctime)s: %(levelname)s/%(processName)s] [%(task_name)s(%(task_id)s)] %(message)s',
    worker_log_color=False,
    worker_task_log_color=False,
    task_track_started=True,
    task_send_sent_event=True,
    task_time_limit=120,
    task_soft_time_limit=110,
)

logger.debug("Celery config loaded and logging enabled.")