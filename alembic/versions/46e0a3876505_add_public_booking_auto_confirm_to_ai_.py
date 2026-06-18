"""add public_booking_auto_confirm to ai_agents

Revision ID: 46e0a3876505
Revises: c2cc5673cbc9
Create Date: 2026-06-18 19:21:03.819773

Per-branch toggle: auto-confirm public-site bookings (+ confirmation SMS) vs the
default pending-for-review. Autogenerate also surfaced spurious FK/index churn on
unrelated tables — removed by hand; this migration only adds the column.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '46e0a3876505'
down_revision: Union[str, None] = 'c2cc5673cbc9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'ai_agents',
        sa.Column('public_booking_auto_confirm', sa.Boolean(), server_default='false', nullable=False),
    )


def downgrade() -> None:
    op.drop_column('ai_agents', 'public_booking_auto_confirm')
