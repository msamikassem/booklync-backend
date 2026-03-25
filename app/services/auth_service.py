"""
app/services/auth_service.py

All authentication business logic lives here.

The endpoints call these functions.
These functions talk to the database.
No route logic here. No HTTP concepts here. Just pure business logic.
"""

from sqlalchemy.orm import Session
from loguru import logger

from app.models.models import Business
from app.schemas.auth import BusinessRegister, BusinessLogin
from app.core.security import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
)
from app.core.exceptions import (
    ConflictException,
    UnauthorizedException,
    NotFoundException,
    BusinessInactiveException,
)


# ─────────────────────────────────────────────────────────────────────────────
# REGISTER
# ─────────────────────────────────────────────────────────────────────────────

def register(db: Session, data: BusinessRegister) -> dict:
    """
    Register a new business account.

    Steps:
    1. Check email not already taken
    2. Check phone not already taken
    3. Hash the password
    4. Create the business record
    5. Generate tokens
    6. Store refresh token
    7. Return tokens

    Args:
        db:   database session from get_db()
        data: validated BusinessRegister schema

    Returns:
        dict with access_token and refresh_token

    Raises:
        ConflictException: if email or phone already registered
    """

    # Step 1 — check email is not already registered
    existing_email = db.query(Business).filter(
        Business.email == data.email
    ).first()

    if existing_email:
        raise ConflictException(
            detail="An account with this email already exists",
            code="EMAIL_TAKEN"
        )

    # Step 2 — check phone is not already registered
    existing_phone = db.query(Business).filter(
        Business.phone == data.phone
    ).first()

    if existing_phone:
        raise ConflictException(
            detail="An account with this phone number already exists",
            code="PHONE_TAKEN"
        )

    # Step 3 — hash the password
    # never store plain text passwords
    hashed = hash_password(data.password)

    # Step 4 — create the business record
    business = Business(
        name=data.name,
        email=data.email,
        password_hash=hashed,
        phone=data.phone,
        business_type_id=data.business_type_id,
        subscription_plan="free",
        active=True,            # set to False if you want email verification first
    )

    db.add(business)
    db.flush()      # flush sends the INSERT to the database but doesn't commit yet
                    # this gives us the auto-generated business.id we need for tokens

    # Step 5 — generate tokens
    access_token = create_access_token(subject=business.id)
    refresh_token = create_refresh_token(subject=business.id)

    # Step 6 — store hashed refresh token in database
    # we store it so we can revoke it on logout
    business.refresh_token = hash_password(refresh_token)

    # Step 7 — commit everything to the database
    db.commit()
    db.refresh(business)

    logger.info(f"New business registered: {business.email} (id={business.id})")

    # Step 8 — return tokens
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
    }


# ─────────────────────────────────────────────────────────────────────────────
# LOGIN
# ─────────────────────────────────────────────────────────────────────────────

def login(db: Session, data: BusinessLogin) -> dict:
    """
    Log in an existing business.

    Steps:
    1. Find business by email
    2. Verify password
    3. Check account is active
    4. Generate new tokens
    5. Store new refresh token
    6. Update last_login timestamp
    7. Return tokens

    Args:
        db:   database session
        data: validated BusinessLogin schema

    Returns:
        dict with access_token and refresh_token

    Raises:
        UnauthorizedException: if email not found or password wrong
        BusinessInactiveException: if account is inactive
    """

    # Step 1 — find business by email
    business = db.query(Business).filter(
        Business.email == data.email
    ).first()

    # Step 2 — verify password
    # we use the same error message for both "email not found" and "wrong password"
    # this prevents attackers from knowing which emails are registered
    if not business or not verify_password(data.password, business.password_hash):
        raise UnauthorizedException(
            detail="Invalid email or password",
            code="INVALID_CREDENTIALS"
        )

    # Step 3 — check account is active
    if not business.active:
        raise BusinessInactiveException()

    # Step 4 — generate new tokens
    access_token = create_access_token(subject=business.id)
    refresh_token = create_refresh_token(subject=business.id)

    # Step 5 — store hashed refresh token
    business.refresh_token = hash_password(refresh_token)

    # Step 6 — update last login timestamp
    from sqlalchemy.sql import func
    business.last_login = func.now()

    db.commit()

    logger.info(f"Business logged in: {business.email} (id={business.id})")

    # Step 7 — return tokens
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
    }


# ─────────────────────────────────────────────────────────────────────────────
# REFRESH TOKEN
# ─────────────────────────────────────────────────────────────────────────────

def refresh_access_token(db: Session, refresh_token: str) -> dict:
    """
    Issue a new access token using a valid refresh token.

    Steps:
    1. Decode the refresh token JWT
    2. Find the business in the database
    3. Verify the refresh token matches what's stored
    4. Issue a new access token

    Args:
        db:            database session
        refresh_token: the refresh token string sent by the client

    Returns:
        dict with new access_token

    Raises:
        UnauthorizedException: if token is invalid, expired, or revoked
    """
    from app.core.security import decode_token

    # Step 1 — decode the JWT
    payload = decode_token(refresh_token)

    if not payload:
        raise UnauthorizedException(
            detail="Invalid or expired refresh token",
            code="INVALID_REFRESH_TOKEN"
        )

    # make sure it's actually a refresh token not an access token
    if payload.get("type") != "refresh":
        raise UnauthorizedException(
            detail="Invalid token type",
            code="INVALID_TOKEN_TYPE"
        )

    business_id = payload.get("sub")

    # Step 2 — find the business
    business = db.query(Business).filter(
        Business.id == int(business_id)
    ).first()

    if not business:
        raise UnauthorizedException(
            detail="Business not found",
            code="BUSINESS_NOT_FOUND"
        )

    # Step 3 — verify the refresh token matches what's stored in the database
    # this is what allows us to revoke tokens — if we clear the stored token,
    # this check fails even if the JWT itself is still valid
    if not business.refresh_token or not verify_password(refresh_token, business.refresh_token):
        raise UnauthorizedException(
            detail="Refresh token has been revoked",
            code="TOKEN_REVOKED"
        )

    # Step 4 — issue a new access token
    # refresh token stays the same — no need to replace it yet
    access_token = create_access_token(subject=business.id)

    logger.info(f"Access token refreshed for business id={business.id}")

    return {
        "access_token": access_token,
        "token_type": "bearer",
    }


# ─────────────────────────────────────────────────────────────────────────────
# LOGOUT
# ─────────────────────────────────────────────────────────────────────────────

def logout(db: Session, business_id: int) -> dict:
    """
    Log out a business by revoking their refresh token.

    Clears the stored refresh token from the database.
    The access token will expire naturally after 30 minutes.
    Any attempt to use the refresh token after this will fail.

    Args:
        db:          database session
        business_id: id of the business to log out

    Returns:
        dict with success message
    """
    business = db.query(Business).filter(
        Business.id == business_id
    ).first()

    if not business:
        raise NotFoundException(detail="Business not found")

    # clear the refresh token — it can never be used again
    business.refresh_token = None
    db.commit()

    logger.info(f"Business logged out: id={business_id}")

    return {"message": "Logged out successfully"}


# ─────────────────────────────────────────────────────────────────────────────
# GET CURRENT BUSINESS
# ─────────────────────────────────────────────────────────────────────────────

def get_current_business(db: Session, business_id: int) -> Business:
    """
    Fetch the currently logged in business record.
    Used by GET /api/v1/auth/me

    Args:
        db:          database session
        business_id: from the decoded JWT token

    Returns:
        Business SQLAlchemy model instance

    Raises:
        NotFoundException: if business not found
    """
    business = db.query(Business).filter(
        Business.id == business_id
    ).first()

    if not business:
        raise NotFoundException(detail="Business not found")

    return business