import os

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.services import customers, orders, products
from app.db.session import get_db
from app.models import Customer
from app.core.auth import (
    DUMMY_HASH, password_hasher, create_access_token, unauthorized,
    get_current_customer, require_admin, require_customer_access,
)
from app.schemas import (
    CustomerCreate, ProductCreate, OrderCreate, MessageResponse,
    OrderCreatedResponse, ProductResponse, OrderResponse,
    LoginRequest, TokenResponse, CustomerResponse,
)

APP_ENV = os.getenv("APP_ENV", "development")
router = APIRouter()


@router.get('/health', tags=['Environment'], include_in_schema=False)
@router.get('/health/ready', tags=['Environment'])
def health(session: Session = Depends(get_db)):
    session.execute(select(1))
    return {"status": "ok", "environment": APP_ENV}


@router.get('/health/live', tags=['Environment'])
def liveness():
    return {"status": "ok", "environment": APP_ENV}


@router.post('/auth/register', status_code=201, response_model=MessageResponse)
def create_customer(customer: CustomerCreate, session: Session = Depends(get_db)):
    return customers.create_customer(customer, session)


@router.post('/auth/login', response_model=TokenResponse)
def login(data: LoginRequest, session: Session = Depends(get_db)):
    customer = session.scalar(select(Customer).where(Customer.email == str(data.email)))
    stored_hash = customer.password_hash if customer and customer.password_hash else DUMMY_HASH
    valid = password_hasher.verify(data.password, stored_hash)
    if customer is None or customer.password_hash is None or not valid:
        raise unauthorized()
    return {"access_token": create_access_token(customer.id), "token_type": "bearer"}


@router.get('/auth/me', response_model=CustomerResponse)
def me(customer: Customer = Depends(get_current_customer)):
    return customer


@router.post('/products', response_model=MessageResponse)
def add_product(product: ProductCreate, session: Session = Depends(get_db),
                admin: Customer = Depends(require_admin)):
    return products.create_product(product, session)

@router.post('/orders', status_code=201, response_model=OrderCreatedResponse)
def create_order(order: OrderCreate, session: Session = Depends(get_db),
                 customer: Customer = Depends(get_current_customer)):
    return orders.create_order(order, customer.id, session)

@router.get('/products', response_model=list[ProductResponse])
def list_products(
    limit : int= Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db)):
    return products.list_products(limit, offset, session)

@router.get('/orders/{id}', response_model=OrderResponse)
def get_order(id: int, session: Session = Depends(get_db),
              customer: Customer = Depends(get_current_customer)):
    order = orders.get_order_by_id(id, session)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    require_customer_access(customer, order.customer_id)
    return order


@router.get("/customers/{id}/orders", response_model=list[OrderResponse])
def get_customer_orders(
    id: int,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db),
    customer: Customer = Depends(get_current_customer),
):
    require_customer_access(customer, id)
    return orders.get_customer_order(
        id=id,
        session=session,
        limit=limit,
        offset=offset
    )
