from datetime import datetime, timedelta, timezone

import jwt
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from auth import ALGORITHM, JWT_SECRET, create_access_token, password_hasher
from conftest import TEST_HASH, TEST_PASSWORD
from models import Customer


def authorization(customer_id):
    return {"Authorization": "Bearer " + create_access_token(customer_id)}


def test_register_login_and_me(raw_client, pg_engine, seeded):
    data = {"name": "New user", "email": "new@example.com", "password": TEST_PASSWORD}
    assert raw_client.post("/auth/register", json=data).status_code == 201
    with Session(pg_engine) as session:
        customer = session.scalar(select(Customer).where(Customer.email == data["email"]))
        assert customer.role == "customer"
        assert customer.password_hash != TEST_PASSWORD
        assert password_hasher.verify(TEST_PASSWORD, customer.password_hash)
    response = raw_client.post("/auth/login", json={"email": data["email"], "password": TEST_PASSWORD})
    assert response.status_code == 200
    assert response.json()["token_type"] == "bearer"
    me = raw_client.get("/auth/me", headers={"Authorization": "Bearer " + response.json()["access_token"]})
    assert me.status_code == 200
    assert set(me.json()) == {"id", "name", "email", "role"}
    assert me.json()["email"] == data["email"]


@pytest.mark.parametrize("extra", [{"role": "admin"}, {"password_hash": "fake"}])
def test_registration_rejects_privilege_fields(raw_client, seeded, extra):
    assert raw_client.post("/auth/register", json={
        "name": "Attacker", "email": "attacker@example.com", "password": TEST_PASSWORD, **extra,
    }).status_code == 422


@pytest.mark.parametrize("email,password", [
    ("missing@example.com", TEST_PASSWORD),
    ("customer@example.com", "wrong-password"),
])
def test_invalid_login_is_generic(raw_client, seeded, email, password):
    response = raw_client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or expired credentials"


def test_legacy_customer_cannot_login(raw_client, pg_engine, seeded):
    with Session(pg_engine) as session:
        session.get(Customer, seeded["customer_id"]).password_hash = None
        session.commit()
    assert raw_client.post("/auth/login", json={
        "email": "customer@example.com", "password": TEST_PASSWORD,
    }).status_code == 401


@pytest.mark.parametrize("kind", ["missing", "garbage", "expired", "signature", "no_exp", "huge_sub", "deleted"])
def test_invalid_credentials_rejected(raw_client, seeded, kind):
    now = datetime.now(timezone.utc)
    claims = {"sub": str(seeded["customer_id"]), "iat": now, "exp": now + timedelta(minutes=15)}
    secret = JWT_SECRET
    if kind == "expired":
        claims["exp"] = now - timedelta(seconds=1)
    elif kind == "signature":
        secret = "another-signing-secret-that-is-long-enough"
    elif kind == "no_exp":
        del claims["exp"]
    elif kind == "huge_sub":
        claims["sub"] = "9" * 100
    elif kind == "deleted":
        claims["sub"] = "2147483647"
    token = "garbage" if kind == "garbage" else jwt.encode(claims, secret, algorithm=ALGORITHM)
    headers = {} if kind == "missing" else {"Authorization": "Bearer " + token}
    response = raw_client.get("/auth/me", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_anonymous_can_only_browse_products(raw_client, seeded):
    assert raw_client.get("/products").status_code == 200
    assert raw_client.post("/orders", json={"items": seeded["items"]}).status_code == 401
    assert raw_client.post("/products", json={"name": "X", "stock": 1, "unit_price": 1}).status_code == 401
    assert raw_client.get("/orders/1").status_code == 401
    assert raw_client.get(f"/customers/{seeded['customer_id']}/orders").status_code == 401


def test_owner_other_customer_and_live_admin_role(client, pg_engine, seeded):
    order_id = client.post("/orders", json={"items": seeded["items"]}).json()["order_id"]
    with Session(pg_engine) as session:
        other = Customer(name="Other", email="other@example.com", password_hash=TEST_HASH)
        session.add(other)
        session.commit()
        other_id = other.id
    headers = authorization(other_id)
    detail_url = f"/orders/{order_id}"
    list_url = f"/customers/{seeded['customer_id']}/orders"
    product = {"name": "Admin product", "stock": 1, "unit_price": 1}
    assert client.get(detail_url).status_code == 200
    assert client.get(detail_url, headers=headers).status_code == 403
    assert client.get(list_url, headers=headers).status_code == 403
    assert client.post("/products", json=product, headers=headers).status_code == 403
    with Session(pg_engine) as session:
        session.get(Customer, other_id).role = "admin"
        session.commit()
    # Same token, permissions loaded from the database on each request.
    assert client.get(detail_url, headers=headers).status_code == 200
    assert client.get(list_url, headers=headers).status_code == 200
    assert client.post("/products", json=product, headers=headers).status_code == 200
    with Session(pg_engine) as session:
        session.get(Customer, other_id).role = "customer"
        session.commit()
    assert client.get(detail_url, headers=headers).status_code == 403


def test_order_rejects_client_supplied_owner(client, seeded):
    assert client.post("/orders", json={**seeded}).status_code == 422


def test_registration_rejects_invalid_email_and_short_password(raw_client, seeded):
    for email, password in [("invalid", TEST_PASSWORD), ("new@example.com", "short")]:
        assert raw_client.post("/auth/register", json={
            "name": "New", "email": email, "password": password,
        }).status_code == 422
