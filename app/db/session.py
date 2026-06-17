"""
app/db/session.py

Database engine + session factory.

SQLAlchemy concepts used here:
  - Engine:      the low-level connection to PostgreSQL
  - SessionLocal: a factory that creates individual database sessions
  - get_db():    a FastAPI dependency that provides one session per request
                 and guarantees it is closed when the request ends
"""

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session
from typing import Generator
from loguru import logger

from app.core.config import settings

# ── Engine ────────────────────────────────────────────────────────────────────
# Supabase (and most hosted PostgreSQL providers) require SSL.
# Set DB_USE_SSL=True in .env when pointing at Supabase.
_connect_args = {"sslmode": "require"} if settings.DB_USE_SSL else {}

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=_connect_args,
    pool_pre_ping=True,     # test connections before using them (handles dropped connections)
    pool_size=5,            # 5 per worker × 4 workers = 20 steady-state connections
    max_overflow=5,         # burst headroom: 10 per worker × 4 workers = 40 max total (under 60 limit)
    pool_recycle=1800,      # recycle every 30 min (Supabase idles out long-lived connections)
    echo=settings.DEBUG,    # log all SQL statements in development
)


@event.listens_for(engine, "connect")
def _force_utc_session(dbapi_connection, connection_record):
    """Pin every DB connection's session timezone to UTC.

    PostgreSQL returns TIMESTAMPTZ values in the *session* timezone, which otherwise
    defaults to the host/server's local timezone. That made `start_time` (and every
    other timestamp) serialize with the machine's offset (e.g. +08:00) instead of UTC,
    violating the API contract that all times are UTC. With the session pinned to UTC,
    every timestamp comes back UTC-aware (+00:00) regardless of where the server runs;
    per-branch local times are still computed explicitly via pytz in the service layer.
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("SET TIME ZONE 'UTC'")
    finally:
        cursor.close()

# ── Session factory ───────────────────────────────────────────────────────────
# autocommit=False: we control when transactions are committed
# autoflush=False:  we control when pending changes are flushed to the DB
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
)


# ── FastAPI dependency ────────────────────────────────────────────────────────
def get_db() -> Generator[Session, None, None]:
    """
    Yields a database session for the duration of a single request.

    Usage in any endpoint:
        from app.db.session import get_db
        from sqlalchemy.orm import Session
        from fastapi import Depends

        @router.get("/something")
        def my_endpoint(db: Session = Depends(get_db)):
            ...

    The session is automatically closed in the finally block even if
    an exception is raised during the request.
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()       # commit if no exception was raised
    except Exception:
        db.rollback()     # roll back on any error — keeps DB consistent
        raise
    finally:
        db.close()        # always release the connection back to the pool
