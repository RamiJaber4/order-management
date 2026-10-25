"""Database connection and request-scoped SQLAlchemy sessions."""

import os
import logging
from collections.abc import Generator

from dotenv import load_dotenv
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker, selectinload

load_dotenv()

from models import Customer, OrderItem, Product, Order
from schemas import CustomerCreate, ProductCreate, OrderCreate
from database_settings import connection_arguments, positive_integer

logger = logging.getLogger(__name__)


# Engine construction is lazy: a connection opens only when needed.
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+psycopg://postgres@localhost:5432/ecommerce_db"
)
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    hide_parameters=True,
    pool_timeout=positive_integer("DB_POOL_TIMEOUT_SECONDS", 3),
    connect_args=connection_arguments(),
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """Use Depends(get_db) in FastAPI; routes/services explicitly own commit()."""
    with SessionLocal() as session:
        try:
            yield session
        except Exception:
            session.rollback()
            raise

def create_customer(customer: CustomerCreate, session: Session):
    from auth import password_hasher

    session.add(Customer(
        name=customer.name,
        email=str(customer.email),
        password_hash=password_hasher.hash(customer.password),
        role="customer",
    ))
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        if (getattr(exc.orig, "sqlstate", None) == "23505"
                and getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
                == "customers_email_key"):
            raise HTTPException(status_code=409, detail="Email already exists") from exc
        raise
    logger.info("customer_created")
    return {"msg": "customer created"}


def create_product(product: ProductCreate, session: Session):
    session.add(Product(**product.model_dump()))
    session.commit()
    logger.info("product_created")
    return {"msg": "product created"}


def list_products(limit:int, offset: int, session: Session):
    return session.scalars(select(Product).order_by(Product.id).limit(limit).offset(offset)).all()

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
