"""
app/core/cors.py

Smart CORS middleware that applies two different policies:

  /api/v1/public/*  →  Access-Control-Allow-Origin: *
                        (booking pages loaded from custom domains need this)

  Everything else   →  only origins in settings.CORS_ORIGINS are allowed
                        (dashboard, admin — only known Booklync frontends)

Why not use Starlette's built-in CORSMiddleware?
  It applies one policy to every route. Supporting custom domains on public
  endpoints requires *, but using * globally would loosen authenticated routes
  unnecessarily. This middleware applies the right policy per path prefix.

Security note on allow_credentials:
  Auth uses Authorization: Bearer headers, not cookies. Browsers do not
  auto-send custom headers cross-origin, so CSRF is not possible regardless
  of the allow_origins policy. allow_credentials=true is kept only for
  private routes so the browser can include it in future cookie-based flows
  if needed.
"""

from starlette.datastructures import Headers
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send
from typing import Sequence

_PUBLIC_PREFIX = "/api/v1/public/"

_PREFLIGHT_ALLOW_HEADERS = "Content-Type, Authorization"
_PREFLIGHT_ALLOW_METHODS = "GET, POST, PUT, DELETE, PATCH, OPTIONS"
_PREFLIGHT_MAX_AGE       = "3600"


class SmartCORSMiddleware:
    def __init__(self, app: ASGIApp, private_origins: Sequence[str]) -> None:
        self.app             = app
        self.private_origins = set(private_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers   = Headers(scope=scope)
        origin    = headers.get("origin", "")
        path      = scope.get("path", "")
        method    = scope["method"]
        is_public = path.startswith(_PUBLIC_PREFIX)

        # ── Preflight (OPTIONS) ───────────────────────────────────────────
        if method == "OPTIONS" and origin:
            allowed = self._resolve_origin(origin, is_public)
            resp_headers: dict[str, str] = {
                "Access-Control-Allow-Methods": _PREFLIGHT_ALLOW_METHODS,
                "Access-Control-Allow-Headers": _PREFLIGHT_ALLOW_HEADERS,
                "Access-Control-Max-Age":       _PREFLIGHT_MAX_AGE,
            }
            if allowed:
                resp_headers["Access-Control-Allow-Origin"] = allowed
                if not is_public:
                    resp_headers["Access-Control-Allow-Credentials"] = "true"
                    resp_headers["Vary"] = "Origin"
            await Response(status_code=204, headers=resp_headers)(scope, receive, send)
            return

        # ── Actual request ────────────────────────────────────────────────
        if not origin:
            # Same-origin or non-browser — no CORS headers needed.
            await self.app(scope, receive, send)
            return

        allowed = self._resolve_origin(origin, is_public)

        async def send_with_cors(message: dict) -> None:
            if message["type"] == "http.response.start" and allowed:
                extra = [(b"access-control-allow-origin", allowed.encode())]
                if not is_public:
                    extra.append((b"access-control-allow-credentials", b"true"))
                    extra.append((b"vary", b"Origin"))
                message = {**message, "headers": list(message.get("headers", [])) + extra}
            await send(message)

        await self.app(scope, receive, send_with_cors)

    def _resolve_origin(self, origin: str, is_public: bool) -> str:
        """Return the value to use for Access-Control-Allow-Origin, or '' to deny."""
        if is_public:
            return "*"
        return origin if origin in self.private_origins else ""
