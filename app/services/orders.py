"""Order operations with explicit transaction ownership."""
import logging

from fastapi import HTTPException
from sqlalchemy import select
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
    session :Session        
):
    try:
        if session.get(Customer, customer_id) is None:
            raise HTTPException(status_code=404, detail="Customer not found")
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

        new_order = Order(customer_id=customer_id)
        session.add(new_order)
        for item in order.items:
            products[item.product_id].stock -= item.quantity
        session.flush()  # Persist order and all stock changes before inserting items.
        for item in order.items:
            session.add(OrderItem(
                order_id=new_order.id,
                product_id=item.product_id,
                quantity=item.quantity,
                price=products[item.product_id].unit_price,
            ))
        session.commit()
    except Exception:
        session.rollback()
        raise

    logger.info("order_created", extra={"order_id": new_order.id, "item_count": len(order.items)})
    return {"msg": "order created", "order_id": new_order.id}
