"""Persist customer-scoped order retry keys atomically with orders."""
from alembic import op
import sqlalchemy as sa

revision = '0006_order_idempotency'
down_revision = '0005_order_tracking'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('orders', sa.Column('idempotency_key', sa.String(255), nullable=True), schema='ecommerce')
    op.add_column('orders', sa.Column('request_fingerprint', sa.String(64), nullable=True), schema='ecommerce')
    op.create_unique_constraint('orders_customer_idempotency_key', 'orders',
                                ['customer_id', 'idempotency_key'], schema='ecommerce')
    op.create_check_constraint(
        'orders_idempotency_pair_check', 'orders',
        '(idempotency_key IS NULL AND request_fingerprint IS NULL) OR '
        '(idempotency_key IS NOT NULL AND request_fingerprint IS NOT NULL)',
        schema='ecommerce',
    )


def downgrade():
    op.drop_constraint('orders_idempotency_pair_check', 'orders', schema='ecommerce', type_='check')
    op.drop_constraint('orders_customer_idempotency_key', 'orders', schema='ecommerce', type_='unique')
    op.drop_column('orders', 'request_fingerprint', schema='ecommerce')
    op.drop_column('orders', 'idempotency_key', schema='ecommerce')
