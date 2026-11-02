"""A running attempt survives publication rollback; success commits with its data."""
import json

from sqlalchemy import text

COUNTS = ('rows_read','rows_inserted','rows_updated','rows_unchanged','rows_rejected')
SAFE_REASONS = {
    'missing_order_date','invalid_order_items','orders_without_items: fix_source_data',
    'source_reconciliation_failed','source_tracking_migration_required',
    'another_etl_run_is_active','unsupported_etl_schema_version',
    'checkpoint_source_mismatch: use_full','source_clock_moved_backwards',
    'invalid_batch_or_lookback','reconcile_requires_publish',
    'target engine required for publication',
}


def safe_reason(exc):
    return str(exc) if str(exc) in SAFE_REASONS else type(exc).__name__


def start_run(engine, report):
    with engine.begin() as connection:
        connection.execute(text('''INSERT INTO analytics.etl_runs
            (run_id,snapshot_started_at,source_calendar,status,totals,published_at,
             pipeline_name,mode,started_at,watermark_before,lookback_seconds)
            VALUES (:run_id,:snapshot_started_at,:source_calendar,'running','{}',NULL,
                    'order_sales',:mode,:started_at,:watermark_before,:lookback_seconds)'''), report)


def finish_run(connection, report):
    counters = ','.join(f'{field}=:{field}' for field in COUNTS)
    connection.execute(text(f'''UPDATE analytics.etl_runs SET status=:status,
        snapshot_started_at=:snapshot_started_at,finished_at=:finished_at,
        duration_seconds=:duration_seconds,error_code=:error_code,totals=CAST(:totals AS jsonb),
        watermark_after=:watermark_after,
        published_at=CASE WHEN :status='complete' THEN clock_timestamp() ELSE NULL END,
        {counters} WHERE run_id=:run_id AND status='running'
        '''),
        dict(report,totals=json.dumps(report['totals'])))


def committed_run(connection, run_id):
    row = connection.execute(text('''SELECT status,mode,snapshot_started_at,finished_at,
        duration_seconds,rows_read,rows_inserted,rows_updated,rows_unchanged,rows_rejected,
        totals,watermark_before,watermark_after,error_code FROM analytics.etl_runs
        WHERE run_id=:id AND status='complete' '''), {'id':run_id}).mappings().one_or_none()
    return dict(row) if row else None
