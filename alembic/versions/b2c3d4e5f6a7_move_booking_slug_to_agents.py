"""move booking_page_slug from businesses to ai_agents

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-05-23 13:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _slugify(name: str) -> str:
    import re
    slug = name.lower()
    slug = re.sub(r'[^a-z0-9]+', '-', slug)
    return slug.strip('-') or 'branch'


def upgrade() -> None:
    # Remove slug from businesses
    op.drop_index('ix_businesses_booking_page_slug', table_name='businesses')
    op.drop_constraint('uq_businesses_booking_page_slug', 'businesses', type_='unique')
    op.drop_column('businesses', 'booking_page_slug')

    # Add slug to ai_agents
    op.add_column('ai_agents', sa.Column('booking_page_slug', sa.Text(), nullable=True))

    # Backfill: generate slug from location_name, fall back to agent_name
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, location_name, agent_name FROM ai_agents ORDER BY id")
    ).fetchall()

    used: set[str] = set()
    for row in rows:
        base = _slugify(row.location_name or row.agent_name)
        slug = base
        counter = 2
        while slug in used:
            slug = f"{base}-{counter}"
            counter += 1
        used.add(slug)
        conn.execute(
            sa.text("UPDATE ai_agents SET booking_page_slug = :slug WHERE id = :id"),
            {"slug": slug, "id": row.id},
        )

    op.create_unique_constraint('uq_ai_agents_booking_page_slug', 'ai_agents', ['booking_page_slug'])
    op.create_index('ix_ai_agents_booking_page_slug', 'ai_agents', ['booking_page_slug'])


def downgrade() -> None:
    op.drop_index('ix_ai_agents_booking_page_slug', table_name='ai_agents')
    op.drop_constraint('uq_ai_agents_booking_page_slug', 'ai_agents', type_='unique')
    op.drop_column('ai_agents', 'booking_page_slug')

    op.add_column('businesses', sa.Column('booking_page_slug', sa.Text(), nullable=True))
    op.create_unique_constraint('uq_businesses_booking_page_slug', 'businesses', ['booking_page_slug'])
    op.create_index('ix_businesses_booking_page_slug', 'businesses', ['booking_page_slug'])
