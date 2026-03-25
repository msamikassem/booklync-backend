"""
app/core/dependencies.py

Reusable FastAPI dependencies.

The most important one here is get_current_business.
It reads the JWT token from the request header,
validates it, and returns the logged in business.

Any endpoint that requires login just adds:
    business: Business = Depends(get_current_business)

And FastAPI automatically:
1. Reads the Authorization header
2. Validates the JWT token
3. Fetches the business from the database
4. Passes it to the endpoint

If the token is missing or invalid, it raises UnauthorizedException
before the endpoint even runs.
"""

from fastapi import Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.security import decode_token
from app.core.exceptions import UnauthorizedException
from app.models.models import Business

# HTTPBearer reads the Authorization: Bearer <token> header automatically
bearer_scheme = HTTPBearer()


def get_current_business(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> Business:
    """
    Dependency that protects any endpoint requiring authentication.

    Usage in any endpoint:
        @router.get("/something-private")
        def private_endpoint(business: Business = Depends(get_current_business)):
            # business is the logged in Business object
            # if token is missing or invalid, this endpoint never runs
            return {"name": business.name}

    Steps:
    1. HTTPBearer reads the Authorization header and extracts the token
    2. We decode and validate the JWT
    3. We fetch the business from the database
    4. We return the business object to the endpoint

    Raises:
        UnauthorizedException: if token is missing, invalid, or expired
    """

    # Step 1 — get the raw token string from the Authorization header
    token = credentials.credentials

    # Step 2 — decode and validate the JWT
    payload = decode_token(token)

    if not payload:
        raise UnauthorizedException(
            detail="Invalid or expired token",
            code="INVALID_TOKEN"
        )

    # make sure it's an access token not a refresh token
    if payload.get("type") != "access":
        raise UnauthorizedException(
            detail="Invalid token type",
            code="INVALID_TOKEN_TYPE"
        )

    # get the business id from the token payload
    business_id = payload.get("sub")

    if not business_id:
        raise UnauthorizedException(
            detail="Invalid token payload",
            code="INVALID_TOKEN"
        )

    # Step 3 — fetch the business from the database
    business = db.query(Business).filter(
        Business.id == int(business_id)
    ).first()

    if not business:
        raise UnauthorizedException(
            detail="Business not found",
            code="BUSINESS_NOT_FOUND"
        )

    # check account is still active
    if not business.active:
        raise UnauthorizedException(
            detail="This account is inactive",
            code="ACCOUNT_INACTIVE"
        )

    # Step 4 — return the business object to the endpoint
    return business