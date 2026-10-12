"""Structured and privacy-conscious logging for Careline.

The request middleware records operational data useful for troubleshooting,
but intentionally excludes authorization headers, cookies, request bodies,
query strings, and patient data.
"""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from time import perf_counter
from typing import Any
from uuid import UUID, uuid4

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

request_id_context: ContextVar[str] = ContextVar("request_id", default="-")

# LogRecord fields are excluded from the structured metadata object because
# they are already represented by stable top-level JSON properties.
STANDARD_LOG_RECORD_FIELDS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "taskName",
    "thread",
    "threadName",
}


class JsonFormatter(logging.Formatter):
    """Serialize each log record as one machine-readable JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
            "request_id": getattr(record, "request_id", request_id_context.get()),
        }

        # Explicit ``extra`` fields provide detailed operational context. The
        # default=str fallback handles UUIDs and other safe scalar objects.
        for key, value in record.__dict__.items():
            if key not in STANDARD_LOG_RECORD_FIELDS and key not in payload:
                payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str, separators=(",", ":"))


def configure_logging(level: str) -> None:
    """Configure the root logger once, updating its level on later calls."""

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Reusing our handler avoids duplicate lines when create_app is called by
    # multiple tests in the same Python process.
    handler = next(
        (item for item in root_logger.handlers if getattr(item, "careline_handler", False)),
        None,
    )
    if handler is None:
        handler = logging.StreamHandler(sys.stdout)
        handler.careline_handler = True  # type: ignore[attr-defined]
        handler.setFormatter(JsonFormatter())
        root_logger.addHandler(handler)
    handler.setLevel(level)

    # These libraries may log complete URLs, including query strings. Careline
    # emits its own sanitized request records, so routine third-party access
    # logs are suppressed while warnings and errors remain visible.
    for logger_name in ("httpx", "httpcore", "uvicorn.access"):
        logging.getLogger(logger_name).setLevel(logging.WARNING)


def _request_id_from_header(value: str | None) -> str:
    """Accept only UUID request IDs; generate one for absent/unsafe values."""

    if value:
        try:
            return str(UUID(value))
        except ValueError:
            pass
    return str(uuid4())


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Attach a request ID and emit one completion log per HTTP request."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        request_id = _request_id_from_header(request.headers.get("X-Request-ID"))
        context_token = request_id_context.set(request_id)
        request.state.request_id = request_id
        started_at = perf_counter()
        status_code = 500
        outcome = "error"

        try:
            response = await call_next(request)
            status_code = response.status_code
            outcome = "success" if status_code < 400 else "client_or_server_error"
            response.headers["X-Request-ID"] = request_id
            return response
        except Exception as exc:
            # Log only the exception class here. Exception messages can contain
            # database values or other sensitive context and are not safe by
            # default. Global error handling will be added later in Milestone 2.
            logging.getLogger("careline.request").error(
                "request_unhandled_exception",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "error_type": type(exc).__name__,
                },
            )
            raise
        finally:
            duration_ms = round((perf_counter() - started_at) * 1000, 2)
            logging.getLogger("careline.request").info(
                "request_completed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                    "outcome": outcome,
                },
            )
            request_id_context.reset(context_token)
