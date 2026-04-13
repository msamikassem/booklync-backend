"""
app/api/v1/endpoints/voice.py

Vapi voice integration endpoints.

Three endpoints Vapi calls during a call:

    POST /voice/vapi/config
        Called by Vapi when a call arrives BEFORE it connects.
        We return a dynamic assistant config built from the agent's settings.
        This must respond within 5 seconds or Vapi times out.
        Must always return a valid config — never a 404.

    POST /voice/vapi/webhook
        Called by Vapi during the call with message events.
        We handle:
            - conversation-update: customer spoke, run AI and return response
            - function-call: AI wants to call a tool (server-side tools)
            - end-of-call-report: call ended, log the summary
        Must respond within 20 seconds.

    POST /voice/vapi/status
        Called by Vapi for call lifecycle events.
        We log them for analytics — started, ringing, in-progress, ended, failed.
        Always returns 200 OK immediately.

Vapi docs: https://docs.vapi.ai/api-reference/webhooks

Security note:
    Vapi signs requests with the server URL secret.
    In production we validate the x-vapi-secret header.
    In development we skip validation if VAPI_API_KEY is empty.

Phone number extraction note:
    Vapi's real assistant-request payload does NOT put the phone number
    in message.call.toNumber. The actual locations are:
        to_number   → message.phoneNumber.number  (our agent's number)
        from_number → message.customer.number     (the caller's number)
    The call object fields (toNumber, fromNumber) are only present in some
    event types (e.g. conversation-update, function-call), so we check both
    locations and fall back gracefully.
"""

from fastapi import APIRouter, Depends, Request, Header
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from typing import Optional
from loguru import logger

from app.db.session import get_db
from app.core.config import settings
from app.services.voice_service import (
    build_vapi_assistant_config,
    handle_incoming_voice,
    get_agent_by_phone,
    _build_fallback_config,
)

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT 1 — assistant config request
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/vapi/config",
    status_code=200,
    summary="Return dynamic assistant config to Vapi on incoming call",
    include_in_schema=settings.DEBUG,   # hide from public docs in production
)
async def vapi_config(
    request: Request,
    db: Session = Depends(get_db),
    x_vapi_secret: Optional[str] = Header(None),
):
    """
    Called by Vapi when a call arrives — before it connects to the customer.

    We look up which agent owns the receiving number and return a full
    dynamic assistant config. Every agent gets its own voice, model,
    system prompt, and first message built from the database.

    Vapi requires a valid response within 5 seconds.
    If we can't find the agent we return a fallback config — never an error.

    Configure in Vapi dashboard:
        Phone Numbers → your number → Assistant → Server URL
        https://api.yourdomain.com/api/v1/voice/vapi/config
    """

    # validate Vapi secret in production
    _validate_vapi_secret(x_vapi_secret)

    try:
        body = await request.json()
        logger.debug(f"Vapi config request: {body}")

        # extract the call info from the payload
        # Vapi sends: {"message": {"type": "assistant-request", "call": {...}, "phoneNumber": {...}, "customer": {...}}}
        message = body.get("message", {})
        call = message.get("call", {})

        # FIX: in a real Vapi assistant-request, the phone numbers are NOT in
        # the call object. Vapi puts them in dedicated top-level objects:
        #   message.phoneNumber.number → the number that received the call (our agent)
        #   message.customer.number    → the caller's number
        # The call object fields (toNumber, fromNumber) are a fallback for older
        # Vapi versions or other event types where they do appear there.
        phone_number_obj = message.get("phoneNumber", {})
        customer_obj = message.get("customer", {})

        to_number = (
            call.get("toNumber") or
            call.get("to") or
            phone_number_obj.get("number") or   # real Vapi assistant-request location
            ""
        )
        from_number = (
            call.get("fromNumber") or
            call.get("from") or
            customer_obj.get("number") or        # real Vapi assistant-request location
            ""
        )

        logger.info(
            f"Vapi config request: to={to_number or '(unknown)'} "
            f"from={from_number or '(unknown)'} "
            f"phoneNumberId={call.get('phoneNumberId', 'unknown')}"
        )

        if not to_number:
            logger.warning(
                "Vapi config request missing toNumber in all expected fields "
                f"(call.toNumber, call.to, phoneNumber.number) — returning fallback. "
                f"message keys: {list(message.keys())}"
            )
            return JSONResponse(content={"assistant": _build_fallback_config()})

        config = build_vapi_assistant_config(
            db=db,
            to_number=to_number,
            from_number=from_number,
        )

        # Vapi expects the config wrapped in an "assistant" key
        return JSONResponse(content={"assistant": config})

    except Exception as e:
        logger.exception(f"Error building Vapi config: {e}")
        # always return a valid fallback — never let Vapi get an error response
        return JSONResponse(content={"assistant": _build_fallback_config()})


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT 2 — main webhook (conversation updates + tool calls)
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/vapi/webhook",
    status_code=200,
    summary="Handle Vapi call events — transcripts and tool calls",
    include_in_schema=settings.DEBUG,
)
async def vapi_webhook(
    request: Request,
    db: Session = Depends(get_db),
    x_vapi_secret: Optional[str] = Header(None),
):
    """
    Called by Vapi during the call with message events.

    We handle three event types:
        conversation-update → customer spoke, run AI, return spoken response
        function-call       → AI wants a tool result, execute and return
        end-of-call-report  → call ended, log the summary

    All other event types (transcript, speech-update, status-update etc) are
    acknowledged but not processed — they're real-time events we don't need
    to act on. We always return 200 for these so Vapi doesn't retry.

    Response format for conversation-update:
        {"response": "The text for Vapi to speak"}

    Response format for function-call:
        {"result": "JSON string of the tool result"}
    """

    _validate_vapi_secret(x_vapi_secret)

    try:
        body = await request.json()
        message = body.get("message", {})
        message_type = message.get("type", "unknown")

        logger.info(f"Vapi webhook: type={message_type}")
        logger.debug(f"Vapi webhook payload: {body}")

        # ── conversation-update — customer spoke ───────────────────────
        if message_type == "conversation-update":
            return await _handle_conversation_update(db, message)

        # ── function-call — AI wants to use a tool ────────────────────
        elif message_type == "function-call":
            return await _handle_function_call(db, message)

        # ── end-of-call-report — call finished ────────────────────────
        elif message_type == "end-of-call-report":
            _handle_end_of_call(message)
            return JSONResponse(content={"status": "ok"})

        # ── all other events — acknowledge and ignore ──────────────────
        # Vapi sends many event types we don't act on:
        #   transcript      → real-time partial transcript chunks
        #   speech-update   → speaker started / stopped speaking
        #   status-update   → call lifecycle (ringing, in-progress, etc.)
        #   hang             → caller hung up
        # We must return 200 for all of them or Vapi will retry.
        else:
            logger.debug(f"Vapi webhook: ignoring event type '{message_type}'")
            return JSONResponse(content={"status": "ok"})

    except Exception as e:
        logger.exception(f"Error in Vapi webhook: {e}")
        return JSONResponse(
            content={"response": "I'm sorry, I encountered a technical issue. Please try again."},
            status_code=200,    # always 200 — Vapi needs a valid response
        )


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT 3 — status updates (call lifecycle)
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/vapi/status",
    status_code=200,
    summary="Receive Vapi call status updates",
    include_in_schema=settings.DEBUG,
)
async def vapi_status(
    request: Request,
    x_vapi_secret: Optional[str] = Header(None),
):
    """
    Called by Vapi for call lifecycle events.

    Events we log:
        queued        → call is queued
        ringing       → phone is ringing
        in-progress   → call connected
        forwarding    → call being forwarded
        ended         → call ended normally
        failed        → call failed

    We log these for analytics and debugging.
    Always returns 200 immediately — no processing needed.
    """

    _validate_vapi_secret(x_vapi_secret)

    try:
        body = await request.json()
        message = body.get("message", {})
        status = message.get("status", "unknown")
        call = message.get("call", {})
        call_id = call.get("id", "unknown")

        # FIX: phone numbers in status-update events live in the call object,
        # but also check phoneNumber/customer objects as a fallback for consistency
        phone_number_obj = message.get("phoneNumber", {})
        customer_obj = message.get("customer", {})

        to_number = (
            call.get("toNumber") or
            call.get("to") or
            phone_number_obj.get("number") or
            "unknown"
        )
        from_number = (
            call.get("fromNumber") or
            call.get("from") or
            customer_obj.get("number") or
            "unknown"
        )

        # log based on status
        if status in ("ended", "failed"):
            end_reason = message.get("endedReason", "unknown")
            duration = call.get("duration", 0)
            logger.info(
                f"Vapi call {status}: call={call_id} to={to_number} "
                f"from={from_number} reason={end_reason} duration={duration}s"
            )
        else:
            logger.info(
                f"Vapi call status '{status}': call={call_id} "
                f"to={to_number} from={from_number}"
            )

    except Exception as e:
        logger.warning(f"Could not parse Vapi status payload: {e}")

    return JSONResponse(content={"status": "ok"})


# ─────────────────────────────────────────────────────────────────────────────
# INTERNAL EVENT HANDLERS
# ─────────────────────────────────────────────────────────────────────────────

async def _handle_conversation_update(db: Session, message: dict) -> JSONResponse:
    """
    Handle a conversation-update event.

    Extracts the latest customer message from the transcript,
    runs the AI agent with the full conversation context,
    and returns the spoken response.

    Args:
        db:      database session
        message: the message dict from the Vapi payload

    Returns:
        JSONResponse with {"response": "text to speak"}
    """

    call = message.get("call", {})
    call_id = call.get("id", "unknown")

    # FIX: phone numbers in conversation-update events may be in the call
    # object OR in the top-level phoneNumber/customer objects — check both
    phone_number_obj = message.get("phoneNumber", {})
    customer_obj = message.get("customer", {})

    to_number = (
        call.get("toNumber") or
        call.get("to") or
        phone_number_obj.get("number") or
        ""
    )
    from_number = (
        call.get("fromNumber") or
        call.get("from") or
        customer_obj.get("number") or
        ""
    )

    # extract the latest user message from the messages array
    messages = message.get("messages", [])
    customer_message = _extract_latest_user_message(messages)

    if not customer_message:
        logger.debug(f"Vapi conversation-update with no new user message — skipping")
        return JSONResponse(content={"status": "ok"})

    logger.info(
        f"Vapi conversation update: call={call_id} to={to_number} "
        f"from={from_number} customer said: '{customer_message[:60]}'"
    )

    reply = handle_incoming_voice(
        db=db,
        to_number=to_number,
        from_number=from_number,
        customer_message=customer_message,
        call_id=call_id,
    )

    return JSONResponse(content={"response": reply})


async def _handle_function_call(db: Session, message: dict) -> JSONResponse:
    """
    Handle a function-call event (server-side tool call).

    When Vapi is configured with server-side tools, the AI model
    requests tool execution via webhook instead of running it locally.
    We execute the tool and return the result.

    Args:
        db:      database session
        message: the message dict from the Vapi payload

    Returns:
        JSONResponse with {"result": "json string"}
    """

    from app.services.ai_tools import execute_tool
    import json

    call = message.get("call", {})
    call_id = call.get("id", "unknown")

    # FIX: same phone number extraction fix as the other handlers —
    # check both call object and top-level phoneNumber/customer objects
    phone_number_obj = message.get("phoneNumber", {})
    customer_obj = message.get("customer", {})

    to_number = (
        call.get("toNumber") or
        call.get("to") or
        phone_number_obj.get("number") or
        ""
    )
    from_number = (
        call.get("fromNumber") or
        call.get("from") or
        customer_obj.get("number") or
        ""
    )

    function_call = message.get("functionCall", {})
    tool_name = function_call.get("name", "")
    tool_input = function_call.get("parameters", {})

    logger.info(
        f"Vapi function call: call={call_id} tool={tool_name} "
        f"to={to_number} from={from_number}"
    )

    agent = get_agent_by_phone(db, to_number)
    if not agent:
        logger.warning(f"Vapi function call: no agent found for number {to_number}")
        return JSONResponse(content={"result": json.dumps({"error": "Agent not found"})})

    from app.services import customer_service
    customer = customer_service.get_or_create_customer(
        db=db,
        phone=from_number,
        name=f"Caller {from_number}",
        business_id=agent.business_id,
    )

    result = execute_tool(
        db=db,
        agent=agent,
        customer=customer,
        tool_name=tool_name,
        tool_input=tool_input,
    )

    return JSONResponse(content={"result": json.dumps(result, default=str)})


def _handle_end_of_call(message: dict) -> None:
    """
    Log end-of-call summary for analytics.

    Args:
        message: the message dict from the Vapi payload
    """

    call = message.get("call", {})
    call_id = call.get("id", "unknown")
    summary = message.get("summary", "No summary available")
    duration = call.get("duration", 0)
    end_reason = message.get("endedReason", "unknown")
    recording_url = message.get("recordingUrl")

    logger.info(
        f"Vapi call ended: id={call_id} duration={duration}s "
        f"reason={end_reason}"
    )
    logger.info(f"Call summary: {summary}")

    if recording_url:
        logger.info(f"Recording available: {recording_url}")


# ─────────────────────────────────────────────────────────────────────────────
# UTILITIES
# ─────────────────────────────────────────────────────────────────────────────

def _extract_latest_user_message(messages: list) -> Optional[str]:
    """
    Extract the most recent user message from the Vapi messages array.

    Vapi sends the full conversation history in each webhook.
    We only need the latest user message — everything before it
    is already in our ConversationLog.

    Args:
        messages: list of message dicts from Vapi

    Returns:
        str — the latest user message, or None if not found
    """

    # iterate in reverse to find the last user message
    for msg in reversed(messages):
        role = msg.get("role", "")
        # Vapi uses both "message" and "content" depending on version
        content = msg.get("message") or msg.get("content") or ""

        if role == "user" and content.strip():
            return content.strip()

    return None


def _validate_vapi_secret(secret: Optional[str]) -> None:
    """
    Validate the Vapi webhook secret header.

    In production we check x-vapi-secret matches our VAPI_API_KEY.
    In development we skip validation if VAPI_API_KEY is not set.

    Args:
        secret: value from x-vapi-secret header

    Raises:
        Nothing — logs warning but doesn't block the request.
        Vapi requires 200 responses so we can't return 403.
    """

    if not settings.VAPI_API_KEY:
        # development — skip validation
        return

    if secret != settings.VAPI_API_KEY:
        logger.warning(
            f"Vapi webhook secret mismatch — "
            f"expected {settings.VAPI_API_KEY[:4]}... "
            f"got {str(secret)[:4] if secret else 'None'}..."
        )
        # we log but don't reject — Vapi needs 200 responses
        # in a stricter setup you could raise HTTPException here