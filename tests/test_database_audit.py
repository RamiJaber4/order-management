"""The audit must expose integrity problems without altering source records."""
import json
from uuid import uuid4

from sqlalchemy import event, text

from etl_analytics.load import initialize_target
from scripts.audit_database import audit


def test_audit_is_read_only_and_omits_customer_records(pg_engine, seeded):
    statements = []

    def track(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(pg_engine, 'before_cursor_execute', track)
    try:
        report = audit(pg_engine)
    finally:
        event.remove(pg_engine, 'before_cursor_execute', track)
    assert report['status'] == 'ok'
    assert report['counts']['customers'] == 1
    assert report['counts']['products'] == 1
    assert report['migrations']['actual'] == report['migrations']['expected']
    assert all(row['next_id_above_max'] for row in report['sequences'].values())
    assert 'SET TRANSACTION READ ONLY' in statements
    assert not any(statement.lstrip().upper().startswith(
        ('INSERT ', 'UPDATE ', 'DELETE ', 'TRUNCATE ', 'ALTER ', 'CREATE ', 'DROP ')
    ) for statement in statements)
    assert 'customer@example.com' not in json.dumps(report)
    with pg_engine.connect() as connection:
        assert connection.execute(text('SELECT count(*) FROM ecommerce.customers')).scalar_one() == 1


def test_audit_reports_incomplete_orders_without_repairing_them(pg_engine, seeded):
    with pg_engine.begin() as connection:
        order_id = connection.execute(text('''
            INSERT INTO ecommerce.orders(customer_id,order_date) VALUES (:customer,NULL)
            RETURNING id
        '''), {'customer': seeded['customer_id']}).scalar_one()
    report = audit(pg_engine)
    assert report['status'] == 'issues_found'
    assert report['checks']['orders_without_items'] == 1
    assert report['checks']['orders_without_date'] == 1
    assert {'orders_without_items', 'orders_without_date'} <= set(report['issues'])
    with pg_engine.connect() as connection:
        assert connection.execute(text('SELECT order_date FROM ecommerce.orders WHERE id=:id'),
                                  {'id': order_id}).one() == (None,)


def test_audit_detects_a_legacy_incomplete_publication(pg_engine, seeded):
    initialize_target(pg_engine)
    run_id = uuid4()
    with pg_engine.begin() as connection:
        connection.execute(text('''
            INSERT INTO analytics.etl_runs
            (run_id,snapshot_started_at,source_calendar,status,totals)
            VALUES (:id,now(),'test','incomplete','{}')
        '''), {'id': run_id})
        connection.execute(text('''
            INSERT INTO analytics.etl_current_run(singleton,run_id) VALUES (true,:id)
            ON CONFLICT(singleton) DO UPDATE SET run_id=EXCLUDED.run_id
        '''), {'id': run_id})
    report = audit(pg_engine)
    assert report['checks']['orders_without_items'] == 0
    assert report['analytics']['current_status'] == 'incomplete'
    assert 'published_analytics_incomplete' in report['issues']
