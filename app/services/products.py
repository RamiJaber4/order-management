"""Product catalog operations."""
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Product
from app.schemas import ProductCreate

logger = logging.getLogger(__name__)


def create_product(product: ProductCreate, session: Session):
    session.add(Product(**product.model_dump()))
    session.commit()
    logger.info("product_created")
    return {"msg": "product created"}


def list_products(limit:int, offset: int, session: Session):
    return session.scalars(select(Product).order_by(Product.id).limit(limit).offset(offset)).all()
