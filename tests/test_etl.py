"""ETL integration checks use only the suite's disposable PostgreSQL database."""
from datetime import date

import pytest
from sqlalchemy import create_engine, event, text

from etl_analytics.load import initialize_target, LOCK_ID
from etl_analytics.pipeline import run_pipeline
from etl_analytics.transform import transform
from etl_analytics.validate import validate


@pytest.fixture
def etl_source(pg_engine, seeded):
    initialize_target(pg_engine)
    with pg_engine.begin() as conn:
        conn.execute(text('DELETE FROM analytics.etl_current_run'))
        conn.execute(text('DELETE FROM analytics.daily_order_metrics'))
        conn.execute(text('DELETE FROM analytics.etl_runs'))
        product = seeded['items'][0]['product_id']
        other = conn.execute(text("INSERT INTO ecommerce.products(name,unit_price,stock) VALUES ('Other',40,100) RETURNING id")).scalar_one()
        a = conn.execute(text("INSERT INTO ecommerce.orders(customer_id,order_date) VALUES (:c,'2026-01-01 10:00') RETURNING id"), {'c':seeded['customer_id']}).scalar_one()
        b = conn.execute(text("INSERT INTO ecommerce.orders(customer_id,order_date) VALUES (:c,'2026-01-02 00:00') RETURNING id"), {'c':seeded['customer_id']}).scalar_one()
        conn.execute(text('INSERT INTO ecommerce.order_items(order_id,product_id,quantity,price) VALUES (:o,:p,:q,:v)'), [
            dict(o=a,p=product,q=2,v=30), dict(o=a,p=other,q=1,v=40),dict(o=b,p=product,q=1,v=50)])
    return pg_engine, a, b


def read_days(engine):
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(text('SELECT * FROM analytics.daily_order_metrics ORDER BY order_day')).mappings()]


def test_etl_expected_totals_and_idempotency(etl_source):
    engine, _, _ = etl_source
    first, days = run_pipeline(engine, engine, publish=True)
    assert first['totals'] == dict(order_count=2, orders_with_items=2, orders_without_items=0,
                                  item_count=3, units=4, recorded_item_value_ils=150)
    assert days[0]['recorded_item_value_ils'] == 100
    assert days[0]['order_count'] == 1
    second, again = run_pipeline(engine, engine, publish=True)
    assert days == again
    assert len(read_days(engine)) == 2
    assert first['run_id'] != second['run_id']


def test_etl_daily_sql_counts_orders_once(etl_source):
    engine, _, b = etl_source
    with engine.begin() as conn:
        conn.execute(text("UPDATE ecommerce.orders SET order_date='2026-01-01 23:59:59' WHERE id=:id"), {'id': b})
    report, days = run_pipeline(engine)
    assert len(days) == 1
    assert days[0] == dict(order_day=date(2026, 1, 1), order_count=2,
                          orders_with_items=2, orders_without_items=0,
                          item_count=3, units=4, recorded_item_value_ils=150)
    assert report['status'] == 'complete'


def test_etl_dry_run_does_not_publish(etl_source):
    engine, _, _ = etl_source
    report, _ = run_pipeline(engine)
    assert not report['published']
    assert read_days(engine) == []


def test_etl_missing_items_are_a_source_error_and_preserve_last_publication(etl_source):
    engine, _, _ = etl_source
    run_pipeline(engine, engine, publish=True)
    before = read_days(engine)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO ecommerce.orders(order_date) VALUES ('2026-01-01')"))
    with pytest.raises(ValueError, match='orders_without_items'):
        run_pipeline(engine, engine, publish=True)
    assert read_days(engine) == before


def test_etl_changes_deletions_and_empty_refresh(etl_source):
    engine, a, b = etl_source
    run_pipeline(engine, engine, publish=True)
    with engine.begin() as conn:
        conn.execute(text('UPDATE ecommerce.products SET unit_price=999'))
    assert run_pipeline(engine)[0]['totals']['recorded_item_value_ils'] == 150
    with engine.begin() as conn:
        conn.execute(text('UPDATE ecommerce.order_items SET price=60 WHERE order_id=:id'), {'id':b})
        conn.execute(text('DELETE FROM ecommerce.order_items WHERE order_id=:id'), {'id':a})
        conn.execute(text('DELETE FROM ecommerce.orders WHERE id=:id'), {'id':a})
    report, _ = run_pipeline(engine, engine, publish=True)
    assert report['totals']['recorded_item_value_ils'] == 60
    assert read_days(engine)[0]['order_day'] == date(2026,1,2)
    assert len(read_days(engine)) == 1
    with engine.begin() as conn:
        conn.execute(text('DELETE FROM ecommerce.order_items'))
        conn.execute(text('DELETE FROM ecommerce.orders'))
    report, _ = run_pipeline(engine, engine, publish=True)
    assert report['day_count'] == 0
    assert read_days(engine) == []
    with engine.connect() as conn:
        assert str(conn.execute(text('SELECT run_id FROM analytics.etl_current_run')).scalar_one()) == report['run_id']


def test_etl_publication_failure_rolls_back(etl_source, monkeypatch):
    engine, _, _ = etl_source
    run_pipeline(engine, engine, publish=True)
    before = read_days(engine)
    def failing_load(conn, days, report):
        conn.execute(text('DELETE FROM analytics.daily_order_metrics'))
        raise RuntimeError('injected_failure_after_delete')
    monkeypatch.setattr('etl_analytics.pipeline.load', failing_load)
    with pytest.raises(RuntimeError, match='injected_failure'):
        run_pipeline(engine, engine, publish=True)
    assert read_days(engine) == before
    with engine.connect() as conn:
        assert conn.execute(text('SELECT count(*) FROM analytics.etl_runs')).scalar_one() == 1


def test_etl_overlap_is_rejected(etl_source):
    engine, _, _ = etl_source
    with engine.begin() as conn:
        conn.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key':LOCK_ID})
        with pytest.raises(RuntimeError, match='another_etl_run'):
            run_pipeline(engine, engine, publish=True)


def test_etl_null_date_is_rejected(etl_source):
    engine, a, _ = etl_source
    with engine.begin() as conn:
        conn.execute(text('UPDATE ecommerce.orders SET order_date=NULL WHERE id=:id'), {'id':a})
    with pytest.raises(ValueError, match='missing_order_date'):
        run_pipeline(engine, engine, publish=True)


def test_etl_validation_uses_same_snapshot(etl_source, monkeypatch):
    engine, _, b = etl_source
    from etl_analytics.validate import reconcile
    def concurrent_change(conn, totals):
        with engine.begin() as writer:
            writer.execute(text('UPDATE ecommerce.order_items SET price=70 WHERE order_id=:id'), {'id':b})
        return reconcile(conn, totals)
    monkeypatch.setattr('etl_analytics.pipeline.reconcile', concurrent_change)
    assert run_pipeline(engine, reconcile_source=True)[0]['totals']['recorded_item_value_ils'] == 150


def test_etl_invalid_values_fail_before_publication():
    with pytest.raises(ValueError, match='invalid_order_items'):
        transform([dict(order_day=date(2026,1,1),invalid_items=1)])


def test_etl_default_avoids_extra_source_scans(etl_source):
    engine, _, _ = etl_source
    reads = []
    def track_reads(conn, cursor, statement, parameters, context, executemany):
        if 'ecommerce.' in statement:
            reads.append(statement)
    event.listen(engine, 'before_cursor_execute', track_reads)
    try:
        report, days = run_pipeline(engine)
        assert len(reads) == 1
        assert report['reconciled'] is False
        reads.clear()
        audited, audited_days = run_pipeline(engine, reconcile_source=True)
        assert len(reads) == 3
        assert audited['reconciled'] is True
        assert audited_days == days
        assert audited['totals'] == report['totals']
    finally:
        event.remove(engine, 'before_cursor_execute', track_reads)


def test_etl_audit_catches_totals_that_local_checks_cannot(etl_source, monkeypatch):
    engine, _, _ = etl_source
    from etl_analytics.extract import extract
    def changed_result(conn):
        days = [dict(day) for day in extract(conn)]
        days[0]['recorded_item_value_ils'] += 1
        return days
    monkeypatch.setattr('etl_analytics.pipeline.extract', changed_result)
    assert run_pipeline(engine)[0]['totals']['recorded_item_value_ils'] == 151
    with pytest.raises(ValueError, match='source_reconciliation_failed'):
        run_pipeline(engine, reconcile_source=True)


@pytest.mark.parametrize('change,reason', [
    ({'units': -1}, 'negative_daily_metric'),
    ({'order_count': 3}, 'inconsistent_daily_order_counts'),
    ({'units': 1}, 'inconsistent_daily_item_counts'),
])
def test_etl_local_result_checks(change, reason):
    day = dict(order_day=date(2026, 1, 1), order_count=1, orders_with_items=1,
               orders_without_items=0, item_count=2, units=3, recorded_item_value_ils=60)
    day.update(change)
    with pytest.raises(ValueError, match=reason):
        validate([day])


def test_etl_duplicate_days_are_rejected():
    day = dict(order_day=date(2026, 1, 1), order_count=1, orders_with_items=1,
               orders_without_items=0, item_count=2, units=3, recorded_item_value_ils=60)
    with pytest.raises(ValueError, match='duplicate_order_day'):
        validate([day, day])


def test_demo_creates_complete_data_in_a_separate_database(pg_engine):
    from etl_analytics.demo import create_demo
    result = create_demo(pg_engine.url, order_count=37)
    demo = create_engine(pg_engine.url.set(database=result['database']))
    try:
        assert result['database'] != pg_engine.url.database
        report, _ = run_pipeline(demo, reconcile_source=True)
        assert report['totals']['order_count'] == 37
        assert report['totals']['orders_without_items'] == 0
        assert report['totals']['item_count'] == result['items']
        with demo.connect() as conn:
            assert conn.execute(text('''
                SELECT count(*) FROM ecommerce.products p
                LEFT JOIN (SELECT product_id,sum(quantity) AS units
                           FROM ecommerce.order_items GROUP BY product_id) i
                ON i.product_id=p.id
                WHERE p.stock + coalesce(i.units,0) <> 100000
            ''')).scalar_one() == 0
    finally:
        demo.dispose()
        # create_demo generates the exact name; only its separate test database is removed.
        admin = create_engine(pg_engine.url.set(database='postgres'), isolation_level='AUTOCOMMIT')
        try:
            with admin.connect() as conn:
                conn.execute(text(f'DROP DATABASE "{result["database"]}" WITH (FORCE)'))
        finally:
            admin.dispose()
