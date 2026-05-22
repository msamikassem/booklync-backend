# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Development server (auto-reloads on file changes)
python run.py

# Run all tests
pytest

# Run a single test file
pytest tests/test_appointments.py

# Run a single test by name
pytest tests/test_appointments.py::test_create_appointment

# Database migrations
alembic upgrade head                              # apply all pending migrations
alembic revision --autogenerate -m "description" # generate a new migration
alembic downgrade -1                             # roll back one migration

# View live logs (production)
sudo journalctl -u booking_platform -f
```

## Environment Setup

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # then fill in values
```

Required `.env` keys: `DATABASE_URL`, `SECRET_KEY`. Set `DB_USE_SSL=True` when connecting to Supabase (or any SSL-required host). Optional: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `VAPI_API_KEY`, `VAPI_SERVER_URL`.

The API docs are at `/docs` (only when `DEBUG=True`). The health check endpoint is `/health`.

## Architecture

This is a FastAPI + PostgreSQL backend for an AI-powered appointment booking SaaS. Each paying client is a **Business**; a business runs one or more **AIAgent** instances (one per branch/location). Each agent has its own Twilio phone number, timezone, employees, services, and business hours.

### Layer structure

```
app/api/v1/endpoints/  → HTTP routers (thin — validate input, call services, return response)
app/services/          → all business logic
app/models/models.py   → all SQLAlchemy ORM models in one file
app/schemas/           → Pydantic request/response schemas (one file per feature)
app/core/              → config, security, dependencies, exceptions, logging
app/db/session.py      → engine, SessionLocal, get_db() dependency
```

### Key data model relationships

- `Business` → many `AIAgent` (branches)
- `AIAgent` → `Employee`, `Service`, `BusinessHour`, `Appointment`, `ConversationLog`
- `Customer` is scoped to a `Business` (unique by `business_id + phone`)
- `Appointment` links `AIAgent`, `Customer`, `Employee`, and `Service`
- `DayOff` applies to either a whole agent (branch closure) or a specific employee — `employee_id IS NULL` means branch-wide
- `BusinessType` holds the base AI system prompt for a vertical (e.g. hair salon vs. dental); `AgentTool` links business types to available AI tools

### AI / SMS flow

Incoming SMS hits the Twilio webhook (`/api/v1/twilio`) → `ai_agent_service.handle_incoming_sms()` → loads conversation history from `ConversationLog.messages` (JSONB) → calls `_run_ai()` which detects the provider from `AIAgent.model` (`claude-*` → Anthropic, `gpt-*` → OpenAI) → runs a tool-use loop → returns reply.

Tool definitions are in `app/services/ai_tools.py` (`TOOLS` list + `execute_tool()`). Adding a new AI tool means adding an entry to `TOOLS` and a handler in `_route_tool()`.

`sms_post_welcome_behavior` on `AIAgent` controls whether the agent replies after the welcome message: `"ai"` (default) runs the full AI loop; `"silent"` suppresses all further replies.

### Voice (Vapi)

Voice calls are handled by Vapi (external service). Our server only: (1) returns assistant config when a call arrives, and (2) executes tool-call webhooks from Vapi. We do **not** run the LLM for voice — Vapi manages the entire conversation loop.

### SMS reminders (APScheduler)

`main.py` starts a `BackgroundScheduler` that calls `notification_service.run_reminder_check()` every minute. This queries upcoming appointments and sends SMS reminders based on configurable time windows. The scheduler creates its own `SessionLocal()` session (cannot share request-scoped sessions). Reminder deduplication is handled via an in-memory set (`_notification_log`).

### Auth

JWT-based. `POST /api/v1/auth/login` returns `access_token` (30 min) and `refresh_token` (30 days). Protected endpoints use `Depends(get_current_business)` from `app/core/dependencies.py`. All business logic is scoped to the authenticated business via `business_id` — never trust a `business_id` from the request body, always use the one from the JWT.

### Error handling

Raise custom exceptions from `app/core/exceptions.py` (`NotFoundException`, `ConflictException`, `SlotUnavailableException`, etc.). They carry a machine-readable `code` string (e.g. `"SLOT_UNAVAILABLE"`) and are automatically converted to JSON responses by the registered handlers. All errors return `{detail, code, status_code}`.

### Database sessions

`get_db()` is a FastAPI dependency that yields one session per request, commits on success, and rolls back + closes on error. Use `flag_modified(obj, "field")` when mutating a JSONB column (like `ConversationLog.messages`) to ensure SQLAlchemy detects the change.

### Migrations

All schema changes go through Alembic. Never use `Base.metadata.create_all()` in production. After changing `models.py`, run `alembic revision --autogenerate` and review the generated file before applying.
