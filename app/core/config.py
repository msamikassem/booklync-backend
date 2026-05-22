"""
app/core/config.py

Centralised settings — reads every value from the .env file.
Access anywhere: from app.core.config import settings
"""

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List


class Settings(BaseSettings):
    # ── App ───────────────────────────────────────────────────────────────
    APP_NAME: str = "Booking Platform API"
    APP_ENV: str = "development"
    DEBUG: bool = True
    API_BASE_URL: str = "http://localhost:8000"
    FRONTEND_URL: str = "http://localhost:3000"

    # ── Database ──────────────────────────────────────────────────────────
    DATABASE_URL: str
    # Set to True when connecting to Supabase (or any host that requires SSL).
    # Local PostgreSQL typically does not need this.
    DB_USE_SSL: bool = False

    # ── Security ──────────────────────────────────────────────────────────
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # ── Twilio ────────────────────────────────────────────────────────────
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_PHONE_NUMBER: str = ""

    # ── AI Providers ──────────────────────────────────────────────────────
    ANTHROPIC_API_KEY: str = ""
    OPENAI_API_KEY: str = ""

    # ── Vapi (voice) ──────────────────────────────────────────────────────
    VAPI_API_KEY: str = ""
    # Your public server URL — Vapi calls this for webhooks
    # e.g. https://api.yourdomain.com
    VAPI_SERVER_URL: str = ""
    # ElevenLabs voice ID — default is "rachel"
    # Find more at elevenlabs.io/voice-library
    ELEVENLABS_VOICE_ID: str = "rachel"

    # ── Storage ───────────────────────────────────────────────────────────
    # "local"    → save files to UPLOAD_DIR on disk, served via /uploads
    # "supabase" → upload to Supabase Storage bucket
    STORAGE_BACKEND: str = "local"
    UPLOAD_DIR: str = "uploads"
    SUPABASE_URL: str = ""          # required when STORAGE_BACKEND=supabase
    SUPABASE_SERVICE_KEY: str = ""  # service role key (not anon key)

    # ── Logging ───────────────────────────────────────────────────────────
    LOG_LEVEL: str = "INFO"
    LOG_FILE: str = "logs/app.log"

    # ── CORS ──────────────────────────────────────────────────────────────
    @computed_field
    @property
    def CORS_ORIGINS(self) -> List[str]:
        if not self.DEBUG:
            return [self.FRONTEND_URL]
        # Both localhost and 127.0.0.1 are listed — browsers treat them as
        # different origins even though they resolve to the same address.
        return [
            self.FRONTEND_URL,
            "http://localhost:5500",    # VS Code Live Server
            "http://127.0.0.1:5500",
            "http://localhost:8080",    # python -m http.server 8080
            "http://127.0.0.1:8080",
            "http://localhost:3000",    # future Next.js dev server
            "http://127.0.0.1:3000",
        ]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


settings = Settings()