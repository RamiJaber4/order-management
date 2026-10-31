"""Audit the configured database with read-only queries; never repair source data.

Exit codes: 0 = checks passed, 1 = integrity/schema issues, 2 = audit unavailable.
Only aggregate diagnostics are printed; credentials and customer records are omitted.
"""
import argparse
import json
import os
from pathlib import Path
import sys

from alembic.config import Config
from alembic.script import ScriptDirectory
from dotenv import dotenv_values
from sqlalchemy import create_engine, inspect, text

ROOT = Path(__file__).resolve().parents[1]
TABLES = ('categories', 'customers', 'products', 'orders', 'order_items', 'payment')
CHECKS = {
    'orders_without_items': '''SELECT count(*) FROM ecommerce.orders o
        WHERE NOT EXISTS (SELECT 1 FROM ecommerce.order_items i WHERE i.order_id=o.id)''',
    'orders_without_date': 'SELECT count(*) FROM ecommerce.orders WHERE order_date IS NULL',
    'invalid_products': '''SELECT count(*) FROM ecommerce.products
        WHERE stock IS NULL OR stock<0 OR unit_price IS NULL OR unit_price<=0''',
    'invalid_items': '''SELECT count(*) FROM ecommerce.order_items
        WHERE price IS NULL OR price<0 OR quantity IS NULL OR quantity<=0''',
    'orphan_item_orders': '''SELECT count(*) FROM ecommerce.order_items i
        LEFT JOIN ecommerce.orders o ON o.id=i.order_id WHERE o.id IS NULL''',
    'orphan_item_products': '''SELECT count(*) FROM ecommerce.order_items i
        LEFT JOIN ecommerce.products p ON p.id=i.product_id WHERE p.id IS NULL''',
    'orphan_order_customers': '''SELECT count(*) FROM ecommerce.orders o
        LEFT JOIN ecommerce.customers c ON c.id=o.customer_id
        WHERE o.customer_id IS NOT NULL AND c.id IS NULL''',
    'orphan_product_categories': '''SELECT count(*) FROM ecommerce.products p
        LEFT JOIN ecommerce.categories c ON c.name=p.category_name
        WHERE p.category_name IS NOT NULL AND c.name IS NULL''',
    'orphan_payment_orders': '''SELECT count(*) FROM ecommerce.payment p
        LEFT JOIN ecommerce.orders o ON o.id=p.order_id
        WHERE p.order_id IS NOT NULL AND o.id IS NULL''',
    'unvalidated_constraints': '''SELECT count(*) FROM pg_constraint c
        JOIN pg_namespace n ON n.oid=c.connamespace
        WHERE n.nspname IN ('ecommerce','analytics') AND NOT c.convalidated''',
}


def audit_analytics(connection, inspector, report):
    tables = set(inspector.get_table_names(schema='analytics'))
    expected = {'etl_schema_version', 'etl_runs', 'etl_current_run', 'daily_order_metrics'}
    if not tables:
        report['warnings'].append('analytics_not_initialized')
        return
    if not expected <= tables:
        report['issues'].append({'missing_analytics_tables': sorted(expected - tables)})
        return
    versions = connection.execute(text('SELECT version FROM analytics.etl_schema_version')).scalars().all()
    current = connection.execute(text('''
        SELECT r.run_id,r.status FROM analytics.etl_runs r
        JOIN analytics.etl_current_run cur ON cur.run_id=r.run_id
    ''')).mappings().all()
    daily = connection.execute(text('''
        SELECT count(*) AS daily_rows,
               coalesce(sum(orders_without_items),0) AS orders_without_items
        FROM analytics.daily_order_metrics
    ''')).mappings().one()
    report['analytics'] = dict(daily, schema_versions=versions,
                              current_status=current[0]['status'] if len(current) == 1 else None)
    if versions != [1]:
        report['issues'].append('unsupported_analytics_schema_version')
    if len(current) > 1 or (not current and daily['daily_rows']):
        report['issues'].append('invalid_current_publication')
    if current:
        if current[0]['status'] != 'complete' or daily['orders_without_items']:
            report['issues'].append('published_analytics_incomplete')
        other_run_rows = connection.execute(text('''
            SELECT count(*) FROM analytics.daily_order_metrics WHERE run_id<>:run_id
        '''), {'run_id': current[0]['run_id']}).scalar_one()
        if other_run_rows:
            report['issues'].append('daily_metrics_reference_another_run')


def audit(engine):
    report = {'status': 'ok', 'issues': [], 'warnings': []}
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as connection:
        with connection.begin():
            connection.execute(text('SET TRANSACTION READ ONLY'))
            report['identity'] = dict(connection.execute(text('''
                SELECT current_database() AS database, inet_server_port() AS server_port
            ''')).mappings().one())
            report['identity'].update(configured_host=engine.url.host,
                                      configured_port=engine.url.port or 5432)
            inspector = inspect(connection)
            present = set(inspector.get_table_names(schema='ecommerce'))
            missing = sorted(set(TABLES) - present)
            if missing:
                report['issues'].append({'missing_tables': missing})
                report['status'] = 'issues_found'
                return report
            # Compare column names, types, nullability and constraints separately
            # through the repository's migration/model integration tests.
            config = Config(str(ROOT / 'alembic.ini'))
            config.set_main_option('script_location', str(ROOT / 'alembic'))
            expected = sorted(ScriptDirectory.from_config(config).get_heads())
            version_schema = next((schema for schema in ('public', 'ecommerce')
                                   if inspector.has_table('alembic_version', schema=schema)), None)
            actual = (sorted(connection.execute(text(
                f'SELECT version_num FROM {version_schema}.alembic_version'
            )).scalars()) if version_schema else [])
            report['migrations'] = {'expected': expected, 'actual': actual}
            if actual != expected:
                report['issues'].append('migration_revision_mismatch')
            report['counts'] = {table: connection.execute(text(
                f'SELECT count(*) FROM ecommerce.{table}'
            )).scalar_one() for table in TABLES}
            report['checks'] = {name: connection.execute(text(query)).scalar_one()
                                for name, query in CHECKS.items()}
            report['issues'].extend(name for name, count in report['checks'].items() if count)
            customer_columns = {col['name'] for col in inspector.get_columns('customers', schema='ecommerce')}
            if {'role', 'password_hash'} <= customer_columns:
                invalid_roles = connection.execute(text("""SELECT count(*) FROM ecommerce.customers
                    WHERE role IS NULL OR role NOT IN ('customer','admin')""")).scalar_one()
                report['checks']['invalid_roles'] = invalid_roles
                if invalid_roles:
                    report['issues'].append('invalid_roles')
                legacy = connection.execute(text('''SELECT count(*) FROM ecommerce.customers
                    WHERE password_hash IS NULL''')).scalar_one()
                report['legacy_customers_without_credentials'] = legacy
                if legacy:
                    report['warnings'].append('legacy_customers_cannot_log_in')
            else:
                report['issues'].append('customer_auth_columns_missing')
            report['sequences'] = {}
            for table in ('customers', 'products', 'orders', 'payment'):
                sequence = connection.execute(text(
                    f'SELECT last_value,is_called FROM ecommerce.{table}_id_seq'
                )).mappings().one()
                maximum = connection.execute(text(
                    f'SELECT coalesce(max(id),0) FROM ecommerce.{table}'
                )).scalar_one()
                safe = sequence['last_value'] + int(sequence['is_called']) > maximum
                report['sequences'][table] = {'max_id': maximum, 'next_id_above_max': safe}
                if not safe:
                    report['issues'].append(f'{table}_sequence_behind_data')
            audit_analytics(connection, inspector, report)
            if report['issues']:
                report['status'] = 'issues_found'
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', default=str(ROOT / '.env'))
    args = parser.parse_args()
    engine = None
    try:
        settings = dotenv_values(args.env_file)
        url = (os.getenv('ETL_SOURCE_URL') or settings.get('ETL_SOURCE_URL')
               or os.getenv('DATABASE_URL') or settings.get('DATABASE_URL'))
        if not url:
            raise ValueError('missing_database_url')
        engine = create_engine(url, hide_parameters=True, connect_args={
            'connect_timeout': 5, 'options': '-cstatement_timeout=30000 -clock_timeout=3000',
        })
        if engine.dialect.name != 'postgresql':
            raise ValueError('postgresql_required')
        report = audit(engine)
        print(json.dumps(report, default=str, indent=2))
        return 0 if report['status'] == 'ok' else 1
    except Exception as exc:
        print(json.dumps({'status': 'audit_unavailable', 'error_type': type(exc).__name__,
                          'sqlstate': getattr(getattr(exc, 'orig', None), 'sqlstate', None)}),
              file=sys.stderr)
        return 2
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
