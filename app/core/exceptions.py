"""
app/core/exceptions.py

Custom exception classes and FastAPI exception handlers.

Why custom exceptions?
  - Gives every error a consistent JSON shape: {detail, code, status_code}
  - Makes it easy to catch specific error types in services
  - The handlers below convert them to proper HTTP responses automatically
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from loguru import logger


# ── Custom exception classes ──────────────────────────────────────────────────

class AppException(Exception):
    """Base exception for all application errors."""
    def __init__(self, detail: str, code: str, status_code: int = 400):
        self.detail = detail
        self.code = code              # machine-readable code e.g. "SLOT_UNAVAILABLE"
        self.status_code = status_code
        super().__init__(detail)


class NotFoundException(AppException):
    """Resource not found — maps to HTTP 404."""
    def __init__(self, detail: str = "Resource not found", code: str = "NOT_FOUND"):
        super().__init__(detail=detail, code=code, status_code=404)


class UnauthorizedException(AppException):
    """Invalid or missing credentials — maps to HTTP 401."""
    def __init__(self, detail: str = "Not authenticated", code: str = "UNAUTHORIZED"):
        super().__init__(detail=detail, code=code, status_code=401)


class ForbiddenException(AppException):
    """Authenticated but not allowed — maps to HTTP 403."""
    def __init__(self, detail: str = "Access forbidden", code: str = "FORBIDDEN"):
        super().__init__(detail=detail, code=code, status_code=403)


class ConflictException(AppException):
    """Conflict with existing data — maps to HTTP 409."""
    def __init__(self, detail: str = "Conflict", code: str = "CONFLICT"):
        super().__init__(detail=detail, code=code, status_code=409)


class SlotUnavailableException(AppException):
    """Requested time slot is not available for booking."""
    def __init__(self, detail: str = "The requested time slot is not available"):
        super().__init__(detail=detail, code="SLOT_UNAVAILABLE", status_code=409)


class BusinessInactiveException(AppException):
    """The business account is not yet active."""
    def __init__(self):
        super().__init__(
            detail="This business account is inactive",
            code="BUSINESS_INACTIVE",
            status_code=403,
        )


# ── Exception handlers — registered in main.py ────────────────────────────────

def register_exception_handlers(app: FastAPI) -> None:
    """Register all exception handlers on the FastAPI app instance."""

    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
        """Handle all our custom AppException subclasses."""
        logger.warning(f"{exc.code}: {exc.detail} | path={request.url.path}")
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": exc.detail,
                "code": exc.code,
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """
        Handle Pydantic validation errors (bad request body / query params).
        Returns a clean list of which fields failed and why.
        """
        errors = [
            {
                "field": " → ".join(str(loc) for loc in err["loc"]),
                "message": err["msg"],
                "type": err["type"],
            }
            for err in exc.errors()
        ]
        logger.warning(f"Validation error on {request.url.path}: {errors}")
        return JSONResponse(
            status_code=422,
            content={
                "detail": "Request validation failed",
                "code": "VALIDATION_ERROR",
                "errors": errors,
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """
        Catch-all for any unhandled exception.
        Logs the full traceback but returns a generic message to the client
        so internal details are never exposed in production.
        """
        logger.exception(f"Unhandled exception on {request.url.path}: {exc}")
        return JSONResponse(
            status_code=500,
            content={
                "detail": "An internal server error occurred",
                "code": "INTERNAL_SERVER_ERROR",
            },
        )
