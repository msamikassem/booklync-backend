"""
app/core/logging.py

Centralised logging setup using Loguru.

Loguru is simpler than Python's built-in logging module:
  - Automatic log rotation (no need to configure handlers manually)
  - Coloured console output in development
  - Structured JSON output in production
  - Call from anywhere: from loguru import logger; logger.info("...")
"""

import sys
from loguru import logger
from app.core.config import settings


def setup_logging() -> None:
    """
    Configure loguru. Called once at application startup in main.py.

    Development: coloured human-readable output to console + file.
    Production:  JSON-structured output to file only (easier to parse
                 with log aggregation tools like Datadog, Grafana Loki).
    """
    # Remove the default loguru handler
    logger.remove()

    if settings.DEBUG:
        # ── Development: pretty console output ────────────────────────────
        logger.add(
            sys.stdout,
            level=settings.LOG_LEVEL,
            format=(
                "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
                "<level>{level: <8}</level> | "
                "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
                "<level>{message}</level>"
            ),
            colorize=True,
        )
    else:
        # ── Production: structured JSON to stdout (captured by systemd/docker) ──
        logger.add(
            sys.stdout,
            level=settings.LOG_LEVEL,
            serialize=True,    # outputs JSON lines
        )

    # ── Always write to a rotating log file ───────────────────────────────
    logger.add(
        settings.LOG_FILE,
        level=settings.LOG_LEVEL,
        rotation="10 MB",      # new file when current hits 10MB
        retention="30 days",   # delete files older than 30 days
        compression="zip",     # compress rotated files
        serialize=not settings.DEBUG,  # JSON in production, plain in dev
    )

    logger.info(f"Logging initialised — level={settings.LOG_LEVEL}, env={settings.APP_ENV}")
