"""financials phase 2: discounts catalogue + sale discount applications

Revision ID: a7e2c4f9b1d3
Revises: f1c0a5d7e9b2
Create Date: 2026-05-31

Phase 2 of the financials module (see docs/modules/financials.md):
  - discounts        : reusable per-branch discount catalogue (incl. promo codes)
  - sale_discounts   : each application of a discount to a sale (snapshots terms)

Purely additive + reversible. The two enum types are shared by both tables, so
they are created once explicitly (checkfirst) and referenced with
create_type=False; downgrade drops them after the tables.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7e2c4f9b1d3'
down_revision: Union[str, None] = 'f1c0a5d7e9b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The two enum types are shared by both tables. The first table to reference
    # each one creates it (default create_type=True); the second references it
    # with create_type=False so Postgres isn't asked to CREATE TYPE twice.

    # ── discounts (reusable catalogue, per branch) ────────────────────────────
    op.create_table(
        'discounts',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('agent_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('type', sa.Enum('percent', 'fixed', name='discount_type_enum'), nullable=False),
        sa.Column('scope', sa.Enum('order', 'line', name='discount_scope_enum'),
                  server_default='order', nullable=False),
        sa.Column('value', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('code', sa.Text(), nullable=True),
        sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('valid_from', sa.DateTime(timezone=True), nullable=True),
        sa.Column('valid_to', sa.DateTime(timezone=True), nullable=True),
        sa.Column('max_uses', sa.Integer(), nullable=True),
        sa.Column('per_customer_limit', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['agent_id'], ['ai_agents.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint('value > 0', name='ck_discount_value_positive'),
    )
    op.create_index('ix_discount_agent', 'discounts', ['agent_id'])
    # Promo codes unique per branch — only when a code is set.
    op.create_index(
        'uq_discount_agent_code', 'discounts', ['agent_id', 'code'],
        unique=True, postgresql_where=sa.text('code IS NOT NULL'),
    )

    # ── sale_discounts (one application of a discount to a sale) ───────────────
    op.create_table(
        'sale_discounts',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('sale_id', sa.Integer(), nullable=False),
        sa.Column('discount_id', sa.Integer(), nullable=True),
        sa.Column('sale_line_item_id', sa.Integer(), nullable=True),
        sa.Column('name', sa.Text(), nullable=True),
        sa.Column('code', sa.Text(), nullable=True),
        sa.Column('type', sa.Enum('percent', 'fixed', name='discount_type_enum', create_type=False), nullable=False),
        sa.Column('scope', sa.Enum('order', 'line', name='discount_scope_enum', create_type=False), nullable=False),
        sa.Column('value', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('amount', sa.Numeric(precision=10, scale=2), server_default='0', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['sale_id'], ['sales.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['discount_id'], ['discounts.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['sale_line_item_id'], ['sale_line_items.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_sale_discount_sale', 'sale_discounts', ['sale_id'])
    op.create_index('ix_sale_discount_discount', 'sale_discounts', ['discount_id'])


def downgrade() -> None:
    op.drop_index('ix_sale_discount_discount', table_name='sale_discounts')
    op.drop_index('ix_sale_discount_sale', table_name='sale_discounts')
    op.drop_table('sale_discounts')

    op.drop_index('uq_discount_agent_code', table_name='discounts')
    op.drop_index('ix_discount_agent', table_name='discounts')
    op.drop_table('discounts')

    # Postgres keeps enum types after their tables are dropped — remove explicitly.
    op.execute('DROP TYPE IF EXISTS discount_scope_enum')
    op.execute('DROP TYPE IF EXISTS discount_type_enum')
