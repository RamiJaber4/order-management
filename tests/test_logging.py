"""Logging guarantees require no database connection."""
import asyncio
import json
import logging
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core.error_logging import HTTP_REASONS, install_error_logging

from app.core.logging_config import JsonFormatter, RequestLoggingMiddleware, request_id_context


@pytest.mark.parametrize("detail,reason", [(detail, value[0]) for detail, value in HTTP_REASONS.items()])
def test_rejection_has_safe_reason_and_request_id(caplog, detail, reason):
    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)
    install_error_logging(app)

    @app.get("/reject")
    def reject():
        raise HTTPException(409, detail)

    with caplog.at_level(logging.WARNING), TestClient(app) as client:
        response = client.get("/reject?token=super-secret", headers={"Authorization": "Bearer private"})
    record = next(r for r in caplog.records if r.msg == "request_rejected")
    assert record.reason == reason
    assert record.description
    assert response.status_code == 409
    assert len(response.headers["x-request-id"]) == 32
    output = JsonFormatter().format(record)
    assert "super-secret" not in output and "Bearer private" not in output


def test_validation_logs_safe_fields_without_input_or_unknown_keys(caplog):
    from app.schemas import CustomerCreate

    app = FastAPI()
    install_error_logging(app)

    @app.post("/register")
    def register(data: CustomerCreate):
        return {}

    with caplog.at_level(logging.WARNING), TestClient(app) as client:
        response = client.post("/register", json={
            "name": "Private Name", "email": "private-email", "password": "xyz",
            "secret-in-field-name": "secret-in-value",
        })
    assert response.status_code == 422
    record = next(r for r in caplog.records if r.msg == "request_rejected")
    output = JsonFormatter().format(record)
    assert record.reason == "request_validation_failed"
    assert record.error_count == 3
    assert any(issue["field"] == ["body", "password"] for issue in record.validation_errors)
    for secret in ("Private Name", "private-email", "xyz", "secret-in-field-name", "secret-in-value"):
        assert secret not in output


@pytest.mark.parametrize("reason", ["credentials_missing", "token_expired", "token_invalid", "account_unavailable"])
def test_auth_reasons_preserve_generic_response(caplog, reason):
    from app.core.auth import unauthorized

    app = FastAPI()
    install_error_logging(app)

    @app.get("/protected")
    def protected():
        raise unauthorized(reason)

    with caplog.at_level(logging.WARNING), TestClient(app) as client:
        response = client.get("/protected")
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid or expired credentials"}
    assert response.headers["www-authenticate"] == "Bearer"
    assert next(r for r in caplog.records if r.msg == "request_rejected").reason == reason


def test_unknown_http_detail_is_never_logged(caplog):
    app = FastAPI()
    install_error_logging(app)

    @app.get("/reject")
    def reject():
        raise HTTPException(400, "password=super-secret")

    with caplog.at_level(logging.WARNING), TestClient(app) as client:
        assert client.get("/reject").status_code == 400
    record = next(r for r in caplog.records if r.msg == "request_rejected")
    assert record.reason == "http_request_rejected"
    assert "super-secret" not in JsonFormatter().format(record)


def test_database_errors_have_safe_actionable_description():
    from app.core.error_logging import unexpected_error_details
    from sqlalchemy.exc import OperationalError

    error = OperationalError("SELECT private", {"password": "secret"}, Exception("credentials"))
    details = unexpected_error_details(error)
    assert details["reason"] == "database_operation_failed"
    assert "availability" in details["description"]
    assert "secret" not in json.dumps(details)


def test_exception_logs_omit_sensitive_values():
    try:
        raise ValueError("password=private token=secret")
    except ValueError:
        import sys
        record = logging.LogRecord("app", logging.ERROR, __file__, 1,
                                   "request_failed", (), sys.exc_info())
    output = JsonFormatter().format(record)
    entry = json.loads(output)
    assert entry["exception_type"] == "ValueError"
    assert entry["stack"]
    assert "private" not in output
    assert "secret" not in output


def test_requests_are_correlated_without_raw_urls(caplog):
    ids = []

    async def endpoint(scope, receive, send):
        ids.append(request_id_context.get())
        await send({"type": "http.response.start", "status": 404, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def run():
        for _ in range(2):
            messages = []

            async def send(message):
                messages.append(message)

            await RequestLoggingMiddleware(endpoint)(
                {"type": "http", "method": "GET", "path": "/private-email",
                 "query_string": b"token=secret", "headers": []}, None, send,
            )
            assert (b"x-request-id", ids[-1].encode()) in messages[0]["headers"]
            assert request_id_context.get() is None

    with caplog.at_level(logging.INFO):
        asyncio.run(run())
    records = [record for record in caplog.records if record.msg == "request_completed"]
    assert len(records) == 2
    assert ids[0] != ids[1]
    assert all(record.route == "<unmatched>" and record.status_code == 404 for record in records)
    assert "secret" not in caplog.text
    assert "private-email" not in caplog.text


def test_failed_requests_log_and_restore_context(caplog):
    async def endpoint(scope, receive, send):
        raise RuntimeError("sensitive exception")

    async def run():
        messages = []

        async def send(message):
            messages.append(message)

        await RequestLoggingMiddleware(endpoint)(
            {"type": "http", "method": "GET"}, None, send,
        )
        assert messages[0]["status"] == 500
        assert b"x-request-id" in dict(messages[0]["headers"])
        assert request_id_context.get() is None

    with caplog.at_level(logging.ERROR):
        asyncio.run(run())
    records = [r for r in caplog.records if r.name == "app.core.logging_config"]
    assert [r.msg for r in records] == ["request_failed", "request_completed"]
    assert records[-1].status_code == 500
    assert "sensitive exception" not in JsonFormatter().format(records[0])
