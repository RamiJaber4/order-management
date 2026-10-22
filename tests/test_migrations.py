from alembic import command
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from conftest import migration_config


def test_migrations_match_models(pg_engine):
    with pg_engine.begin() as connection:
        command.check(migration_config(connection))


def test_upgrade_downgrade_and_existing_customer_data(pg_engine):
    # Only the randomly named disposable test database is affected.
    with pg_engine.begin() as connection:
        config = migration_config(connection)
        command.downgrade(config, "base")
        assert "ecommerce" not in inspect(connection).get_schema_names()
        command.upgrade(config, "0001_initial")
        connection.execute(text("ALTER TABLE ecommerce.customers ADD COLUMN phone varchar(20)"))
        connection.execute(text(
            "INSERT INTO ecommerce.customers (name, email, phone) "
            "VALUES ('Existing customer', 'existing@example.com', '12345')"
        ))
        command.upgrade(config, "head")
        assert connection.execute(text("SELECT phone FROM ecommerce.customers")).scalar_one() == "12345"
        assert connection.execute(text(
            "SELECT password_hash, role FROM ecommerce.customers"
        )).one() == (None, "customer")
        assert "notes" in {c["name"] for c in inspect(connection).get_columns("orders", schema="ecommerce")}
        command.downgrade(config, "0001_initial")
        assert connection.execute(text("SELECT phone FROM ecommerce.customers")).scalar_one() == "12345"
        command.upgrade(config, "head")
        connection.execute(text("ALTER TABLE ecommerce.customers DROP COLUMN phone"))
        command.check(config)


def test_required_values_migration_preserves_existing_rows(pg_engine):
    with pg_engine.begin() as connection:
        command.downgrade(migration_config(connection), "c64c69d63974")
        # Price is present, stock is missing: failure on the second ALTER must
        # also roll back the first ALTER, without filling in fabricated data.
        product_id = connection.execute(text(
            "INSERT INTO ecommerce.products (name, unit_price, stock) "
            "VALUES ('Legacy product', 19, NULL) RETURNING id"
        )).scalar_one()
    try:
        with pytest.raises(IntegrityError):
            with pg_engine.begin() as connection:
                command.upgrade(migration_config(connection), "head")
        with pg_engine.begin() as connection:
            columns = {c["name"]: c for c in inspect(connection).get_columns("products", schema="ecommerce")}
            assert columns["unit_price"]["nullable"]
            assert columns["stock"]["nullable"]
            assert connection.execute(text("SELECT stock FROM ecommerce.products WHERE id=:id"), {"id": product_id}).scalar_one() is None
            connection.execute(text("UPDATE ecommerce.products SET stock=4 WHERE id=:id"), {"id": product_id})
            command.upgrade(migration_config(connection), "head")
            assert connection.execute(text(
                "SELECT unit_price, stock FROM ecommerce.products WHERE id=:id"
            ), {"id": product_id}).one() == (19, 4)
            command.check(migration_config(connection))
    finally:
        with pg_engine.begin() as connection:
            connection.execute(text("UPDATE ecommerce.products SET stock=4 WHERE stock IS NULL"))
            command.upgrade(migration_config(connection), "head")
