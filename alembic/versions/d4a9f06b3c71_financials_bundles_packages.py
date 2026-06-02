"""financials phase 4-5: bundles (combos) + packages (prepaid credits)

Revision ID: d4a9f06b3c71
Revises: c3f8b21e6a40
Create Date: 2026-05-31

Phase 4 (combos) + Phase 5 (prepaid packages) of the financials module
(see docs/modules/financials.md):
  - bundles / bundle_items : per-branch combo + package catalogue
  - package_credits        : a customer's prepaid balance (sessions or value)
  - sales.bundle_id, sale_line_items.bundle_id / redeemed_credit_id

Purely additive + reversible. `credit_kind_enum` is shared by bundles +
package_credits → bundles creates it (create_type=True), package_credits
references it (create_type=False); both enums dropped after their tables.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4a9f06b3c71'
down_revision: Union[str, None] = 'c3f8b21e6a40'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── bundles (combo + package catalogue) ───────────────────────────────────
    op.create_table(
        'bundles',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('agent_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.Enum('combo', 'package', name='bundle_kind_enum'), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('price', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('credit_kind', sa.Enum('sessions', 'value', name='credit_kind_enum'), nullable=True),
        sa.Column('credit_value', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('expires_days', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['agent_id'], ['ai_agents.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint('price >= 0', name='ck_bundle_price_non_negative'),
    )
    op.create_index('ix_bundle_agent', 'bundles', ['agent_id'])

    # ── bundle_items ──────────────────────────────────────────────────────────
    op.create_table(
        'bundle_items',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('bundle_id', sa.Integer(), nullable=False),
        sa.Column('service_id', sa.Integer(), nullable=True),
        sa.Column('variation_id', sa.Integer(), nullable=True),
        sa.Column('qty', sa.Integer(), server_default='1', nullable=False),
        sa.ForeignKeyConstraint(['bundle_id'], ['bundles.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['service_id'], ['services.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['variation_id'], ['service_variations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint('qty > 0', name='ck_bundle_item_qty_positive'),
    )
    op.create_index('ix_bundle_item_bundle', 'bundle_items', ['bundle_id'])

    # ── package_credits ───────────────────────────────────────────────────────
    op.create_table(
        'package_credits',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('customer_id', sa.Integer(), nullable=False),
        sa.Column('bundle_id', sa.Integer(), nullable=True),
        sa.Column('agent_id', sa.Integer(), nullable=False),
        sa.Column('sale_id', sa.Integer(), nullable=True),
        sa.Column('kind', sa.Enum('sessions', 'value', name='credit_kind_enum', create_type=False), nullable=False),
        sa.Column('service_id', sa.Integer(), nullable=True),
        sa.Column('variation_id', sa.Integer(), nullable=True),
        sa.Column('remaining_qty', sa.Integer(), nullable=True),
        sa.Column('balance', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['bundle_id'], ['bundles.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['agent_id'], ['ai_agents.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['sale_id'], ['sales.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['service_id'], ['services.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['variation_id'], ['service_variations.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_package_credit_customer', 'package_credits', ['customer_id'])
    op.create_index('ix_package_credit_agent', 'package_credits', ['agent_id'])
    op.create_index('ix_package_credit_sale', 'package_credits', ['sale_id'])

    # ── link columns on existing financial tables ─────────────────────────────
    op.add_column('sales', sa.Column('bundle_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_sales_bundle', 'sales', 'bundles', ['bundle_id'], ['id'], ondelete='SET NULL')

    op.add_column('sale_line_items', sa.Column('bundle_id', sa.Integer(), nullable=True))
    op.add_column('sale_line_items', sa.Column('redeemed_credit_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_sale_line_bundle', 'sale_line_items', 'bundles', ['bundle_id'], ['id'], ondelete='SET NULL')
    op.create_foreign_key('fk_sale_line_credit', 'sale_line_items', 'package_credits',
                          ['redeemed_credit_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    op.drop_constraint('fk_sale_line_credit', 'sale_line_items', type_='foreignkey')
    op.drop_constraint('fk_sale_line_bundle', 'sale_line_items', type_='foreignkey')
    op.drop_column('sale_line_items', 'redeemed_credit_id')
    op.drop_column('sale_line_items', 'bundle_id')

    op.drop_constraint('fk_sales_bundle', 'sales', type_='foreignkey')
    op.drop_column('sales', 'bundle_id')

    op.drop_index('ix_package_credit_sale', table_name='package_credits')
    op.drop_index('ix_package_credit_agent', table_name='package_credits')
    op.drop_index('ix_package_credit_customer', table_name='package_credits')
    op.drop_table('package_credits')

    op.drop_index('ix_bundle_item_bundle', table_name='bundle_items')
    op.drop_table('bundle_items')

    op.drop_index('ix_bundle_agent', table_name='bundles')
    op.drop_table('bundles')

    op.execute('DROP TYPE IF EXISTS credit_kind_enum')
    op.execute('DROP TYPE IF EXISTS bundle_kind_enum')
