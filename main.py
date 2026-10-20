from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy.orm import Session

import db
from db import get_db
from models import CustomerCreate, ProductCreate, OrderCreate

app = FastAPI()


@app.post('/customers')
def create_customer(customer: CustomerCreate, session: Session = Depends(get_db)):
    return db.create_customer(customer, session)


@app.post('/products')
def add_product(product: ProductCreate, session: Session = Depends(get_db)):
    return db.create_product(product, session)

@app.post('/orders', status_code=201)
def create_order(order: OrderCreate, session: Session = Depends(get_db)):
    return db.create_order(order, session)

@app.get('/products')
def list_products(
    limit : int= Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db)):
    return db.list_products(limit, offset, session)

@app.get('/orders/{id}')
def get_order(id: int,session: Session = Depends(get_db)):
    order = db.get_order_by_id(id, session)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@app.get("/customers/{id}/orders")
def get_customer_orders(
    id: int,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db)
):
    return db.get_customer_order(
        id=id,
        session=session,
        limit=limit,
        offset=offset
    )
