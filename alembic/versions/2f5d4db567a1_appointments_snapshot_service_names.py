"""appointments: snapshot service_name and variation_name at booking time

Revision ID: 2f5d4db567a1
Revises: 64aac67694fe
Create Date: 2026-05-30

Adds denormalised name columns to appointments so historical records retain
readable service/variation names even after the source rows are deleted.
Existing rows are backfilled via a JOIN to services and service_variations.
"""
import sqlalchemy as sa
from alembic import op

revision = '2f5d4db567a1'
down_revision = '64aac67694fe'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('appointments', sa.Column('service_name',   sa.Text(), nullable=True))
    op.add_column('appointments', sa.Column('variation_name', sa.Text(), nullable=True))

    # Backfill existing rows from the live FK targets.
    op.execute("""
        UPDATE appointments a
        SET service_name = s.name
        FROM services s
        WHERE a.service_id = s.id
          AND a.service_name IS NULL
    """)

    op.execute("""
        UPDATE appointments a
        SET variation_name = sv.name
        FROM service_variations sv
        WHERE a.variation_id = sv.id
          AND a.variation_name IS NULL
    """)


def downgrade() -> None:
    op.drop_column('appointments', 'variation_name')
    op.drop_column('appointments', 'service_name')
