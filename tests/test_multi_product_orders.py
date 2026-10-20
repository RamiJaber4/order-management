from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from models import Order, OrderItem, Product


@pytest.fixture
def basket(pg_engine, seeded):
    with Session(pg_engine) as session:
        product = Product(name="Second product", unit_price=40, stock=4)
        session.add(product)
        session.commit()
        return {**seeded, "items": [*seeded["items"], {"product_id": product.id, "quantity": 1}]}


def assert_unchanged(engine, basket):
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Order)) == 0
        assert session.scalar(select(func.count()).select_from(OrderItem)) == 0
        assert [session.get(Product, item["product_id"]).stock for item in basket["items"]] == [5, 4]


def test_multiple_products_share_one_order(client, pg_engine, basket):
    response = client.post("/orders", json=basket)
    assert response.status_code == 201
    order_id = response.json()["order_id"]
    with Session(pg_engine) as session:
        assert session.scalar(select(func.count()).select_from(Order)) == 1
        items = session.scalars(select(OrderItem).order_by(OrderItem.product_id)).all()
        assert [(i.order_id, i.product_id, i.quantity, i.price, i.total_price) for i in items] == [
            (order_id, basket["items"][0]["product_id"], 2, 25, 50),
            (order_id, basket["items"][1]["product_id"], 1, 40, 40),
        ]
        assert [session.get(Product, item["product_id"]).stock for item in basket["items"]] == [3, 3]
    detail = client.get(f"/orders/{order_id}")
    assert detail.status_code == 200
    assert {i["product_id"] for i in detail.json()["items"]} == {i["product_id"] for i in basket["items"]}


@pytest.mark.parametrize("failure,status", [("missing", 404), ("stock", 409)])
def test_one_unavailable_product_rejects_entire_order(client, pg_engine, basket, failure, status):
    second = {**basket["items"][1]}
    second.update({"product_id": 2147483647} if failure == "missing" else {"quantity": 5})
    response = client.post("/orders", json={**basket, "items": [basket["items"][0], second]})
    assert response.status_code == status
    assert_unchanged(pg_engine, basket)


@pytest.mark.parametrize("invalid", ["empty", "duplicate", "missing_items", "invalid_id", "invalid_quantity"])
def test_invalid_basket_is_rejected_before_writes(client, pg_engine, basket, invalid):
    payload = {**basket}
    if invalid == "empty":
        payload["items"] = []
    elif invalid == "duplicate":
        payload["items"] = [basket["items"][0], basket["items"][0]]
    elif invalid == "missing_items":
        del payload["items"]
    else:
        item = {**basket["items"][0], "product_id" if invalid == "invalid_id" else "quantity": 0}
        payload["items"] = [item]
    assert client.post("/orders", json=payload).status_code == 422
    assert_unchanged(pg_engine, basket)


def test_item_insert_failure_rolls_back_every_product(client, pg_engine, basket):
    second_id = basket["items"][1]["product_id"]
    with pg_engine.begin() as connection:
        connection.execute(text(
            f"ALTER TABLE ecommerce.order_items ADD CONSTRAINT test_second_item_failure CHECK (product_id <> {second_id})"
        ))
    try:
        assert client.post("/orders", json=basket).status_code == 500
        assert_unchanged(pg_engine, basket)
    finally:
        with pg_engine.begin() as connection:
            connection.execute(text("ALTER TABLE ecommerce.order_items DROP CONSTRAINT test_second_item_failure"))
    assert client.post("/orders", json=basket).status_code == 201


def test_opposite_basket_order_cannot_oversell_or_deadlock(client, pg_engine, basket):
    with Session(pg_engine) as session:
        for item in basket["items"]:
            session.get(Product, item["product_id"]).stock = 1
        session.commit()
    items = [{**item, "quantity": 1} for item in basket["items"]]
    barrier = Barrier(2)

    def place_order(reverse):
        barrier.wait(timeout=10)
        return client.post("/orders", json={**basket, "items": items[::-1] if reverse else items}).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(place_order, reverse) for reverse in (False, True)]
        assert sorted(f.result(timeout=20) for f in futures) == [201, 409]
    with Session(pg_engine) as session:
        assert session.scalar(select(func.count()).select_from(Order)) == 1
        assert session.scalar(select(func.count()).select_from(OrderItem)) == 2
        assert all(session.get(Product, item["product_id"]).stock == 0 for item in items)
