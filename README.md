# Order Management

## Run with Docker

The local development and test environments are explicitly separated:

| Environment | Swagger | Database | Docker service |
| --- | --- | --- | --- |
| Development | http://localhost:8000/docs | Existing host PostgreSQL at port 5433 | `api` |
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
Local Python/Alembic commands still use `.env` and `127.0.0.1:5433`.
These are two paths to the same database. Development does **not** run migrations
automatically. Back up, inspect data compatibility, then run migrations explicitly.

Start both development and test:

```powershell
docker compose --profile test up --build -d --wait
```

Test reads `.env.test`, uses its own JWT secret and database, and applies migrations
at startup. PostgreSQL has no published host port. The existing external volume
`backend-improve_postgres_data` is retained; a fresh machine must create that empty
volume using `docker volume create backend-improve_postgres_data` first.
The bundled database password is only for this local test setup.

Check identity via `/health` and the Swagger title. Both APIs are loopback-only.
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
`models.py` contains the mappings; `base.py` defines the shared ORM base.
`schemas.py` contains Pydantic request and response models. Routes declare
`response_model` to validate and expose only the documented response fields.
ORM-backed responses use `from_attributes=True`. Order responses include their
items; customer/payment/product ORM relationships are not exposed.
`db.py` provides the engine, session factory, and a FastAPI session dependency.

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
and `get_db` from `db`. Add `db: Session = Depends(get_db)` to your route.
Import ORM classes from `models` in your application code.

`get_db()` supplies one session and closes it after the request. Your route or
service explicitly calls `db.commit()` when a unit of work succeeds. An escaping
exception causes rollback; closing also releases the connection and discards
uncommitted work. `autoflush=False` means queries do not automatically flush
pending changes; call `db.flush()` if you need generated IDs before committing.

For scripts, use `with SessionLocal() as db:` and explicitly commit writes.

ORM models describe database rows. `CustomerCreate`, `ProductCreate`, and
`OrderCreate` live in `schemas.py` as Pydantic request models.
`main.py` exposes registration/login, products, and orders, using one database session per request.
The create helpers convert request models into ORM instances before saving.

Start the API with `.\.venv\Scripts\python -m uvicorn main:app --reload`.

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

Order routes delegate database operations to `db.py`. The order
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
