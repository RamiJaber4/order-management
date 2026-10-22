"""Add customer credentials and roles without inventing legacy passwords."""
from alembic import op
import sqlalchemy as sa

revision = "0004_customer_auth"
down_revision = "0003_product_required"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("customers", sa.Column("password_hash", sa.Text(), nullable=True), schema="ecommerce")
    op.add_column("customers", sa.Column("role", sa.String(20), nullable=False,
                                       server_default=sa.text("'customer'")), schema="ecommerce")
    op.create_check_constraint("customers_role_check", "customers",
                               "role IN ('customer', 'admin')", schema="ecommerce")


def downgrade() -> None:
    op.drop_constraint("customers_role_check", "customers", type_="check", schema="ecommerce")
    op.drop_column("customers", "role", schema="ecommerce")
    op.drop_column("customers", "password_hash", schema="ecommerce")
