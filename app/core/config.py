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
    # The Next.js web app (dashboard + public sites). Kept distinct from
    # FRONTEND_URL (which is load-bearing for CORS + the accept-invite redirect):
    # used to call the app's on-demand revalidation endpoint after a site is
    # published, and as the canonical origin for public booking links.
    WEB_APP_URL: str = "http://localhost:5173"

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
    # Website-builder image uploads (logos, hero/gallery photos). Larger than the
    # 2MB profile-picture cap. Uses the SAME STORAGE_BACKEND switch above, so
    # flipping to "supabase" later moves these too with no code change.
    SITE_ASSET_MAX_MB: int = 8

    # ── Website builder ───────────────────────────────────────────────────
    # Shared secret the backend sends when asking the Next app to re-render a
    # published site (POST {WEB_APP_URL}/api/revalidate). Must match the value
    # the web app reads from REVALIDATE_SECRET. Empty = revalidation disabled
    # (publish still works; the live page refreshes on its own ISR interval).
    REVALIDATE_SECRET: str = ""
    # How long a signed site-preview token stays valid (editor live preview).
    PREVIEW_TOKEN_EXPIRE_MINUTES: int = 30
    # Cloudflare Turnstile secret for verifying the public booking form. When set,
    # the public booking endpoint requires + verifies a Turnstile token; when
    # blank, the check is skipped (dev/no-op). Pair with the frontend's
    # NEXT_PUBLIC_TURNSTILE_SITE_KEY.
    TURNSTILE_SECRET: str = ""

    # ── Custom domains ────────────────────────────────────────────────────
    # Vercel API credentials — used to programmatically add/remove custom
    # domains so Vercel provisions SSL automatically.
    # Leave blank if not hosting on Vercel.
    VERCEL_TOKEN: str = ""
    VERCEL_PROJECT_ID: str = ""
    VERCEL_TEAM_ID: str = ""   # required only for team-owned Vercel projects
    # The CNAME value users must point their domain at.
    # On Vercel hosting this is always cname.vercel-dns.com.
    CNAME_TARGET: str = "cname.vercel-dns.com"

    # ── Staff invites & email ─────────────────────────────────────────────
    RESEND_API_KEY: str = ""
    # The verified "from" address in Resend (must match your verified domain).
    EMAIL_FROM: str = "Booklync <noreply@booklync.com>"
    # How many hours a pending invite link stays valid.
    INVITE_EXPIRE_HOURS: int = 48

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
            "http://localhost:5173",    # Vite dev server
            "http://127.0.0.1:5173",
        ]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


settings = Settings()