"""
app/core/rbac.py

Canonical definitions for the role-based access-control system:

  • PERMISSIONS  — the catalogue of atomic permission keys
  • ROLE_PRESETS — the four system roles and the permissions each grants

This module is the single source of truth. The database `permissions`, `roles`
and `role_permissions` tables are kept in sync with these definitions by
`rbac_service.sync_rbac_catalogue()`, which runs idempotently on app startup —
so adding a new permission or tweaking a preset is a code change here, not a
manual migration.

Design notes
------------
- The OWNER role is modelled by the `User.is_owner` flag and short-circuits to
  "all permissions on every branch in the tenant". We still seed it with the
  full permission set so the UI can render it and so future demotion is clean.
- A permission is an atomic capability. Whether it applies to "own" vs "branch"
  data (e.g. a staff member updating only their own appointment status) is a
  SCOPING rule enforced in the dependency/service layer — never encoded as a
  separate permission.
"""

from __future__ import annotations

# ── Role keys (system presets) ──────────────────────────────────────────────
ROLE_OWNER          = "owner"
ROLE_BRANCH_MANAGER = "branch_manager"
ROLE_RECEPTIONIST   = "receptionist"
ROLE_STAFF          = "staff"

SYSTEM_ROLE_KEYS = (ROLE_OWNER, ROLE_BRANCH_MANAGER, ROLE_RECEPTIONIST, ROLE_STAFF)


# ── Permission keys ───────────────────────────────────────────────────────────
# Grouped by category for UI display. Add new keys here to extend the system.

# Appointments / calendar
P_MANAGE_APPOINTMENTS     = "manage_appointments"       # create + edit appointments
P_RESCHEDULE_APPOINTMENTS = "reschedule_appointments"   # move appointments in time
P_UPDATE_APPT_STATUS      = "update_appointment_status" # complete / no_show / cancel
P_VIEW_BRANCH_CALENDAR    = "view_branch_calendar"      # see every employee's calendar
P_VIEW_OWN_CALENDAR       = "view_own_calendar"         # see only own calendar

# Customers
P_VIEW_CUSTOMERS    = "view_customers"
P_MANAGE_CUSTOMERS  = "manage_customers"     # create / edit / delete customer records
P_MESSAGE_CUSTOMERS = "message_customers"    # send SMS / chat to customers

# Scheduling & catalogue
P_MANAGE_SCHEDULES = "manage_schedules"      # working hours, breaks, days off
P_MANAGE_SERVICES  = "manage_services"       # services, titles, variations, pricing
P_MANAGE_EMPLOYEES = "manage_employees"      # create / edit / deactivate employees

# Access control
P_INVITE_USERS = "invite_users"              # send dashboard invitations
P_MANAGE_USERS = "manage_users"              # manage users + their branch assignments
P_MANAGE_ROLES = "manage_roles"              # create / edit custom roles

# Business-wide
P_MANAGE_BRANCHES   = "manage_branches"      # create / configure branches (agents)
P_MANAGE_AUTOMATIONS = "manage_automations"  # AI messaging / automation config
P_ACCESS_ANALYTICS  = "access_analytics"
P_ACCESS_BILLING    = "access_billing"
P_MANAGE_SETTINGS   = "manage_settings"      # global company settings


# Catalogue: (key, description, category)
PERMISSIONS: list[dict] = [
    {"key": P_MANAGE_APPOINTMENTS,     "description": "Create and edit appointments",                       "category": "appointments"},
    {"key": P_RESCHEDULE_APPOINTMENTS, "description": "Reschedule (move) appointments",                     "category": "appointments"},
    {"key": P_UPDATE_APPT_STATUS,      "description": "Update appointment status (complete, no-show, cancel)", "category": "appointments"},
    {"key": P_VIEW_BRANCH_CALENDAR,    "description": "View the whole branch calendar and all employees",   "category": "appointments"},
    {"key": P_VIEW_OWN_CALENDAR,       "description": "View own calendar and appointments only",            "category": "appointments"},

    {"key": P_VIEW_CUSTOMERS,          "description": "View customer records",                              "category": "customers"},
    {"key": P_MANAGE_CUSTOMERS,        "description": "Create, edit and delete customer records",           "category": "customers"},
    {"key": P_MESSAGE_CUSTOMERS,       "description": "Send messages to customers",                         "category": "customers"},

    {"key": P_MANAGE_SCHEDULES,        "description": "Manage working hours, breaks and days off",          "category": "scheduling"},
    {"key": P_MANAGE_SERVICES,         "description": "Manage services, titles, variations and pricing",    "category": "catalogue"},
    {"key": P_MANAGE_EMPLOYEES,        "description": "Create, edit and deactivate employees",              "category": "employees"},

    {"key": P_INVITE_USERS,            "description": "Invite people to dashboard accounts",                "category": "access"},
    {"key": P_MANAGE_USERS,            "description": "Manage dashboard users and branch assignments",      "category": "access"},
    {"key": P_MANAGE_ROLES,            "description": "Create and edit custom roles",                       "category": "access"},

    {"key": P_MANAGE_BRANCHES,         "description": "Create and configure branches",                      "category": "business"},
    {"key": P_MANAGE_AUTOMATIONS,      "description": "Configure AI automations and messaging",             "category": "business"},
    {"key": P_ACCESS_ANALYTICS,        "description": "View analytics and reports",                         "category": "business"},
    {"key": P_ACCESS_BILLING,          "description": "View and manage billing and subscriptions",          "category": "business"},
    {"key": P_MANAGE_SETTINGS,         "description": "Manage global company settings",                     "category": "business"},
]

ALL_PERMISSION_KEYS: set[str] = {p["key"] for p in PERMISSIONS}


# ── Role presets ──────────────────────────────────────────────────────────────
# permissions == "*" means "every permission" (owner). The resolver also
# short-circuits owners via User.is_owner, but we still seed the full set.

ROLE_PRESETS: dict[str, dict] = {
    ROLE_OWNER: {
        "name": "Owner",
        "description": "Full platform access across every branch, including billing and settings.",
        "permissions": "*",
    },
    ROLE_BRANCH_MANAGER: {
        "name": "Branch Manager",
        "description": "Runs assigned branches: appointments, schedules, services, employees. No billing or global settings.",
        "permissions": {
            P_MANAGE_APPOINTMENTS,
            P_RESCHEDULE_APPOINTMENTS,
            P_UPDATE_APPT_STATUS,
            P_VIEW_BRANCH_CALENDAR,
            P_VIEW_CUSTOMERS,
            P_MANAGE_SCHEDULES,
            P_MANAGE_SERVICES,
            P_MANAGE_EMPLOYEES,
            P_INVITE_USERS,
        },
    },
    ROLE_RECEPTIONIST: {
        "name": "Receptionist",
        "description": "Front desk: create/edit/reschedule bookings and message customers. No employee or service management.",
        "permissions": {
            P_MANAGE_APPOINTMENTS,
            P_RESCHEDULE_APPOINTMENTS,
            P_UPDATE_APPT_STATUS,
            P_VIEW_BRANCH_CALENDAR,
            P_VIEW_CUSTOMERS,
            P_MANAGE_CUSTOMERS,
            P_MESSAGE_CUSTOMERS,
        },
    },
    ROLE_STAFF: {
        "name": "Staff",
        "description": "Sees only their own calendar and can update the status of their own appointments.",
        "permissions": {
            P_VIEW_OWN_CALENDAR,
            P_UPDATE_APPT_STATUS,
        },
    },
}


def resolved_permissions(role_key: str) -> set[str]:
    """Return the concrete permission-key set a preset grants ('*' → all keys)."""
    preset = ROLE_PRESETS[role_key]
    perms = preset["permissions"]
    return set(ALL_PERMISSION_KEYS) if perms == "*" else set(perms)
