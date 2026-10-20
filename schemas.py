"""Pydantic request validation and public API response contracts."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CustomerCreate(BaseModel):
    name: str
    email: str


class ProductCreate(BaseModel):
    name: str
    unit_price: int = Field(gt=0)
    stock: int = Field(ge=0)


class OrderCreate(BaseModel):
    customer_id: int = Field(gt=0)
    p_id: int = Field(gt=0)
    quantity: int = Field(gt=0)


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


class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_id: int | None
    order_date: datetime | None
    notes: str | None
