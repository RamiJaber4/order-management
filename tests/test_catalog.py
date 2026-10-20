import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models import Customer, Product


def test_created_product_can_be_ordered(client, pg_engine, seeded):
    assert client.post("/products", json={
        "name": "New product", "unit_price": 40, "stock": 3,
    }).status_code == 200
    with Session(pg_engine) as session:
        product = session.scalar(select(Product).where(Product.name == "New product"))
        product_id = product.id
        assert (product.unit_price, product.stock) == (40, 3)
    response = client.post("/orders", json={**seeded, "items": [{"product_id": product_id, "quantity": 2}]})
    assert response.status_code == 201
    with Session(pg_engine) as session:
        assert session.get(Product, product_id).stock == 1


@pytest.mark.parametrize("fields", [
    {}, {"unit_price": 10}, {"stock": 2},
    {"unit_price": None, "stock": 2}, {"unit_price": 10, "stock": None},
    {"unit_price": 0, "stock": 2}, {"unit_price": -1, "stock": 2},
    {"unit_price": 10, "stock": -1},
])
def test_invalid_product_is_not_saved(client, pg_engine, seeded, fields):
    assert client.post("/products", json={"name": "Invalid", **fields}).status_code == 422
    with Session(pg_engine) as session:
        assert session.scalar(select(func.count()).select_from(Product)) == 1


def test_zero_stock_product_is_valid_but_cannot_be_ordered(client, pg_engine, seeded):
    assert client.post("/products", json={
        "name": "Sold out", "unit_price": 10, "stock": 0,
    }).status_code == 200
    with Session(pg_engine) as session:
        product_id = session.scalar(select(Product.id).where(Product.name == "Sold out"))
    assert client.post("/orders", json={**seeded, "items": [{"product_id": product_id, "quantity": 2}]}).status_code == 409


def test_duplicate_email_returns_conflict_and_next_request_succeeds(client, pg_engine, seeded):
    response = client.post("/customers", json={"name": "Duplicate", "email": "customer@example.test"})
    assert response.status_code == 409
    assert response.json()["detail"] == "Email already exists"
    with Session(pg_engine) as session:
        assert session.scalar(select(func.count()).select_from(Customer)) == 1
    assert client.post("/customers", json={"name": "Next", "email": "next@example.test"}).status_code == 200


def test_product_offset_returns_next_product(client, pg_engine, seeded):
    client.post("/products", json={"name": "Second", "unit_price": 10, "stock": 2})
    first = client.get("/products", params={"limit": 1}).json()
    second = client.get("/products", params={"limit": 1, "offset": 1}).json()
    assert first[0]["id"] == seeded["items"][0]["product_id"]
    assert len(second) == 1
    assert second[0]["name"] == "Second"
