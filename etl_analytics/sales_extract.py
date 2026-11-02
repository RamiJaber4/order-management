"""Stream all items of changed orders; bounded timestamp polling, not CDC."""
from sqlalchemy import text


def require_tracking(connection):
    count = connection.execute(text('''
        SELECT count(*) FROM information_schema.columns WHERE table_schema='ecommerce'
        AND table_name IN ('orders','order_items') AND column_name='updated_at'
    ''')).scalar_one()
    triggers = connection.execute(text('''
        SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='ecommerce'
        AND t.tgname IN ('orders_touch_updated_at','order_items_touch_updated_at')
        AND t.tgenabled IN ('O','A')
    ''')).scalar_one()
    if count != 2 or triggers != 2:
        raise ValueError('source_tracking_migration_required')


def extract_sales(connection, *, lower, upper, batch_size):
    changed = '''WITH changed_orders AS (
        SELECT id FROM ecommerce.orders WHERE updated_at>=:lower AND updated_at<=:upper
        UNION
        SELECT order_id FROM ecommerce.order_items WHERE updated_at>=:lower AND updated_at<=:upper
    )''' if lower is not None else ''
    selected = 'JOIN changed_orders c ON c.id=o.id' if lower is not None else ''
    query = text(f'''{changed}
        SELECT o.id AS order_id,o.customer_id,o.order_date,o.updated_at AS order_updated_at,
               i.product_id,i.quantity,i.price AS unit_price,i.updated_at AS item_updated_at
        FROM ecommerce.orders o {selected}
        LEFT JOIN ecommerce.order_items i ON i.order_id=o.id
        ORDER BY o.id,i.product_id
    ''')
    with connection.execution_options(stream_results=True).execute(
        query, {'lower': lower, 'upper': upper}
    ) as result:
        mappings = result.mappings()
        while batch := mappings.fetchmany(batch_size):
            yield batch


def transform_sale(row):
    if row['order_date'] is None:
        raise ValueError('missing_order_date')
    if row['product_id'] is None:
        raise ValueError('orders_without_items: fix_source_data')
    if (row['unit_price'] is None or row['quantity'] is None
            or row['unit_price'] < 0 or row['quantity'] <= 0):
        raise ValueError('invalid_order_items')
    sale = dict(row)
    sale['line_value'] = int(row['unit_price']) * int(row['quantity'])
    return sale


def reconcile_sales(source, target):
    """Optional global totals audit; cannot detect offsetting or dimension-only errors."""
    orders = source.execute(text('SELECT count(*) FROM ecommerce.orders')).scalar_one()
    items = source.execute(text('''SELECT count(*) AS item_count,
        coalesce(sum(quantity::bigint),0) AS units,
        coalesce(sum(price::bigint*quantity::bigint),0) AS value
        FROM ecommerce.order_items''')).mappings().one()
    actual = target.execute(text('''SELECT count(DISTINCT order_id) AS order_count,
        count(*) AS item_count,coalesce(sum(quantity),0) AS units,
        coalesce(sum(line_value),0) AS value FROM analytics.fact_order_sales''')).mappings().one()
    if (orders,items['item_count'],items['units'],items['value']) != tuple(actual.values()):
        raise ValueError('source_reconciliation_failed')
    return {key: int(value) for key,value in actual.items()}
