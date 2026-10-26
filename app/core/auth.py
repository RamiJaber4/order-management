"""JWT authentication and reusable authorization dependencies."""
import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.exceptions import ExpiredSignatureError, InvalidTokenError
from pwdlib import PasswordHash
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import Customer

# db imports load_dotenv before this configuration is read. No fallback signing key.
JWT_SECRET = os.environ.get("JWT_SECRET", "")
if len(JWT_SECRET.encode()) < 32:
    raise RuntimeError("Set JWT_SECRET to a random secret of at least 32 bytes")

ALGORITHM = "HS256"
TOKEN_MINUTES = 15
password_hasher = PasswordHash.recommended()
DUMMY_HASH = password_hasher.hash("not-a-real-account-password")
bearer = HTTPBearer(auto_error=False)


def unauthorized(reason="authentication_failed") -> HTTPException:
    exc = HTTPException(status_code=401, detail="Invalid or expired credentials",
                        headers={"WWW-Authenticate": "Bearer"})
    exc.log_reason = reason
    return exc


def create_access_token(customer_id: int) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": str(customer_id), "iat": now,
                       "exp": now + timedelta(minutes=TOKEN_MINUTES)},
                      JWT_SECRET, algorithm=ALGORITHM)


def get_current_customer(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    session: Session = Depends(get_db),
) -> Customer:
    if credentials is None:
        raise unauthorized("credentials_missing")
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET,
                             algorithms=[ALGORITHM],
                             options={"require": ["sub", "iat", "exp"]})
        customer_id = int(payload["sub"])
        if not 0 < customer_id <= 2147483647:
            raise ValueError("Invalid customer id")
    except ExpiredSignatureError as exc:
        raise unauthorized("token_expired") from exc
    except (InvalidTokenError, ValueError, TypeError, OverflowError) as exc:
        raise unauthorized("token_invalid") from exc
    customer = session.get(Customer, customer_id)
    if customer is None:
        raise unauthorized("account_unavailable")
    return customer


def require_admin(customer: Customer = Depends(get_current_customer)) -> Customer:
    if customer.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return customer


def require_customer_access(customer: Customer, customer_id: int | None) -> None:
    """Allow the owner or an administrator to access customer-owned data."""
    if customer.id != customer_id and customer.role != "admin":
        raise HTTPException(status_code=403, detail="Access denied")
