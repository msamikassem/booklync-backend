"""financials: per-branch currency/tax + sales / line-items / payments

Revision ID: f1c0a5d7e9b2
Revises: 3dcf174c1cd1
Create Date: 2026-05-31

Phase 1 of the financials module (see docs/modules/financials.md):
  - per-branch currency + tax on ai_agents
  - sales / sale_line_items / payments ledger

Purely additive + reversible. Enum types are created inline by create_table
(same pattern as a39acf61ab79) and dropped explicitly in downgrade.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1c0a5d7e9b2'
down_revision: Union[str, None] = '3dcf174c1cd1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Per-branch currency + tax on ai_agents ────────────────────────────────
    op.add_column('ai_agents', sa.Column('currency', sa.String(length=3), server_default='USD', nullable=False))
    op.add_column('ai_agents', sa.Column('tax_rate', sa.Numeric(precision=5, scale=2), nullable=True))
    op.add_column('ai_agents', sa.Column('tax_inclusive', sa.Boolean(), server_default=sa.text('false'), nullable=False))

    # ── sales (order header) ──────────────────────────────────────────────────
    op.create_table(
        'sales',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('agent_id', sa.Integer(), nullable=False),
        sa.Column('customer_id', sa.Integer(), nullable=True),
        sa.Column('created_by_user_id', sa.Integer(), nullable=True),
        sa.Column('status', sa.Enum('open', 'partially_paid', 'paid', 'refunded', 'void', name='sale_status_enum'),
                  server_default='open', nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('subtotal', sa.Numeric(precision=10, scale=2), server_default='0', nullable=False),
        sa.Column('discount_total', sa.Numeric(precision=10, scale=2), server_default='0', nullable=False),
        sa.Column('tax_total', sa.Numeric(precision=10, scale=2), server_default='0', nullable=False),
        sa.Column('tip_total', sa.Numeric(precision=10, scale=2), server_default='0', nullable=False),
        sa.Column('total', sa.Numeric(precision=10, scale=2), server_default='0', nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['agent_id'], ['ai_agents.id'], ),
        sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_sale_agent_created', 'sales', ['agent_id', 'created_at'])
    op.create_index('ix_sale_customer', 'sales', ['customer_id'])

    # ── sale_line_items ───────────────────────────────────────────────────────
    op.create_table(
        'sale_line_items',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('sale_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.Enum('service', 'product', 'package', 'adjustment', name='sale_line_kind_enum'),
                  server_default='service', nullable=False),
        sa.Column('service_id', sa.Integer(), nullable=True),
        sa.Column('variation_id', sa.Integer(), nullable=True),
        sa.Column('appointment_id', sa.Integer(), nullable=True),
        sa.Column('employee_id', sa.Integer(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('employee_name', sa.Text(), nullable=True),
        sa.Column('qty', sa.Numeric(precision=10, scale=2), server_default='1', nullable=False),
        sa.Column('unit_price', sa.Numeric(precision=10, scale=2), server_default='0', nullable=False),
        sa.Column('surcharge', sa.Numeric(precision=10, scale=2), server_default='0', nullable=False),
        sa.Column('discount_amount', sa.Numeric(precision=10, scale=2), server_default='0', nullable=False),
        sa.Column('tax_amount', sa.Numeric(precision=10, scale=2), server_default='0', nullable=False),
        sa.Column('tip_amount', sa.Numeric(precision=10, scale=2), server_default='0', nullable=False),
        sa.Column('line_total', sa.Numeric(precision=10, scale=2), server_default='0', nullable=False),
        sa.Column('commission_rate', sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column('commission_amount', sa.Numeric(precision=10, scale=2), server_default='0', nullable=False),
        sa.ForeignKeyConstraint(['sale_id'], ['sales.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['service_id'], ['services.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['variation_id'], ['service_variations.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['appointment_id'], ['appointments.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint('qty > 0', name='ck_sale_line_qty_positive'),
    )
    op.create_index('ix_sale_line_sale', 'sale_line_items', ['sale_id'])
    op.create_index('ix_sale_line_employee', 'sale_line_items', ['employee_id'])
    op.create_index('ix_sale_line_appointment', 'sale_line_items', ['appointment_id'])

    # ── payments (settlement ledger) ──────────────────────────────────────────
    op.create_table(
        'payments',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('sale_id', sa.Integer(), nullable=False),
        sa.Column('amount', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('tip_amount', sa.Numeric(precision=10, scale=2), server_default='0', nullable=False),
        sa.Column('method', sa.Enum('cash', 'card', 'transfer', 'other', name='payment_method_enum'), nullable=False),
        sa.Column('paid_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('provider', sa.Text(), nullable=True),
        sa.Column('provider_ref', sa.Text(), nullable=True),
        sa.Column('intent_id', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['sale_id'], ['sales.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint('amount >= 0', name='ck_payment_amount_non_negative'),
    )
    op.create_index('ix_payment_sale', 'payments', ['sale_id'])


def downgrade() -> None:
    op.drop_index('ix_payment_sale', table_name='payments')
    op.drop_table('payments')

    op.drop_index('ix_sale_line_appointment', table_name='sale_line_items')
    op.drop_index('ix_sale_line_employee', table_name='sale_line_items')
    op.drop_index('ix_sale_line_sale', table_name='sale_line_items')
    op.drop_table('sale_line_items')

    op.drop_index('ix_sale_customer', table_name='sales')
    op.drop_index('ix_sale_agent_created', table_name='sales')
    op.drop_table('sales')

    op.drop_column('ai_agents', 'tax_inclusive')
    op.drop_column('ai_agents', 'tax_rate')
    op.drop_column('ai_agents', 'currency')

    # Postgres keeps enum types after their tables are dropped — remove explicitly.
    op.execute('DROP TYPE IF EXISTS payment_method_enum')
    op.execute('DROP TYPE IF EXISTS sale_line_kind_enum')
    op.execute('DROP TYPE IF EXISTS sale_status_enum')
