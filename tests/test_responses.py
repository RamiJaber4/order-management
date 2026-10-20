from datetime import datetime


def test_product_response_has_only_public_fields(client, seeded):
    response = client.get("/products")
    assert response.status_code == 200
    assert response.json() == [{
        "id": seeded["p_id"], "name": "Test product",
        "category_name": None, "unit_price": 25, "stock": 5,
    }]


def test_order_responses_serialize_orm_rows_and_nullable_fields(client, seeded):
    created = client.post("/orders", json=seeded)
    assert created.status_code == 201
    assert set(created.json()) == {"msg", "order_id"}
    order_id = created.json()["order_id"]
    response = client.get(f"/orders/{order_id}")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"id", "customer_id", "order_date", "notes"}
    assert body["id"] == order_id
    assert body["customer_id"] == seeded["customer_id"]
    assert body["notes"] is None
    datetime.fromisoformat(body["order_date"])
    listing = client.get(f"/customers/{seeded['customer_id']}/orders")
    assert listing.status_code == 200
    assert listing.json() == [body]
    assert client.get("/orders/2147483647").status_code == 404
