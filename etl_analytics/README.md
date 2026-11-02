# Order sales ETL: automatic full then incremental

The default pipeline loads one row per order item into
`analytics.fact_order_sales`. The business key is `(order_id, product_id)`.
The first **successful publication** loads everything; later publications poll
recent source changes and UPSERT the affected items. This is timestamp polling,
not CDC, and has the bounded-delay limitations described below.

Arabic walkthrough: [كيف تعمل النسخة؟](LEARNING_AR.md).

## Try the complete learning example

Install `requirements.txt`, configure a PostgreSQL account with `CREATEDB` in
private `.env`, then run from the repository root:

```powershell
python -m etl_analytics.walkthrough
```

This always creates a NEW `etl_demo_<12 hex digits>` database and applies source
migrations there. It runs five steps and prints their reports:

1. Full publication of three synthetic orders and six items.
2. Incremental replay with no changes; facts are not rewritten.
3. One new item on a late-dated order and one old item price correction.
4. A deliberate failure after attempting the checkpoint write: facts and
   checkpoint both roll back; a failed run record remains.
5. A successful incremental retry.

The new database is retained for inspection. Existing development, test and demo
databases are not changed. Recent items are re-read because of the overlap
window, so `rows_read` can exceed the number of changed records.

## Source prerequisites and target initialization

Source Alembic revision `0005_order_tracking` adds indexed `updated_at timestamptz`
columns to orders and order items. Database triggers set `clock_timestamp()` on
EVERY insert/update, including direct SQL changes; a default alone would not
track updates. Existing business fields are preserved. Initial timestamp values
represent migration initialization, not historical sale/change times.

The API models also require this migration. For an existing source, back up and
review it before the operator explicitly runs `python -m alembic upgrade head`.
The ETL never applies source migrations automatically. It reports
`source_tracking_migration_required` if the required columns/triggers are absent.
The isolated demo generator applies migrations only in its newly created database.

For a ready source, initialize/upgrade the TARGET explicitly:

```powershell
python -m etl_analytics.run --init-target
```

This creates analytics schema version 2, or upgrades version 1 atomically while
preserving old daily publications and run records. It also performs a dry run,
which does not initialize a checkpoint. Unsupported schema versions are rejected.

Publish twice to observe automatic mode selection:

```powershell
python -m etl_analytics.run --publish --reconcile
python -m etl_analytics.run --publish --reconcile
```

A checkpoint row exists only after successful loading. An empty successful full
load still initializes it; an empty fact table is not used to select the mode.
A failed first load is retried as full. To explicitly rebuild after a correction,
a missed change, or a source endpoint change:

```powershell
python -m etl_analytics.run --publish --full --reconcile
```

Default execution without `--publish` only previews/validates source rows. It
never records a run or advances the checkpoint. For the sales pipeline,
`--reconcile` requires publication because it compares the new target state with
the source snapshot. Target initialization is an explicit write even if no
publication was requested.

## Settings

- `ETL_SOURCE_URL` falls back to `DATABASE_URL`; `ETL_TARGET_URL` falls back to source.
- `ETL_BATCH_SIZE` defaults to 2000 (`--batch-size` overrides it).
- `ETL_LOOKBACK_SECONDS` defaults to 300 (`--lookback-seconds` overrides it).
- Both connections use PostgreSQL. Credentials stay in environment variables or
  private `.env` files; no JWT configuration is required for the ETL.
- Target checkpoints are bound to a hash of the configured source endpoint and
  database. Endpoint changes require explicit `--full`. This does not detect an
  unrelated database restored to the exact same endpoint/name.

## How incremental extraction works

Inside one read-only `REPEATABLE READ` source transaction:

1. Obtain the upper bound from the SOURCE database clock.
2. Read order IDs changed between `watermark - lookback` and that upper bound,
   using both `orders.updated_at` and `order_items.updated_at` indexes.
3. Fetch all items of those orders. An old order date/customer correction thus
   updates all its fact rows; an old sale arriving today is also captured.
4. Validate rows and stream them in bounded batches; do not load millions of
   Python dictionaries into memory.

Each batch is copied to temporary staging on the target and UPSERTed by business
key. Unchanged source fields/timestamps cause no fact UPDATE. A changed order
with several items can cause extra item reads, but unrelated historical orders
are not extracted. Incremental mode never deletes/reloads the complete fact table.
`--full` deliberately replaces it in one transaction.

Saved item prices are used, not current product prices. Quantities and prices
remain in the source's integer units without currency conversion. Order value
is not proof of payment collection or profit. Business `order_date` is preserved
without timezone conversion; tracking timestamps are timezone-aware.

## Transaction and run logging guarantees

Publishers acquire the PostgreSQL advisory lock before the source snapshot.
Only one cooperating daily/sales publisher can run at a time.

- A `running` record is committed independently before extraction starts.
- Facts, the checkpoint and the `complete` run update commit together.
- Failure rolls all publication writes back, then records `failed` in a separate
  transaction. The old checkpoint is kept and replay is safe.
- If a COMMIT acknowledgement is lost, inspect the durable run before recording
  failure; a committed success is never downgraded. An unavailable target can
  leave the outcome unknown, reported explicitly for recovery.
- Failed `rows_inserted`/`rows_updated` are zero (nothing committed); attempted
  counts are retained in `totals`. `rows_read`/`rows_rejected` describe observed
  input, not an exhaustive rejection count after fail-fast validation.
- Each started run records mode, time, duration, counters, before/after watermark,
  overlap and safe error code. Raw SQL errors and credentials are not recorded.
- Preflight failures before a run can be created are reported as JSON. If the
  target is unavailable while logging failure, JSON includes `run_log_saved=false`.
- A process killed before publication commits can leave `running` history; this
  version does not automatically mark abandoned attempts or run a heartbeat.

The fact row's `run_id` is its last material update run. The checkpoint's `run_id`
is the latest successful publication, including no-change runs; they need not match.

Useful queries:

```sql
SELECT mode,status,rows_read,rows_inserted,rows_updated,rows_unchanged,
       duration_seconds,watermark_before,watermark_after,error_code
FROM analytics.etl_runs WHERE pipeline_name='order_sales' ORDER BY started_at;
SELECT * FROM analytics.etl_checkpoints;
SELECT * FROM analytics.sales_daily_metrics ORDER BY order_day;
```

## Policy limits and recovery

- Hard deletes and primary-key changes are not tracked. `updated_at` disappears
  with a deleted row. Do not assume incremental mode propagates deletions.
- A timestamp is assigned before commit. A transaction can commit after the
  extraction snapshot with an older timestamp. The overlap catches such commits
  ONLY when their change timestamps remain inside the configured replay window.
  Choose a window covering expected transaction delays; zero overlap is for
  controlled experiments only, not a lossless general solution.
- The optional `--reconcile` scans source/target global order/item/unit/value totals
  in the extraction snapshot and rolls publication back on mismatch. It costs
  extra full scans and cannot detect offsetting errors or every dimension-only
  mismatch. Periodic explicit full rebuilds are the recovery/backstop here.
- Fully tracked deletes and unbounded late commits require a stronger design,
  such as soft deletes/change logs/CDC. They are not claimed by this version.
- Invalid dates, items and orders without items stop publication. Existing
  incomplete development data is not fabricated or silently excluded.

## Files and legacy daily pipeline

| File | Responsibility |
| --- | --- |
| `pipeline.py` | Mode selection, snapshot, atomic checkpoint/publication and failure lifecycle |
| `sales_extract.py` | Changed-order extraction, row validation and optional totals audit |
| `sales_load.py` | COPY staging, counts, UPSERT and checkpoint operations |
| `run_logging.py` | Durable attempt start and safe final outcome |
| `load.py`, `schema*.sql` | Explicit target bootstrap/version upgrade and shared publisher lock |
| `walkthrough.py` | Isolated, reproducible full/incremental/failure/retry example |
| `demo.py` | Create complete synthetic sources, up to one million orders |

The previous SQL daily full-refresh implementation is preserved in
`daily_pipeline.py` with `extract.py`, `transform.py` and `validate.py`:

```powershell
python -m etl_analytics.run --pipeline daily --publish --reconcile
```

It publishes `daily_order_metrics`/`etl_current_run`; it does not initialize or
advance the SALES checkpoint. Sales publishes facts and exposes
`sales_daily_metrics` as a view over them. Neither pipeline has a scheduler yet.

The Docker image includes both pipelines. Rebuild it after changing code.
Run tests with `TEST_POSTGRES_URL` pointing to an account with `CREATEDB`:

```powershell
python -m pytest -q
```

Tests use only uniquely named disposable PostgreSQL databases. Coverage includes
separate source/target loading, no-op replay, old updates/late arrivals, in-flight
commits, overlap limits, hard-delete detection/recovery, schema upgrades, durable
running/failure history, atomic checkpoints and retries. Generated evidence under
`output/` and `tmp/` remains local and is not current operational documentation.
