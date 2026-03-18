"""
workers/celery_app.py
Celery application instance. 4 concurrent workers, 600s task timeout.
No automatic retry — failed runs should be inspected before re-running.
Automatic retries could amplify LLM costs on systematic failures.
"""

import sys
from pathlib import Path

# Ensure the project root is on sys.path so forked worker processes can
# resolve top-level packages (models, core, graph, etc.)
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from celery import Celery

from core.settings import get_settings

settings = get_settings()

celery_app = Celery(
    "grasp",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    worker_concurrency=settings.celery_worker_concurrency,
    task_soft_time_limit=settings.celery_task_timeout,
    task_acks_late=True,  # ack only after task completes (prevents loss on worker crash)
    task_reject_on_worker_lost=True,
    task_max_retries=0,  # NO automatic retry — inspect before re-running
)
