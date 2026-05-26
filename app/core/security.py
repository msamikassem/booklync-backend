"""
app/core/security.py

All authentication helpers:
  - Password hashing and verification (bcrypt)
  - JWT access token creation and decoding
  - Refresh token creation
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext #manages password hashing

from app.core.config import settings

# ── Password hashing ──────────────────────────────────────────────────────────
# bcrypt is the industry standard — slow by design to resist brute force
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain_password: str) -> str:
    """Hash a plain text password. Store the result, never the plain text."""
    return pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Return True if the plain password matches the stored hash."""
    return pwd_context.verify(plain_password, hashed_password)


# ── JWT tokens ────────────────────────────────────────────────────────────────

def create_access_token(subject: str | int, expires_delta: Optional[timedelta] = None) -> str:
    """
    Create a short-lived JWT access token.

    Args:
        subject: The business ID or identifier to encode in the token.
        expires_delta: Custom expiry. Defaults to ACCESS_TOKEN_EXPIRE_MINUTES.

    Returns:
        Encoded JWT string.
    """
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    payload = {
        "sub": str(subject),   # subject — who this token belongs to
        "exp": expire,         # expiry timestamp
        "type": "access",      # token type guard
    }
    # Creates a JWT token
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_refresh_token(subject: str | int) -> str:
    """
    Create a long-lived refresh token used to issue new access tokens.

    Refresh tokens are stored (hashed) in the database so they can be revoked.
    """
    expire = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {
        "sub": str(subject),
        "exp": expire,
        "type": "refresh",
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    """
    Decode and validate a JWT token.

    Returns the payload dict if valid, None if expired or tampered.
    """
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload
    except JWTError:
        return None
