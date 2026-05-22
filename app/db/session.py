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
    pool_size=10,           # number of persistent connections in the pool
    max_overflow=20,        # extra connections allowed under high load
    pool_recycle=3600,      # recycle connections after 1 hour (prevents stale connections)
    echo=settings.DEBUG,    # log all SQL statements in development
)

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
