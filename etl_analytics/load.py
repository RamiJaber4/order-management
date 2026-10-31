"""Explicit schema initialization and atomic publication."""
import json
from pathlib import Path

from sqlalchemy import text

LOCK_ID = 731040281


def initialize_target(engine):
    with engine.begin() as connection:
        connection.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': LOCK_ID})
        for statement in Path(__file__).with_name('schema.sql').read_text(encoding='utf-8').split(';'):
            if statement.strip():
                connection.execute(text(statement))


def acquire_lock(connection):
    if not connection.execute(text('SELECT pg_try_advisory_xact_lock(:key)'), {'key': LOCK_ID}).scalar_one():
        raise RuntimeError('another_etl_run_is_active')
    version = connection.execute(text('SELECT version FROM analytics.etl_schema_version')).scalar_one()
    if version != 1:
        raise RuntimeError('unsupported_etl_schema_version')


def load(connection, days, report):
    connection.execute(text('''
        INSERT INTO analytics.etl_runs(run_id,snapshot_started_at,source_calendar,status,totals)
        VALUES (:run_id,:snapshot_started_at,:source_calendar,:status,CAST(:totals AS jsonb))
    '''), dict(report, totals=json.dumps(report['totals'])))
    connection.execute(text('DELETE FROM analytics.daily_order_metrics'))
    if days:
        connection.execute(text('''
            INSERT INTO analytics.daily_order_metrics
            (order_day,order_count,orders_with_items,orders_without_items,item_count,units,recorded_item_value_ils,run_id)
            VALUES (:order_day,:order_count,:orders_with_items,:orders_without_items,:item_count,:units,:recorded_item_value_ils,:run_id)
        '''), [dict(day, run_id=report['run_id']) for day in days])
    connection.execute(text('''
        INSERT INTO analytics.etl_current_run(singleton,run_id) VALUES (true,:run_id)
        ON CONFLICT (singleton) DO UPDATE SET run_id=EXCLUDED.run_id
    '''), report)
