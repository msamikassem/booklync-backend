"""multi-branch employees + multi-branch dashboard invites

Restructures Employee↔AIAgent as a many-to-many via a new
employee_branches join table that carries the per-branch active +
can_receive_bookings flags. Adds agent_id to employee_working_hours
and blocked_times (per-branch weekly schedule + breaks). Converts
user_invites from a single agent_id to an agent_ids JSONB list so a
single invite can grant dashboard access to multiple branches at once.

Backfills are run before NOT NULL is set and before source columns are
dropped, so existing rows survive the upgrade unchanged in meaning.

LOSSY DOWNGRADE WARNING — read before running `alembic downgrade`:
  The downgrade rebuilds `employees.agent_id` from `MIN(agent_id)` in
  `employee_branches`. For any employee assigned to more than one branch
  the additional branches are silently dropped (along with their per-branch
  working-hours and blocked-times, since those become unique-key violations
  once `agent_id` is removed). Run only when you're sure no multi-branch
  employees exist yet, or take an explicit backup first.

Revision ID: b3a2c1d4e5f7
Revises: 9c4ad2e7b1f3
Create Date: 2026-05-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b3a2c1d4e5f7"
down_revision: Union[str, None] = "9c4ad2e7b1f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. Create the employee_branches join table ───────────────────────────
    op.create_table(
        "employee_branches",
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("agent_id", sa.Integer(), nullable=False),
        sa.Column(
            "active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "can_receive_bookings",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"], ["employees.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["ai_agents.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("employee_id", "agent_id"),
    )
    op.create_index(
        "ix_employee_branches_agent_id",
        "employee_branches",
        ["agent_id"],
        unique=False,
    )

    # Backfill: one row per existing employee, carrying its current flags.
    op.execute(
        """
        INSERT INTO employee_branches
            (employee_id, agent_id, active, can_receive_bookings, created_at, updated_at)
        SELECT id, agent_id, active, can_receive_bookings, now(), now()
        FROM employees
        """
    )

    # ── 2. employee_working_hours.agent_id ──────────────────────────────────
    op.add_column(
        "employee_working_hours",
        sa.Column("agent_id", sa.Integer(), nullable=True),
    )
    op.execute(
        """
        UPDATE employee_working_hours wh
        SET agent_id = e.agent_id
        FROM employees e
        WHERE wh.employee_id = e.id
        """
    )
    op.alter_column("employee_working_hours", "agent_id", nullable=False)
    op.create_foreign_key(
        "fk_employee_working_hours_agent_id_ai_agents",
        "employee_working_hours",
        "ai_agents",
        ["agent_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint(
        "uq_employee_working_hour",
        "employee_working_hours",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_employee_working_hour",
        "employee_working_hours",
        ["employee_id", "agent_id", "day_of_week"],
    )

    # ── 3. blocked_times.agent_id ───────────────────────────────────────────
    op.add_column(
        "blocked_times",
        sa.Column("agent_id", sa.Integer(), nullable=True),
    )
    op.execute(
        """
        UPDATE blocked_times bt
        SET agent_id = e.agent_id
        FROM employees e
        WHERE bt.employee_id = e.id
        """
    )
    op.alter_column("blocked_times", "agent_id", nullable=False)
    op.create_foreign_key(
        "fk_blocked_times_agent_id_ai_agents",
        "blocked_times",
        "ai_agents",
        ["agent_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # ── 4. user_invites.agent_ids (JSONB) ───────────────────────────────────
    op.add_column(
        "user_invites",
        sa.Column(
            "agent_ids",
            sa.dialects.postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.execute(
        """
        UPDATE user_invites
        SET agent_ids = jsonb_build_array(agent_id)
        """
    )
    # Drop the server default once existing rows are backfilled; new rows
    # always send agent_ids explicitly.
    op.alter_column("user_invites", "agent_ids", server_default=None)
    op.drop_constraint(
        "user_invites_agent_id_fkey",
        "user_invites",
        type_="foreignkey",
    )
    op.drop_column("user_invites", "agent_id")

    # ── 5. Drop now-redundant columns on employees ──────────────────────────
    op.drop_column("employees", "can_receive_bookings")
    op.drop_column("employees", "active")
    op.drop_constraint(
        "employees_agent_id_fkey",
        "employees",
        type_="foreignkey",
    )
    op.drop_column("employees", "agent_id")


def downgrade() -> None:
    # ── 5. Restore employees.agent_id / active / can_receive_bookings ───────
    op.add_column(
        "employees",
        sa.Column("agent_id", sa.Integer(), nullable=True),
    )
    op.execute(
        """
        UPDATE employees e
        SET agent_id = eb.agent_id
        FROM employee_branches eb
        WHERE eb.employee_id = e.id
          AND eb.agent_id = (
              SELECT MIN(agent_id) FROM employee_branches WHERE employee_id = e.id
          )
        """
    )
    op.alter_column("employees", "agent_id", nullable=False)
    op.create_foreign_key(
        "employees_agent_id_fkey",
        "employees",
        "ai_agents",
        ["agent_id"],
        ["id"],
    )
    op.add_column(
        "employees",
        sa.Column(
            "active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
    )
    op.execute(
        """
        UPDATE employees e
        SET active = eb.active
        FROM employee_branches eb
        WHERE eb.employee_id = e.id
          AND eb.agent_id = e.agent_id
        """
    )
    op.add_column(
        "employees",
        sa.Column(
            "can_receive_bookings",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
    )
    op.execute(
        """
        UPDATE employees e
        SET can_receive_bookings = eb.can_receive_bookings
        FROM employee_branches eb
        WHERE eb.employee_id = e.id
          AND eb.agent_id = e.agent_id
        """
    )

    # ── 4. Restore user_invites.agent_id ────────────────────────────────────
    op.add_column(
        "user_invites",
        sa.Column("agent_id", sa.Integer(), nullable=True),
    )
    op.execute(
        """
        UPDATE user_invites
        SET agent_id = (agent_ids->>0)::int
        WHERE jsonb_array_length(agent_ids) >= 1
        """
    )
    op.alter_column("user_invites", "agent_id", nullable=False)
    op.create_foreign_key(
        "user_invites_agent_id_fkey",
        "user_invites",
        "ai_agents",
        ["agent_id"],
        ["id"],
    )
    op.drop_column("user_invites", "agent_ids")

    # ── 3. Drop blocked_times.agent_id ──────────────────────────────────────
    op.drop_constraint(
        "fk_blocked_times_agent_id_ai_agents",
        "blocked_times",
        type_="foreignkey",
    )
    op.drop_column("blocked_times", "agent_id")

    # ── 2. Drop employee_working_hours.agent_id (restore old UNIQUE) ────────
    op.drop_constraint(
        "uq_employee_working_hour",
        "employee_working_hours",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_employee_working_hour",
        "employee_working_hours",
        ["employee_id", "day_of_week"],
    )
    op.drop_constraint(
        "fk_employee_working_hours_agent_id_ai_agents",
        "employee_working_hours",
        type_="foreignkey",
    )
    op.drop_column("employee_working_hours", "agent_id")

    # ── 1. Drop employee_branches ───────────────────────────────────────────
    op.drop_index(
        "ix_employee_branches_agent_id",
        table_name="employee_branches",
    )
    op.drop_table("employee_branches")
