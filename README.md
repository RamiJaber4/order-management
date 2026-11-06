# Order Management API & Sales Analytics

[![Tests](https://github.com/RamiJaber4/order-management/actions/workflows/tests.yml/badge.svg)](https://github.com/RamiJaber4/order-management/actions/workflows/tests.yml)

A Python backend for managing customer orders and inventory, with a PostgreSQL
sales analytics pipeline. The project focuses on transaction correctness,
concurrent stock updates, access control, and recoverable data loading.

**Stack:** Python · FastAPI · PostgreSQL · SQLAlchemy 2 · Alembic · Pydantic ·
JWT / Argon2 · Docker Compose · pytest · GitHub Actions

## What it does

- **Authenticated ordering:** register and log in, browse products, place orders
  with multiple products, and retrieve order history. Customers access their own
  orders; admins can create products and view other customers' orders.
- **Atomic inventory updates:** lock product rows with `SELECT FOR UPDATE` in a
  consistent order, validate the whole basket, and commit orders, items, and stock
  together. A failure rolls back every change; concurrent requests cannot both
  purchase the final unit.
- **Incremental sales analytics:** start with a full load, then poll indexed
  change timestamps and replay an overlap window. Stream batches through
  PostgreSQL COPY staging and UPSERT facts by `(order_id, product_id)`.
- **Recoverable publication:** commit facts, the ETL checkpoint, and success
  status in one transaction. Keep failure history separately so a retry resumes
  from the last successful checkpoint.
- **Operational visibility:** JSON logs with request IDs, separate liveness and
  database readiness checks, bounded database waits, and Docker recovery checks.

## Architecture

```mermaid
flowchart LR
    Client[Client / Swagger UI] --> API[FastAPI routes and auth]
    API --> Services[Order and catalog services]
    Services --> DB[(PostgreSQL: ecommerce)]
    Alembic[Alembic migrations] --> DB
    DB --> ETL[Extract / validate / COPY / UPSERT]
    ETL --> Analytics[(PostgreSQL: analytics)]
    Analytics --> Metrics[Daily sales view and run history]
```

The API uses one SQLAlchemy session per request. Services own write transactions;
Alembic manages schema changes explicitly. ETL source and target connections can
point to the same database or separate PostgreSQL databases.

The operational schema contains customers, categories, products, orders, order
items, and payment records. Payment processing is outside the implemented API.

## Run locally

Prerequisites: Python 3.10+ and a running PostgreSQL server. CI and the Docker
image use Python 3.13; CI tests against PostgreSQL 16.

```bash
git clone https://github.com/RamiJaber4/order-management.git
cd order-management
python -m venv .venv
```

Activate the environment and copy the configuration template:

| Shell | Activate | Copy configuration |
| --- | --- | --- |
| PowerShell | `.\.venv\Scripts\Activate.ps1` | `Copy-Item .env.example .env` |
| bash / zsh | `source .venv/bin/activate` | `cp .env.example .env` |

```bash
python -m pip install -r requirements.txt
python -c "import secrets; print(secrets.token_hex(32))"
```

Create a new, empty database named `ecommerce_db` using your PostgreSQL client.
Edit your private `.env`:

- Set `DATABASE_URL` to
  `postgresql+psycopg://postgres:YOUR_PASSWORD@localhost:5432/ecommerce_db`, using
  your actual account, port, and database. URL-encode special characters in credentials.
- Set `JWT_SECRET` to the generated value (at least 32 bytes).

Initialize the empty database and start the API:

```bash
python -m alembic upgrade head
python -m uvicorn app.main:app --reload
```

Open **[Swagger UI](http://localhost:8000/docs)** or
[OpenAPI JSON](http://localhost:8000/openapi.json).
For an existing database, back it up and compare its schema with the migration
history before upgrading. `alembic stamp` only records a revision; it does not
validate or repair a schema. Revision `0006_order_idempotency` is required by the
current API and sales ETL. Application startup does not run migrations.

## Run with Docker

After configuring `.env` and installing Python dependencies, generate the private
Compose environment files (existing files are kept):

```bash
python scripts/setup_environments.py
```

The development API connects to your host PostgreSQL using
`host.docker.internal` (Docker Desktop). For a new database, apply migrations
before starting the API:

```bash
docker compose --profile tools run --rm --build migrate-development
# Continue after the migration succeeds:
docker compose up --build -d --wait api
```

Development Swagger runs at `http://localhost:8000/docs`. The optional test API
uses a separate Docker database and runs at `http://localhost:8001/docs`:

```bash
# Create the external test volume once on a new machine:
docker volume create backend-improve_postgres_data
docker compose --profile test up --build -d --wait api-test
```

Compose applies test migrations before starting `api-test`. `docker compose
--profile test stop` stops services and preserves data; the external test volume
also survives `down`. View development logs with `docker compose logs -f api`.
Database failures return readiness `503` while liveness stays `200`; readiness
recovers after PostgreSQL becomes available. For disposable verification without
private `.env` files or existing volumes, see [Testing](#testing).

## Try an order in Swagger

1. Register with `POST /auth/register`:

   ```json
   {"name": "Demo User", "email": "demo@example.com", "password": "demo-password-123"}
   ```

2. Log in with `POST /auth/login` using the same email and password. Copy the
   returned `access_token` into Swagger's **Authorize** dialog.
3. Promote this demo account from a trusted terminal, then create a product with
   `POST /products`:

   ```bash
   python manage.py promote-admin demo@example.com
   ```

   ```json
   {"name": "Keyboard", "unit_price": 40, "stock": 5}
   ```

4. Use `GET /products` to find its ID, then send `POST /orders` with that ID:

   ```json
   {"items": [{"product_id": 1, "quantity": 2}]}
   ```

5. Retrieve the returned `order_id` with `GET /orders/{id}`. Check `GET /products`
   again to see the reduced stock. Requesting more than the remaining stock
   returns `409` and leaves the database unchanged.

Ownership comes from the verified token; callers cannot submit a `customer_id`
or their own item prices. New accounts default to the customer role; admin
promotion is a separate operator command.

For safe retries, send an `Idempotency-Key` header with `POST /orders` (for
example, a UUID generated once per checkout). Reuse that key after a timeout or
lost response. Keys accept 1-255 visible ASCII characters without spaces and are
scoped to the authenticated customer. The same key and basket return the original
`201` response and `order_id`, even if stock or prices have since changed. Item
order does not matter. Reusing the key with different products or quantities
returns `409`. Concurrent retries create one order and deduct stock once.

The key is committed with the order and has no time-based expiry; it remains
reserved while the order exists. Failed requests roll back the key so they can be
retried. Requests without the header retain their existing behavior and create a
new order on each successful call. Use a new key for a new checkout.

## API reference

| Method | Endpoint | Access / purpose |
| --- | --- | --- |
| POST | `/auth/register` | Public; create a customer account |
| POST | `/auth/login` | Public; issue a bearer access token |
| GET | `/auth/me` | Authenticated account |
| GET | `/products` | Public; paginated catalog (`limit`, `offset`) |
| POST | `/products` | Admin; create a product |
| POST | `/orders` | Authenticated; place an order for the current customer |
| GET | `/orders/{id}` | Order owner or admin |
| GET | `/customers/{id}/orders` | Matching customer or admin; paginated history |
| GET | `/health/live` | Process liveness, without a database query |
| GET | `/health/ready` | Database readiness; returns `503` during an outage |

Passwords are hashed with Argon2. Access tokens use HS256 and expire after
15 minutes. Roles are checked against the database on each protected request.
Response models exclude password hashes. Responses include `X-Request-ID` for
correlation with JSON logs.

## Try the analytics pipeline

With dependencies installed and `DATABASE_URL` configured for a PostgreSQL
account with `CREATEDB`, run:

```bash
python -m etl_analytics.walkthrough
```

This creates a new database containing synthetic data and demonstrates a full
load, an unchanged incremental replay, new and corrected items, an injected
failure with rollback, and a successful retry. It prints JSON reports and retains
the new demo database for inspection.

For a migrated source database, preview and publish explicitly:

```bash
python -m etl_analytics.run                 # Read-only preview
python -m etl_analytics.run --init-target   # Explicit target schema initialization
python -m etl_analytics.run --publish --reconcile
python -m etl_analytics.run --publish --reconcile  # Incremental after first success
```

`analytics.fact_order_sales` stores one row per order item;
`analytics.sales_daily_metrics` groups facts by order date. Run history records
mode, counts, duration, watermarks, and safe failure codes. Item values use the
saved sale price and integer source units; they do not represent collected
payments or profit.

See the [ETL guide](etl_analytics/README.md) for connection settings, schema
upgrades, reconciliation, full rebuilds, and the overlap policy, or the
[Arabic walkthrough](etl_analytics/LEARNING_AR.md) for a code walkthrough.

## Testing

The suite uses real PostgreSQL databases with unique names, applies Alembic
migrations, and removes its test databases afterward.

To run the suite and recovery checks in a disposable Docker environment, install
Docker with Compose support and run from the repository root:

```bash
python scripts/check_docker_reliability.py
```

This builds the image, runs pytest, verifies behavior during a real PostgreSQL
outage, checks recovery after the API process exits, and checks data persistence
after container recreation. It cleans up its own temporary containers and volume.
It does not require application credentials or use existing application volumes.

To run pytest against your own dedicated test server:

```bash
python -m pip install -r requirements-test.txt
```

Set `TEST_POSTGRES_URL` to an account with `CREATEDB` permission:

```powershell
# PowerShell
$env:TEST_POSTGRES_URL = 'postgresql+psycopg://postgres:YOUR_PASSWORD@localhost:5432/postgres'
```

```bash
# bash / zsh
export TEST_POSTGRES_URL='postgresql+psycopg://postgres:YOUR_PASSWORD@localhost:5432/postgres'
```

```bash
python -m pytest -q
```

Tests cover authentication and authorization, validation and response privacy,
concurrent ordering and rollback, migration round trips, structured logging and
database failures, plus incremental ETL, overlap replay, atomic checkpoints,
separate source/target databases, and schema upgrades.
[GitHub Actions](https://github.com/RamiJaber4/order-management/actions/workflows/tests.yml)
runs the suite on pushes and pull requests using PostgreSQL 16.

## Repository guide

| Path | Purpose |
| --- | --- |
| [`app/`](app/) | Routes, authentication, database sessions, models, and services |
| [`alembic/versions/`](alembic/versions/) | Versioned operational schema migrations |
| [`etl_analytics/`](etl_analytics/) | Extraction, validation, staging, publication, and demos |
| [`tests/`](tests/) | PostgreSQL integration tests and disposable Compose environment |
| [`scripts/`](scripts/) | Database audit, backup, environment, and recovery tools |

## Scope and tradeoffs

- The project provides a backend API and CLI analytics pipeline. It has no
  frontend, payment gateway, or automatic ETL scheduler.
- JWT refresh, server-side token revocation, and password recovery are not
  implemented. A public deployment needs HTTPS and authentication rate limiting.
- Incremental ETL uses timestamp polling with a configurable overlap (300 seconds
  by default). Hard deletes, key changes, and commits delayed beyond that window
  require reconciliation and an explicit full rebuild; lossless CDC is outside
  the current design.
- Global reconciliation checks totals and cannot prove every field matches.
  Prices retain the source's integer representation; currency conversion is
  outside the pipeline.
- Private `.env` files, database backups, and generated output are excluded from
  Git. Example credentials in disposable test configuration are for local tests.
