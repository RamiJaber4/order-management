"""Alembic configuration for the application and isolated test connections."""
import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

from base import Base
import models  # Register mapped tables.

config = context.config
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)
target_metadata = Base.metadata


def include_name(name, type_, parent_names):
    # Never propose dropping another application's tables during autogenerate.
    if type_ == "schema":
        return name in (None, "ecommerce")
    if type_ == "table":
        return parent_names.get("schema_name") == "ecommerce"
    return True


def configure_connection(connection):
    context.configure(
        connection=connection, target_metadata=target_metadata,
        include_schemas=True, include_name=include_name, compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations():
    connection = config.attributes.get("connection")
    if connection is not None:
        configure_connection(connection)
        return
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("Set DATABASE_URL in the environment or .env")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    if context.is_offline_mode():
        context.configure(
            url=database_url, target_metadata=target_metadata,
            literal_binds=True, dialect_opts={"paramstyle": "named"},
            include_schemas=True, include_name=include_name,
        )
        with context.begin_transaction():
            context.run_migrations()
    else:
        engine = engine_from_config(
            config.get_section(config.config_ini_section),
            prefix="sqlalchemy.", poolclass=pool.NullPool,
        )
        try:
            with engine.connect() as connection:
                configure_connection(connection)
        finally:
            engine.dispose()


run_migrations()
