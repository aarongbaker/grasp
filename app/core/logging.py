"""
core/logging.py
Structured logging setup for GRASP.

Uses structlog for JSON output in production, pretty-print in development.
Integrates with stdlib logging so all loggers (including third-party) are
captured through the same pipeline.

Why structlog over stdlib logging directly?
  - JSON output in production: each log line is a parseable JSON object
    that Railway/Datadog/CloudWatch can index and query on structured fields
  - Context variables: bind_session_context() adds session_id to all subsequent
    log lines in a request without passing it through every function call
  - Pretty-print in development: the ConsoleRenderer adds colors, aligned
    columns, and readable timestamps without any configuration

The shared_processors list runs on EVERY log record regardless of which
logger created it (structlog or stdlib). This ensures consistent timestamp
format and log level naming across all libraries.

Third-party loggers are quieted to WARNING to avoid flooding logs with
httpx connection pool noise, OpenAI request/response dumps, etc.
"""

import logging
import sys

import structlog

from app.core.settings import get_settings


def setup_logging() -> None:
    """Configure structlog + stdlib logging. Call once at startup.

    Called at the top of app/main.py BEFORE any routers are imported.
    This ensures that even module-level loggers in route files use the
    configured handlers and formatters from the start.

    The ProcessorFormatter bridge is what makes stdlib loggers (e.g.
    logging.getLogger("uvicorn")) pass through structlog's processor chain.
    Without it, stdlib loggers would use the default unformatted output.
    """
    settings = get_settings()
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    # Processors applied to every log record from any source (structlog or stdlib).
    # Order matters — each processor receives the output of the previous one.
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,  # inject session_id etc. from bind_session_context()
        structlog.stdlib.add_log_level,            # adds "level": "info" to the event dict
        structlog.stdlib.add_logger_name,          # adds "logger": "app.graph.nodes.generator"
        structlog.processors.TimeStamper(fmt="iso"),  # adds "timestamp": "2024-01-01T12:00:00Z"
        structlog.processors.StackInfoRenderer(),  # renders exc_info stack traces
        structlog.processors.UnicodeDecoder(),     # decodes bytes to str for JSON compatibility
    ]

    # Production: JSON renderer — one JSON object per line, machine-readable.
    # Development: ConsoleRenderer — human-readable with color-coded levels.
    if settings.app_env == "production":
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            # wrap_for_formatter is the bridge to stdlib's ProcessorFormatter below.
            # It packs the event dict into a format that ProcessorFormatter can read.
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        # cache_logger_on_first_use: avoids re-creating the logger proxy on every log call.
        # Safe because the logger configuration is fixed after setup_logging() returns.
        cache_logger_on_first_use=True,
    )

    # ProcessorFormatter bridges structlog and stdlib.
    # - foreign_pre_chain: processors for records from stdlib loggers (httpx, uvicorn, etc.)
    # - processors: final rendering applied to all records (structlog + stdlib)
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        foreign_pre_chain=shared_processors,
    )

    # Replace the root logger's handlers with our structlog handler.
    # root_logger.handlers.clear() removes the default StreamHandler that Python
    # adds automatically — without this, you'd get duplicate log output.
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)

    # Quiet noisy third-party loggers that would otherwise flood development output.
    # WARNING level still captures actual errors from these libraries.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)

    if settings.app_env == "production":
        # Railway log quotas get overwhelmed by per-request access logs during
        # polling-heavy UI flows (GET /sessions/{id} every 2 seconds).
        # Keep application-level errors (uvicorn.error) but drop routine 2xx
        # access log noise (uvicorn.access). This prevents Railway from
        # rate-limiting the log stream during high-traffic periods.
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
        logging.getLogger("uvicorn.error").setLevel(log_level)


def bind_session_context(session_id: str) -> None:
    """Bind session_id to structlog context vars for correlation.

    After this call, all subsequent log lines in the same async task/thread
    will include "session_id": "<uuid>" automatically. This makes it easy
    to grep logs for all activity related to a specific pipeline run.

    Call this at the start of each Celery task or request handler that
    processes a specific session.
    """
    structlog.contextvars.bind_contextvars(session_id=session_id)


def clear_session_context() -> None:
    """Clear bound context vars.

    Call at the end of each Celery task to prevent session_id from leaking
    into the next task that runs on the same worker process.
    """
    structlog.contextvars.clear_contextvars()
