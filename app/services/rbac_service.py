"""
app/services/rbac_service.py

Keeps the database RBAC catalogue (permissions, system roles, and their
permission mappings) in sync with the canonical definitions in
`app/core/rbac.py`. Runs idempotently on every app startup so the four system
role presets and the permission catalogue always reflect the code.

Custom, tenant-owned roles (business_id IS NOT NULL) are never touched here.
"""

from loguru import logger
from sqlalchemy.orm import Session

from app.core import rbac
from app.core.exceptions import NotFoundException
from app.models.models import Permission, Role, RolePermission


def sync_rbac_catalogue(db: Session) -> None:
    """Upsert the permission catalogue and the four system role presets."""
    # ── Permissions ────────────────────────────────────────────────────────
    existing = {p.key: p for p in db.query(Permission).all()}
    for spec in rbac.PERMISSIONS:
        perm = existing.get(spec["key"])
        if perm:
            perm.description = spec["description"]
            perm.category = spec["category"]
        else:
            db.add(Permission(**spec))
    db.flush()

    perm_by_key = {p.key: p for p in db.query(Permission).all()}

    # ── System roles + their permission mappings ─────────────────────────────
    for role_key in rbac.SYSTEM_ROLE_KEYS:
        preset = rbac.ROLE_PRESETS[role_key]
        role = (
            db.query(Role)
            .filter(Role.key == role_key, Role.business_id.is_(None))
            .first()
        )
        if not role:
            role = Role(key=role_key, business_id=None, is_system=True)
            db.add(role)
        role.name = preset["name"]
        role.description = preset["description"]
        role.is_system = True
        db.flush()

        desired = rbac.resolved_permissions(role_key)
        current = {rp.permission.key: rp for rp in role.permissions}

        for key in desired - current.keys():
            db.add(RolePermission(role_id=role.id, permission_id=perm_by_key[key].id))
        for key in current.keys() - desired:
            db.delete(current[key])

    db.commit()
    logger.info(
        f"RBAC catalogue synced: {len(rbac.PERMISSIONS)} permissions, "
        f"{len(rbac.SYSTEM_ROLE_KEYS)} system roles"
    )


def get_system_role(db: Session, role_key: str) -> Role:
    """Return a system role preset by key (e.g. 'branch_manager')."""
    role = (
        db.query(Role)
        .filter(Role.key == role_key, Role.business_id.is_(None))
        .first()
    )
    if not role:
        raise NotFoundException(detail=f"Role '{role_key}' not found", code="ROLE_NOT_FOUND")
    return role


def get_role_for_business(db: Session, role_id: int, business_id: int) -> Role:
    """
    Load a role usable by a tenant: either a system preset (business_id NULL)
    or one of the tenant's own custom roles. Rejects another tenant's role.
    """
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role or (role.business_id is not None and role.business_id != business_id):
        raise NotFoundException(detail="Role not found", code="ROLE_NOT_FOUND")
    return role
