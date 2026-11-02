"""Track order and item inserts/updates, including direct SQL writes.

Timestamps are change times, NOT commit positions. Incremental readers must use
an overlap window and periodic reconciliation; hard deletes are not tracked.
"""
from alembic import op
import sqlalchemy as sa

revision = '0005_order_tracking'
down_revision = '0004_customer_auth'
branch_labels = None
depends_on = None


def upgrade():
    # Stable default backfills existing rows without inventing business dates.
    for table in ('orders', 'order_items'):
        op.add_column(table, sa.Column('updated_at', sa.DateTime(timezone=True),
                      nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')), schema='ecommerce')
        op.create_index(f'ix_{table}_updated_at', table, ['updated_at'], schema='ecommerce')
    op.execute('''CREATE FUNCTION ecommerce.touch_order_updated_at() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            NEW.updated_at := clock_timestamp();
            RETURN NEW;
        END;
        $$''')
    for table in ('orders', 'order_items'):
        op.execute(f'''CREATE TRIGGER {table}_touch_updated_at
            BEFORE INSERT OR UPDATE ON ecommerce.{table}
            FOR EACH ROW EXECUTE FUNCTION ecommerce.touch_order_updated_at()''')


def downgrade():
    for table in ('orders', 'order_items'):
        op.execute(f'DROP TRIGGER {table}_touch_updated_at ON ecommerce.{table}')
    op.execute('DROP FUNCTION ecommerce.touch_order_updated_at()')
    for table in ('orders', 'order_items'):
        op.drop_index(f'ix_{table}_updated_at', table_name=table, schema='ecommerce')
        op.drop_column(table, 'updated_at', schema='ecommerce')
