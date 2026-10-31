"""Aggregate orders by day in PostgreSQL before fetching the small result."""
from sqlalchemy import text

DAILY_METRICS = text('''
WITH item_totals AS (
    SELECT order_id, count(*) AS item_count, sum(quantity::bigint) AS units,
           sum(price::bigint * quantity::bigint) AS value_ils,
            count(*) FILTER (WHERE price IS NULL OR quantity IS NULL
                            OR price < 0 OR quantity <= 0) AS invalid_items
    FROM ecommerce.order_items GROUP BY order_id
)
SELECT o.order_date::date AS order_day,
        count(*) AS order_count,
        count(*) FILTER (WHERE i.order_id IS NOT NULL) AS orders_with_items,
        count(*) FILTER (WHERE i.order_id IS NULL) AS orders_without_items,
        sum(coalesce(i.item_count, 0)) AS item_count,
        sum(coalesce(i.units, 0)) AS units,
        sum(coalesce(i.value_ils, 0)) AS recorded_item_value_ils,
        sum(coalesce(i.invalid_items, 0)) AS invalid_items
FROM ecommerce.orders o
LEFT JOIN item_totals i ON i.order_id = o.id
GROUP BY o.order_date::date
ORDER BY order_day
''')


def extract(connection):
    with connection.execute(DAILY_METRICS) as result:
        return result.mappings().all()
