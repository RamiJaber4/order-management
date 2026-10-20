"""Initial ecommerce schema, before orders.notes."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA ecommerce")
    for table in ("customers", "products", "orders", "payment"):
        op.execute(f"CREATE SEQUENCE ecommerce.{table}_id_seq")
    op.execute("CREATE TYPE ecommerce.payment_way AS ENUM ('cash', 'visa', 'on_deliver')")
    op.create_table(
        "categories",
        sa.Column("name", sa.String(255), primary_key=True),
        schema="ecommerce",
    )
    op.create_table(
        "customers",
        sa.Column("id", sa.Integer(), server_default=sa.text("nextval('ecommerce.customers_id_seq'::regclass)"), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.UniqueConstraint("email", name="customers_email_key"),
        schema="ecommerce",
    )
    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), server_default=sa.text("nextval('ecommerce.products_id_seq'::regclass)"), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("category_name", sa.String(255), sa.ForeignKey("ecommerce.categories.name")),
        sa.Column("unit_price", sa.Integer()),
        sa.Column("stock", sa.Integer()),
        sa.CheckConstraint("stock >= 0", name="products_stock_check"),
        sa.CheckConstraint("unit_price > 0", name="products_unit_price_check"),
        schema="ecommerce",
    )
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), server_default=sa.text("nextval('ecommerce.orders_id_seq'::regclass)"), primary_key=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("ecommerce.customers.id")),
        sa.Column("order_date", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        schema="ecommerce",
    )
    op.create_index("idx_orders_customer_id", "orders", ["customer_id"], schema="ecommerce")
    op.create_table(
        "order_items",
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("ecommerce.orders.id"), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("ecommerce.products.id"), primary_key=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("price", sa.Integer(), nullable=False),
        sa.Column("total_price", sa.Integer(), sa.Computed("COALESCE(price, 0) * COALESCE(quantity, 0)", persisted=True)),
        sa.CheckConstraint("price >= 0", name="order_items_price_check"),
        sa.CheckConstraint("quantity > 0", name="order_items_quantity_check"),
        schema="ecommerce",
    )
    op.create_table(
        "payment",
        sa.Column("id", sa.Integer(), server_default=sa.text("nextval('ecommerce.payment_id_seq'::regclass)"), primary_key=True),
        sa.Column("way", postgresql.ENUM("cash", "visa", "on_deliver", name="payment_way", schema="ecommerce", create_type=False), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("discount", sa.Numeric()),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("ecommerce.orders.id")),
        sa.UniqueConstraint("order_id", name="payment_order_id_key"),
        schema="ecommerce",
    )
    for table in ("customers", "products", "orders", "payment"):
        op.execute(f"ALTER SEQUENCE ecommerce.{table}_id_seq OWNED BY ecommerce.{table}.id")


def downgrade() -> None:
    for table in ("payment", "order_items", "orders", "products", "customers", "categories"):
        op.drop_table(table, schema="ecommerce")
    # Owned sequences disappear with their tables. No CASCADE: preserve unrelated objects.
    op.execute("DROP TYPE ecommerce.payment_way")
    op.execute("DROP SCHEMA ecommerce")
