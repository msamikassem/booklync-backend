"""add branch_sites (website builder)

Revision ID: c2cc5673cbc9
Revises: e5b1d9a2c8f4
Create Date: 2026-06-17 21:43:26.667025

Adds the website-builder document table. One row per branch (AIAgent), holding
the draft + published site config as JSONB. Autogenerate also surfaced spurious
FK/index churn on unrelated tables (role_permissions, roles, user_branch_roles,
users, employee_branches) — that drift was removed by hand; this migration only
creates `branch_sites`.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c2cc5673cbc9'
down_revision: Union[str, None] = 'e5b1d9a2c8f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'branch_sites',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('agent_id', sa.Integer(), nullable=False),
        sa.Column('theme_id', sa.Text(), nullable=True),
        sa.Column('draft_config', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('published_config', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('published_version', sa.Integer(), server_default='0', nullable=False),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('branding_removed', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['agent_id'], ['ai_agents.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    # unique → enforces the 1:1 (one site per branch) at the DB level.
    op.create_index(op.f('ix_branch_sites_agent_id'), 'branch_sites', ['agent_id'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_branch_sites_agent_id'), table_name='branch_sites')
    op.drop_table('branch_sites')
