"""
run.py

Local entry point for the backend (and, in dev/preview, the frontend too).

    python run.py
        Development (when DEBUG=True in .env): starts three servers in parallel —
          - Backend API     http://localhost:8000   (auto-reload)
          - Legacy frontend http://localhost:8080   (~/booklync-frontend-old/ — reference app)
          - New React app   http://localhost:5173   (frontend/ — `npm run dev`, on-demand
                                                       compile → slower first hit per page)

    python run.py --prod
        Local PRODUCTION PREVIEW: compiles the Next.js app and serves the built
        output, so the dashboard is as fast as the real deployment —
          - Backend API     http://localhost:8000   (no reload)
          - New React app   http://localhost:5173   (frontend/ — `next build` + `next start`)
        Add --skip-build to reuse an existing build and skip the ~20s compile.
        (Production has no hot-reload: rebuild to pick up frontend changes.)
        Keep DEBUG=True in .env for this — otherwise CORS only allows FRONTEND_URL
        and the preview on :5173 is blocked.

For real production the frontend is deployed to Vercel and the backend runs from
the systemd service (`uvicorn app.main:app --workers 4`) — not this script.
"""

import argparse
import atexit
import os
import signal
import subprocess
import sys

import uvicorn
from app.core.config import settings

_child_procs: list[subprocess.Popen] = []


def _spawn(cmd: list[str], **kwargs) -> subprocess.Popen:
    """Start a child server in its own process group and track it for cleanup.

    The frontend runs via `npm`, which spawns `next` as a child; signalling only
    npm would orphan the actual server (it keeps holding :5173). Starting each
    child in its own session (start_new_session=True) lets _kill_children() signal
    the whole process group, so the entire tree goes down together.
    """
    p = subprocess.Popen(cmd, start_new_session=True, **kwargs)
    _child_procs.append(p)
    return p


def _kill_children() -> None:
    # SIGTERM the whole group (npm + next/http.server) for a graceful stop…
    for p in _child_procs:
        if p.poll() is not None:
            continue  # already exited
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    # …then SIGKILL anything that didn't exit in time.
    for p in _child_procs:
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            except Exception:
                pass
        except Exception:
            pass


def _frontend_dir() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend"))


def _node_env() -> dict:
    """A copy of os.environ with the local Node install (~/.local/node/bin) on PATH."""
    env = os.environ.copy()
    node_bin = os.path.expanduser("~/.local/node/bin")
    env["PATH"] = f"{node_bin}:{env.get('PATH', '')}"
    return env


def _start_dev_frontends() -> None:
    """Dev mode: legacy reference app (:8080) + Next dev server (:5173)."""
    # Legacy vanilla-JS frontend on :8080 — relocated to ~/booklync-frontend-old;
    # kept running so the reference app works until it's fully retired at parity.
    old_frontend = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "booklync-frontend-old")
    )
    if os.path.isdir(old_frontend):
        _spawn([sys.executable, "-m", "http.server", "8080"], cwd=old_frontend)
        print("  Legacy frontend → http://localhost:8080")

    # New React frontend on :5173 — `npm run dev` (Turbopack, on-demand compile).
    _spawn(["npm", "run", "dev"], cwd=_frontend_dir(), env=_node_env())
    print("  New React app →  http://localhost:5173  (dev server)")


def _start_prod_frontend(skip_build: bool) -> None:
    """Local production preview: build the Next app, then serve the compiled output."""
    frontend = _frontend_dir()
    env = _node_env()

    # Can't serve a build that doesn't exist — build anyway if asked to skip.
    if skip_build and not os.path.exists(os.path.join(frontend, ".next", "BUILD_ID")):
        print("  No existing production build found — building anyway.")
        skip_build = False

    if not skip_build:
        print("  Building frontend (next build)… ~20s")
        if subprocess.run(["npm", "run", "build"], cwd=frontend, env=env).returncode != 0:
            print("  ✗ Frontend build failed — aborting.", file=sys.stderr)
            sys.exit(1)

    if not settings.DEBUG:
        print(
            f"  ⚠ DEBUG=False — CORS allows only FRONTEND_URL ({settings.FRONTEND_URL}); "
            "the preview on :5173 will be blocked.\n"
            "    Set DEBUG=True in backend/.env for a local preview."
        )

    # `npm start` → `next start -p 5173`, serving the compiled build.
    _spawn(["npm", "start"], cwd=frontend, env=env)
    print("  New React app →  http://localhost:5173  (production build)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Booklync local entry point.")
    parser.add_argument(
        "--prod",
        action="store_true",
        help="Serve the Next.js frontend as a fast production build instead of the dev server.",
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="With --prod, reuse the existing build instead of recompiling (~20s).",
    )
    args = parser.parse_args()

    if args.prod:
        _start_prod_frontend(skip_build=args.skip_build)
        atexit.register(_kill_children)
    elif settings.DEBUG:
        _start_dev_frontends()
        atexit.register(_kill_children)

    try:
        uvicorn.run(
            "app.main:app",
            host="0.0.0.0",
            port=8000,
            # Auto-reload in dev only. A production preview (--prod) runs without it.
            reload=settings.DEBUG and not args.prod,
            # Single worker keeps the APScheduler reminder job from firing per-worker.
            # (Real production sets workers via systemd, not here.)
            workers=1 if (settings.DEBUG or args.prod) else None,
            log_level=settings.LOG_LEVEL.lower(),
        )
    except KeyboardInterrupt:
        # Ctrl+C: uvicorn has already shut down gracefully. Swallow the residual
        # KeyboardInterrupt/CancelledError so the console exits cleanly instead of
        # dumping a traceback. atexit still runs _kill_children() to stop the
        # frontend subprocess(es).
        print("\n  Stopped.")
