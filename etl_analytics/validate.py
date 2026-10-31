"""Cheap result checks by default; source reconciliation is an explicit audit."""
from datetime import date

from sqlalchemy import text

from etl_analytics.transform import METRIC_FIELDS


def validate(days):
    """Inspect only daily results already in memory; execute no database queries."""
    seen_days = set()
    totals = dict.fromkeys(METRIC_FIELDS, 0)
    for day in days:
        if not isinstance(day['order_day'], date):
            raise ValueError('invalid_order_day')
        if day['order_day'] in seen_days:
            raise ValueError('duplicate_order_day')
        seen_days.add(day['order_day'])
        for field in METRIC_FIELDS:
            if day[field] < 0:
                raise ValueError('negative_daily_metric')
            totals[field] += day[field]
        if day['order_count'] != day['orders_with_items'] + day['orders_without_items']:
            raise ValueError('inconsistent_daily_order_counts')
        if day['orders_without_items']:
            raise ValueError('orders_without_items: fix_source_data')
        if day['item_count'] < day['orders_with_items'] or day['units'] < day['item_count']:
            raise ValueError('inconsistent_daily_item_counts')
    return totals


def reconcile(connection, totals):
    """Audit totals with extra source scans, on the extraction snapshot."""
    orders = connection.execute(text('SELECT count(*) FROM ecommerce.orders')).scalar_one()
    items = connection.execute(text('''
        SELECT count(*) AS item_count, coalesce(sum(quantity::bigint),0) AS units,
               coalesce(sum(price::bigint * quantity::bigint),0) AS value_ils
        FROM ecommerce.order_items
    ''')).mappings().one()
    expected = (int(orders), int(items['item_count']), int(items['units']), int(items['value_ils']))
    actual = tuple(totals[k] for k in ('order_count','item_count','units','recorded_item_value_ils'))
    if actual != expected:
        raise ValueError('source_reconciliation_failed')
