"""
app/main.py
"""

import os
from contextlib import asynccontextmanager
from pathlib import Path
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from app.core.cors import SmartCORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

# Resolve the frontend directory relative to this file's location
FRONTEND_DIR = Path(__file__).parent.parent.parent / "frontend"

from app.core.config import settings
from app.core.logging import setup_logging
from app.core.exceptions import register_exception_handlers
from app.db.session import SessionLocal

# import all routers
from app.api.v1.endpoints import auth
from app.api.v1.endpoints import agents
from app.api.v1.endpoints import employees
from app.api.v1.endpoints import services
from app.api.v1.endpoints import business_hours
from app.api.v1.endpoints import availability
from app.api.v1.endpoints import customers
from app.api.v1.endpoints import appointments
from app.api.v1.endpoints import twilio_webhook
from app.api.v1.endpoints import chat
from app.api.v1.endpoints import voice
from app.api.v1.endpoints import notifications
from app.api.v1.endpoints import public
from app.api.v1.endpoints import staff_auth
from app.api.v1.endpoints import users
from app.api.v1.endpoints import financials


# ── Scheduler instance ────────────────────────────────────────────────────────
# Created here so it can be started and stopped in the lifespan function
scheduler = BackgroundScheduler()


def _run_reminder_job():
    """
    Wrapper function called by APScheduler every minute.

    Creates its own database session because APScheduler runs in a
    separate thread — it cannot share the request-scoped sessions
    that FastAPI's get_db() provides.

    Always closes the session in the finally block even if an error occurs.
    """
    from app.services.notification_service import run_reminder_check

    db = SessionLocal()
    try:
        result = run_reminder_check(db)
        if result["sent"] > 0:
            logger.info(f"Scheduler: sent {result['sent']} reminders")
    except Exception as e:
        logger.exception(f"Scheduler: reminder job failed: {e}")
    finally:
        db.close()


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    logger.info(f"Starting {settings.APP_NAME} — env={settings.APP_ENV}")

    # ── Sync RBAC catalogue (permissions + system role presets) ───────────
    from app.services.rbac_service import sync_rbac_catalogue
    db = SessionLocal()
    try:
        sync_rbac_catalogue(db)
    except Exception as e:
        logger.exception(f"RBAC catalogue sync failed: {e}")
    finally:
        db.close()

    # ── Start scheduler ───────────────────────────────────────────────────
    scheduler.add_job(
        func=_run_reminder_job,
        trigger=IntervalTrigger(minutes=1),   # runs every minute
        id="reminder_check",
        name="SMS Reminder Check",
        replace_existing=True,
        max_instances=1,    # never run two at the same time
    )
    scheduler.start()
    logger.info("Scheduler started — reminder check every 1 minute")

    yield

    # ── Stop scheduler cleanly on shutdown ────────────────────────────────
    scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped")
    logger.info(f"Shutting down {settings.APP_NAME}")


# ── App factory ───────────────────────────────────────────────────────────────
def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version="1.0.0",
        description="AI-powered appointment booking platform API",
        docs_url="/docs" if settings.DEBUG else None,
        redoc_url="/redoc" if settings.DEBUG else None,
        openapi_url="/openapi.json" if settings.DEBUG else None,
        lifespan=lifespan,
    )

    app.add_middleware(SmartCORSMiddleware, private_origins=settings.CORS_ORIGINS)

    if not settings.DEBUG:
        allowed_hosts = [
            settings.API_BASE_URL.replace("https://", "").replace("http://", ""),
            "localhost",
        ]
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

    register_exception_handlers(app)

    if settings.STORAGE_BACKEND == "local":
        os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
        app.mount("/uploads", StaticFiles(directory=settings.UPLOAD_DIR), name="uploads")

    app.include_router(auth.router,             prefix="/api/v1/auth",          tags=["Auth"])
    app.include_router(agents.router,           prefix="/api/v1/agents",        tags=["Agents"])
    app.include_router(employees.router,        prefix="/api/v1/employees",     tags=["Employees"])
    app.include_router(services.router,         prefix="/api/v1/services",      tags=["Services"])
    app.include_router(business_hours.router,   prefix="/api/v1",               tags=["Business Hours"])
    app.include_router(availability.router,     prefix="/api/v1/availability",  tags=["Availability"])
    app.include_router(customers.router,        prefix="/api/v1/customers",     tags=["Customers"])
    app.include_router(appointments.router,     prefix="/api/v1/appointments",  tags=["Appointments"])
    app.include_router(twilio_webhook.router,   prefix="/api/v1/twilio",        tags=["Twilio"])
    app.include_router(chat.router,             prefix="/api/v1/chat",          tags=["Chat"])
    app.include_router(voice.router,            prefix="/api/v1/voice",         tags=["Voice"])
    app.include_router(notifications.router,    prefix="/api/v1/notifications", tags=["Notifications"])
    app.include_router(public.router,           prefix="/api/v1/public",        tags=["Public"])
    app.include_router(staff_auth.router,       prefix="/api/v1/staff",         tags=["Staff"])
    app.include_router(users.router,            prefix="/api/v1/users",         tags=["Users & Roles"])
    app.include_router(financials.router,       prefix="/api/v1/financials",    tags=["Financials"])

    @app.get("/health", tags=["Health"])
    def health_check():
        return {"status": "ok", "app": settings.APP_NAME, "env": settings.APP_ENV}

    # ── Clean booking-page URLs ────────────────────────────────────────────────
    # /b/{slug}  →  /book.html?slug={slug}
    # In production this is handled by the web server (nginx / Vercel).
    # Here it lets you use localhost:8000/b/luxe-hair-salon during development.
    @app.get("/b/{slug}", include_in_schema=False)
    def booking_page_redirect(slug: str):
        return RedirectResponse(url=f"/book.html?slug={slug}")

    @app.get("/accept-invite", include_in_schema=False)
    def accept_invite_redirect(token: str = ""):
        return RedirectResponse(url=f"{settings.FRONTEND_URL}/accept-invite.html?token={token}")

    # Serve the frontend static files at root (dev only — comes last so all
    # /api/* and named routes above take precedence).
    if FRONTEND_DIR.exists():
        app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")

    return app


app = create_app()