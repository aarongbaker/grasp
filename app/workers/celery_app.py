"""
app/workers/celery_app.py
Celery application instance for GRASP background work.

Production contract:
- process command stays on ``--pool=solo --concurrency=1`` for current memory limits
- broker reconnect on startup is explicit to avoid Celery 5.4 startup warnings
- task auto-retries remain disabled so failed LLM runs are inspected before re-running
"""

from celery import Celery

from app.core.settings import get_settings

settings = get_settings()

celery_app = Celery(
    "grasp",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    worker_concurrency=settings.celery_worker_concurrency,
    task_soft_time_limit=settings.celery_task_timeout,
    broker_connection_retry=True,
    broker_connection_retry_on_startup=True,
    task_acks_late=True,  # ack only after task completes (prevents loss on worker crash)
    task_reject_on_worker_lost=True,
    task_max_retries=0,  # NO automatic retry — inspect before re-running
)
