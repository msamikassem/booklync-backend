"""
app/services/ai_agent_service.py

The AI agent core — receives messages and runs the AI with tools.

Tool definitions and execution logic live in ai_tools.py.
This file focuses only on:
    - Receiving incoming messages (SMS or HTTP chat)
    - Managing conversation history
    - Running the AI provider (Claude or GPT-4)
    - Handling the tool use loop
    - Building the system prompt
"""

import json
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session
from loguru import logger

from app.models.models import AIAgent, Customer, ConversationLog
from app.core.config import settings
from app.core.exceptions import NotFoundException
from app.services import customer_service
from app.services.ai_tools import TOOLS, execute_tool


# ─────────────────────────────────────────────────────────────────────────────
# MAIN HANDLER — called by Twilio webhook and HTTP chat endpoint
# ─────────────────────────────────────────────────────────────────────────────

def handle_incoming_sms(
    db: Session,
    to_number: str,
    from_number: str,
    message_body: str,
) -> str:
    """
    Main entry point for incoming SMS messages.

    1. Find agent by Twilio number
    2. Get or create customer
    3. Load or create conversation
    4. Append user message
    5. Run AI with tools
    6. Save updated conversation
    7. Return AI reply

    Args:
        db:           database session
        to_number:    the agent's Twilio number (which number received the SMS)
        from_number:  the customer's phone number
        message_body: the SMS text

    Returns:
        str — the AI's reply to send back via SMS
    """

    logger.info(f"Incoming SMS: from={from_number} to={to_number} body='{message_body[:50]}'")

    # find agent by phone number
    agent = db.query(AIAgent).filter(
        AIAgent.phone_number == to_number,
        AIAgent.active == True,
    ).first()

    if not agent:
        logger.warning(f"No active agent for number {to_number}")
        return "Sorry, this service is currently unavailable."

    # get or create customer
    customer = customer_service.get_or_create_customer(
        db=db,
        phone=from_number,
        name=f"Customer {from_number}",
        business_id=agent.business_id,
    )

    # get or create conversation
    conversation_log = _get_or_create_conversation(db, agent, customer)
    messages = conversation_log.messages or []

    # append user message
    messages.append({
        "role": "user",
        "content": message_body,
        "ts": datetime.utcnow().isoformat(),
    })

    # build system prompt
    system_prompt = _build_system_prompt(agent, customer)

    # convert to provider message format
    provider_messages = [
        {"role": m["role"], "content": m["content"]}
        for m in messages
    ]

    # run AI
    ai_response = _run_ai(
        db=db,
        agent=agent,
        customer=customer,
        system_prompt=system_prompt,
        messages=provider_messages,
    )

    # save updated conversation
    messages.append({
        "role": "assistant",
        "content": ai_response,
        "ts": datetime.utcnow().isoformat(),
    })

    from sqlalchemy.orm.attributes import flag_modified

    conversation_log.messages = messages
    flag_modified(conversation_log, "messages")
    db.add(conversation_log)
    db.commit()

    logger.info(f"AI reply for customer {customer.id}: '{ai_response[:100]}'")

    return ai_response


# ─────────────────────────────────────────────────────────────────────────────
# AI RUNNER — detects provider and runs the tool use loop
# ─────────────────────────────────────────────────────────────────────────────

def _run_ai(
    db: Session,
    agent: AIAgent,
    customer: Customer,
    system_prompt: str,
    messages: list,
) -> str:
    """
    Run the AI model with tools.
    Detects provider from the model name:
        claude-* → Anthropic
        gpt-*    → OpenAI
    """

    model = agent.model.lower()

    if model.startswith("claude"):
        return _run_claude(db, agent, customer, system_prompt, messages)
    elif model.startswith("gpt"):
        return _run_openai(db, agent, customer, system_prompt, messages)
    else:
        logger.error(f"Unknown model type: {agent.model}")
        return "I'm sorry, I'm having trouble processing your request right now."


# ─────────────────────────────────────────────────────────────────────────────
# CLAUDE RUNNER
# ─────────────────────────────────────────────────────────────────────────────

def _run_claude(
    db: Session,
    agent: AIAgent,
    customer: Customer,
    system_prompt: str,
    messages: list,
) -> str:
    """
    Run Claude with the tool use loop.

    Loop:
        send messages → if tool_use → execute tools → append results → repeat
        until end_turn → return final text
    """

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

        # convert tools to Anthropic format
        anthropic_tools = [
            {
                "name": t["name"],
                "description": t["description"],
                "input_schema": t["input_schema"],
            }
            for t in TOOLS
        ]

        current_messages = list(messages)

        while True:
            response = client.messages.create(
                model=agent.model,
                max_tokens=1024,
                system=system_prompt,
                tools=anthropic_tools,
                messages=current_messages,
            )

            if response.stop_reason == "end_turn":
                for block in response.content:
                    if hasattr(block, "text"):
                        return block.text
                return "I'm sorry, I couldn't process your request."

            if response.stop_reason == "tool_use":
                tool_results = []

                for block in response.content:
                    if block.type == "tool_use":
                        result = execute_tool(
                            db=db,
                            agent=agent,
                            customer=customer,
                            tool_name=block.name,
                            tool_input=block.input,
                        )
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result),
                        })

                current_messages.append({
                    "role": "assistant",
                    "content": response.content,
                })
                current_messages.append({
                    "role": "user",
                    "content": tool_results,
                })

            else:
                logger.warning(f"Unexpected Claude stop_reason: {response.stop_reason}")
                return "I'm sorry, I encountered an issue. Please try again."

    except Exception as e:
        logger.exception(f"Claude API error: {e}")
        return "I'm sorry, I'm having trouble right now. Please try again in a moment."


# ─────────────────────────────────────────────────────────────────────────────
# OPENAI RUNNER
# ─────────────────────────────────────────────────────────────────────────────

def _run_openai(
    db: Session,
    agent: AIAgent,
    customer: Customer,
    system_prompt: str,
    messages: list,
) -> str:
    """
    Run GPT-4 with the function calling loop.

    Loop:
        send messages → if tool_calls → execute functions → append results → repeat
        until stop → return final text
    """

    try:
        from openai import OpenAI
        client = OpenAI(api_key=settings.OPENAI_API_KEY)

        # convert tools to OpenAI format
        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                },
            }
            for t in TOOLS
        ]

        current_messages = [
            {"role": "system", "content": system_prompt}
        ] + list(messages)

        while True:
            response = client.chat.completions.create(
                model=agent.model,
                tools=openai_tools,
                messages=current_messages,
            )

            choice = response.choices[0]

            if choice.finish_reason == "stop":
                return choice.message.content or "I'm sorry, I couldn't process your request."

            if choice.finish_reason == "tool_calls":
                current_messages.append(choice.message)

                for tool_call in choice.message.tool_calls:
                    tool_input = json.loads(tool_call.function.arguments)
                    result = execute_tool(
                        db=db,
                        agent=agent,
                        customer=customer,
                        tool_name=tool_call.function.name,
                        tool_input=tool_input,
                    )
                    current_messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(result),
                    })

            else:
                logger.warning(f"Unexpected OpenAI finish_reason: {choice.finish_reason}")
                return "I'm sorry, I encountered an issue. Please try again."

    except Exception as e:
        logger.exception(f"OpenAI API error: {e}")
        return "I'm sorry, I'm having trouble right now. Please try again in a moment."


# ─────────────────────────────────────────────────────────────────────────────
# SYSTEM PROMPT BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def _build_system_prompt(agent: AIAgent, customer: Customer) -> str:
    """
    Build the system prompt for the AI.

    Combines:
        - Base prompt from business type (e.g. "You are a hair salon receptionist")
        - Agent context (name, location, timezone)
        - Customer context (name, phone)
        - Strict behavioural rules
    """

    base_prompt = ""
    if agent.business and agent.business.business_type:
        base_prompt = agent.business.business_type.system_prompt or ""

    return f"""
{base_prompt}

You are {agent.agent_name}, the AI receptionist for {agent.location_name}.
Business timezone: {agent.timezone}.
You are speaking with: {customer.name}.


IDENTITY & ROLE:

You are a professional, warm, and efficient AI receptionist. Your sole purpose is to help customers manage their appointments. You do not discuss anything outside of appointments, services, and business hours. If asked about anything unrelated, politely redirect to how you can help with bookings.


MANDATORY FIRST STEP:

Before doing anything else in every conversation, call get_current_time.
You must know the current local date and time before checking availability or making any booking decisions.


BOOKING FLOW:

When a customer wants to book:

Step 1 — Identify the service.
If they say "haircut" or name a service, call get_services to confirm the service ID and duration.
If they are vague (e.g. "I want an appointment"), ask: "Of course, what service would you like?"
Remember the chosen service ID

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
If they say anyone is fine, use the employee_id tied to the selected time slot.

Step 5 — Confirm before booking.
At this point, lock in and remember the confirmed service_id, employee_id, and start_time. These must not change.
Always summarise before calling create_appointment:
"Perfect. I have you down for [service] with [employee first name] on [day, date] at [time]. Shall I confirm that?"
Wait for a clear yes before proceeding.

Step 6 — Book and confirm.
Call create_appointment with the confirmed details.
On success, say: "You're all set. [Service] with [employee first name] on [day, date] at [time] at [location]. We'll see you then. Is there anything else I can help you with?"


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
1. Keep responses to 2-3 sentences maximum.
2. No bullet points or markdown.
3. Times in plain format: "10:30 AM" is fine for SMS.
4. Dates in plain format: "Tuesday April 22nd".
5. Always end with a question or confirmation.
6. If confirming a booking, put the key details on separate lines:
   Service: Haircut
   With: Sarah
   Date: Tuesday April 22nd
   Time: 10:30 AM
   This is the only exception to the no-formatting rule.


TONE & PROFESSIONALISM:

- Warm but efficient. Not overly chatty.
- Confident. Never say "I think" or "I believe" when you have data from the tools.
- If a tool returns an error, handle it gracefully without exposing technical details.
- Address the customer by their first name occasionally but not on every turn.
- When the conversation is complete, close naturally: "Wonderful, we'll see you then. Have a great day. Goodbye!"
""".strip()


# ─────────────────────────────────────────────────────────────────────────────
# CONVERSATION HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _get_or_create_conversation(
    db: Session,
    agent: AIAgent,
    customer: Customer,
) -> ConversationLog:
    """
    Get the most recent conversation for this customer+agent or create one.
    One rolling conversation per customer per agent.
    """

    log = db.query(ConversationLog).filter(
        ConversationLog.agent_id == agent.id,
        ConversationLog.customer_id == customer.id,
    ).order_by(ConversationLog.created_at.desc()).first()

    if not log:
        log = ConversationLog(
            agent_id=agent.id,
            customer_id=customer.id,
            customer_phone=customer.phone,
            channel="sms",
            messages=[],
        )
        db.add(log)
        db.commit()
        db.refresh(log)

    return log