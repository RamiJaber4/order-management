# ETL test location

The implemented tests live in [`../../tests/test_etl.py`](../../tests/test_etl.py).
They share the repository's PostgreSQL fixtures, which create a uniquely named
database and remove only that test database afterward.

Run from the repository root with `TEST_POSTGRES_URL` configured:

```powershell
python -m pytest -q tests/test_etl.py
```

See [`../README.md`](../README.md) for current pipeline behavior and limitations.
