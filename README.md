# Order Management

FastAPI order management with PostgreSQL, JWT authentication, transactional stock
updates and a full-refresh daily order analytics pipeline.

## Project layout

```text
app/
  main.py             # FastAPI entry point and middleware
  api/routes.py       # HTTP endpoints and dependencies
  core/               # Authentication, logging and database settings
  db/                 # SQLAlchemy base, engine and session lifecycle
  models.py           # SQLAlchemy table mappings
  schemas.py          # Pydantic request and response models
  services/           # Customer, product and order operations
alembic/              # Database migration history
etl_analytics/        # Daily SQL aggregation, validation and atomic publication
tests/                # Automated tests and disposable Docker environment
scripts/              # Environment, backup and verification tools
manage.py             # Administrative CLI
```

Run commands from the repository root. Application imports use the `app` package.
The API entry point is `app.main:app`; update external launch configurations
that previously used `main:app`. Docker uses the new entry point automatically.
Keep runtime configuration, Docker files and dependency manifests at the root.
Local `.env` files, backups and generated caches are ignored by Git.

## Run with Docker

The local development and test environments are explicitly separated:

| Environment | Swagger | Database | Docker service |
| --- | --- | --- | --- |
| Development | http://localhost:8000/docs | Existing host PostgreSQL configured in `.env` | `api` |
| Test | http://localhost:8001/docs | Existing Docker PostgreSQL volume | `api-test` |

Install Docker with Compose support (Docker Desktop on Windows, using Linux
containers). Keep `.env` pointing to the original local development database.
If setting up from scratch, generate a JWT signing secret with:

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

Save the output as `JWT_SECRET=<generated value>` in `.env`. Create the private
Docker environment files once (existing files are never overwritten):

```powershell
.\.venv\Scripts\python scripts/setup_environments.py
```

Start development from the project root (host PostgreSQL must be running):

```powershell
docker compose up --build -d --wait
```

Development reads `.env.development`: its database hostname is
`host.docker.internal`, which reaches the Windows host from the container.
Local Python/Alembic commands still use the host and port in `.env`.
These are two paths to the same database. Development does **not** run migrations
automatically. Back up, inspect data compatibility, then run migrations explicitly.

Start both development and test:

```powershell
docker compose --profile test up --build -d --wait
```

Test reads `.env.test` and uses its own JWT secret and database. The separate
`migrate-test` deployment service must finish successfully before `api-test` starts;
the API process itself never runs migrations. PostgreSQL has no published host port. The existing external volume
`backend-improve_postgres_data` is retained; a fresh machine must create that empty
volume using `docker volume create backend-improve_postgres_data` first.
The bundled database password is only for this local test setup.

Check identity via `/health/ready` and the Swagger title. `/health` remains an
alias for readiness. Both APIs are loopback-only.
Normal commands:

```powershell
docker compose ps
docker compose logs -f api
docker compose --profile test stop
# Start again, reusing the saved database:
docker compose up -d --wait
```

`stop` preserves containers and data. `build` builds application images;
`up --build` also recreates the running application when needed. The external
database volume is not part of the image and is not removed by Compose `down`.
Do not delete the volume in Docker Desktop. The original host database is separate.
`.dockerignore` excludes local secrets, virtual environments, and caches from
the build context. The API image runs as an unprivileged user.

## Database audit and daily ETL

Before migrations or ETL publication, verify the configured source database:

```powershell
.\.venv\Scripts\python scripts/audit_database.py
```

The audit uses a read-only repeatable-read transaction and prints the database
name/port, table counts, migration revision, missing order items/dates, invalid
values, broken references, ID-sequence checks and incomplete legacy analytics
publications. It reports legacy accounts
without credentials as a warning. It prints no customer records or credentials
and does not repair data. Exit codes are 0 (checks passed), 1 (issues found), or
2 (audit unavailable). Passing these checks does not replace the migration/model
integration tests or establish the business meaning of legacy data.

Verify the database identity as well as the port: another PostgreSQL instance
may belong to a different project. A connection timeout is not evidence of data
corruption. Do not create an empty replacement or point migrations at another
database simply to make the connection succeed.

The ETL aggregates item quantities and recorded order value by day in SQL,
validates the results in Python and replaces the analytics results atomically.
Its source and target URLs can be configured separately. Start with a dry run:

```powershell
python -m etl_analytics.run --reconcile
```

See [the ETL guide](etl_analytics/README.md) for explicit target initialization,
publication, isolated demo data, metric definitions and limitations. Generated
run evidence, PDFs and temporary files stay local under ignored `output/` and
`tmp/` directories. Application data and backups are not part of the repository.

## Health, timeouts and recovery

`GET /health/live` returns 200 when the application can handle a request; it does
not access PostgreSQL. `GET /health/ready` checks PostgreSQL with `SELECT 1`.
Unavailable connections, exhausted connection pools and database timeouts return
503 with `{"detail":"Service temporarily unavailable"}`. Unexpected application
errors return 500 with `{"detail":"Internal server error"}`. Both responses include
`X-Request-ID`, as do successful responses. Internal exception details and arbitrary
server-error headers are never forwarded to clients. Data conflicts such as an
existing registration email remain 409; database constraint/programming errors
are not automatically treated as temporary outages.

The connection pool checks stale connections before reuse. Once PostgreSQL is
available again, subsequent readiness requests can return 200 without restarting
the API. Compose uses readiness for health checks. An unhealthy status alone does
not restart a container; `restart: unless-stopped` restarts an exited service and
permits restart after Docker restarts, unless the operator explicitly stopped it.

Defaults can be overridden in the appropriate private environment file:

| Variable | Default | Purpose |
| --- | --- | --- |
| `DB_CONNECT_TIMEOUT_SECONDS` | 3 | Database connection attempt |
| `DB_POOL_TIMEOUT_SECONDS` | 3 | Wait for a pooled connection |
| `DB_STATEMENT_TIMEOUT_MS` | 5000 | Each SQL statement |
| `DB_LOCK_TIMEOUT_MS` | 2000 | Each database lock wait |
| `DB_IDLE_TRANSACTION_TIMEOUT_MS` | 10000 | Idle transaction |
| `MIGRATION_STATEMENT_TIMEOUT_MS` | 120000 | Migration statement |
| `MIGRATION_LOCK_TIMEOUT_MS` | 10000 | Migration lock wait |
| `MIGRATION_IDLE_TRANSACTION_TIMEOUT_MS` | 60000 | Idle migration transaction |

All values must be positive integers. These bound individual database waits, not
the total duration of a request containing several operations. Migrations use
longer limits because DDL can take longer. Uvicorn also limits idle keep-alive
connections to 5 seconds and graceful shutdown to 20 seconds. Keep-alive is not
a request execution timeout. Docker readiness probes wait up to 12 seconds and
have a 15-second process timeout with these defaults; review probe limits if
increasing database waits.

## Migration deployment steps

For development, back up and check compatibility with the existing database as
described below before explicitly deploying migrations:

```powershell
docker compose --profile tools run --rm --build migrate-development
# Continue only if the migration command succeeds:
docker compose up --build -d --wait api
```

For a test deployment, apply migrations as a separate, visible step before
recreating the API. A failure must stop deployment; inspect the migration logs.

```powershell
docker compose --profile test up -d --wait db
docker compose --profile test run --rm --build migrate-test
# Continue only if the migration command succeeds:
docker compose --profile test up --build -d --wait api-test
```

The convenience `docker compose --profile test up --build -d --wait` also orders
the one-shot `migrate-test` service before the test API. A normal API restart
does not run migrations. Do not start multiple migration jobs against one database.

## Troubleshooting

- **Readiness is 503, liveness is 200:** the process is alive but PostgreSQL is
  unavailable or timed out. Check the host PostgreSQL service on port 5433 for
  development, or `docker compose --profile test ps` and
  `docker compose --profile test logs --tail 100 db` for test. Check the relevant
  private environment file locally; do not paste credentials into logs or tickets.
- **Readiness is 404:** rebuild the API image; older images lack the new routes.
- **API fails to start after migration failure:** inspect
  `docker compose --profile test logs --tail 100 migrate-test`, fix the cause,
  rerun the migration step, then start the API. Never stamp a revision merely to
  bypass a failed migration.
- **Request is 500 or 503:** keep its `X-Request-ID` and find the matching JSON
  events in the API logs. `reason`, `description`, exception type and safe stack
  locations describe the issue. Pool exhaustion, lock/statement timeouts and
  database availability need different remedies; do not blindly increase limits.
- **Container exited:** check its logs and exit state. An explicitly stopped
  service needs `docker compose start` or `up`; `unless-stopped` respects that stop.

Run a complete disposable verification (requires Docker and dependency downloads):

```powershell
.\.venv\Scripts\python scripts/check_docker_reliability.py
# If pytest already passed, rerun only the Docker recovery checks:
.\.venv\Scripts\python scripts/check_docker_reliability.py --skip-pytest
```

This creates a unique Compose project, runs the full pytest suite, stops and starts
its own PostgreSQL, verifies live=200/ready=503 and recovery=200, restarts containers,
checks automatic recovery after the API process exits, and recreates its database
container to check volume persistence. Its temporary
containers and volume are removed afterward. Existing environments, private env
files and the external application volume are not used.

## Application logs

The API writes JSON logs to stdout, collected by Docker. Compose rotates logs at
10 MB and retains up to three files per container. Each entry includes a
UTC timestamp, severity, logger and environment. Requests include a generated
request ID, route template, status and duration in milliseconds. The ID is also
returned in the `X-Request-ID` response header for responses sent by the API.
Unknown URLs are logged as `<unmatched>`; query strings, request bodies, headers,
emails, passwords and tokens are not recorded. Unexpected errors include the
exception type and stack locations without exception text, source lines or SQL
parameters. Successful writes are logged only after commit.

Rejected requests also emit `request_rejected` with a fixed `reason` and readable
`description`: insufficient stock, invalid product price, missing resources,
duplicate registration, missing/invalid/expired bearer credentials and permission
failures. Client authentication responses remain generic. Validation failures
include up to 20 issues with approved field names and error types plus the total
error count; submitted values, unknown field names, validator messages and context
are omitted. These events share the request ID with the completion log.

Set `LOG_LEVEL=DEBUG|INFO|WARNING|ERROR|CRITICAL` in `.env.development` or
`.env.test` (default `INFO`), then recreate the service with `docker compose up
-d` (include `--profile test` for test). All HTTP requests, including health checks,
emit completion logs at `INFO` or above with status and elapsed time. Uvicorn
access logs are replaced by the request middleware.
Use module loggers (`logging.getLogger(__name__)`) and fixed event messages;
never interpolate user input or secrets into log messages.

```powershell
docker compose logs -f --tail 100 api
docker compose --profile test logs -f --tail 100 api-test
```

## Continuous integration

`.github/workflows/tests.yml` runs on pushes, pull requests, and manual dispatch.
It installs Python 3.13 and the test dependencies, starts a disposable PostgreSQL
16 service, waits for database readiness, and runs `python -m pytest -q`.
The tests run Alembic migrations in randomly named test databases. No production
database credentials, repository secrets, or committed `.env` file are needed.
After pushing the workflow, open the repository's **Actions** tab and select
**Tests** to see results. Manual **Run workflow** is available once the workflow
is on the default branch. This workflow tests code; it does not deploy it.

This project manages six PostgreSQL tables using SQLAlchemy 2.x and Alembic.
`app/models.py` contains the mappings; `app/db/base.py` defines the shared ORM base.
`app/schemas.py` contains Pydantic request and response models. Routes declare
`response_model` to validate and expose only the documented response fields.
ORM-backed responses use `from_attributes=True`. Order responses include their
items; customer/payment/product ORM relationships are not exposed.
`app/db/session.py` provides the engine, session factory, and a FastAPI session dependency.

## Setup

Use Python 3.10 or newer. In PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` with your PostgreSQL username, password, host, port, and database.
The driver is **psycopg 3**, so the URL starts with `postgresql+psycopg://`.
URL-encode special characters in credentials. Never commit `.env`.

For a new installation, create an empty PostgreSQL database first, point
`DATABASE_URL` to it, then run:

```powershell
.\.venv\Scripts\python -m alembic upgrade head
```

Alembic creates the `ecommerce` schema, enum, sequences, tables, constraints,
indexes, adds `orders.notes`, and makes product price and stock required.
Neither application startup nor model
imports change the database. Do not run this initialization against existing
untracked tables; follow the existing-database instructions below.

The connection searches `ecommerce`, then `public`, and table names in the
models explicitly use the `ecommerce` schema.

## Existing databases and future migrations

Back up an existing database and verify its structure against the migration
files before adopting Alembic. `stamp` only records a revision; it does not
validate, create, or repair tables, sequences, enum labels, or constraints.
These commands are conditional alternatives, not steps to run together:

- If all initial objects exist and match `0001_initial`, but `orders.notes` is
  absent, run `python -m alembic stamp 0001_initial`, then
  `python -m alembic upgrade head`.
- If the schema matches the notes revision, including `orders.notes` but still
  allowing NULL product price/stock, run
  `python -m alembic stamp c64c69d63974`, then `python -m alembic upgrade head`.
- If the schema matches the latest revision including NOT NULL product
  price/stock, run `python -m alembic stamp 0003_product_required`.
- If `alembic current` already reports `c64c69d63974`, do not stamp or recreate
  anything. Its revision ID remains valid with the added initial ancestor;
  run `python -m alembic upgrade head` for subsequent changes.
- If the structure differs, reconcile those differences explicitly first.
  A legacy `customers.phone` column may remain; the notes migration no longer
  removes it. Future autogenerate output may propose dropping it: review and
  remove unwanted destructive operations before applying a migration.

For future model changes:

```powershell
.\.venv\Scripts\python -m alembic revision --autogenerate -m "describe the change"
# Review the generated upgrade AND downgrade before executing:
.\.venv\Scripts\python -m alembic upgrade head
.\.venv\Scripts\python -m alembic check
```

Before applying `0003_product_required` to existing data, inspect products where
`unit_price IS NULL OR stock IS NULL` and supply their correct values. The
migration deliberately fails and rolls back if any missing values remain;
it does not guess prices, reset stock, or delete products.

`POST /products` requires `name`, integer `unit_price > 0`, and integer
`stock >= 0`. For example: `{"name": "Keyboard", "unit_price": 40, "stock": 5}`.
Missing/NULL/invalid price or stock returns 422; zero stock is allowed.
Duplicate customer email returns 409. Product pagination accepts `offset` and
orders rows by product ID.

Autogenerate is a proposal, not a complete schema validator (for example, enum
labels and sequence changes need manual review). Migrations are frozen schema
definitions and do not import live application models. Downgrading to `base`
deletes the application's tables and their data; use it only on disposable
databases or as part of an explicitly planned recovery.

## Reading the models

- `Mapped[str]` describes the Python value and implies `NOT NULL`.
- `Mapped[str | None]` allows SQL `NULL`.
- `mapped_column(...)` declares the actual database column.
- `ForeignKey(...)` declares a database reference.
- `relationship(...)` exposes related objects in Python; it adds no columns.
- `server_default` lets PostgreSQL supply IDs and timestamps.
- `Computed(...)` marks `order_items.total_price` as generated by PostgreSQL.

| Model | Table | Related attributes |
| --- | --- | --- |
| `Category` | `ecommerce.categories` | `products` |
| `Customer` | `ecommerce.customers` | `orders` |
| `Product` | `ecommerce.products` | `category`, `order_items` |
| `Order` | `ecommerce.orders` | `customer`, `items`, `payment` |
| `OrderItem` | `ecommerce.order_items` | `order`, `product` |
| `Payment` | `ecommerce.payment` | `order` |

`OrderItem` uses `(order_id, product_id)` as its composite primary key.
`Order.payment` is optional and scalar because `payment.order_id` is unique.
Prices stay integers, and `discount` uses Python `Decimal` to match `numeric`.
The database permits NULL in several columns even when a CHECK constraint exists;
the models preserve that behavior.

The initial migration creates `payment_way` with `cash`, `visa`, and
`on_deliver`, matching the model. PostgreSQL validates these values. For an
existing database, verify its enum labels before stamping a revision.

Relationships use `passive_deletes="all"` on parent collections so deleting a
parent leaves enforcement to the existing foreign keys. No delete cascades are
added. Related objects load on access while the session is open.

## Using a session

For FastAPI, import `Session` from `sqlalchemy.orm`, `Depends` from `fastapi`,
and `get_db` from `app.db.session`. Add `db: Session = Depends(get_db)` to your route.
Import ORM classes from `app.models` in your application code.

`get_db()` supplies one session and closes it after the request. Your route or
service explicitly calls `db.commit()` when a unit of work succeeds. An escaping
exception causes rollback; closing also releases the connection and discards
uncommitted work. `autoflush=False` means queries do not automatically flush
pending changes; call `db.flush()` if you need generated IDs before committing.

For scripts, use `with SessionLocal() as db:` and explicitly commit writes.

ORM models describe database rows. `CustomerCreate`, `ProductCreate`, and
`OrderCreate` live in `app/schemas.py` as Pydantic request models.
`app/api/routes.py` exposes registration/login, products, and orders, using one database session per request.
The create helpers convert request models into ORM instances before saving.

Start the API with `.\.venv\Scripts\python -m uvicorn app.main:app --reload`.

## Order atomicity and integration tests

`POST /orders` accepts one or more distinct products using the following shape
(requires a bearer token; the owner comes from the authenticated account):

```json
{
  "items": [
    {"product_id": 3, "quantity": 2},
    {"product_id": 7, "quantity": 1}
  ]
}
```

Empty baskets, duplicate products, and invalid IDs/quantities return 422.
A missing product returns 404; insufficient stock returns 409. Either rejects
the entire order without saving items or deducting any stock. All product rows
are locked in ascending ID order, regardless of their order in the request.
Prices are read from the database, not supplied by the caller. GET order
responses include each item's product ID, quantity, price, and computed total.

Order routes delegate database operations to `app/services/orders.py`. The order
service owns one transaction for customer/product validation, stock deduction,
order creation, and item creation. It locks the product with `SELECT FOR UPDATE`
until commit, so concurrent orders cannot consume the same stock. A missing product returns 404; insufficient stock returns 409. Any failure rolls back the complete transaction.

Run the API integration tests against a PostgreSQL test server:

```powershell
.\.venv\Scripts\python -m pip install -r requirements-test.txt
$env:TEST_POSTGRES_URL = 'postgresql+psycopg://postgres:YOUR_PASSWORD@localhost:5432/postgres'
.\.venv\Scripts\python -m pytest -q
```

The supplied account needs `CREATEDB` permission. Tests create a separate database
named `ecommerce_test_<random UUID>`, run `alembic upgrade head`, and drop that
database afterward. They do not modify tables in the database named in the URL.
Use a dedicated test server/account. Missing configuration fails explicitly;
tests never fall back to the application database or SQLite.

Coverage includes successful commit, rejected client-supplied ownership, missing products, insufficient and
invalid quantities, concurrent requests for the final
unit, and a PostgreSQL constraint failure when inserting the item after the
order and stock have been flushed. Persisted state is checked from fresh
sessions, including rollback and a successful request following that failure.
Migration tests also check model/schema agreement, downgrade to an empty
database and upgrade again, and preservation of existing customer/phone data
when adding and removing `orders.notes`.
They also verify NOT NULL enforcement, safe rollback when legacy products
contain missing values, product input validation and ordering newly created
products, duplicate email handling, and product pagination.


## JWT authentication and permissions

`POST /auth/register` replaces the old unauthenticated `POST /customers` route.
Send `name`, `email`, and a password of 4 to 128 characters. Registration returns
201, duplicate email returns 409, and unsupported fields (including `role`) return
422. Email validation normalizes the domain; the local part remains case-sensitive,
including for existing customers. No bulk email rewriting is performed.

```json
{"name": "Rami", "email": "rami@example.com", "password": "my-long-password-123"}
```

`POST /auth/login` accepts JSON with `email` and `password`; it returns
`access_token` and `token_type: "bearer"`. Invalid credentials return a generic
401. Paste the token into **Authorize** in `/docs`, or send this header:

```http
Authorization: Bearer <access_token>
```

`GET /auth/me` returns only the current account's id, name, email, and role.
Passwords are hashed with Argon2; hashes are never returned. JWTs contain only
`sub` (customer ID), `iat`, and `exp`, and expire after 15 minutes. The signing
algorithm is fixed to HS256 and the secret must be at least 32 bytes. Generate
it randomly; there is no default key. Tests inject a separate test-only key.

| Operation | Access |
| --- | --- |
| Register/login | Public |
| GET /products | Public |
| POST /products | Admin |
| POST /orders | Logged-in customer, including admins; always owns the new order |
| GET /orders/{id} | Owner or admin |
| GET /customers/{id}/orders | Matching customer or admin |
| GET /auth/me | Logged-in customer |

`OrderCreate` no longer accepts `customer_id`; sending it returns 422. Customer
ownership is taken from the verified account. Roles are loaded from the database
on every protected request, so promotion/demotion applies to the next request
without issuing another token. Existing customers retain their data and receive
role `customer`, but their password hash remains NULL: they cannot log in until
credentials are provisioned through a trusted process. Password recovery and
legacy account activation are not implemented.

To grant the first admin role, register that account normally, then use the
trusted operator command (there is no public role-change endpoint):

```powershell
# Docker:
docker compose exec api python manage.py promote-admin rami@example.com
# Local environment:
.\.venv\Scripts\python manage.py promote-admin rami@example.com
```

To protect a new route, add `customer: Customer = Depends(get_current_customer)`.
For admin-only routes use `admin: Customer = Depends(require_admin)` instead.
For customer-owned data also call `require_customer_access(customer, owner_id)`
before returning or changing it. Missing/invalid tokens return 401; insufficient
permissions return 403. Authentication reads and order writes share the same
request transaction; the order service commits or rolls back all stock/item changes.

This is the access-token foundation: there are no refresh tokens or server-side
logout/revocation yet. Client logout discards its token; a copied token remains
valid until expiry. Before exposing the service publicly, configure HTTPS and
login/registration rate limiting at the gateway or application layer. Never log
passwords or bearer tokens. Token persistence in a browser must be designed with
the frontend; no browser storage mechanism is selected here.

The test suite checks real signed tokens, registration/hash storage, generic
login failures, legacy accounts, token expiry/tampering, ownership, role changes,
response privacy, and the existing transaction/concurrency/migration behavior.
