"""Require product price and stock without inventing values for existing rows."""
from alembic import op
import sqlalchemy as sa

revision = "0003_product_required"
down_revision = "c64c69d63974"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostgreSQL rejects existing NULLs; transactional DDL rolls back both changes.
    # Correct missing values explicitly before upgrading an existing database.
    op.alter_column("products", "unit_price", existing_type=sa.Integer(), nullable=False, schema="ecommerce")
    op.alter_column("products", "stock", existing_type=sa.Integer(), nullable=False, schema="ecommerce")


def downgrade() -> None:
    op.alter_column("products", "stock", existing_type=sa.Integer(), nullable=True, schema="ecommerce")
    op.alter_column("products", "unit_price", existing_type=sa.Integer(), nullable=True, schema="ecommerce")
