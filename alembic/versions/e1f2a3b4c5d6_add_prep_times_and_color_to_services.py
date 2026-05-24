"""add preparation times and color to services

Revision ID: e1f2a3b4c5d6
Revises: d3c4f78f98e8
Create Date: 2026-05-24 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, None] = 'a0d1e368195b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('services', sa.Column('preparation_before_minutes', sa.Integer(), nullable=True))
    op.add_column('services', sa.Column('preparation_after_minutes',  sa.Integer(), nullable=True))
    op.add_column('services', sa.Column('color', sa.Text(), nullable=True))

    op.create_check_constraint(
        'ck_service_prep_before_non_negative',
        'services',
        'preparation_before_minutes IS NULL OR preparation_before_minutes >= 0',
    )
    op.create_check_constraint(
        'ck_service_prep_after_non_negative',
        'services',
        'preparation_after_minutes IS NULL OR preparation_after_minutes >= 0',
    )


def downgrade() -> None:
    op.drop_constraint('ck_service_prep_after_non_negative',  'services', type_='check')
    op.drop_constraint('ck_service_prep_before_non_negative', 'services', type_='check')
    op.drop_column('services', 'color')
    op.drop_column('services', 'preparation_after_minutes')
    op.drop_column('services', 'preparation_before_minutes')
