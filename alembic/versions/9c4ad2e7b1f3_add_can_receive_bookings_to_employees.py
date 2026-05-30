"""add_can_receive_bookings_to_employees

Revision ID: 9c4ad2e7b1f3
Revises: f1a2b3c4d5e6
Create Date: 2026-05-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9c4ad2e7b1f3'
down_revision: Union[str, None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Existing employees keep their booking ability (server_default true).
    op.add_column(
        'employees',
        sa.Column('can_receive_bookings', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    )


def downgrade() -> None:
    op.drop_column('employees', 'can_receive_bookings')
