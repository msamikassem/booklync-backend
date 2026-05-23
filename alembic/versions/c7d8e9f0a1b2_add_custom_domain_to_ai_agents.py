"""add custom_domain and domain_verified to ai_agents

Revision ID: c7d8e9f0a1b2
Revises: b2c3d4e5f6a7
Create Date: 2026-05-23

"""
from alembic import op
import sqlalchemy as sa
from typing import Union

revision: str = 'c7d8e9f0a1b2'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('ai_agents', sa.Column('custom_domain', sa.Text(), nullable=True))
    op.add_column('ai_agents', sa.Column('domain_verified', sa.Boolean(), nullable=False, server_default='false'))
    op.create_index('ix_ai_agents_custom_domain', 'ai_agents', ['custom_domain'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_ai_agents_custom_domain', table_name='ai_agents')
    op.drop_column('ai_agents', 'domain_verified')
    op.drop_column('ai_agents', 'custom_domain')
