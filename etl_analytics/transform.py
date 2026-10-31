"""Check and normalize daily SQL results; aggregation belongs to the query."""

METRIC_FIELDS = (
    'order_count', 'orders_with_items', 'orders_without_items',
    'item_count', 'units', 'recorded_item_value_ils',
)


def transform(rows):
    days = []
    for row in rows:
        if row['order_day'] is None:
            raise ValueError('missing_order_date')
        if row['invalid_items']:
            raise ValueError('invalid_order_items')
        day = {'order_day': row['order_day']}
        for field in METRIC_FIELDS:
            day[field] = int(row[field])
        days.append(day)
    return days
