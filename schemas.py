"""Pydantic request validation and public API response contracts."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CustomerCreate(BaseModel):
    name: str
    email: str


class ProductCreate(BaseModel):
    name: str
    unit_price: int = Field(gt=0)
    stock: int = Field(ge=0)


class OrderItemCreate(BaseModel):
    product_id: int = Field(gt=0)
    quantity: int = Field(gt=0)


class OrderCreate(BaseModel):
    customer_id: int = Field(gt=0)
    items: list[OrderItemCreate] = Field(min_length=1)

    @field_validator("items")
    @classmethod
    def unique_products(cls, items: list[OrderItemCreate]) -> list[OrderItemCreate]:
        if len({item.product_id for item in items}) != len(items):
            raise ValueError("Each product must appear only once")
        return items


class MessageResponse(BaseModel):
    msg: str


class OrderCreatedResponse(MessageResponse):
    order_id: int


class ProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    category_name: str | None
    unit_price: int
    stock: int


class OrderItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    product_id: int
    quantity: int
    price: int
    total_price: int | None


class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_id: int | None
    order_date: datetime | None
    notes: str | None
    items: list[OrderItemResponse]
