"""Create a new isolated, deterministic dataset; never seed the original database."""
import argparse
import json
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from etl_analytics.config import engines


def create_demo(source_url, order_count=10000):
    if not 1 <= order_count <= 1_000_000:
        raise ValueError('orders must be between 1 and 1000000')
    name = 'etl_demo_' + uuid4().hex[:12]
    base_url = make_url(source_url)
    admin = create_engine(base_url.set(database='postgres'), isolation_level='AUTOCOMMIT',
                          hide_parameters=True, connect_args={'connect_timeout': 5})
    demo = create_engine(base_url.set(database=name), hide_parameters=True,
                         connect_args={'connect_timeout': 5})
    created = False
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
            created = True
        with demo.begin() as connection:
            config = Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini'))
            config.attributes['connection'] = connection
            command.upgrade(config, 'head')
            connection.execute(text('''
                INSERT INTO ecommerce.categories(name)
                SELECT 'Category ' || n FROM generate_series(1, 10) AS n
            '''))
            connection.execute(text('''
                INSERT INTO ecommerce.customers(name,email)
                SELECT 'Demo customer ' || n, 'demo' || n || '@example.invalid'
                FROM generate_series(1, 1000) AS n
            '''))
            connection.execute(text('''
                INSERT INTO ecommerce.products(name,category_name,unit_price,stock)
                SELECT 'Demo product ' || n, 'Category ' || (1 + n % 10),
                       10 + n * 5, 100000
                FROM generate_series(1, 100) AS n
            '''))
            connection.execute(text('''
                INSERT INTO ecommerce.orders(customer_id,order_date)
                SELECT 1 + (n - 1) % 1000,
                       TIMESTAMP '2026-01-01' + ((n - 1) % 270) * INTERVAL '1 day'
                       + ((n * 37) % 86400) * INTERVAL '1 second'
                FROM generate_series(1, :orders) AS n
            '''), {'orders': order_count})
            connection.execute(text('''
                INSERT INTO ecommerce.order_items(order_id,product_id,quantity,price)
                SELECT o.id, p.id, 1 + (o.id + slot) % 3, p.unit_price
                FROM ecommerce.orders o
                CROSS JOIN LATERAL generate_series(1, 1 + o.id % 3) AS slot
                JOIN ecommerce.products p ON p.id = 1 + (o.id * 7 + slot * 13) % 100
            '''))
            connection.execute(text('''
                UPDATE ecommerce.products p SET stock = p.stock - used.units
                FROM (
                    SELECT product_id,sum(quantity)::integer AS units
                    FROM ecommerce.order_items GROUP BY product_id
                ) used WHERE used.product_id = p.id
            '''))
            item_count = connection.execute(text('SELECT count(*) FROM ecommerce.order_items')).scalar_one()
        # Statistics belong to the newly created demo database only.
        with demo.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
            connection.execute(text('ANALYZE ecommerce.orders'))
            connection.execute(text('ANALYZE ecommerce.order_items'))
        return {'database': name, 'orders': order_count, 'items': item_count,
                'source_calendar': 'synthetic_wall_clock'}
    except Exception:
        demo.dispose()
        if created:
            # name is generated here, and only this newly created database is removed.
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        raise
    finally:
        demo.dispose()
        admin.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--orders', type=int, default=10000)
    args = parser.parse_args()
    source = target = None
    try:
        source, target = engines()
        print(json.dumps(create_demo(source.url, args.orders)))
        return 0
    except Exception as exc:
        reason = str(exc) if str(exc) == 'orders must be between 1 and 1000000' else type(exc).__name__
        print(json.dumps({'status': 'failed', 'reason': reason}))
        return 1
    finally:
        for engine in (source, target):
            if engine is not None:
                engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
