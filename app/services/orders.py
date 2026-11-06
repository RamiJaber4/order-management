"""Order operations with explicit transaction ownership."""
import hashlib
import json
import logging

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, selectinload

from app.models import Customer, OrderItem, Product, Order
from app.schemas import OrderCreate

logger = logging.getLogger(__name__)


def get_order_by_id(id : int, session: Session):
    stm = select(Order).options(selectinload(Order.items)).where(Order.id == id)
    return session.scalars(stm).one_or_none()

def get_customer_order(
    id: int,
    session: Session,
    limit: int = 20,
    offset: int = 0
):
    stmt = (
        select(Order)
        .options(selectinload(Order.items))
        .where(Order.customer_id == id)
        .order_by(Order.id)
        .limit(limit)
        .offset(offset)
    )

    return session.scalars(stmt).all()

def create_order(
    order: OrderCreate,
    customer_id: int,
    session: Session,
    idempotency_key: str | None = None,
):
    try:
        if session.get(Customer, customer_id) is None:
            raise HTTPException(status_code=404, detail="Customer not found")
        # Reserve the key before locking products. PostgreSQL waits for a competing
        # insert to commit/roll back, so no process-local locks or pending records
        # are needed. The reservation rolls back with stock and items on failure.
        fingerprint = None
        if idempotency_key is not None:
            basket = sorted((item.product_id, item.quantity) for item in order.items)
            fingerprint = hashlib.sha256(json.dumps(basket, separators=(",", ":")).encode()).hexdigest()
        statement = insert(Order).values(
            customer_id=customer_id,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
        )
        if idempotency_key is not None:
            statement = statement.on_conflict_do_nothing(constraint="orders_customer_idempotency_key")
        order_id = session.scalar(statement.returning(Order.id))
        if order_id is None:
            existing = session.execute(
                select(Order.id, Order.request_fingerprint).where(
                    Order.customer_id == customer_id,
                    Order.idempotency_key == idempotency_key,
                )
            ).one()
            if existing.request_fingerprint != fingerprint:
                raise HTTPException(status_code=409, detail="Idempotency key already used with a different basket")
            session.commit()
            return {"msg": "order created", "order_id": existing.id}
        # Acquire shared product locks in the same order for every request.
        # This reduces deadlocks when baskets list products in different orders.
        products = {
            product.id: product for product in session.scalars(
                select(Product)
                .where(Product.id.in_([item.product_id for item in order.items]))
                .order_by(Product.id)
                .with_for_update()
            )
        }
        for item in order.items:
            product = products.get(item.product_id)
            if product is None:
                raise HTTPException(status_code=404, detail="Product not found")
            if product.unit_price is None or product.unit_price <= 0:
                raise HTTPException(status_code=409, detail="Product has no valid price")
            if product.stock is None or product.stock < item.quantity:
                raise HTTPException(status_code=409, detail="Insufficient stock")

        for item in order.items:
            products[item.product_id].stock -= item.quantity
        session.flush()  # Persist stock changes before inserting items.
        for item in order.items:
            session.add(OrderItem(
                order_id=order_id,
                product_id=item.product_id,
                quantity=item.quantity,
                price=products[item.product_id].unit_price,
            ))
        session.commit()
    except Exception:
        session.rollback()
        raise

    logger.info("order_created", extra={"order_id": order_id, "item_count": len(order.items)})
    return {"msg": "order created", "order_id": order_id}
