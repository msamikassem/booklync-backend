"""
app/services/voice_service.py

Vapi voice integration — server-side architecture.

HOW IT WORKS:
    Vapi owns the entire conversation loop:
        Customer speaks → Vapi transcribes (STT)
        → Vapi runs the LLM → LLM decides to call a tool
        → Vapi sends function-call webhook to our server
        → We execute the tool and return the result
        → Vapi continues the conversation with the result
        → LLM responds → Vapi speaks (TTS)

Our server's only job:
    1. Return assistant config when call arrives (config endpoint)
    2. Execute tool calls when Vapi asks (webhook endpoint)

We do NOT run the LLM. We do NOT manage conversation turns.
Vapi handles everything — we just execute tools.

Benefits:
    - No LLM latency on our server
    - Vapi handles streaming, retries, interruptions
    - Our server only does what it's good at — database operations
    - Scales without any changes to our infra
"""

from typing import Optional

from sqlalchemy.orm import Session
from loguru import logger

from app.models.models import AIAgent
from app.core.config import settings
from app.services import customer_service
from app.services.ai_tools import TOOLS


# ─────────────────────────────────────────────────────────────────────────────
# AGENT LOOKUP
# ─────────────────────────────────────────────────────────────────────────────

def get_agent_by_phone(db: Session, phone_number: str) -> Optional[AIAgent]:
    """
    Look up an active agent by phone number.

    Handles Vapi sending numbers with or without the + prefix.
    Tries three variants before giving up.

    Args:
        db:           database session
        phone_number: may or may not have + prefix

    Returns:
        AIAgent if found, None otherwise
    """

    normalised = phone_number.strip()
    if not normalised.startswith("+"):
        normalised = "+" + normalised

    without_plus = normalised.lstrip("+")

    agent = db.query(AIAgent).filter(
        AIAgent.active == True,
        AIAgent.phone_number.in_([normalised, without_plus, phone_number.strip()]),
    ).first()

    if agent:
        logger.info(f"Agent found for {phone_number}: '{agent.agent_name}' (id={agent.id})")
    else:
        logger.warning(f"No active agent for number: {phone_number}")

    return agent


# ─────────────────────────────────────────────────────────────────────────────
# ASSISTANT CONFIG BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def build_vapi_assistant_config(
    db: Session,
    to_number: str,
    from_number: str,
) -> dict:
    """
    Build a complete dynamic Vapi assistant config for an incoming call.

    Tools are embedded as server tools — Vapi calls our webhook
    when the LLM wants to use them. We execute and return the result.
    Vapi continues the conversation using the result.

    If no agent is found returns a polite fallback config.
    Vapi requires a valid config — we cannot return an error.

    Args:
        db:          database session
        to_number:   our agent's number (received the call)
        from_number: the caller's number

    Returns:
        dict — full Vapi assistant config
    """

    agent = get_agent_by_phone(db, to_number)

    if not agent:
        logger.warning(f"Returning fallback config for unknown number {to_number}")
        return _build_fallback_config()

    # get or create customer for greeting personalisation
    customer = customer_service.get_or_create_customer(
        db=db,
        phone=from_number,
        name=f"Caller {from_number}",
        business_id=agent.business_id,
    )

    # detect AI provider
    model_provider, model_name = _detect_model_provider(agent.model)

    # build tools as Vapi server tools
    # Vapi calls our webhook URL when the LLM wants to use a tool
    webhook_url = f"{settings.VAPI_SERVER_URL}/api/v1/voice/vapi/webhook"
    vapi_tools = _build_vapi_server_tools(webhook_url)

    config = {
        "name": agent.agent_name,
        "firstMessage": _build_first_message(agent, customer),
        "firstMessageMode": "assistant-speaks-first",

        # ── Model — Vapi runs this, not our server ─────────────────────
        "model": {
            "provider": model_provider,
            "model": model_name,
            "systemPrompt": _build_voice_system_prompt(agent, customer),
            "temperature": 0.7,
            "maxTokens": 150,               # keep voice responses short
            "emotionRecognitionEnabled": True,
            # Tools embedded here — Vapi knows to call our server for execution
            "tools": vapi_tools,
        },

        # ── Voice — ElevenLabs TTS ─────────────────────────────────────
        "voice": {
            "provider": "vapi",
            "voiceId": "Elliot",   # just use the name directly
        },

        # ── Transcriber — Deepgram STT ─────────────────────────────────
        "transcriber": {
            "provider": "deepgram",
            "model": "nova-2",
            "language": "en",
            "smartFormat": True,
        },

        # ── Server — where Vapi sends tool call webhooks ───────────────
        # This is ONLY for tool calls — Vapi does NOT send conversation
        # turns here. The LLM runs on Vapi's side.
        "server": {
            "url": webhook_url,
            "secret": settings.VAPI_API_KEY,
            "timeoutSeconds": 20,
        },

        # ── Call behaviour ─────────────────────────────────────────────
        "recordingEnabled": True,
        "endCallFunctionEnabled": True,
        "dialKeypadFunctionEnabled": False,
        "silenceTimeoutSeconds": 30,
        "maxDurationSeconds": 600,
        "backgroundDenoisingEnabled": True,
        "endCallMessage": "Thank you for calling. Have a great day. Goodbye!",

        # ── Metadata — passed back in every webhook ────────────────────
        "metadata": {
            "agent_id": agent.id,
            "business_id": agent.business_id,
            "customer_id": customer.id,
            "customer_phone": from_number,
            "timezone": agent.timezone,
        },
    }

    logger.info(
        f"Vapi config built: agent='{agent.agent_name}' "
        f"model={model_name} tools={len(vapi_tools)} "
        f"caller={from_number}"
    )

    return config


# ─────────────────────────────────────────────────────────────────────────────
# TOOL CALL HANDLER
# ─────────────────────────────────────────────────────────────────────────────

def handle_vapi_tool_call(
    db: Session,
    agent_id: int,
    business_id: int,
    customer_id: int,
    customer_phone: str,
    tool_name: str,
    tool_input: dict,
) -> dict:
    """
    Execute a tool call sent by Vapi.

    Called when the LLM (running on Vapi) decides to use a tool.
    We execute the tool using the same logic as SMS and HTTP chat,
    then return the result. Vapi feeds it back into the conversation.

    Args:
        db:            database session
        agent_id:      from the call metadata
        business_id:   from the call metadata
        customer_id:   from the call metadata
        customer_phone: caller's phone number
        tool_name:     which tool the LLM wants to call
        tool_input:    arguments the LLM passed

    Returns:
        dict — the tool result (Vapi JSON-serializes this)
    """

    from app.models.models import AIAgent, Customer
    from app.services.ai_tools import execute_tool

    # load agent and customer from IDs stored in call metadata
    agent = db.query(AIAgent).filter(AIAgent.id == agent_id).first()
    customer = db.query(Customer).filter(Customer.id == customer_id).first()

    if not agent:
        logger.error(f"Tool call: agent id={agent_id} not found")
        return {"error": "Agent not found"}

    if not customer:
        # customer might have been auto-created — try by phone
        customer = customer_service.get_or_create_customer(
            db=db,
            phone=customer_phone,
            name=f"Caller {customer_phone}",
            business_id=business_id,
        )

    return execute_tool(
        db=db,
        agent=agent,
        customer=customer,
        tool_name=tool_name,
        tool_input=tool_input,
    )


# ─────────────────────────────────────────────────────────────────────────────
# VAPI SERVER TOOLS BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def _build_vapi_server_tools(webhook_url: str) -> list:
    """
    Convert our TOOLS definitions into Vapi server tool format.

    Each tool gets a "server" block pointing to our webhook.
    When the LLM calls a tool, Vapi sends a function-call webhook
    to our server URL and waits for our result.

    Vapi server tool format:
    {
        "type": "function",
        "function": {
            "name": "...",
            "description": "...",
            "parameters": {...}
        },
        "server": {
            "url": "https://ourserver.com/webhook",
            "secret": "optional"
        }
    }

    Args:
        webhook_url: our webhook endpoint URL

    Returns:
        list of Vapi-format tool definitions
    """

    vapi_tools = []

    for tool in TOOLS:
        vapi_tool = {
            "type": "function",
            "async": False,
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["input_schema"],
            },
            "server": {
                "url": webhook_url,
                "secret": settings.VAPI_API_KEY,
                "timeoutSeconds" : 20,
            },
        }
        vapi_tools.append(vapi_tool)

    logger.debug(f"Built {len(vapi_tools)} Vapi server tools")
    return vapi_tools


# ─────────────────────────────────────────────────────────────────────────────
# SYSTEM PROMPT
# ─────────────────────────────────────────────────────────────────────────────

def _build_voice_system_prompt(agent: AIAgent, customer) -> str:
    """
    Build the voice-specific system prompt sent to the LLM via Vapi.

    Stricter than SMS — everything is spoken out loud.
    TTS reads markdown and special characters literally.

    Args:
        agent:    AIAgent object
        customer: Customer object

    Returns:
        str — complete system prompt
    """

    base_prompt = ""
    if agent.business and agent.business.business_type:
        base_prompt = agent.business.business_type.system_prompt or ""

    return f"""
{base_prompt}

You are {agent.agent_name}, an AI phone receptionist for {agent.location_name}.
The business timezone is {agent.timezone}.
You are speaking with {customer.name}.

Your job is to help customers book, cancel, or reschedule appointments by phone.

CRITICAL VOICE RULES — you are speaking out loud, not writing text:
1. Respond in 1 to 2 short sentences maximum. Never longer.
2. Use natural spoken language. Say "ten o'clock in the morning" not "10:00 AM".
3. Never use bullet points, dashes, asterisks, numbers lists, or any formatting.
4. Never use markdown of any kind.
5. Never read out IDs, URLs, or email addresses.
6. Always end your response with a question to keep the conversation moving.
7. When confirming a booking say: the service name, the stylist's first name, the day and date, and the time. Nothing else.
8. Offer at most two time alternatives. Say "I have ten AM or two PM, which works for you?"
9. Always call get_current_time first before checking availability or making bookings.
10. Never book without the customer clearly saying yes.
11. Be warm, natural, and conversational — like a real receptionist on the phone.
12. If the customer says goodbye or thank you — end the call naturally.
""".strip()


# ─────────────────────────────────────────────────────────────────────────────
# FIRST MESSAGE
# ─────────────────────────────────────────────────────────────────────────────

def _build_first_message(agent: AIAgent, customer) -> str:
    """
    Build the greeting spoken when the call connects.

    New callers get a standard greeting.
    Returning customers get their first name used.

    Args:
        agent:    AIAgent
        customer: Customer

    Returns:
        str — opening spoken message
    """

    business_name = (
        agent.location_name.split(" - ")[0]
        if " - " in agent.location_name
        else agent.location_name
    )

    is_new = customer.name.startswith("Caller ")

    if is_new:
        return (
            f"Thank you for calling {business_name}, this is {agent.agent_name}. "
            f"How can I help you today?"
        )
    else:
        first_name = customer.name.split()[0]
        return (
            f"Thank you for calling {business_name}, this is {agent.agent_name}. "
            f"Hi {first_name}, how can I help you today?"
        )


# ─────────────────────────────────────────────────────────────────────────────
# FALLBACK CONFIG
# ─────────────────────────────────────────────────────────────────────────────

def _build_fallback_config() -> dict:
    """
    Minimal config returned when no agent is found for the number.
    Politely ends the call. Vapi requires a valid response — never an error.
    """
    return {
        "name": "Receptionist",
        "firstMessage": (
            "Thank you for calling. "
            "Unfortunately this service is not currently available. "
            "Please try again later. Goodbye."
        ),
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
    Detect the Vapi provider string from the model name.

    Returns:
        tuple of (provider, model_name) in Vapi format
    """

    model_lower = model_name.lower()

    if model_lower.startswith(("gpt", "o1", "o3")):
        return "openai", model_name
    elif model_lower.startswith("claude"):
        return "anthropic", model_name
    elif model_lower.startswith("gemini"):
        return "google", model_name
    else:
        logger.warning(f"Unknown model '{model_name}', defaulting to openai")
        return "openai", model_name