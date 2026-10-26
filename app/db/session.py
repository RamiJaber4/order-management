"""Database connection and request-scoped SQLAlchemy sessions."""

import os
from collections.abc import Generator

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

load_dotenv()

from app.core.database_settings import connection_arguments, positive_integer



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
