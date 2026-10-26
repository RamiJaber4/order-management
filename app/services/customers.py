"""Customer registration and transaction handling."""
import logging

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Customer
from app.schemas import CustomerCreate

logger = logging.getLogger(__name__)


def create_customer(customer: CustomerCreate, session: Session):
    from app.core.auth import password_hasher

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
