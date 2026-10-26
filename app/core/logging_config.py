"""Structured stdout logging and request correlation without sensitive payloads."""
import json
import logging
import os
import sys
import traceback
from contextvars import ContextVar
from datetime import datetime, timezone
from logging.config import dictConfig
from time import perf_counter
from uuid import uuid4

request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)
logger = logging.getLogger(__name__)


class JsonFormatter(logging.Formatter):
    def format(self, record):
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "environment": os.getenv("APP_ENV", "development"),
            "message": record.getMessage(),
        }
        request_id = request_id_context.get()
        if request_id:
            entry["request_id"] = request_id
        for field in ("method", "route", "status_code", "duration_ms", "order_id", "item_count",
                      "reason", "description", "validation_errors", "error_count"):
            if hasattr(record, field):
                entry[field] = getattr(record, field)
        if record.exc_info and record.exc_info[0]:
            # Exception strings, source lines and locals can expose SQL values/secrets.
            entry["exception_type"] = record.exc_info[0].__name__
            entry["stack"] = [
                {"file": os.path.basename(frame.filename), "line": frame.lineno,
                 "function": frame.name}
                for frame in traceback.extract_tb(record.exc_info[2])
            ]
        return json.dumps(entry, ensure_ascii=True)


def configure_logging():
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    if level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        raise ValueError("LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR or CRITICAL")
    dictConfig({
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"json": {"()": JsonFormatter}},
        "handlers": {"stdout": {"class": "logging.StreamHandler", "stream": sys.stdout,
                                  "formatter": "json"}},
        "root": {"handlers": ["stdout"], "level": level},
        "loggers": {
            "uvicorn": {"handlers": [], "propagate": True, "level": level},
            "uvicorn.error": {"handlers": [], "propagate": True, "level": level},
            # Our middleware replaces access logs, which otherwise include raw URLs.
            "uvicorn.access": {"handlers": [], "propagate": False},
            "sqlalchemy.engine": {"handlers": [], "propagate": True, "level": "WARNING"},
        },
    })


class RequestLoggingMiddleware:
    """Pure ASGI middleware preserves streaming and context in sync dependencies."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_id = uuid4().hex
        token = request_id_context.set(request_id)
        started = perf_counter()
        status = 500
        failed = False
        response_started = False

        async def send_response(message):
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                status = message["status"]
                response_started = True
                message = dict(message)
                message["headers"] = [
                    (key, value) for key, value in message.get("headers", [])
                    if key.lower() != b"x-request-id"
                ] + [(b"x-request-id", request_id.encode("ascii"))]
            await send(message)

        try:
            await self.app(scope, receive, send_response)
        except Exception as exc:
            from app.core.error_logging import database_unavailable, server_error_response, unexpected_error_details

            failed = True
            logger.exception("request_failed", extra=unexpected_error_details(exc))
            if response_started:
                # A streaming response cannot be replaced after headers were sent.
                raise
            response = server_error_response(503 if database_unavailable(exc) else 500)
            await response(scope, receive, send_response)
        finally:
            route = getattr(scope.get("route"), "path", "<unmatched>")
            level = logging.ERROR if failed or status >= 500 else (
                logging.WARNING if status >= 400 else logging.INFO
            )
            try:
                logger.log(level, "request_completed", extra={
                    "method": scope["method"], "route": route, "status_code": status,
                    "duration_ms": round((perf_counter() - started) * 1000, 2),
                })
            finally:
                request_id_context.reset(token)
