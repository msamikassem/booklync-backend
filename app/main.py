"""
app/main.py

FastAPI application factory.

This file:
  1. Creates the FastAPI app instance
  2. Configures CORS (which frontends are allowed to call our API)
  3. Registers all exception handlers
  4. Mounts all API routers
  5. Sets up startup / shutdown lifecycle events
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from loguru import logger

from app.core.config import settings
from app.core.logging import setup_logging
from app.core.exceptions import register_exception_handlers

# ── Routers — uncomment each as you build them ────────────────────────────────
from app.api.v1.endpoints import auth,agents,employees,services
# from app.api.v1.endpoints import auth, businesses, agents, employees
# from app.api.v1.endpoints import services, appointments, customers, availability
# from app.api.v1.endpoints import twilio_webhook


# ── Lifespan: runs on startup and shutdown ────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Code before `yield` runs on startup.
    Code after `yield` runs on shutdown.
    """
    # Startup
    setup_logging()
    logger.info(f"Starting {settings.APP_NAME} — env={settings.APP_ENV}")

    yield

    # Shutdown
    logger.info(f"Shutting down {settings.APP_NAME}")


# ── App factory ───────────────────────────────────────────────────────────────
def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version="1.0.0",
        description="AI-powered appointment booking platform API",
        # Hide docs in production — never expose your API schema publicly
        docs_url="/docs" if settings.DEBUG else None,
        redoc_url="/redoc" if settings.DEBUG else None,
        openapi_url="/openapi.json" if settings.DEBUG else None,
        lifespan=lifespan,
    )

    # ── CORS ──────────────────────────────────────────────────────────────
    # Controls which origins (domains) are allowed to call the API.
    # In production this should be ONLY your Next.js domain.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,       # allow cookies and auth headers
        allow_methods=["*"],          # GET, POST, PUT, DELETE, etc.
        allow_headers=["*"],          # Content-Type, Authorization, etc.
    )

    # ── Trusted host (production security) ────────────────────────────────
    # Rejects requests with a Host header that doesn't match.
    # Prevents HTTP Host header attacks.
    if not settings.DEBUG:
        app.add_middleware(
            TrustedHostMiddleware,
            allowed_hosts=[
                settings.API_BASE_URL.replace("https://", "").replace("http://", ""),
                "localhost",
            ],
        )

    # ── Exception handlers ────────────────────────────────────────────────
    register_exception_handlers(app)

    # ── Routers ───────────────────────────────────────────────────────────
    # All routes live under /api/v1/ — versioning from day one means
    # you can release /api/v2/ later without breaking existing clients.
    #
    # Uncomment each router as you build the corresponding module:
    #
    app.include_router(auth.router,         prefix="/api/v1/auth",         tags=["Auth"])
    # app.include_router(businesses.router,   prefix="/api/v1/businesses",   tags=["Businesses"])
    app.include_router(agents.router,       prefix="/api/v1/agents",       tags=["Agents"])
    app.include_router(employees.router,    prefix="/api/v1/employees",    tags=["Employees"])
    app.include_router(services.router,     prefix="/api/v1/services",     tags=["Services"])
    # app.include_router(appointments.router, prefix="/api/v1/appointments", tags=["Appointments"])
    # app.include_router(customers.router,    prefix="/api/v1/customers",    tags=["Customers"])
    # app.include_router(availability.router, prefix="/api/v1/availability", tags=["Availability"])
    # app.include_router(twilio_webhook.router, prefix="/api/v1/twilio",     tags=["Twilio"])

    # ── Health check ──────────────────────────────────────────────────────
    @app.get("/health", tags=["Health"])
    def health_check():
        """
        Simple endpoint to verify the API is running.
        Used by load balancers and monitoring tools.
        """
        return {"status": "ok", "app": settings.APP_NAME, "env": settings.APP_ENV}

    return app


# Create the app instance
app = create_app()
