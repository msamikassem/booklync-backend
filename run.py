"""
run.py

Development server entry point.

For development:
    python run.py

For production, use the systemd service (see nginx.conf notes) which runs:
    uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
"""

import uvicorn
from app.core.config import settings

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        # reload=True restarts the server automatically when you save a file.
        # Only use in development — never in production.
        reload=settings.DEBUG,
        # Number of worker processes.
        # In development: 1 (reload doesn't work with multiple workers).
        # In production: set via systemd command, not here.
        workers=1 if settings.DEBUG else None,
        # Log level passed to uvicorn's own logger.
        log_level=settings.LOG_LEVEL.lower(),
    )
