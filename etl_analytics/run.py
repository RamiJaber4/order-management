"""Manual CLI; dry-run by default. Never print connection strings or raw DB errors."""
import argparse
import json
import sys

from etl_analytics.config import engines
from etl_analytics.load import initialize_target
from etl_analytics.pipeline import run_pipeline


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--init-target', action='store_true', help='Explicitly create analytics schema v1')
    parser.add_argument('--reconcile', action='store_true', help='Audit totals with extra source queries')
    parser.add_argument('--demo-database', help='Use an isolated database created by etl_analytics.demo')
    parser.add_argument('--source-calendar',
                        help='Metadata only: no timezone conversion is performed')
    args = parser.parse_args()
    source = target = None
    try:
        source, target = engines(args.demo_database)
        if args.init_target:
            initialize_target(target)
        report, _ = run_pipeline(source, target, publish=args.publish,
                                reconcile_source=args.reconcile,
                                source_calendar=args.source_calendar or (
                                    'synthetic_wall_clock' if args.demo_database
                                    else 'unconfirmed_source_wall_clock'))
        print(json.dumps(report, default=str))
        return 0
    except Exception as exc:
        # Library ValueErrors may contain connection input; expose only our codes.
        known_reasons = {
            'Set DATABASE_URL or ETL_SOURCE_URL and ETL_TARGET_URL',
            'ETL requires PostgreSQL',
            'target engine required for publication', 'missing_order_date',
            'invalid_order_items', 'source_reconciliation_failed',
            'orders_without_items: fix_source_data', 'invalid_demo_database_name',
            'invalid_order_day', 'duplicate_order_day', 'negative_daily_metric',
            'inconsistent_daily_order_counts', 'inconsistent_daily_item_counts',
            'another_etl_run_is_active', 'unsupported_etl_schema_version',
        }
        reason = str(exc) if str(exc) in known_reasons else type(exc).__name__
        print(json.dumps({'status':'failed','reason':reason}), file=sys.stderr)
        return 1
    finally:
        for engine in (source, target):
            if engine is not None:
                engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
