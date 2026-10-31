"""Full refresh with SQL daily aggregation and one atomic target commit."""
from contextlib import nullcontext
from datetime import datetime, timezone
from time import monotonic
from uuid import uuid4

from sqlalchemy import text

from etl_analytics.extract import extract
from etl_analytics.transform import transform
from etl_analytics.validate import validate, reconcile
from etl_analytics.load import acquire_lock, load


def run_pipeline(source_engine, target_engine=None, *, publish=False,
                 reconcile_source=False,
                 source_calendar='unconfirmed_source_wall_clock'):
    if publish and target_engine is None:
        raise ValueError('target engine required for publication')
    started = monotonic()
    report = dict(run_id=str(uuid4()), snapshot_started_at=datetime.now(timezone.utc),
                  source_calendar=source_calendar, published=False)
    # Lock before opening the source snapshot: an older run cannot publish last.
    with target_engine.begin() if publish else nullcontext(None) as target:
        if publish:
            acquire_lock(target)
        with source_engine.connect().execution_options(isolation_level='REPEATABLE READ') as source:
            with source.begin():
                source.execute(text('SET TRANSACTION READ ONLY'))
                report['snapshot_started_at'] = source.execute(text('SELECT transaction_timestamp()')).scalar_one()
                days = transform(extract(source))
                report['totals'] = validate(days)
                if reconcile_source:
                    reconcile(source, report['totals'])
        report['reconciled'] = reconcile_source
        report['status'] = 'complete'
        report['day_count'] = len(days)
        if publish:
            load(target, days, report)
    report['published'] = publish
    report['duration_seconds'] = round(monotonic() - started, 3)
    return report, days
