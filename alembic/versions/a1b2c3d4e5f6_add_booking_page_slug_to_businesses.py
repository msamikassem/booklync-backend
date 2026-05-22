"""add booking_page_slug to businesses

Revision ID: a1b2c3d4e5f6
Revises: bcfa4f1d9f3f
Create Date: 2026-05-23 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'bcfa4f1d9f3f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _slugify(name: str) -> str:
    import re
    slug = name.lower()
    slug = re.sub(r'[^a-z0-9]+', '-', slug)
    slug = slug.strip('-')
    return slug or 'business'


def upgrade() -> None:
    op.add_column('businesses', sa.Column('booking_page_slug', sa.Text(), nullable=True))

    # Backfill existing rows: generate a slug from the business name.
    # Use a raw connection to iterate and handle collisions.
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, name FROM businesses ORDER BY id")).fetchall()

    used: set[str] = set()
    for row in rows:
        base = _slugify(row.name)
        slug = base
        counter = 2
        while slug in used:
            slug = f"{base}-{counter}"
            counter += 1
        used.add(slug)
        conn.execute(
            sa.text("UPDATE businesses SET booking_page_slug = :slug WHERE id = :id"),
            {"slug": slug, "id": row.id},
        )

    op.create_unique_constraint('uq_businesses_booking_page_slug', 'businesses', ['booking_page_slug'])
    op.create_index('ix_businesses_booking_page_slug', 'businesses', ['booking_page_slug'])


def downgrade() -> None:
    op.drop_index('ix_businesses_booking_page_slug', table_name='businesses')
    op.drop_constraint('uq_businesses_booking_page_slug', 'businesses', type_='unique')
    op.drop_column('businesses', 'booking_page_slug')
