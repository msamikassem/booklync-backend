"""appointments: employee FK on delete SET NULL + snapshot employee_name

Revision ID: 1480d06b11c7
Revises: 2f5d4db567a1
Create Date: 2026-05-30

Deleting an employee must preserve appointment history.
Makes employee_id nullable, changes the FK to ON DELETE SET NULL, and adds
an employee_name snapshot column populated at booking time.
Existing rows are backfilled from the employees table.
"""
import sqlalchemy as sa
from alembic import op

revision = '1480d06b11c7'
down_revision = '2f5d4db567a1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add snapshot column
    op.add_column('appointments', sa.Column('employee_name', sa.Text(), nullable=True))

    # 2. Backfill from live employee rows
    op.execute("""
        UPDATE appointments a
        SET employee_name = e.name
        FROM employees e
        WHERE a.employee_id = e.id
          AND a.employee_name IS NULL
    """)

    # 3. Make employee_id nullable
    op.alter_column('appointments', 'employee_id', nullable=True)

    # 4. Swap FK to ON DELETE SET NULL
    op.drop_constraint('appointments_employee_id_fkey', 'appointments', type_='foreignkey')
    op.create_foreign_key(
        'appointments_employee_id_fkey',
        'appointments', 'employees',
        ['employee_id'], ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    # Restore FK without SET NULL
    op.drop_constraint('appointments_employee_id_fkey', 'appointments', type_='foreignkey')
    op.create_foreign_key(
        'appointments_employee_id_fkey',
        'appointments', 'employees',
        ['employee_id'], ['id'],
    )

    # Restore NOT NULL — only safe if no orphaned rows exist
    op.alter_column('appointments', 'employee_id', nullable=False)

    op.drop_column('appointments', 'employee_name')
