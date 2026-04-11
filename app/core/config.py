"""
app/core/config.py

Centralised settings — reads every value from the .env file.
Access anywhere in the app with: from app.core.config import settings

Using pydantic-settings means:
  - All env vars are validated on startup
  - Missing required vars raise a clear error immediately
  - Type casting is automatic (e.g. DEBUG=True → bool)
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import AnyHttpUrl, field_validator
from typing import List


class Settings(BaseSettings):
    # ── App ───────────────────────────────────────────────────────────────
    APP_NAME: str = "Booking Platform API"
    APP_ENV: str = "development"          # development | production
    DEBUG: bool = True
    API_BASE_URL: str = "http://localhost:8000"
    FRONTEND_URL: str = "http://localhost:3000"

    # ── Database ──────────────────────────────────────────────────────────
    DATABASE_URL: str

    # ── Security ──────────────────────────────────────────────────────────
    SECRET_KEY: str                        # openssl rand -hex 32
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # ── Twilio ────────────────────────────────────────────────────────────
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_PHONE_NUMBER: str = ""

    # ── AI ────────────────────────────────────────────────────────────────
    ANTHROPIC_API_KEY: str = ""
    OPENAI_API_KEY: str = ""

    # ── Logging ───────────────────────────────────────────────────────────
    LOG_LEVEL: str = "INFO"
    LOG_FILE: str = "logs/app.log"

    # ── CORS origins ──────────────────────────────────────────────────────
    # Built from FRONTEND_URL automatically — no need to set separately
    @property
    def CORS_ORIGINS(self) -> List[str]:
        origins = [self.FRONTEND_URL]
        if self.DEBUG:
            # Allow local development ports when in debug mode
            origins += [
                "http://localhost:3000",
                "http://localhost:3001",
                "http://127.0.0.1:3000",
            ]
        return origins

    # ── Pydantic settings config ───────────────────────────────────────────
    model_config = SettingsConfigDict(
        env_file=".env",          # reads from .env in project root
        env_file_encoding="utf-8",
        case_sensitive=True,      # DATABASE_URL != database_url
        extra="ignore",           # ignore unknown env vars silently
    )


# Single instance imported everywhere — created once on startup
settings = Settings()
