"""Database connection and request-scoped SQLAlchemy sessions."""

import os
from collections.abc import Generator

from dotenv import load_dotenv
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

load_dotenv()

from base import Base
from models import Customer, CustomerCreate, ProductCreate,OrderItem, Product, Order, OrderCreate


# Engine construction is lazy: a connection opens only when needed.
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+psycopg://postgres@localhost:5432/ecommerce_db"
)
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    connect_args={"options": "-csearch_path=ecommerce,public"},
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
    session.add(Customer(**customer.model_dump()))
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        if (getattr(exc.orig, "sqlstate", None) == "23505"
                and getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
                == "customers_email_key"):
            raise HTTPException(status_code=409, detail="Email already exists") from exc
        raise
    return {"msg": "customer created"}


def create_product(product: ProductCreate, session: Session):
    session.add(Product(**product.model_dump()))
    session.commit()
    return {"msg": "product created"}


def list_products(limit:int, offset: int, session: Session):
    return session.scalars(select(Product).order_by(Product.id).limit(limit).offset(offset)).all()

def get_order_by_id(id : int, session: Session):
    stm = select(Order).where(Order.id == id)
    return session.scalars(stm).one_or_none()

def get_customer_order(
    id: int,
    session: Session,
    limit: int = 20,
    offset: int = 0
):
    stmt = (
        select(Order)
        .where(Order.customer_id == id)
        .order_by(Order.id)
        .limit(limit)
        .offset(offset)
    )

    return session.scalars(stmt).all()

def create_order(
    order: OrderCreate,
    session :Session        
):
    try:
        with session.begin():
            if session.get(Customer, order.customer_id) is None:
                raise HTTPException(status_code=404, detail="Customer not found")
            product = session.scalar(
                select(Product).where(Product.id == order.p_id).with_for_update()
            )
            if product is None:
                raise HTTPException(status_code=404, detail="Product not found")
            if product.unit_price is None or product.unit_price <= 0:
                raise HTTPException(status_code=409, detail="Product has no valid price")
            if product.stock is None or product.stock < order.quantity:
                raise HTTPException(status_code=409, detail="Insufficient stock")

            product.stock -= order.quantity
            new_order = Order(customer_id=order.customer_id)
            session.add(new_order)
            session.flush()  # Persist the order and stock inside the same transaction.
            session.add(OrderItem(
                order_id=new_order.id,
                product_id=product.id,
                quantity=order.quantity,
                price=product.unit_price,
            ))
    except Exception:
        session.rollback()
        raise

    return {"msg": "order created", "order_id": new_order.id}
