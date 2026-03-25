"""
app/api/v1/endpoints/auth.py

Authentication routes.

These endpoints do three things only:
1. Receive the request
2. Call the service
3. Return the response

No business logic here. No database queries here.
All logic lives in services/auth_service.py
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.dependencies import get_current_business
from app.models.models import Business
from app.schemas.auth import (
    BusinessRegister,
    BusinessLogin,
    RefreshTokenRequest,
    TokenResponse,
    AccessTokenResponse,
    BusinessResponse,
    MessageResponse,
)
from app.services import auth_service

# APIRouter is like a mini FastAPI app for this feature
# all routes defined here get the /api/v1/auth prefix from main.py
router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
# REGISTER
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=201,                # 201 = Created (more specific than 200)
    summary="Register a new business",
)
def register(
    data: BusinessRegister,         # Pydantic validates the request body
    db: Session = Depends(get_db),  # database session injected automatically
):
    """
    Register a new business account.

    Returns access and refresh tokens immediately after registration
    so the business is logged in straight away — no separate login needed.
    """
    return auth_service.register(db, data)


# ─────────────────────────────────────────────────────────────────────────────
# LOGIN
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/login",
    response_model=TokenResponse,
    status_code=200,
    summary="Log in to an existing business account",
)
def login(
    data: BusinessLogin,
    db: Session = Depends(get_db),
):
    """
    Log in with email and password.

    Returns access and refresh tokens.
    Store both on the client side:
    - access token: send with every API request in Authorization header
    - refresh token: use to get a new access token when it expires
    """
    return auth_service.login(db, data)


# ─────────────────────────────────────────────────────────────────────────────
# REFRESH TOKEN
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/refresh",
    response_model=AccessTokenResponse,
    status_code=200,
    summary="Get a new access token using a refresh token",
)
def refresh(
    data: RefreshTokenRequest,
    db: Session = Depends(get_db),
):
    """
    Exchange a valid refresh token for a new access token.

    Call this when the access token expires (after 30 minutes).
    The refresh token stays the same — only the access token is replaced.
    """
    return auth_service.refresh_access_token(db, data.refresh_token)


# ─────────────────────────────────────────────────────────────────────────────
# LOGOUT
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/logout",
    response_model=MessageResponse,
    status_code=200,
    summary="Log out — revoke refresh token",
)
def logout(
    db: Session = Depends(get_db),
    # get_current_business validates the JWT and returns the logged in business
    # if the token is missing or invalid, this endpoint never runs
    current_business: Business = Depends(get_current_business),
):
    """
    Log out the currently authenticated business.

    Revokes the refresh token in the database.
    The access token will expire naturally after 30 minutes.
    """
    return auth_service.logout(db, current_business.id)


# ─────────────────────────────────────────────────────────────────────────────
# GET CURRENT BUSINESS (me)
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/me",
    response_model=BusinessResponse,
    status_code=200,
    summary="Get the currently logged in business",
)
def me(
    db: Session = Depends(get_db),
    current_business: Business = Depends(get_current_business),
):
    """
    Returns the profile of the currently authenticated business.

    Useful for the dashboard to load the business name, plan, etc.
    The client calls this once after login to get the business details.
    """
    return auth_service.get_current_business(db, current_business.id)