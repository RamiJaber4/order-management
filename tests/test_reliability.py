"""Safe HTTP failures, independent probes, recovery and bounded PostgreSQL waits."""
import json
import logging
from time import perf_counter

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError, TimeoutError as PoolTimeoutError

from database_settings import connection_arguments
from db import get_db
from error_logging import install_error_logging
from logging_config import JsonFormatter, RequestLoggingMiddleware
from main import app


@pytest.fixture
def probe_client():
    class Database:
        available = True
        calls = 0

        def execute(self, statement):
            self.calls += 1
            if not self.available:
                raise OperationalError("private SQL", {"password": "private-value"}, Exception("private-error"))

    database = Database()

    def session():
        yield database

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = session
    try:
        with TestClient(app) as client:
            yield client, database
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def test_liveness_is_independent_and_readiness_recovers(probe_client, caplog):
    client, database = probe_client
    with caplog.at_level(logging.INFO):
        assert client.get("/health/ready").status_code == 200
        database.available = False
        calls = database.calls
        live = client.get("/health/live")
        assert live.status_code == 200
        assert database.calls == calls
        for path in ("/health/ready", "/health"):
            response = client.get(path)
            assert response.status_code == 503
            assert response.json() == {"detail": "Service temporarily unavailable"}
            assert response.headers["x-request-id"]
        database.available = True
        assert client.get("/health/ready").status_code == 200
    completion = [r for r in caplog.records if r.msg == "request_completed"]
    assert len(completion) == 5
    assert all(r.levelno >= logging.INFO for r in completion)
    output = "\n".join(JsonFormatter().format(r) for r in caplog.records)
    for secret in ("private SQL", "private-value", "private-error"):
        assert secret not in output


@pytest.mark.parametrize("error,status", [
    (RuntimeError("private-error"), 500),
    (OperationalError("private SQL", {}, Exception("private-error")), 503),
    (PoolTimeoutError("private-error"), 503),
    (IntegrityError("private SQL", {}, Exception("private-error")), 500),
    (ProgrammingError("private SQL", {}, Exception("private-error")), 500),
    (HTTPException(500, "private-error", headers={"x-private": "private-header"}), 500),
    (HTTPException(503, {"password": "private-error"}), 503),
])
def test_server_errors_are_safe_correlated_and_logged(error, status, caplog):
    application = FastAPI()
    application.add_middleware(RequestLoggingMiddleware)
    install_error_logging(application)
    captured = []

    class Capture(logging.Handler):
        def emit(self, record):
            captured.append(JsonFormatter().format(record))

    handler = Capture()
    logging.getLogger().addHandler(handler)

    @application.get("/fail")
    def fail():
        raise error

    try:
        with caplog.at_level(logging.INFO), TestClient(application) as client:
            response = client.get("/fail")
    finally:
        logging.getLogger().removeHandler(handler)
    assert response.status_code == status
    request_id = response.headers["x-request-id"]
    events = [json.loads(line) for line in captured if json.loads(line).get("request_id") == request_id]
    assert any(event["message"] == "request_completed" and event["status_code"] == status for event in events)
    assert any(event["message"] in ("request_failed", "request_rejected") for event in events)
    for secret in ("private-error", "private SQL", "private-header"):
        assert secret not in response.text
        assert secret not in str(response.headers)
        assert secret not in "\n".join(captured)


@pytest.mark.parametrize("state,status", [("08006", 503), ("57014", 503), ("55P03", 503), ("42501", 500)])
def test_operational_errors_classified_by_sqlstate(state, status):
    from error_logging import database_unavailable

    class DriverError(Exception):
        sqlstate = state

    error = OperationalError("private", {}, DriverError())
    assert database_unavailable(error) == (status == 503)


def test_postgresql_statement_and_pool_timeouts(pg_engine, monkeypatch):
    monkeypatch.setenv("DB_STATEMENT_TIMEOUT_MS", "150")
    engine = create_engine(pg_engine.url, connect_args=connection_arguments(),
                           pool_size=1, max_overflow=0, pool_timeout=0.2)
    try:
        with engine.connect() as connection:
            started = perf_counter()
            with pytest.raises(OperationalError) as error:
                connection.execute(text("SELECT pg_sleep(2)"))
            assert error.value.orig.sqlstate == "57014"
            assert perf_counter() - started < 1.5
            connection.rollback()
            with pytest.raises(PoolTimeoutError):
                with engine.connect():
                    pass
            assert connection.scalar(text("SELECT 1")) == 1
    finally:
        engine.dispose()


def test_postgresql_lock_timeout(pg_engine, monkeypatch):
    monkeypatch.setenv("DB_LOCK_TIMEOUT_MS", "150")
    engine = create_engine(pg_engine.url, connect_args=connection_arguments())
    try:
        with engine.begin() as owner, engine.connect() as waiter:
            owner.execute(text("SELECT pg_advisory_xact_lock(714250031)"))
            with pytest.raises(OperationalError) as error:
                waiter.execute(text("SELECT pg_advisory_xact_lock(714250031)"))
            assert error.value.orig.sqlstate == "55P03"
    finally:
        engine.dispose()
