# ETL test location

The implemented tests live in:

- [`../../tests/test_incremental_etl.py`](../../tests/test_incremental_etl.py):
  full/incremental sales facts, checkpoints, overlap, failure recovery, and schema upgrades.
- [`../../tests/test_etl.py`](../../tests/test_etl.py): legacy daily aggregation,
  validation, reconciliation, and atomic publication.

They share the repository's PostgreSQL fixtures, which create uniquely named
databases and remove only those test databases afterward.

Run from the repository root with `TEST_POSTGRES_URL` configured:

```powershell
python -m pytest -q tests/test_etl.py tests/test_incremental_etl.py
```

See [`../README.md`](../README.md) for current pipeline behavior and limitations.
