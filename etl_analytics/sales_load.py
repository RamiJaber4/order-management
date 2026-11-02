"""Batch COPY staging and business-key UPSERT; never add deltas to old totals."""
from sqlalchemy import text

FIELDS = ('order_id','product_id','customer_id','order_date','quantity','unit_price',
          'line_value','order_updated_at','item_updated_at')
COLUMNS = ','.join(FIELDS)
CHANGED = '''(f.customer_id,f.order_date,f.quantity,f.unit_price,f.line_value,
               f.order_updated_at,f.item_updated_at)
    IS DISTINCT FROM
    (s.customer_id,s.order_date,s.quantity,s.unit_price,s.line_value,
     s.order_updated_at,s.item_updated_at)'''


def create_stage(connection):
    connection.execute(text(f'''CREATE TEMP TABLE etl_sales_stage ON COMMIT DROP
        AS SELECT {COLUMNS} FROM analytics.fact_order_sales WITH NO DATA'''))


def upsert_batch(connection, sales, run_id):
    connection.execute(text('TRUNCATE pg_temp.etl_sales_stage'))
    with connection.connection.driver_connection.cursor() as cursor:
        with cursor.copy(f'COPY pg_temp.etl_sales_stage ({COLUMNS}) FROM STDIN') as copy:
            for sale in sales:
                copy.write_row(tuple(sale[field] for field in FIELDS))
    counts = connection.execute(text(f'''SELECT
        count(*) FILTER (WHERE f.order_id IS NULL) AS inserted,
        count(*) FILTER (WHERE f.order_id IS NOT NULL AND ({CHANGED})) AS updated
        FROM pg_temp.etl_sales_stage s LEFT JOIN analytics.fact_order_sales f
        USING(order_id,product_id)''')).mappings().one()
    assignments = ','.join(f'{field}=EXCLUDED.{field}' for field in FIELDS[2:])
    changed = CHANGED.replace('s.', 'EXCLUDED.')
    connection.execute(text(f'''INSERT INTO analytics.fact_order_sales AS f ({COLUMNS},run_id)
        SELECT {COLUMNS},CAST(:run_id AS uuid) FROM pg_temp.etl_sales_stage WHERE true
        ON CONFLICT(order_id,product_id) DO UPDATE SET {assignments},
        run_id=EXCLUDED.run_id,loaded_at=clock_timestamp() WHERE {changed}'''), {'run_id': run_id})
    return dict(inserted=counts['inserted'],updated=counts['updated'],
                unchanged=len(sales)-counts['inserted']-counts['updated'])


def read_checkpoint(connection):
    row = connection.execute(text("""SELECT source_key,watermark,run_id
        FROM analytics.etl_checkpoints WHERE pipeline_name='order_sales'""")).mappings().one_or_none()
    return dict(row) if row else None


def save_checkpoint(connection, report, source_key):
    connection.execute(text('''INSERT INTO analytics.etl_checkpoints
        (pipeline_name,source_key,watermark,run_id) VALUES ('order_sales',:key,:watermark,:run_id)
        ON CONFLICT(pipeline_name) DO UPDATE SET source_key=EXCLUDED.source_key,
        watermark=EXCLUDED.watermark,run_id=EXCLUDED.run_id,updated_at=clock_timestamp()
    '''), {'key':source_key,'watermark':report['watermark_after'],'run_id':report['run_id']})
