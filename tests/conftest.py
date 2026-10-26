"""Real PostgreSQL tests; create and remove only a uniquely named test database."""
import os
from uuid import uuid4
from pathlib import Path

# Test-only signing secret; configured before the application is imported.
os.environ["JWT_SECRET"] = "integration-test-secret-never-use-in-deployment-12345"

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.main import app
from app.models import Customer, Product
from app.core.auth import password_hasher, create_access_token

TEST_PASSWORD = "test-password-12345"
TEST_HASH = password_hasher.hash(TEST_PASSWORD)


def migration_config(connection):
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.attributes["connection"] = connection
    return config


@pytest.fixture(scope="session")
def pg_engine():
    url = os.getenv("TEST_POSTGRES_URL")
    if not url:
        pytest.fail("Set TEST_POSTGRES_URL to a PostgreSQL server with CREATEDB permission")
    url = make_url(url)
    if url.get_backend_name() != "postgresql":
        pytest.fail("Integration tests require PostgreSQL")
    name = "ecommerce_test_" + uuid4().hex
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    engine = None
    created = False
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
            created = True
        engine = create_engine(url.set(database=name))
        with engine.begin() as connection:
            command.upgrade(migration_config(connection), "head")
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        if created:
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def seeded(pg_engine):
    with pg_engine.begin() as connection:
        connection.execute(text(
            "TRUNCATE ecommerce.payment, ecommerce.order_items, ecommerce.orders, "
            "ecommerce.products, ecommerce.customers, ecommerce.categories CASCADE"
        ))
    with Session(pg_engine) as session:
        customer = Customer(name="Test customer", email="customer@example.com", password_hash=TEST_HASH)
        product = Product(name="Test product", unit_price=25, stock=5)
        session.add_all([customer, product])
        session.commit()
        return {"customer_id": customer.id, "items": [{"product_id": product.id, "quantity": 2}]}


@pytest.fixture
def raw_client(pg_engine):
    def test_db():
        with Session(pg_engine, autoflush=False, expire_on_commit=False) as session:
            try:
                yield session
            except Exception:
                session.rollback()
                raise

    app.dependency_overrides[get_db] = test_db
    try:
        with TestClient(app, raise_server_exceptions=False) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def client(raw_client, seeded):
    """Existing order tests exercise authentication with a real signed token."""
    raw_client.headers["Authorization"] = "Bearer " + create_access_token(seeded["customer_id"])
    return raw_client
