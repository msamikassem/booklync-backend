"""rbac: users, roles, permissions, branch assignments

Replaces the staff_accounts / employee_invites auth model with a role-based
access-control system: permissions, roles (+ system presets), role_permissions,
users (single login identity), user_branch_roles (per-branch role assignment),
and user_invites.

Clean cutover: the old staff_accounts and employee_invites tables are dropped
(no data backfilled). The shared `invite_status_enum` Postgres type is reused.

Revision ID: f1a2b3c4d5e6
Revises: a39acf61ab79
Create Date: 2026-05-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "a39acf61ab79"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The invite_status_enum type already exists (created by a39acf61ab79); reuse it.
invite_status_enum = postgresql.ENUM(
    "pending", "accepted", "expired", name="invite_status_enum", create_type=False
)


def upgrade() -> None:
    # ── Drop the old dual-principal auth tables (clean cutover) ──────────────
    op.drop_index(op.f("ix_staff_accounts_email"), table_name="staff_accounts")
    op.drop_table("staff_accounts")
    op.drop_index(op.f("ix_employee_invites_token_hash"), table_name="employee_invites")
    op.drop_table("employee_invites")

    # ── permissions ──────────────────────────────────────────────────────────
    op.create_table(
        "permissions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_permissions_key"), "permissions", ["key"], unique=True)

    # ── roles ─────────────────────────────────────────────────────────────────
    op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("business_id", sa.Integer(), nullable=True),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_system", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("business_id", "key", name="uq_role_business_key"),
    )
    # System presets (business_id NULL) are globally unique by key.
    op.create_index(
        "uix_role_system_key", "roles", ["key"], unique=True,
        postgresql_where=sa.text("business_id IS NULL"),
    )

    # ── role_permissions ───────────────────────────────────────────────────────
    op.create_table(
        "role_permissions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("permission_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["permission_id"], ["permissions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("role_id", "permission_id", name="uq_role_permission"),
    )

    # ── users ─────────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("business_id", sa.Integer(), nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=True),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("is_owner", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("refresh_token", sa.Text(), nullable=True),
        sa.Column("last_login", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("employee_id", name="uq_user_employee"),
    )
    op.create_index(op.f("ix_users_business_id"), "users", ["business_id"], unique=False)
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)

    # ── user_branch_roles ──────────────────────────────────────────────────────
    op.create_table(
        "user_branch_roles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("agent_id", sa.Integer(), nullable=False),
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_id"], ["ai_agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "agent_id", name="uq_user_branch"),
    )
    op.create_index(op.f("ix_user_branch_roles_user_id"), "user_branch_roles", ["user_id"], unique=False)
    op.create_index(op.f("ix_user_branch_roles_agent_id"), "user_branch_roles", ["agent_id"], unique=False)

    # ── user_invites ───────────────────────────────────────────────────────────
    op.create_table(
        "user_invites",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("agent_id", sa.Integer(), nullable=False),
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("status", invite_status_enum, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["agent_id"], ["ai_agents.id"]),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"]),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_user_invites_token_hash"), "user_invites", ["token_hash"], unique=True)


def downgrade() -> None:
    # ── Drop the RBAC tables ─────────────────────────────────────────────────
    op.drop_index(op.f("ix_user_invites_token_hash"), table_name="user_invites")
    op.drop_table("user_invites")
    op.drop_index(op.f("ix_user_branch_roles_agent_id"), table_name="user_branch_roles")
    op.drop_index(op.f("ix_user_branch_roles_user_id"), table_name="user_branch_roles")
    op.drop_table("user_branch_roles")
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_index(op.f("ix_users_business_id"), table_name="users")
    op.drop_table("users")
    op.drop_table("role_permissions")
    op.drop_index("uix_role_system_key", table_name="roles")
    op.drop_table("roles")
    op.drop_index(op.f("ix_permissions_key"), table_name="permissions")
    op.drop_table("permissions")

    # ── Recreate the old dual-principal auth tables ──────────────────────────
    op.create_table(
        "employee_invites",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("status", invite_status_enum, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_employee_invites_token_hash"), "employee_invites", ["token_hash"], unique=True)
    op.create_table(
        "staff_accounts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("refresh_token", sa.Text(), nullable=True),
        sa.Column("last_login", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("employee_id"),
    )
    op.create_index(op.f("ix_staff_accounts_email"), "staff_accounts", ["email"], unique=True)
