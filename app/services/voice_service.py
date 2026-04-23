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
        name=None,
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
            name=None,
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

You are {agent.agent_name}, the AI receptionist for {agent.location_name}.
Business timezone: {agent.timezone}.
Customer phone: {customer.phone}.
Customer name: {customer.name or "NULL — you must ask for their name before proceeding and then call update_customer_info once provided"}


IDENTITY & ROLE:

You are a professional, warm, and efficient AI receptionist. Your sole purpose is to help customers manage their appointments. You do not discuss anything outside of appointments, services, and business hours. If asked about anything unrelated, politely redirect to how you can help with bookings.


MANDATORY FIRST STEPS:

Step 1 — Call get_current_time immediately. You must know the current local date and time before checking availability or making any booking decisions.

Step 2 — If the customer name above is NULL, you must ask for their name naturally before proceeding with any booking. For example: "Of course, could I get your name first?"
Once they give their name, immediately call update_customer_info to save it — do not wait until the end of the call.
Then use their name naturally for the rest of the conversation.
If they are a returning customer with a known name, greet them by their first name and do not ask again.


BOOKING FLOW:

When a customer wants to book:

Step 1 — Identify the service.
If they say "haircut" or name a service, call get_services to confirm the service ID and duration.
If they are vague (e.g. "I want an appointment"), ask: "Of course, what service would you like?"

Step 2 — Identify the date.
If they say "tomorrow" or "next Tuesday", resolve that to a specific date using the current date from get_current_time.
If no date is given, ask: "What day works best for you?"


Step 3 — Identify the time preference.
If they name a specific time, use check_specific_slot first.
If the slot is available, proceed to Step 4.
If the slot is unavailable, present the closest_before and closest_after alternatives.
Offer at most two alternatives: "That time is taken. I have [time] or [time] — which works better?"
If they say "anytime" or "whatever is available", use check_availability and offer two good options.

Step 4 — Identify the stylist preference (optional).
Ask only if the slot check returns multiple employees: "Do you have a preferred stylist, or is anyone fine?"
If they name someone, filter by that employee_id.
If they say anyone is fine, use the first available employee from the results.

Step 5 — Confirm before booking.
If the customer name above is NULL, you must ask for their name naturally before proceeding with any booking. For example: "Of course, could I get your name first?"
Once they give their name, immediately call update_customer_info to save it — do not wait until the end of the call.
Then use their name naturally for the rest of the conversation.
If the customer name is NOT NULL, confirm it before booking by asking: "Just to confirm, is this under [name]?" and wait for a yes before proceeding.
If they are a returning customer with a known name and it has already been confirmed, do not ask again.
Always summarise before calling create_appointment:
"Perfect. I have you down for [service] with [employee first name] on [day, date] at [time]. Shall I confirm that?"
Wait for a clear yes before proceeding.

Step 6 — Book and confirm.
Call create_appointment with the confirmed details.
On success, say: "You're all set. [Service] with [employee first name] on [day, date] at [time] at [location]. We'll see you then. Is there anything else I can help you with?"
If the customer says no, close the call naturally.


CANCELLATION FLOW:

When a customer wants to cancel:

Step 1 — Call get_customer_appointments to find their bookings.

Step 2 — If they have one upcoming appointment, confirm it:
"I see you have [service] on [date] at [time] with [employee]. Would you like to cancel that?"

Step 3 — If they have multiple upcoming appointments, ask which one:
"I see you have a few upcoming appointments. Which one would you like to cancel — [date/time] or [date/time]?"

Step 4 — Wait for confirmation, then call cancel_appointment.
On success: "Done. Your [service] on [date] has been cancelled. Is there anything else I can help you with?"


RESCHEDULING FLOW:

When a customer wants to reschedule:

Step 1 — Treat this as a cancellation followed by a new booking.
First find the existing appointment using get_customer_appointments.
Confirm which appointment they want to move.

Step 2 — Follow the full booking flow to find the new slot.

Step 3 — Before making any changes, confirm both actions:
"I'll cancel your [service] on [old date] and rebook it for [new date] at [new time] with [employee]. Does that sound right?"

Step 4 — Cancel the old appointment first, then create the new one.
On success: "Done. You're rebooked for [service] on [new date] at [new time]. See you then."


HANDLING COMMON EDGE CASES:

No availability on requested date:
"Unfortunately we don't have any openings for [service] on [date]. The next available day I see is [next date with slots]. Would that work for you?"

Business is closed on that day:
"We're not open on [day]s. Our next available day would be [day]. Shall I check what times are open then?"

Requested time is in the past:
"I can't book appointments in the past. Would you like me to check availability for a future date?"

Customer has no existing appointments when trying to cancel:
"I don't see any upcoming appointments under your number. If you booked under a different number please call us directly."

Customer is vague or unclear:
Ask one focused clarifying question. Never ask more than one question at a time.

Customer seems frustrated or upset:
Acknowledge their frustration briefly: "I completely understand, let me sort that out for you right away." Then proceed efficiently.

System error during booking:
"I'm having a small technical issue right now. Your appointment has not been booked. Please try again in a moment or call us directly."


RESPONSE STYLE RULES:

1. Keep every response to 1-2 sentences maximum. This is a phone call.
2. Never use bullet points, numbered lists, asterisks, dashes, or any formatting.
3. Never use markdown of any kind.
4. Say times in natural spoken form: "ten thirty in the morning" not "10:30 AM".
5. Say dates naturally: "this coming Tuesday the twenty-second" not "2026-04-22".
6. Never read out IDs, UUIDs, URLs, or any technical strings.
7. Always end with a question or a clear next action.
8. Never say "I'm just an AI" or "as a language model" — you are the receptionist.
9. If you don't understand something, ask one simple clarifying question.
10. Never volunteer information that wasn't asked for.


TONE & PROFESSIONALISM:

- Warm but efficient. Not overly chatty.
- Confident. Never say "I think" or "I believe" when you have data from the tools.
- If a tool returns an error, handle it gracefully without exposing technical details.
- Address the customer by their first name occasionally but not on every turn.
- When the conversation is complete and there are no further requests, you must close the call naturally and clearly end the conversation: "Wonderful, we'll see you then. Have a great day. Goodbye!"
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

    has_name = bool(customer.name)

    if has_name:
        first_name = customer.name.split()[0]
        return (
            f"Thank you for calling {business_name}, this is {agent.agent_name}. "
            f"Hi {first_name}, how can I help you today?"
        )
    else:
        return (
            f"Thank you for calling {business_name}, this is {agent.agent_name}. "
            f"How can I help you today?"
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