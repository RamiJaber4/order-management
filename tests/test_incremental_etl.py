"""Real source/target databases: first full, then changes, rollback and replay."""
import json
from pathlib import Path
import sys
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, text

from test_etl import etl_source
from etl_analytics import pipeline, run as cli
from etl_analytics.load import initialize_target


@pytest.fixture(scope='session')
def sales_target(pg_engine):
    name = 'ecommerce_sales_target_' + uuid4().hex
    admin = create_engine(pg_engine.url.set(database='postgres'), isolation_level='AUTOCOMMIT')
    target = create_engine(pg_engine.url.set(database=name))
    created = False
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
            created = True
        initialize_target(target)
        yield target
    finally:
        target.dispose()
        if created:
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def sales_case(etl_source, sales_target):
    source,a,b = etl_source
    with sales_target.begin() as connection:
        connection.execute(text('DELETE FROM analytics.etl_checkpoints'))
        connection.execute(text('DELETE FROM analytics.fact_order_sales'))
        connection.execute(text('DELETE FROM analytics.etl_runs'))
    # Simulate committed historical data, before the initial snapshot. Only this
    # disposable test database is touched; normal writers cannot backdate tracking.
    with source.begin() as connection:
        for table in ('orders','order_items'):
            connection.execute(text(f'ALTER TABLE ecommerce.{table} DISABLE TRIGGER {table}_touch_updated_at'))
            connection.execute(text(f"UPDATE ecommerce.{table} SET updated_at=clock_timestamp()-INTERVAL '1 hour'"))
            connection.execute(text(f'ALTER TABLE ecommerce.{table} ENABLE TRIGGER {table}_touch_updated_at'))
    return source,sales_target,a,b


def publish(case, **options):
    source,target,_,_ = case
    return pipeline.run_pipeline(source,target,publish=True,batch_size=2,**options)


def state(target):
    with target.connect() as connection:
        facts = [dict(row) for row in connection.execute(text(
            'SELECT * FROM analytics.fact_order_sales ORDER BY order_id,product_id')).mappings()]
        checkpoint = connection.execute(text('SELECT * FROM analytics.etl_checkpoints')).mappings().one_or_none()
    return facts,dict(checkpoint) if checkpoint else None


def test_auto_full_then_no_change_incremental(sales_case):
    source,target,_,_ = sales_case
    assert source.url.database != target.url.database
    first = publish(sales_case,reconcile_source=True)
    assert first['mode'] == 'full'
    assert first['rows_read'] == first['rows_inserted'] == 3
    facts,before = state(target)
    assert len(facts) == 3
    assert sum(row['line_value'] for row in facts) == 150
    second = publish(sales_case,reconcile_source=True)
    assert second['mode'] == 'incremental'
    assert second['rows_read'] == second['rows_inserted'] == second['rows_updated'] == 0
    again,after = state(target)
    assert facts == again
    assert after['watermark'] >= before['watermark']
    assert str(after['run_id']) == second['run_id']


def test_incremental_new_item_old_update_late_order_and_order_date(sales_case):
    source,target,a,b = sales_case
    publish(sales_case)
    with source.begin() as connection:
        connection.execute(text('UPDATE ecommerce.order_items SET price=60 WHERE order_id=:id'), {'id':b})
        connection.execute(text("UPDATE ecommerce.orders SET order_date='2025-12-30' WHERE id=:id"), {'id':a})
        late = connection.execute(text("INSERT INTO ecommerce.orders(order_date) VALUES ('2025-01-01') RETURNING id")).scalar_one()
        product = connection.execute(text('SELECT product_id FROM ecommerce.order_items WHERE order_id=:id'), {'id':b}).scalar_one()
        connection.execute(text('''INSERT INTO ecommerce.order_items(order_id,product_id,quantity,price)
            VALUES (:id,:product,2,20)'''), {'id':late,'product':product})
    report = publish(sales_case,reconcile_source=True)
    assert report['mode'] == 'incremental'
    assert report['rows_inserted'] == 1
    assert report['rows_updated'] == 3
    facts,_ = state(target)
    assert len(facts) == 4
    assert sum(row['line_value'] for row in facts) == 200
    assert all(str(row['order_date'].date()) == '2025-12-30' for row in facts if row['order_id']==a)
    assert report['reconciliation_totals']['order_count'] == 3


def test_overlap_replays_without_duplicate_writes(sales_case):
    source,target,_,b = sales_case
    publish(sales_case)
    with source.begin() as connection:
        connection.execute(text('UPDATE ecommerce.order_items SET price=60 WHERE order_id=:id'), {'id':b})
    changed = publish(sales_case)
    before,_ = state(target)
    repeated = publish(sales_case)
    assert changed['rows_updated'] == 1
    assert repeated['rows_read'] == repeated['rows_unchanged'] == 1
    assert repeated['rows_inserted'] == repeated['rows_updated'] == 0
    assert state(target)[0] == before


def test_failure_rolls_back_facts_checkpoint_and_success_then_retry(sales_case,monkeypatch):
    source,target,_,b = sales_case
    publish(sales_case)
    before = state(target)
    with source.begin() as connection:
        connection.execute(text('UPDATE ecommerce.order_items SET price=60 WHERE order_id=:id'), {'id':b})
    original = pipeline.save_checkpoint
    def fail(connection,report,key):
        original(connection,report,key)
        raise RuntimeError('private failure text must not be logged')
    monkeypatch.setattr(pipeline,'save_checkpoint',fail)
    with pytest.raises(pipeline.PipelineFailure) as error:
        publish(sales_case)
    assert state(target) == before
    report = error.value.report
    assert report['status'] == 'failed'
    assert report['watermark_after'] == report['watermark_before']
    assert report['rows_updated'] == 0
    with target.connect() as connection:
        failed = connection.execute(text("SELECT * FROM analytics.etl_runs WHERE status='failed'")).mappings().one()
    assert failed['rows_read'] == 1
    assert failed['duration_seconds'] >= 0
    assert failed['finished_at'] is not None
    assert failed['error_code'] == 'RuntimeError'
    assert failed['watermark_before'] == failed['watermark_after'] == before[1]['watermark']
    assert 'private failure' not in json.dumps(report,default=str)
    monkeypatch.setattr(pipeline,'save_checkpoint',original)
    retried = publish(sales_case,reconcile_source=True)
    assert retried['mode'] == 'incremental'
    assert retried['rows_updated'] == 1
    assert sum(row['line_value'] for row in state(target)[0]) == 160


def test_failed_first_run_retries_full_and_logs_failure(sales_case):
    source,target,a,_ = sales_case
    with source.begin() as connection:
        connection.execute(text('UPDATE ecommerce.orders SET order_date=NULL WHERE id=:id'), {'id':a})
    with pytest.raises(pipeline.PipelineFailure) as error:
        publish(sales_case)
    assert error.value.report['rows_rejected'] == 1
    assert state(target) == ([],None)
    with source.begin() as connection:
        connection.execute(text("UPDATE ecommerce.orders SET order_date='2026-01-01' WHERE id=:id"), {'id':a})
    assert publish(sales_case)['mode'] == 'full'
    with target.connect() as connection:
        assert connection.execute(text('SELECT status FROM analytics.etl_runs ORDER BY started_at')).scalars().all() == ['failed','complete']


def test_inflight_commit_is_recovered_by_overlap(sales_case):
    source,target,_,b = sales_case
    publish(sales_case)
    with source.connect() as writer:
        with writer.begin():
            writer.execute(text('UPDATE ecommerce.order_items SET price=60 WHERE order_id=:id'), {'id':b})
            # The write is still invisible, although its timestamp is already assigned.
            middle = publish(sales_case)
            assert middle['rows_read'] == 0
    recovered = publish(sales_case,reconcile_source=True)
    assert recovered['rows_updated'] == 1
    assert sum(row['line_value'] for row in state(target)[0]) == 160


def test_zero_overlap_limit_is_detected_by_reconciliation_and_full_recovers(sales_case):
    source,target,_,b = sales_case
    publish(sales_case)
    with source.begin() as writer:
        writer.execute(text('UPDATE ecommerce.order_items SET price=60 WHERE order_id=:id'), {'id':b})
        publish(sales_case,lookback_seconds=0)
    before = state(target)
    with pytest.raises(pipeline.PipelineFailure) as error:
        publish(sales_case,lookback_seconds=0,reconcile_source=True)
    assert error.value.report['error_code'] == 'source_reconciliation_failed'
    assert state(target) == before
    assert publish(sales_case,full=True,reconcile_source=True)['mode'] == 'full'
    assert sum(row['line_value'] for row in state(target)[0]) == 160


def test_hard_delete_is_not_silently_claimed_supported(sales_case):
    source,target,a,_ = sales_case
    publish(sales_case)
    before = state(target)
    with source.begin() as connection:
        connection.execute(text('''DELETE FROM ecommerce.order_items
            WHERE order_id=:id AND product_id=(SELECT min(product_id) FROM ecommerce.order_items WHERE order_id=:id)'''), {'id':a})
    with pytest.raises(pipeline.PipelineFailure) as error:
        publish(sales_case,reconcile_source=True)
    assert error.value.report['error_code'] == 'source_reconciliation_failed'
    assert state(target) == before
    assert publish(sales_case,full=True,reconcile_source=True)['rows_inserted'] == 2


def test_incremental_does_not_delete_all_facts_or_scan_all_source_by_design(sales_case):
    source,target,_,_ = sales_case
    publish(sales_case)
    statements = []
    def capture(conn,cursor,statement,parameters,context,executemany):
        statements.append(statement)
    event.listen(target,'before_cursor_execute',capture)
    try:
        report = publish(sales_case)
    finally:
        event.remove(target,'before_cursor_execute',capture)
    assert report['rows_read'] == 0
    assert not any(sql.strip().upper().startswith('DELETE FROM ANALYTICS.FACT_ORDER_SALES') for sql in statements)


def test_empty_initial_load_still_initializes_checkpoint(sales_case):
    source,target,_,_ = sales_case
    with source.begin() as connection:
        connection.execute(text('DELETE FROM ecommerce.order_items'))
        connection.execute(text('DELETE FROM ecommerce.orders'))
    assert publish(sales_case)['mode'] == 'full'
    assert state(target)[1] is not None
    second = publish(sales_case)
    assert second['mode'] == 'incremental'
    assert second['rows_read'] == 0


def test_cli_failure_emits_run_metadata_without_secrets(sales_case,monkeypatch,capsys):
    source,target,a,_ = sales_case
    with source.begin() as connection:
        connection.execute(text('UPDATE ecommerce.orders SET order_date=NULL WHERE id=:id'), {'id':a})
    monkeypatch.setattr(cli,'engines',lambda _: (source,target))
    monkeypatch.setattr(sys,'argv',['etl_analytics.run','--publish'])
    assert cli.main() == 1
    report = json.loads(capsys.readouterr().err)
    assert report['run_id']
    assert report['duration_seconds'] >= 0
    assert report['status'] == 'failed'
    assert report['error_code'] == 'missing_order_date'


def test_dry_run_never_initializes_or_advances_checkpoint(sales_case):
    source,target,_,_ = sales_case
    report = pipeline.run_pipeline(source,target)
    assert report['mode'] == 'full' and report['rows_read'] == 3
    assert not report['published']
    assert state(target) == ([],None)
    with target.connect() as connection:
        assert connection.execute(text('SELECT count(*) FROM analytics.etl_runs')).scalar_one() == 0


def test_tracking_trigger_overrides_backdated_client_timestamp(sales_case):
    source,_,_,b = sales_case
    with source.begin() as connection:
        previous = connection.execute(text('SELECT updated_at FROM ecommerce.orders WHERE id=:id'), {'id':b}).scalar_one()
        current = connection.execute(text("""UPDATE ecommerce.orders
            SET updated_at='1900-01-01',notes='tracking probe' WHERE id=:id RETURNING updated_at"""), {'id':b}).scalar_one()
    assert current > previous


def test_running_log_is_visible_before_publication_commits(sales_case,monkeypatch):
    _,target,_,_ = sales_case
    original = pipeline.upsert_batch
    observed = []
    def inspect_during_load(connection,sales,run_id):
        with target.connect() as reader:
            observed.append(reader.execute(text('SELECT status FROM analytics.etl_runs WHERE run_id=:id'),
                                           {'id':run_id}).scalar_one())
            assert reader.execute(text('SELECT count(*) FROM analytics.fact_order_sales')).scalar_one() == 0
        return original(connection,sales,run_id)
    monkeypatch.setattr(pipeline,'upsert_batch',inspect_during_load)
    publish(sales_case)
    assert observed == ['running','running']


def test_walkthrough_is_isolated_and_demonstrates_recovery(pg_engine):
    from etl_analytics.walkthrough import demonstrate
    result = demonstrate(pg_engine.url)
    assert result['database'] != pg_engine.url.database
    try:
        assert [run['status'] for run in result['runs']] == ['complete','complete','complete','failed','complete']
        assert result['runs'][0]['mode'] == 'full'
        assert all(run['mode']=='incremental' for run in result['runs'][1:])
        assert result['runs'][2]['rows_inserted'] == result['runs'][2]['rows_updated'] == 1
        assert result['steps'][3]['facts_and_checkpoint_unchanged']
        assert result['steps'][-1]['state']['facts']['fact_rows'] == 7
    finally:
        admin = create_engine(pg_engine.url.set(database='postgres'), isolation_level='AUTOCOMMIT')
        try:
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE "{result["database"]}" WITH (FORCE)'))
        finally:
            admin.dispose()


def test_target_v1_upgrade_preserves_old_publication_and_is_repeatable(pg_engine):
    # pg_engine is the suite's disposable database, never the configured app DB.
    run_id = uuid4()
    sql = Path(__file__).with_name('fixtures').joinpath('analytics_v1.sql').read_text(encoding='utf-8')
    with pg_engine.begin() as connection:
        connection.execute(text('DROP SCHEMA IF EXISTS analytics CASCADE'))
        for statement in sql.split(';'):
            if statement.strip():
                connection.execute(text(statement))
        connection.execute(text('''INSERT INTO analytics.etl_runs
            (run_id,snapshot_started_at,source_calendar,status,totals)
            VALUES (:id,now(),'test','complete','{}')'''), {'id':run_id})
        connection.execute(text('''INSERT INTO analytics.daily_order_metrics
            VALUES ('2026-01-01',1,1,0,1,2,60,:id)'''), {'id':run_id})
        connection.execute(text('INSERT INTO analytics.etl_current_run VALUES (true,:id)'), {'id':run_id})
    initialize_target(pg_engine)
    initialize_target(pg_engine)
    with pg_engine.connect() as connection:
        assert connection.execute(text('SELECT version FROM analytics.etl_schema_version')).scalar_one() == 2
        assert connection.execute(text('SELECT recorded_item_value_ils FROM analytics.daily_order_metrics')).scalar_one() == 60
        assert connection.execute(text('SELECT run_id FROM analytics.etl_current_run')).scalar_one() == run_id
        assert connection.execute(text('SELECT pipeline_name FROM analytics.etl_runs')).scalar_one() == 'daily_orders'
        assert connection.execute(text('SELECT count(*) FROM analytics.etl_checkpoints')).scalar_one() == 0


def test_source_endpoint_change_requires_explicit_full(sales_case):
    source,target,_,_ = sales_case
    publish(sales_case)
    before = state(target)
    different = create_engine(source.url.set(database='different_source_not_connected'))
    try:
        with pytest.raises(pipeline.PipelineFailure) as error:
            pipeline.run_pipeline(different,target,publish=True)
        assert error.value.report['error_code'] == 'checkpoint_source_mismatch: use_full'
        assert state(target) == before
    finally:
        different.dispose()


def test_lost_commit_acknowledgement_does_not_downgrade_success(sales_case):
    _,target,_,_ = sales_case
    injected = False
    def lose_ack(connection):
        nonlocal injected
        if not injected and connection.execute(text('SELECT count(*) FROM analytics.fact_order_sales')).scalar_one():
            # Model the server committing, followed by a lost response to the client.
            injected = True
            connection.connection.driver_connection.commit()
            raise RuntimeError('private connection error')
    event.listen(target,'commit',lose_ack)
    try:
        report = publish(sales_case)
    finally:
        event.remove(target,'commit',lose_ack)
    assert injected
    assert report['status'] == 'complete' and report['published']
    assert report['commit_acknowledgement_recovered']
    assert report['rows_inserted'] == 3
    assert str(state(target)[1]['run_id']) == report['run_id']
    with target.connect() as connection:
        assert connection.execute(text('SELECT status FROM analytics.etl_runs')).scalar_one() == 'complete'
