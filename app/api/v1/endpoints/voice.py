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
import json
from fastapi import APIRouter, Depends, Request, Header
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from typing import Optional
from loguru import logger

from app.models.models import AIAgent
from app.db.session import get_db
from app.core.config import settings
from app.services.voice_service import (
    build_vapi_assistant_config,
    handle_vapi_tool_call,
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
    _log_vapi_secret(x_vapi_secret)

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
# ENDPOINT 2 — webhook (tool calls only)
# Vapi calls this when the LLM wants to execute a tool
# We do NOT handle conversation turns here — Vapi owns those
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/vapi/webhook",
    status_code=200,
    summary="Handle Vapi tool call requests",
    include_in_schema=settings.DEBUG,
)
async def vapi_webhook(
    request: Request,
    db: Session = Depends(get_db),
    x_vapi_secret: Optional[str] = Header(None),
):
    """
    Called by Vapi when the LLM wants to execute a server tool.

    The LLM runs entirely on Vapi's side. When it decides to call
    a tool (check availability, book appointment, etc.), Vapi sends
    a function-call webhook here. We execute the tool and return
    the result. Vapi feeds the result back into the conversation.

    We also handle:
        end-of-call-report → log call summary
        all other events   → acknowledge and ignore

    Response format for function-call:
        {"result": "json string of the tool result"}

    Must respond within 20 seconds or Vapi times out.
    Must always return 200 — errors break the call.
    """

    _log_vapi_secret(x_vapi_secret)

    try:
        body = await request.json()
        message = body.get("message", {})
        message_type = message.get("type", "unknown")

        logger.info(f"Vapi webhook: type={message_type}")
        logger.debug(f"Vapi webhook payload: {body}")

        # ── function-call — LLM wants to execute a tool ───────────────
        if message_type in ("function-call", "tool-calls"):
            return await _handle_function_call(db, message)

        # ── end-of-call-report — call finished ────────────────────────
        elif message_type == "end-of-call-report":
            _handle_end_of_call(db,message)
            return JSONResponse(content={"status": "ok"})

        # ── all other events — acknowledge, ignore ─────────────────────
        # We do NOT handle conversation-update here.
        # The LLM runs on Vapi — conversation turns are theirs to manage.
        else:
            logger.debug(f"Vapi webhook: ignoring '{message_type}' — Vapi handles this")
            return JSONResponse(content={"status": "ok"})

    except Exception as e:
        logger.exception(f"Vapi webhook error: {e}")
        # return 200 with error in result — never let Vapi get a non-200
        return JSONResponse(
            content={"result": json.dumps({"error": "Internal server error"})},
            status_code=200,
        )


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT 3 — status updates (call lifecycle logging)
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

    Events: queued, ringing, in-progress, forwarding, ended, failed.
    We log them for analytics and debugging.
    Always returns 200 immediately.
    """

    _log_vapi_secret(x_vapi_secret)

    try:
        body = await request.json()
        message = body.get("message", {})
        status = message.get("status", "unknown")
        call = message.get("call", {})
        call_id = call.get("id", "unknown")
        to_number = call.get("toNumber", "unknown")
        from_number = call.get("fromNumber", "unknown")

        if status in ("ended", "failed"):
            end_reason = message.get("endedReason", "unknown")
            duration = call.get("duration", 0)
            logger.info(
                f"Vapi call {status}: id={call_id} to={to_number} "
                f"from={from_number} reason={end_reason} duration={duration}s"
            )
        else:
            logger.info(
                f"Vapi call status '{status}': id={call_id} "
                f"to={to_number} from={from_number}"
            )

    except Exception as e:
        logger.warning(f"Could not parse Vapi status payload: {e}")

    return JSONResponse(content={"status": "ok"})


# ─────────────────────────────────────────────────────────────────────────────
# INTERNAL — function call handler
# ─────────────────────────────────────────────────────────────────────────────

async def _handle_function_call(db: Session, message: dict) -> JSONResponse:
    """
    Execute a tool call sent by Vapi when the LLM wants a tool result.

    Extracts context from call metadata (agent_id, business_id, customer_id)
    then calls handle_vapi_tool_call which routes to execute_tool.

    Args:
        db:      database session
        message: the message dict from the Vapi webhook payload

    Returns:
        JSONResponse with {"result": "json string"}
    """

    call = message.get("call", {})
    call_id = call.get("id", "unknown")

    # ── Extract tool name, input, and toolCallId ──────────────────────────
    tool_name = ""
    tool_input = {}
    tool_call_id = "unknown"

    function_call = message.get("functionCall", {})
    tool_call_list = message.get("toolCallList", [])

    if function_call:
        tool_name = function_call.get("name", "")
        tool_input = function_call.get("parameters", {})
        tool_call_id = function_call.get("id", "unknown")
    elif tool_call_list:
        first = tool_call_list[0]
        tool_call_id = first.get("id", "unknown")
        fn = first.get("function", {})
        tool_name = fn.get("name", "")
        raw_args = fn.get("arguments", {})
        if isinstance(raw_args, str):
            try:
                tool_input = json.loads(raw_args)
            except json.JSONDecodeError:
                tool_input = {}
        else:
            tool_input = raw_args

    logger.info(f"Vapi function call: call={call_id} tool={tool_name} toolCallId={tool_call_id}")
    logger.debug(f"Tool input: {tool_input}")

    # ── Extract metadata ───────────────────────────────────────────────────
    # Vapi puts assistant metadata at call.assistant.metadata in webhooks,
    # NOT at call.metadata — check both locations.
    metadata = (
        call.get("assistant", {}).get("metadata")
        or call.get("metadata")
        or {}
    )

    logger.debug(f"Metadata resolved: {metadata}")

    agent_id = metadata.get("agent_id")
    business_id = metadata.get("business_id")
    customer_id = metadata.get("customer_id")

    # phone number can be in several places depending on Vapi event type
    customer_phone = (
        metadata.get("customer_phone")
        or call.get("customer", {}).get("number")
        or call.get("fromNumber")
        or ""
    )

    # ── Agent lookup fallback if metadata was missing ──────────────────────
    if not agent_id:
        to_number = (
            call.get("phoneNumber", {}).get("number")
            or call.get("toNumber")
            or ""
        )
        logger.warning(f"Metadata missing agent_id — falling back to phone lookup: {to_number}")
        agent = get_agent_by_phone(db, to_number)
        if not agent:
            logger.error(
                f"Tool call: no agent found. call={call_id} to_number={to_number!r} "
                f"call keys={list(call.keys())} metadata={metadata}"
            )
            return JSONResponse(content={
                "results": [{"toolCallId": tool_call_id, "result": "Error: agent not found"}]
            })
        agent_id = agent.id
        business_id = agent.business_id

    result = handle_vapi_tool_call(
        db=db,
        agent_id=agent_id,
        business_id=business_id,
        customer_id=customer_id,
        customer_phone=customer_phone,
        tool_name=tool_name,
        tool_input=tool_input,
    )

    logger.debug(f"Tool result for '{tool_name}': {result}")

    # Vapi requires this exact format
    return JSONResponse(content={
        "results": [
            {
                "toolCallId": tool_call_id,
                "result": json.dumps(result, default=str),
            }
        ]
    })


def _handle_end_of_call(db: Session, message: dict) -> None:
    """Log end-of-call summary and update agent minutes_used."""

    call = message.get("call", {})
    call_id = call.get("id", "unknown")
    summary = message.get("summary", "No summary")
    end_reason = message.get("endedReason", "unknown")
    recording_url = message.get("recordingUrl")

    # durationMinutes comes directly from the end-of-call-report payload
    duration_minutes = message.get("durationMinutes", 0) or 0

    logger.info(f"Call ended: id={call_id} duration={duration_minutes}min reason={end_reason}")
    logger.info(f"Call summary: {summary}")

    if recording_url:
        logger.info(f"Recording: {recording_url}")

    # ── Update agent minutes_used ──────────────────────────────────────────
    agent_id = call.get("assistant", {}).get("metadata", {}).get("agent_id")

    if agent_id and duration_minutes > 0:
        try:
            agent = db.query(AIAgent).filter(AIAgent.id == agent_id).first()
            if agent:
                # SQLAlchemy line to increment minutes_used
                agent.minutes_used = (agent.minutes_used or 0) + duration_minutes
                db.commit()
                logger.info(f"Agent {agent_id} minutes_used updated: +{duration_minutes:.3f}min (total={agent.minutes_used:.3f})")
            else:
                logger.warning(f"End-of-call: no agent found for id={agent_id}")
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to update agent minutes_used: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# SECURITY HELPER
# ─────────────────────────────────────────────────────────────────────────────

def _log_vapi_secret(secret: Optional[str]) -> None:
    """
    Log a warning if the Vapi secret doesn't match.
    We log but don't reject — Vapi requires 200 responses.
    In a stricter setup you could raise HTTPException here.
    """

    if not settings.VAPI_API_KEY:
        return  # development — skip validation

    if secret != settings.VAPI_API_KEY:
        logger.warning(
            f"Vapi secret mismatch — "
            f"expected ...{settings.VAPI_API_KEY[-4:]} "
            f"got ...{str(secret)[-4:] if secret else 'None'}"
        )