"""FastAPI application entry point and middleware setup."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import APP_ENV, router
from app.core.logging_config import configure_logging, RequestLoggingMiddleware
from app.core.error_logging import install_error_logging

configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    logger.info("application_started")
    try:
        yield
    finally:
        logger.info("application_stopped")


app = FastAPI(title=f"Order Management — {APP_ENV}", lifespan=lifespan)
app.add_middleware(RequestLoggingMiddleware)
install_error_logging(app)
app.include_router(router)
