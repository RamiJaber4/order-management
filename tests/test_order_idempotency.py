from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.auth import create_access_token
from app.models import Customer, Order, Product
from test_orders import persisted_state


HEADERS = {"Idempotency-Key": "checkout-123"}


def test_retry_returns_original_response_after_stock_and_price_change(client, pg_engine, seeded):
    payload = {"items": seeded["items"]}
    first = client.post("/orders", json=payload, headers=HEADERS)
    assert first.status_code == 201
    with Session(pg_engine) as session:
        product = session.get(Product, seeded["items"][0]["product_id"])
        product.stock = 0
        product.unit_price = 99
        session.commit()
    retry = client.post("/orders", json=payload, headers=HEADERS)
    assert retry.status_code == 201
    assert retry.json() == first.json()
    assert persisted_state(pg_engine, seeded["items"][0]["product_id"]) == (1, 1, 0)
    detail = client.get(f"/orders/{first.json()['order_id']}").json()
    assert detail["items"][0]["price"] == 25


def test_basket_item_order_does_not_change_identity(client, pg_engine, seeded):
    with Session(pg_engine) as session:
        product = Product(name="Second product", unit_price=40, stock=1)
        session.add(product)
        session.commit()
        second_id = product.id
    items = [*seeded["items"], {"product_id": second_id, "quantity": 1}]
    first = client.post("/orders", json={"items": items}, headers=HEADERS)
    retry = client.post("/orders", json={"items": items[::-1]}, headers=HEADERS)
    assert first.status_code == retry.status_code == 201
    assert first.json() == retry.json()
    assert persisted_state(pg_engine, seeded["items"][0]["product_id"]) == (1, 2, 3)


@pytest.mark.parametrize("change", ["quantity", "product", "added_item"])
def test_key_cannot_be_reused_for_a_different_basket(client, pg_engine, seeded, change):
    first = client.post("/orders", json={"items": seeded["items"]}, headers=HEADERS)
    assert first.status_code == 201
    item = dict(seeded["items"][0])
    items = [item]
    if change == "quantity":
        item["quantity"] = 1
    elif change == "product":
        item["product_id"] = 2147483647
    else:
        items.append({"product_id": 2147483647, "quantity": 1})
    response = client.post("/orders", json={"items": items}, headers=HEADERS)
    assert response.status_code == 409
    assert response.json()["detail"] == "Idempotency key already used with a different basket"
    assert persisted_state(pg_engine, seeded["items"][0]["product_id"]) == (1, 1, 3)


def test_key_is_scoped_to_authenticated_customer(client, pg_engine, seeded):
    payload = {"items": seeded["items"]}
    first = client.post("/orders", json=payload, headers=HEADERS)
    with Session(pg_engine) as session:
        customer = Customer(name="Other", email="other@example.com")
        session.add(customer)
        session.commit()
        other_id = customer.id
    headers = {**HEADERS, "Authorization": "Bearer " + create_access_token(other_id)}
    second = client.post("/orders", json=payload, headers=headers)
    assert first.status_code == second.status_code == 201
    assert first.json()["order_id"] != second.json()["order_id"]
    with Session(pg_engine) as session:
        assert session.get(Order, second.json()["order_id"]).customer_id == other_id
    assert persisted_state(pg_engine, seeded["items"][0]["product_id"]) == (2, 2, 1)


@pytest.mark.parametrize("keys", [(None, None), ("checkout-1", "checkout-2")])
def test_separate_requests_can_create_identical_orders(client, pg_engine, seeded, keys):
    responses = [client.post("/orders", json={"items": seeded["items"]},
                             headers={} if key is None else {"Idempotency-Key": key}) for key in keys]
    assert all(response.status_code == 201 for response in responses)
    assert responses[0].json()["order_id"] != responses[1].json()["order_id"]
    assert persisted_state(pg_engine, seeded["items"][0]["product_id"]) == (2, 2, 1)


@pytest.mark.parametrize("key", ["", " ", " leading", "trailing ", "two words", "bad\tkey", "x" * 256])
def test_invalid_key_rejected_without_writes(client, pg_engine, seeded, key):
    response = client.post("/orders", json={"items": seeded["items"]}, headers={"Idempotency-Key": key})
    assert response.status_code == 422
    assert persisted_state(pg_engine, seeded["items"][0]["product_id"]) == (0, 0, 5)


def test_maximum_length_key_is_accepted(client, seeded):
    assert client.post("/orders", json={"items": seeded["items"]},
                       headers={"Idempotency-Key": "x" * 255}).status_code == 201


@pytest.mark.parametrize("failure", ["stock", "database"])
def test_failed_request_does_not_consume_key(client, pg_engine, seeded, failure):
    payload = {"items": seeded["items"]}
    if failure == "stock":
        bad_payload = {"items": [{**seeded["items"][0], "quantity": 6}]}
        assert client.post("/orders", json=bad_payload, headers=HEADERS).status_code == 409
    else:
        with pg_engine.begin() as connection:
            connection.execute(text(
                "ALTER TABLE ecommerce.order_items ADD CONSTRAINT test_idempotency_failure CHECK (quantity < 2)"
            ))
        try:
            assert client.post("/orders", json=payload, headers=HEADERS).status_code == 500
        finally:
            with pg_engine.begin() as connection:
                connection.execute(text("ALTER TABLE ecommerce.order_items DROP CONSTRAINT test_idempotency_failure"))
    assert persisted_state(pg_engine, seeded["items"][0]["product_id"]) == (0, 0, 5)
    success = client.post("/orders", json=payload, headers=HEADERS)
    retry = client.post("/orders", json=payload, headers=HEADERS)
    assert success.status_code == retry.status_code == 201
    assert success.json() == retry.json()
    assert persisted_state(pg_engine, seeded["items"][0]["product_id"]) == (1, 1, 3)


@pytest.mark.parametrize("different_basket", [False, True])
def test_simultaneous_key_reuse(client, pg_engine, seeded, different_basket):
    with Session(pg_engine) as session:
        session.get(Product, seeded["items"][0]["product_id"]).stock = 2
        session.commit()
    barrier = Barrier(2)

    def place_order(quantity):
        barrier.wait(timeout=10)
        return client.post("/orders", json={"items": [{**seeded["items"][0], "quantity": quantity}]},
                           headers=HEADERS)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(place_order, quantity) for quantity in (2, 1 if different_basket else 2)]
        responses = [future.result(timeout=20) for future in futures]
    if different_basket:
        assert sorted(response.status_code for response in responses) == [201, 409]
        with Session(pg_engine) as session:
            assert len(session.scalars(select(Order)).all()) == 1
    else:
        assert [response.status_code for response in responses] == [201, 201]
        assert responses[0].json() == responses[1].json()
        assert persisted_state(pg_engine, seeded["items"][0]["product_id"]) == (1, 1, 0)
