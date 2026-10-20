"""SQLAlchemy 2.x models matching the existing ecommerce schema.

Importing these models does not create tables or modify the database.
Mapped[T | None] means nullable; Mapped[T] means NOT NULL.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint, Computed, DateTime, ForeignKey, Index,
    Integer, Numeric, String, Text, UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import Mapped, mapped_column, relationship

from base import Base

# PostgreSQL validates strings. The connection search_path resolves the type.

payment_way_type = ENUM(
    "cash",
    "visa",
    "on_deliver",
    name="payment_way",
    schema="ecommerce",
    create_type=False
)
class Category(Base):
    __tablename__ = "categories"
    __table_args__ = {"schema": "ecommerce"}

    name: Mapped[str] = mapped_column(String(255), primary_key=True)
    products: Mapped[list[Product]] = relationship(
        back_populates="category", passive_deletes="all"
    )


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("email", name="customers_email_key"),
        {"schema": "ecommerce"},
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True,
        server_default=text("nextval('ecommerce.customers_id_seq'::regclass)"),
    )
    name: Mapped[str] = mapped_column(Text)
    email: Mapped[str] = mapped_column(Text)
    orders: Mapped[list[Order]] = relationship(
        back_populates="customer", passive_deletes="all"
    )


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("stock >= 0", name="products_stock_check"),
        CheckConstraint("unit_price > 0", name="products_unit_price_check"),
        {"schema": "ecommerce"},
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True,
        server_default=text("nextval('ecommerce.products_id_seq'::regclass)"),
    )
    name: Mapped[str] = mapped_column(Text)
    category_name: Mapped[str | None] = mapped_column(
        String(255), ForeignKey("ecommerce.categories.name")
    )
    # Keep the existing integer price representation without assuming a currency unit.
    unit_price: Mapped[int] = mapped_column(Integer, nullable=False)
    stock: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[Category | None] = relationship(back_populates="products")
    order_items: Mapped[list[OrderItem]] = relationship(
        back_populates="product", passive_deletes="all"
    )


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        Index("idx_orders_customer_id", "customer_id"),
        {"schema": "ecommerce"},
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True,
        server_default=text("nextval('ecommerce.orders_id_seq'::regclass)"),
    )
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("ecommerce.customers.id"))
    order_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), server_default=text("CURRENT_TIMESTAMP")
    )
    customer: Mapped[Customer | None] = relationship(back_populates="orders")
    items: Mapped[list[OrderItem]] = relationship(
        back_populates="order", passive_deletes="all"
    )
    # UNIQUE(order_id) permits at most one payment per order.
    payment: Mapped[Payment | None] = relationship(
        back_populates="order", passive_deletes="all"
    )
    notes = mapped_column(Text, nullable=True)

class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (
        CheckConstraint("price >= 0", name="order_items_price_check"),
        CheckConstraint("quantity > 0", name="order_items_quantity_check"),
        {"schema": "ecommerce"},
    )

    # Composite primary key: one row per product within an order.
    order_id: Mapped[int] = mapped_column(ForeignKey("ecommerce.orders.id"), primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("ecommerce.products.id"), primary_key=True)
    quantity: Mapped[int] = mapped_column(Integer)
    price: Mapped[int] = mapped_column(Integer)
    # PostgreSQL computes this value; do not assign it yourself.
    total_price: Mapped[int | None] = mapped_column(
        Integer, Computed("COALESCE(price, 0) * COALESCE(quantity, 0)", persisted=True)
    )
    order: Mapped[Order] = relationship(back_populates="items")
    product: Mapped[Product] = relationship(back_populates="order_items")


class Payment(Base):
    __tablename__ = "payment"
    __table_args__ = (
        UniqueConstraint("order_id", name="payment_order_id_key"),
        {"schema": "ecommerce"},
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True,
        server_default=text("nextval('ecommerce.payment_id_seq'::regclass)"),
    )
    way: Mapped[str] = mapped_column(
    payment_way_type,
    nullable=False
)
    amount: Mapped[int] = mapped_column(Integer)
    discount: Mapped[Decimal | None] = mapped_column(Numeric)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("ecommerce.orders.id"))
    order: Mapped[Order | None] = relationship(back_populates="payment")
