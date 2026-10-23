"""Read-only identity and data counts for the original development database."""
from pathlib import Path
from dotenv import dotenv_values
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

root = Path(__file__).resolve().parents[1]
url = make_url(dotenv_values(root / '.env')['DATABASE_URL'])
print(f'Development: {url.host}:{url.port}/{url.database} (credentials hidden)')
engine = create_engine(url, connect_args={'connect_timeout': 5})
with engine.connect() as connection:
    connection.execute(text('SET TRANSACTION READ ONLY'))
    for table in ('products', 'customers', 'orders', 'order_items', 'payment', 'categories'):
        count = connection.execute(text(f'SELECT count(*) FROM ecommerce.{table}')).scalar()
        print(f'{table}: {count}')
    print('Migration:', connection.execute(text('SELECT version_num FROM alembic_version')).scalar())
    print('Products missing price/stock:', connection.execute(text(
        'SELECT count(*) FROM ecommerce.products WHERE unit_price IS NULL OR stock IS NULL'
    )).scalar())
    print('Missing-value rows:', connection.execute(text(
        'SELECT id, name, unit_price, stock FROM ecommerce.products '
        'WHERE unit_price IS NULL OR stock IS NULL'
    )).all())
engine.dispose()
