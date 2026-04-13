"""
app/services/voice_service.py

Vapi voice integration service.

Two responsibilities:
    1. build_vapi_assistant_config()
       Called when a call arrives. Looks up the agent by phone number
       and returns a full dynamic Vapi assistant config. No hardcoded
       assistants in the Vapi dashboard — every agent gets its own config
       built from the database at call time.

    2. handle_incoming_voice()
       Called when the customer has spoken and we have their transcript.
       Reuses the exact same AI agent logic and tools as SMS, but with
       a voice-specific system prompt wrapper that enforces:
           - Short responses (1-2 sentences max)
           - No markdown, no bullet points, no special characters
           - Natural spoken language
           - Numbers spelled out where helpful
           - No URLs or technical strings

Voice response rules are critical because text-to-speech reads
everything literally. "10:00 AM" sounds fine. "* Available slots:"
sounds terrible.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session
from loguru import logger

from app.models.models import AIAgent, Customer, ConversationLog
from app.core.config import settings
from app.core.exceptions import NotFoundException
from app.services import customer_service
from app.services.ai_tools import TOOLS, execute_tool
from app.services.ai_agent_service import (
    _get_or_create_conversation,
    _build_system_prompt,
    _run_ai,
)


# ─────────────────────────────────────────────────────────────────────────────
# AGENT LOOKUP — shared by config and webhook endpoints
# ─────────────────────────────────────────────────────────────────────────────

def get_agent_by_phone(db: Session, phone_number: str) -> Optional[AIAgent]:
    """
    Look up an active agent by phone number.

    Handles Vapi sending numbers with or without the + prefix.
    Tries both formats before giving up.

    Args:
        db:           database session
        phone_number: the phone number to look up (may or may not have + prefix)

    Returns:
        AIAgent if found, None if not found
    """

    # normalise — ensure + prefix
    normalised = phone_number.strip()
    if not normalised.startswith("+"):
        normalised = "+" + normalised

    # also try without + in case stored that way
    without_plus = normalised.lstrip("+")

    agent = db.query(AIAgent).filter(
        AIAgent.active == True,
        AIAgent.phone_number.in_([normalised, without_plus, phone_number]),
    ).first()

    if agent:
        logger.info(f"Agent found for number {phone_number}: {agent.agent_name} (id={agent.id})")
    else:
        logger.warning(f"No active agent found for number {phone_number}")

    return agent


# ─────────────────────────────────────────────────────────────────────────────
# ASSISTANT CONFIG BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def build_vapi_assistant_config(db: Session, to_number: str, from_number: str) -> dict:
    """
    Build a complete dynamic Vapi assistant config for an incoming call.

    Called by POST /voice/vapi/config when Vapi receives a call.
    Returns a full assistant configuration so Vapi knows how to handle the call.
    No assistants need to be pre-configured in the Vapi dashboard.

    If no agent is found for the number, returns a fallback config that
    politely tells the caller the number is not active. Vapi requires a
    valid response — returning an error would break the call.

    Args:
        db:          database session
        to_number:   the number that received the call (our agent's number)
        from_number: the caller's number (customer)

    Returns:
        dict — full Vapi assistant config
    """

    agent = get_agent_by_phone(db, to_number)

    if not agent:
        logger.warning(f"Returning fallback config for unknown number {to_number}")
        return _build_fallback_config()

    # get or create customer for first message personalisation
    customer = customer_service.get_or_create_customer(
        db=db,
        phone=from_number,
        name=f"Caller {from_number}",
        business_id=agent.business_id,
    )

    # build the first message — what the AI says when the call connects
    first_message = _build_first_message(agent, customer)

    # build voice system prompt — stricter than SMS for spoken word
    system_prompt = _build_voice_system_prompt(agent, customer)

    # detect AI provider from model name
    model_provider, model_name = _detect_model_provider(agent.model)

    # build the full config
    config = {
        "name": agent.agent_name,
        "firstMessage": first_message,
        "firstMessageMode": "assistant-speaks-first",

        # ── Model config ───────────────────────────────────────────────
        "model": {
            "provider": model_provider,
            "model": model_name,
            "systemPrompt": system_prompt,
            "temperature": 0.7,
            "maxTokens": 150,           # keep voice responses short
            "emotionRecognitionEnabled": True,
        },

        # ── Voice config ───────────────────────────────────────────────
        "voice": {
        "provider": "vapi",
        "voiceId": "Elliot",   # just use the name directly
        },
        # ── Server config — our webhook for tool calls ─────────────────
        "serverUrl": f"{settings.VAPI_SERVER_URL}/api/v1/voice/vapi/webhook",
        "serverUrlSecret": settings.VAPI_API_KEY,

        # ── Call behaviour ─────────────────────────────────────────────
        "recordingEnabled": True,
        "endCallFunctionEnabled": True,     # AI can end the call naturally
        "dialKeypadFunctionEnabled": False,
        "silenceTimeoutSeconds": 30,
        "maxDurationSeconds": 600,          # 10 minute max
        "backgroundDenoisingEnabled": True,
        "endCallMessage": "Thank you for calling. Have a great day. Goodbye!",

        # ── Transcriber config ─────────────────────────────────────────
        "transcriber": {
            "provider": "deepgram",
            "model": "nova-2",
            "language": "en",
            "smartFormat": True,
        },

        # ── Metadata — we store agent context for our webhook ──────────
        "metadata": {
            "agent_id": agent.id,
            "business_id": agent.business_id,
            "customer_id": customer.id,
            "timezone": agent.timezone,
        },
    }

    logger.info(
        f"Built Vapi config: agent='{agent.agent_name}' "
        f"model={model_name} voice={settings.ELEVENLABS_VOICE_ID} "
        f"caller={from_number}"
    )

    return config


# ─────────────────────────────────────────────────────────────────────────────
# VOICE MESSAGE HANDLER
# ─────────────────────────────────────────────────────────────────────────────

def handle_incoming_voice(
    db: Session,
    to_number: str,
    from_number: str,
    customer_message: str,
    call_id: str,
) -> str:
    """
    Handle a customer's spoken message during a Vapi call.

    Reuses the exact same AI agent logic and tools as SMS.
    The only difference is the voice system prompt wrapper which
    enforces spoken-word response rules.

    Conversation history is stored per call_id so the AI remembers
    context within the call. Uses the same ConversationLog table as SMS
    but with channel="voice" and the call_id stored in customer_phone
    for uniqueness within the call.

    Args:
        db:               database session
        to_number:        agent's phone number
        from_number:      caller's phone number
        customer_message: transcribed customer speech
        call_id:          Vapi call ID (unique per call)

    Returns:
        str — text for Vapi to speak to the customer
    """

    logger.info(
        f"Voice message: call={call_id} from={from_number} "
        f"message='{customer_message[:60]}'"
    )

    agent = get_agent_by_phone(db, to_number)

    if not agent:
        return "I'm sorry, this service is not currently available. Please call back later."

    # get or create customer
    customer = customer_service.get_or_create_customer(
        db=db,
        phone=from_number,
        name=f"Caller {from_number}",
        business_id=agent.business_id,
    )

    # get or create conversation for this call
    # we use a per-call conversation so each call starts fresh
    # but retains context within the call
    conversation_log = _get_or_create_voice_conversation(
        db, agent, customer, call_id
    )

    messages = conversation_log.messages or []

    # append customer message
    messages.append({
        "role": "user",
        "content": customer_message,
        "ts": datetime.utcnow().isoformat(),
    })

    # build voice system prompt — stricter than SMS
    system_prompt = _build_voice_system_prompt(agent, customer)

    # convert to provider format
    provider_messages = [
        {"role": m["role"], "content": m["content"]}
        for m in messages
    ]

    # run AI — same function as SMS, tools are identical
    ai_response = _run_ai(
        db=db,
        agent=agent,
        customer=customer,
        system_prompt=system_prompt,
        messages=provider_messages,
    )

    # clean the response for voice — remove any markdown that crept in
    voice_response = _clean_for_voice(ai_response)

    # save conversation
    messages.append({
        "role": "assistant",
        "content": voice_response,
        "ts": datetime.utcnow().isoformat(),
    })


    from sqlalchemy.orm.attributes import flag_modified

    conversation_log.messages = messages
    flag_modified(conversation_log, "messages")
    db.add(conversation_log)
    db.commit()

    logger.info(
        f"Voice reply: call={call_id} reply='{voice_response[:80]}'"
    )

    return voice_response


# ─────────────────────────────────────────────────────────────────────────────
# VOICE SYSTEM PROMPT
# ─────────────────────────────────────────────────────────────────────────────

def _build_voice_system_prompt(agent: AIAgent, customer: Customer) -> str:
    """
    Build the voice-specific system prompt.

    Extends the base system prompt with strict voice rules.
    Voice responses have different requirements than SMS:
        - Much shorter (1-2 sentences, not 3)
        - No markdown at all (TTS reads * and # literally)
        - No lists (TTS reads "1. 2. 3." awkwardly)
        - Natural spoken language ("ten o'clock" not "10:00")
        - No URLs or email addresses (unreadable when spoken)
        - End with a question to keep the conversation moving

    Args:
        agent:    AIAgent object
        customer: Customer object

    Returns:
        str — the complete voice system prompt
    """

    # get base prompt from business type
    base_prompt = ""
    if agent.business and agent.business.business_type:
        base_prompt = agent.business.business_type.system_prompt or ""

    return f"""
{base_prompt}

You are {agent.agent_name}, an AI phone receptionist for {agent.location_name}.
The business timezone is {agent.timezone}.
You are speaking with {customer.name}.

Your job is to help customers book, cancel, or reschedule appointments by phone.

CRITICAL VOICE RULES — you are speaking out loud, not writing:
1. Respond in 1 to 2 short sentences maximum. Never longer.
2. Use natural spoken language. Say "ten o'clock in the morning" not "10:00 AM".
3. Never use bullet points, dashes, asterisks, or any formatting characters.
4. Never use markdown — no bold, no headers, no lists.
5. Never read out URLs, email addresses, or IDs.
6. Always end your response with a question to keep the conversation going.
7. When confirming a booking say: the service name, the stylist's first name, the day and date, and the time. Nothing else.
8. If you need to offer multiple time options give at most two. Say "I have ten AM or two PM — which works better?"
9. Always call get_current_time first before checking availability.
10. Never book without the customer clearly saying yes.
11. Be warm, natural, and conversational — like a real receptionist on the phone.
12. If the customer says goodbye or thank you — end the call naturally.
""".strip()


# ─────────────────────────────────────────────────────────────────────────────
# FIRST MESSAGE BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def _build_first_message(agent: AIAgent, customer: Customer) -> str:
    """
    Build the greeting spoken when the call connects.

    Personalises based on whether the customer has booked before.
    Returning customers get a warmer, recognition-based greeting.
    New callers get a standard professional greeting.

    Args:
        agent:    AIAgent object
        customer: Customer object

    Returns:
        str — the first spoken message
    """

    from app.models.models import Appointment
    from sqlalchemy.orm import Session

    # check if returning customer
    # we don't have db here but customer object has appointments relationship
    # use a simple heuristic — if customer was auto-created name is "Caller..."
    is_new = customer.name.startswith("Caller ")

    # extract short business name for natural greeting
    business_name = agent.location_name.split(" - ")[0] if " - " in agent.location_name else agent.location_name

    if is_new:
        return (
            f"Thank you for calling {business_name}, this is {agent.agent_name}. "
            f"How can I help you today?"
        )
    else:
        first_name = customer.name.split()[0] if customer.name else "there"
        return (
            f"Thank you for calling {business_name}, this is {agent.agent_name}. "
            f"Hi {first_name}, how can I help you today?"
        )


# ─────────────────────────────────────────────────────────────────────────────
# VOICE RESPONSE CLEANER
# ─────────────────────────────────────────────────────────────────────────────

def _clean_for_voice(text: str) -> str:
    """
    Remove any markdown or formatting from the AI response.

    Even with strict prompt instructions, LLMs occasionally add
    formatting. This is a safety net that strips it before TTS.

    Args:
        text: raw AI response text

    Returns:
        str — cleaned text safe for text-to-speech
    """

    import re

    # remove markdown bold/italic
    text = re.sub(r'\*+([^*]+)\*+', r'\1', text)

    # remove markdown headers
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)

    # remove bullet points and numbered lists
    text = re.sub(r'^\s*[-*•]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)

    # remove backticks
    text = re.sub(r'`+([^`]+)`+', r'\1', text)

    # collapse multiple newlines to single space
    text = re.sub(r'\n+', ' ', text)

    # collapse multiple spaces
    text = re.sub(r' {2,}', ' ', text)

    return text.strip()


# ─────────────────────────────────────────────────────────────────────────────
# FALLBACK CONFIG
# ─────────────────────────────────────────────────────────────────────────────

def _build_fallback_config() -> dict:
    """
    Return a minimal assistant config when no agent is found.

    Vapi requires a valid config response — returning an error or 404
    would cause the call to fail silently. This config politely tells
    the caller the number is not active and ends the call.

    Returns:
        dict — minimal Vapi assistant config
    """

    return {
        "name": "Receptionist",
        "firstMessage": "Thank you for calling. Unfortunately this service is not currently available. Please try again later. Goodbye.",
        "model": {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "systemPrompt": "This number is not active. Politely tell the caller and end the call immediately.",
            "maxTokens": 50,
        },
        "voice": {
            "provider": "11labs",
            "voiceId": settings.ELEVENLABS_VOICE_ID,
        },
        "endCallFunctionEnabled": True,
        "maxDurationSeconds": 30,
    }


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _detect_model_provider(model_name: str) -> tuple[str, str]:
    """
    Detect the AI provider from the model name.

    Returns (provider_string, model_name) in the format Vapi expects.

    Args:
        model_name: the model field from AIAgent e.g. "gpt-4o", "claude-sonnet-4-20250514"

    Returns:
        tuple of (provider, model) for Vapi config
    """

    model_lower = model_name.lower()

    if model_lower.startswith("gpt") or model_lower.startswith("o1") or model_lower.startswith("o3"):
        return "openai", model_name
    elif model_lower.startswith("claude"):
        return "anthropic", model_name
    elif model_lower.startswith("gemini"):
        return "google", model_name
    else:
        # default to openai for unknown models
        logger.warning(f"Unknown model '{model_name}', defaulting to openai provider")
        return "openai", model_name


def _get_or_create_voice_conversation(
    db: Session,
    agent: AIAgent,
    customer: Customer,
    call_id: str,
) -> ConversationLog:
    """
    Get or create a conversation log specific to this voice call.

    Each call gets its own conversation so context doesn't bleed
    between separate calls from the same customer. The call_id is
    stored in customer_phone field with a prefix to keep it unique.

    Args:
        db:       database session
        agent:    AIAgent
        customer: Customer
        call_id:  Vapi call ID (unique per call)

    Returns:
        ConversationLog
    """

    # use call_id as a unique key for this call's conversation
    call_identifier = f"voice:{call_id}"

    log = db.query(ConversationLog).filter(
        ConversationLog.agent_id == agent.id,
        ConversationLog.customer_id == customer.id,
        ConversationLog.customer_phone == call_identifier,
    ).first()

    if not log:
        log = ConversationLog(
            agent_id=agent.id,
            customer_id=customer.id,
            customer_phone=call_identifier,
            channel="voice",
            messages=[],
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        logger.info(f"New voice conversation created for call {call_id}")

    return log