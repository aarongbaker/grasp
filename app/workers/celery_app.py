"""
app/workers/celery_app.py
Celery application instance for GRASP background work.

Why Celery instead of FastAPI BackgroundTasks?
  - FastAPI BackgroundTasks run in the same process as the API server.
    A long-running LangGraph pipeline (up to 10 minutes) would block
    the uvicorn worker and starve other requests.
  - Celery runs in a separate worker process with its own event loop,
    connection pools, and memory space. Pipeline failures don't affect
    the API server and vice versa.
  - Celery's broker (Redis) provides durable task queues — if the worker
    crashes, the task survives in Redis and can be picked up on restart.

Production contract:
  - process command stays on --pool=solo --concurrency=1 for current memory limits.
    LangGraph pipelines load large models and embeddings; running multiple
    pipelines concurrently would OOM the worker.
  - broker reconnect on startup is explicit to avoid Celery 5.4 startup warnings.
  - task auto-retries remain disabled so failed LLM runs are inspected before re-running.
    Automatic retries would re-bill the user for a Claude API call without their consent.

task_acks_late=True: Celery normally acknowledges (removes from queue) a task
when a worker STARTS it. With task_acks_late, acknowledgement happens only after
the task COMPLETES. Combined with task_reject_on_worker_lost, this means a task
that was running when the worker crashed is re-queued, not silently dropped.

task_max_retries=0: prevents Celery's built-in retry mechanism from re-submitting
failed tasks. We want explicit human review of LLM failures, not silent retries
that waste API credits and potentially produce worse results.
"""

from celery import Celery

from app.core.settings import get_settings

settings = get_settings()

celery_app = Celery(
    "grasp",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    # include: tells Celery where to find task definitions so workers can import them.
    # Without this, workers would need to be started with explicit module arguments.
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    # JSON serialization for all task payloads — ensures cross-language compatibility
    # and prevents pickle deserialization vulnerabilities.
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,

    # Worker pool size — controlled by settings so different environments
    # can adjust without code changes (e.g. larger machines in production).
    worker_concurrency=settings.celery_worker_concurrency,

    # Soft time limit: after this many seconds, Celery raises SoftTimeLimitExceeded
    # inside the task. The task can catch this to do cleanup before being killed.
    # Hard time limit (not set) would SIGKILL the worker process without cleanup.
    task_soft_time_limit=settings.celery_task_timeout,

    # Reconnect to broker on startup — avoids Celery 5.4 deprecation warning
    # where broker_connection_retry was silently True but needed explicit opt-in.
    broker_connection_retry=True,
    broker_connection_retry_on_startup=True,

    # task_acks_late + task_reject_on_worker_lost: safe task delivery.
    # If the worker process crashes mid-task, the task is re-queued rather than lost.
    # This pairs with the Celery task itself checking for idempotency — if a session
    # was already finalised, finalise_session() exits early.
    task_acks_late=True,
    task_reject_on_worker_lost=True,

    # Explicit no-retry policy — LLM pipeline failures require human inspection.
    # Do NOT change this to 1 or more without implementing a retry budget check
    # that prevents infinite Claude API re-billing.
    task_max_retries=0,
)
