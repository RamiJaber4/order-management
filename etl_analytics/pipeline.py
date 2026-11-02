"""First successful publication is full; later runs poll timestamp changes."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from time import monotonic
from uuid import uuid4

from sqlalchemy import inspect, text

from etl_analytics.load import acquire_lock
from etl_analytics.run_logging import start_run, finish_run, safe_reason, committed_run
from etl_analytics.sales_extract import require_tracking, extract_sales, transform_sale, reconcile_sales
from etl_analytics.sales_load import create_stage, upsert_batch, read_checkpoint, save_checkpoint


class PipelineFailure(RuntimeError):
    def __init__(self, report):
        super().__init__(report['error_code'])
        self.report = report


def source_identity(engine):
    # A changed source endpoint must not reuse another source's checkpoint.
    url = engine.url
    return sha256(f'{url.host}:{url.port or 5432}/{url.database}'.encode()).hexdigest()


def _select_mode(report, checkpoint, full):
    report['mode'] = 'full' if full or checkpoint is None else 'incremental'
    report['watermark_before'] = checkpoint['watermark'] if checkpoint else None


def _process(source_engine, target, checkpoint, report, *, batch_size, lookback_seconds,
             reconcile_source):
    with source_engine.connect().execution_options(isolation_level='REPEATABLE READ') as source:
        with source.begin():
            source.execute(text('SET TRANSACTION READ ONLY'))
            require_tracking(source)
            upper = source.execute(text('SELECT clock_timestamp()')).scalar_one()
            report['snapshot_started_at'] = upper
            report['extract_upper_bound'] = upper
            previous = report['watermark_before']
            if report['mode'] == 'incremental' and previous > upper:
                raise ValueError('source_clock_moved_backwards')
            lower = (previous - timedelta(seconds=lookback_seconds)
                     if report['mode'] == 'incremental' else None)
            report['extract_lower_bound'] = lower
            if target is not None:
                if report['mode'] == 'full':
                    target.execute(text('DELETE FROM analytics.fact_order_sales'))
                create_stage(target)
            for rows in extract_sales(source, lower=lower, upper=upper, batch_size=batch_size):
                sales = []
                for row in rows:
                    report['rows_read'] += 1
                    try:
                        sale = transform_sale(row)
                    except ValueError:
                        report['rows_rejected'] += 1
                        raise
                    sales.append(sale)
                    report['totals']['units_read'] += int(sale['quantity'])
                    report['totals']['value_read'] += sale['line_value']
                if target is not None:
                    counts = upsert_batch(target, sales, report['run_id'])
                    for key, value in counts.items():
                        report['rows_' + key] += value
            if reconcile_source:
                report['reconciliation_totals'] = reconcile_sales(source, target)
                report['reconciled'] = True
            report['watermark_after'] = upper if target is not None else previous


def _finish(report, started, status):
    report['status'] = status
    report['finished_at'] = datetime.now(timezone.utc)
    report['duration_seconds'] = round(monotonic() - started, 6)


def run_pipeline(source_engine, target_engine=None, *, publish=False, full=False,
                 batch_size=2000, lookback_seconds=300, reconcile_source=False,
                 source_calendar='unconfirmed_source_wall_clock'):
    started = monotonic()
    now = datetime.now(timezone.utc)
    report = dict(run_id=str(uuid4()),pipeline_name='order_sales',mode='full',status='running',
                  started_at=now,snapshot_started_at=now,source_calendar=source_calendar,
                  published=False,reconciled=False,error_code=None,watermark_before=None,
                  watermark_after=None,lookback_seconds=lookback_seconds,
                  rows_read=0,rows_inserted=0,rows_updated=0,rows_unchanged=0,rows_rejected=0,
                  totals={'units_read':0,'value_read':0})
    logged = False
    try:
        if batch_size < 1 or lookback_seconds < 0:
            raise ValueError('invalid_batch_or_lookback')
        if publish and target_engine is None:
            raise ValueError('target engine required for publication')
        if reconcile_source and not publish:
            raise ValueError('reconcile_requires_publish')
        key = source_identity(source_engine)
        if publish:
            # Lock precedes source snapshot; metadata uses its own committed connection.
            with target_engine.connect().execution_options(isolation_level='READ COMMITTED') as target:
                with target.begin():
                    acquire_lock(target)
                    checkpoint = read_checkpoint(target)
                    _select_mode(report, checkpoint, full)
                    start_run(target_engine, report)
                    logged = True
                    if checkpoint and checkpoint['source_key'] != key and not full:
                        raise ValueError('checkpoint_source_mismatch: use_full')
                    _process(source_engine,target,checkpoint,report,batch_size=batch_size,
                             lookback_seconds=lookback_seconds,reconcile_source=reconcile_source)
                    save_checkpoint(target,report,key)
                    _finish(report,started,'complete')
                    finish_run(target,report)
            report['published'] = True
        else:
            checkpoint = None
            if target_engine is not None:
                with target_engine.connect() as target:
                    if inspect(target).has_table('etl_checkpoints',schema='analytics'):
                        checkpoint = read_checkpoint(target)
            _select_mode(report,checkpoint,full)
            if checkpoint and checkpoint['source_key'] != key and not full:
                raise ValueError('checkpoint_source_mismatch: use_full')
            _process(source_engine,None,checkpoint,report,batch_size=batch_size,
                     lookback_seconds=lookback_seconds,reconcile_source=False)
            _finish(report,started,'complete')
        return report
    except Exception as exc:
        commit_may_have_succeeded = report['status'] == 'complete'
        _finish(report,started,'failed')
        report['error_code'] = safe_reason(exc)
        report['watermark_after'] = report['watermark_before']
        report['totals']['attempted_inserted'] = report['rows_inserted']
        report['totals']['attempted_updated'] = report['rows_updated']
        report['rows_inserted'] = report['rows_updated'] = 0
        if logged:
            try:
                # Publication has already rolled back; failure history survives independently.
                with target_engine.begin() as connection:
                    # A lost COMMIT acknowledgement is ambiguous. Never turn a
                    # durably completed run into a failed one or report its old watermark.
                    completed = committed_run(connection,report['run_id'])
                    if completed:
                        report.update(completed)
                        report['duration_seconds'] = float(report['duration_seconds'])
                        report['published'] = True
                        report['commit_acknowledgement_recovered'] = True
                        return report
                    finish_run(connection,report)
            except Exception:
                report['run_log_saved'] = False
                if commit_may_have_succeeded:
                    report['commit_outcome_unknown'] = True
        raise PipelineFailure(report) from None
