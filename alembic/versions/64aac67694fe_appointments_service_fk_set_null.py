"""appointments: service/variation FK on delete SET NULL

Revision ID: 64aac67694fe
Revises: b3a2c1d4e5f7
Create Date: 2026-05-30

Deleting a Service or ServiceVariation must preserve appointment history.
The FK columns are nullable; SET NULL keeps the row alive with no service reference
rather than cascading the delete.
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = '64aac67694fe'
down_revision = 'b3a2c1d4e5f7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # appointments.service_id — NO ACTION → SET NULL
    op.drop_constraint('appointments_service_id_fkey', 'appointments', type_='foreignkey')
    op.create_foreign_key(
        'appointments_service_id_fkey',
        'appointments', 'services',
        ['service_id'], ['id'],
        ondelete='SET NULL',
    )

    # appointments.variation_id — NO ACTION → SET NULL
    op.drop_constraint('appointments_variation_id_fkey', 'appointments', type_='foreignkey')
    op.create_foreign_key(
        'appointments_variation_id_fkey',
        'appointments', 'service_variations',
        ['variation_id'], ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint('appointments_variation_id_fkey', 'appointments', type_='foreignkey')
    op.create_foreign_key(
        'appointments_variation_id_fkey',
        'appointments', 'service_variations',
        ['variation_id'], ['id'],
    )

    op.drop_constraint('appointments_service_id_fkey', 'appointments', type_='foreignkey')
    op.create_foreign_key(
        'appointments_service_id_fkey',
        'appointments', 'services',
        ['service_id'], ['id'],
    )
