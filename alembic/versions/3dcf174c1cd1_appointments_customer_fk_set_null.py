"""appointments: customer FK on delete SET NULL + snapshot customer_name/phone

Revision ID: 3dcf174c1cd1
Revises: 1480d06b11c7
Create Date: 2026-05-30

Deleting a customer must preserve appointment history.
Makes customer_id nullable, changes the FK to ON DELETE SET NULL, and adds
customer_name and customer_phone snapshot columns populated at booking time.
Existing rows are backfilled from the customers table.
"""
import sqlalchemy as sa
from alembic import op

revision = '3dcf174c1cd1'
down_revision = '1480d06b11c7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add snapshot columns
    op.add_column('appointments', sa.Column('customer_name',  sa.Text(), nullable=True))
    op.add_column('appointments', sa.Column('customer_phone', sa.Text(), nullable=True))

    # 2. Backfill from live customer rows
    op.execute("""
        UPDATE appointments a
        SET customer_name  = c.name,
            customer_phone = c.phone
        FROM customers c
        WHERE a.customer_id = c.id
          AND a.customer_name IS NULL
    """)

    # 3. Make customer_id nullable
    op.alter_column('appointments', 'customer_id', nullable=True)

    # 4. Swap FK to ON DELETE SET NULL
    op.drop_constraint('appointments_customer_id_fkey', 'appointments', type_='foreignkey')
    op.create_foreign_key(
        'appointments_customer_id_fkey',
        'appointments', 'customers',
        ['customer_id'], ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint('appointments_customer_id_fkey', 'appointments', type_='foreignkey')
    op.create_foreign_key(
        'appointments_customer_id_fkey',
        'appointments', 'customers',
        ['customer_id'], ['id'],
    )

    op.alter_column('appointments', 'customer_id', nullable=False)

    op.drop_column('appointments', 'customer_phone')
    op.drop_column('appointments', 'customer_name')
