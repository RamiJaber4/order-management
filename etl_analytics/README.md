# Daily order ETL

The implemented pipeline is a manual **full refresh**, using PostgreSQL and
SQLAlchemy. It reads `ecommerce.orders` and `ecommerce.order_items` and publishes
one row per order day to `analytics.daily_order_metrics`.

## Execution

Run from the repository root after installing `requirements.txt`. Configure the
existing source database in `.env` or environment variables:

- `ETL_SOURCE_URL` falls back to `DATABASE_URL`.
- `ETL_TARGET_URL` falls back to the source URL.
- Both connections must use PostgreSQL. Keep credentials in private environment
  files, not source control.

Inspect the configured database before publishing:

```powershell
.\.venv\Scripts\python scripts/audit_database.py
```

Default execution reads and validates without writing results:

```powershell
python -m etl_analytics.run
python -m etl_analytics.run --reconcile
```

Explicitly initialize the target tables and publish:

```powershell
python -m etl_analytics.run --init-target --publish --reconcile
```

`--init-target` is a separate write operation: it creates the analytics schema
even when `--publish` is absent. Source application migrations must already be
applied; this option only initializes analytics schema version 1. Existing
incompatible analytics tables are not repaired by `CREATE TABLE IF NOT EXISTS`.

The Docker image includes the ETL package. For a running development API:

```powershell
docker compose exec api python -m etl_analytics.run --reconcile
```

There is no scheduler or automatic retry loop.

## Stages and guarantees

| File | Responsibility |
| --- | --- |
| `config.py` | Resolve source/target URLs and bounded connection/query waits |
| `run.py` | Parse CLI options; return a JSON report and exit status |
| `pipeline.py` | Coordinate the snapshot, validation and atomic publication |
| `extract.py` | Aggregate items per order, then orders per day inside PostgreSQL |
| `transform.py` | Reject missing dates/invalid items and normalize integer fields |
| `validate.py` | Check daily results; optionally reconcile global totals with the source |
| `load.py` | Initialize analytics tables and replace published metrics in one transaction |
| `schema.sql` | Explicit analytics schema version 1 bootstrap |
| `demo.py` | Generate complete synthetic data in a new isolated database |

Publishing takes a PostgreSQL transaction advisory lock **before** opening the
source snapshot. A second publisher using this lock fails with
`another_etl_run_is_active`. Source reads use a read-only `REPEATABLE READ`
transaction, including optional reconciliation.

The default path runs one source aggregation query. `--reconcile` adds two
source queries to compare order count, item count, units and value. Local
validation alone cannot prove that every aggregate matches the source; the
optional audit compares global totals, not every individual day.

Publication inserts a run record, replaces all daily rows and updates the
current-run pointer in one target transaction. Failure rolls back those writes
and preserves the previous publication. Repeating unchanged input produces the
same metrics with a new run ID. Updates and deletions in the source are reflected
on the next full refresh. Old daily versions are not archived; run records remain.
Failed runs are reported by the CLI and are not persisted as failure records.

## Metric definitions

- `order_count`: number of orders, counted once despite multiple item rows.
- `orders_with_items` / `orders_without_items`: completeness checks. Any order
  without items causes the run to fail; incomplete publication is unsupported.
- `item_count`: number of order-item rows.
- `units`: sum of item quantities.
- `recorded_item_value_ils`: sum of the saved item price multiplied by quantity.
  Current product prices do not enter this calculation.

The legacy `_ils` field name does not establish the source currency or whether
prices are major/minor units. Values are preserved without currency conversion.
Order value is not proof of payment collection or profit.

`order_date` has no timezone. Grouping uses its stored calendar date without
conversion. `--source-calendar` records descriptive metadata only. Daily product
metrics, incremental loading and timezone conversion are not implemented.

## Isolated demo

```powershell
python -m etl_analytics.demo --orders 10000
```

This requires `CREATEDB` and creates a database named `etl_demo_<12 hex digits>`.
It applies application migrations, creates related synthetic rows and adjusts
synthetic stock. It never fills gaps in the original source database. Use the
exact returned name (replace the placeholder below):

```powershell
python -m etl_analytics.run --demo-database etl_demo_<returned_suffix> --init-target --publish --reconcile
```

Both source and target are forced to that demo database. Successful demo databases
remain for inspection; they are not automatically deleted.

## Tests

`tests/test_etl.py` uses a randomly named disposable PostgreSQL database created
by the test suite. Set `TEST_POSTGRES_URL` to a server/account with `CREATEDB`:

```powershell
python -m pytest -q tests/test_etl.py
```

Coverage includes totals, multi-item order counting, idempotency, dry runs,
invalid/incomplete source data, edits/deletions/empty refreshes, publication
rollback, concurrent publishers, snapshot consistency, optional reconciliation
and isolated demo generation.

Older generated PDFs and run-evidence JSON files document previous versions.
They are local artifacts excluded from Git and are not current operational docs.
