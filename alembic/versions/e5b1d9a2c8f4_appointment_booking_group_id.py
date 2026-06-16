"""multi-service booking: appointments.booking_group_id

Revision ID: e5b1d9a2c8f4
Revises: d4a9f06b3c71
Create Date: 2026-06-15

Adds a nullable `booking_group_id` to `appointments`. It links the independent
appointments created together as one multi-service visit (see
appointment_service.create_appointments_batch). NULL for single bookings.
Purely additive + reversible.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5b1d9a2c8f4'
down_revision: Union[str, None] = 'd4a9f06b3c71'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('appointments', sa.Column('booking_group_id', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('appointments', 'booking_group_id')
