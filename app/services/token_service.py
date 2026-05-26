"""
app/services/token_service.py

Shared JWT issuance for the unified User identity. Kept separate so both
auth_service (login/register) and user_service (accept-invite) can mint tokens
without importing each other's internals.
"""

from sqlalchemy.orm import Session

from app.core.security import create_access_token, create_refresh_token, hash_password
from app.models.models import User


def issue_tokens(db: Session, user: User) -> dict:
    """Create access + refresh tokens for a user and persist the hashed refresh token.

    Does not commit — the caller owns the transaction.
    """
    access_token = create_access_token(subject=user.id)
    refresh_token = create_refresh_token(subject=user.id)
    user.refresh_token = hash_password(refresh_token)
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
    }
