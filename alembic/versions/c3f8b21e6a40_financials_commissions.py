"""financials phase 3: employee commissions (config + per-branch rules)

Revision ID: c3f8b21e6a40
Revises: a7e2c4f9b1d3
Create Date: 2026-05-31

Phase 3 of the financials module (see docs/modules/financials.md):
  - businesses        : commission_base (gross|net) + commission_includes_surcharge
                        (company-wide commission policy)
  - commission_rules  : per-branch rate rules (employee/title/service/variation)

Purely additive + reversible. Each enum is referenced by exactly one table, so
they are created inline (default create_type=True) and dropped in downgrade.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3f8b21e6a40'
down_revision: Union[str, None] = 'a7e2c4f9b1d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Company-wide commission policy on businesses ──────────────────────────
    # op.add_column does NOT auto-create the enum type (unlike create_table), so
    # create it explicitly first.
    commission_base_enum = sa.Enum('gross', 'net', name='commission_base_enum')
    commission_base_enum.create(op.get_bind(), checkfirst=True)
    op.add_column(
        'businesses',
        sa.Column('commission_base',
                  sa.Enum('gross', 'net', name='commission_base_enum', create_type=False),
                  server_default='gross', nullable=False),
    )
    op.add_column(
        'businesses',
        sa.Column('commission_includes_surcharge', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    )

    # ── Per-branch commission rules ───────────────────────────────────────────
    op.create_table(
        'commission_rules',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('agent_id', sa.Integer(), nullable=False),
        sa.Column('employee_id', sa.Integer(), nullable=True),
        sa.Column('title_id', sa.Integer(), nullable=True),
        sa.Column('service_id', sa.Integer(), nullable=True),
        sa.Column('variation_id', sa.Integer(), nullable=True),
        sa.Column('type', sa.Enum('percent', 'flat', name='commission_type_enum'), nullable=False),
        sa.Column('value', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['agent_id'], ['ai_agents.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['title_id'], ['service_titles.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['service_id'], ['services.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['variation_id'], ['service_variations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint('value >= 0', name='ck_commission_value_non_negative'),
        sa.CheckConstraint(
            '(CASE WHEN title_id IS NOT NULL THEN 1 ELSE 0 END'
            ' + CASE WHEN service_id IS NOT NULL THEN 1 ELSE 0 END'
            ' + CASE WHEN variation_id IS NOT NULL THEN 1 ELSE 0 END) <= 1',
            name='ck_commission_one_target',
        ),
    )
    op.create_index('ix_commission_rule_agent', 'commission_rules', ['agent_id'])
    op.create_index('ix_commission_rule_employee', 'commission_rules', ['employee_id'])


def downgrade() -> None:
    op.drop_index('ix_commission_rule_employee', table_name='commission_rules')
    op.drop_index('ix_commission_rule_agent', table_name='commission_rules')
    op.drop_table('commission_rules')

    op.drop_column('businesses', 'commission_includes_surcharge')
    op.drop_column('businesses', 'commission_base')

    # Postgres keeps enum types after their tables/columns are dropped.
    op.execute('DROP TYPE IF EXISTS commission_type_enum')
    op.execute('DROP TYPE IF EXISTS commission_base_enum')
