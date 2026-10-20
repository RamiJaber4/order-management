from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import Order, OrderItem, Product


def persisted_state(engine, product_id):
    # A fresh connection observes committed state, independently of the request.
    with Session(engine) as session:
        return (
            session.scalar(select(func.count()).select_from(Order)),
            session.scalar(select(func.count()).select_from(OrderItem)),
            session.get(Product, product_id).stock,
        )


def test_success_commits_order_item_and_stock(client, pg_engine, seeded):
    response = client.post("/orders", json=seeded)
    assert response.status_code == 201
    assert persisted_state(pg_engine, seeded["items"][0]["product_id"]) == (1, 1, 3)
    with Session(pg_engine) as session:
        order = session.get(Order, response.json()["order_id"])
        assert order.customer_id == seeded["customer_id"]
        item = session.scalar(select(OrderItem))
        assert (item.order_id, item.product_id, item.quantity, item.price, item.total_price) == (
            order.id, seeded["items"][0]["product_id"], 2, 25, 50
        )


@pytest.mark.parametrize("change,status,detail", [
    ({"customer_id": 2147483647}, 404, "Customer not found"),
    ({"items": [{"product_id": 2147483647, "quantity": 2}]}, 404, "Product not found"),
    ({"quantity": 6}, 409, "Insufficient stock"),
    ({"quantity": 0}, 422, None),
    ({"quantity": -1}, 422, None),
])
def test_rejected_order_leaves_no_partial_data(client, pg_engine, seeded, change, status, detail):
    payload = {**seeded, **change}
    if "quantity" in change:
        payload = {**seeded, "items": [{**seeded["items"][0], "quantity": change["quantity"]}]}
    response = client.post("/orders", json=payload)
    assert response.status_code == status
    if detail:
        assert response.json()["detail"] == detail
    assert persisted_state(pg_engine, seeded["items"][0]["product_id"]) == (0, 0, 5)


@pytest.mark.parametrize("column", ["stock", "unit_price"])
def test_database_rejects_null_product_values(pg_engine, seeded, column):
    with Session(pg_engine) as session:
        setattr(session.get(Product, seeded["items"][0]["product_id"]), column, None)
        with pytest.raises(IntegrityError) as error:
            session.commit()
        assert error.value.orig.sqlstate == "23502"
        session.rollback()
    assert persisted_state(pg_engine, seeded["items"][0]["product_id"]) == (0, 0, 5)


def test_database_failure_rolls_back_flushed_order_and_stock(client, pg_engine, seeded):
    # Force a real PostgreSQL failure on the item INSERT, after order and stock flush.
    with pg_engine.begin() as connection:
        connection.execute(text(
            "ALTER TABLE ecommerce.order_items ADD CONSTRAINT test_item_failure CHECK (quantity < 2)"
        ))
    try:
        response = client.post("/orders", json=seeded)
        assert response.status_code == 500
        assert persisted_state(pg_engine, seeded["items"][0]["product_id"]) == (0, 0, 5)
    finally:
        with pg_engine.begin() as connection:
            connection.execute(text(
                "ALTER TABLE ecommerce.order_items DROP CONSTRAINT test_item_failure"
            ))
    # A subsequent request still succeeds after the rollback.
    assert client.post("/orders", json=seeded).status_code == 201
    assert persisted_state(pg_engine, seeded["items"][0]["product_id"]) == (1, 1, 3)


def test_concurrent_orders_cannot_oversell(client, pg_engine, seeded):
    with Session(pg_engine) as session:
        session.get(Product, seeded["items"][0]["product_id"]).stock = 1
        session.commit()
    barrier = Barrier(2)

    def place_order():
        barrier.wait(timeout=10)
        return client.post("/orders", json={**seeded, "items": [{**seeded["items"][0], "quantity": 1}]}).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(place_order) for _ in range(2)]
        statuses = [future.result(timeout=20) for future in futures]
    assert sorted(statuses) == [201, 409]
    assert persisted_state(pg_engine, seeded["items"][0]["product_id"]) == (1, 1, 0)
