"""
app/api/v1/endpoints/chat.py

Direct chat endpoint for testing and web chat widget.

Two modes:
    1. Authenticated — requires business login
       Used for testing from Swagger as the business owner.
       Can only chat with agents belonging to the logged in business.

    2. Public — no auth required
       Used for a web chat widget on the customer booking page.
       Only works with active agents.

Both modes use the exact same AI agent logic as the SMS channel.
The only difference is the transport (HTTP vs Twilio SMS).
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from datetime import datetime

from app.db.session import get_db
from app.core import rbac
from app.core.dependencies import CurrentUser, get_current_user
from app.core.exceptions import NotFoundException, ForbiddenException
from app.models.models import AIAgent, ConversationLog
from app.schemas.chat import ChatMessage, ChatMessageResponse, ConversationHistoryResponse
from app.services.ai_agent_service import handle_incoming_sms
from app.services import customer_service

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
# AUTHENTICATED CHAT — for testing from Swagger
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/",
    response_model=ChatMessageResponse,
    status_code=200,
    summary="Send a message to the AI agent (authenticated)",
)
def chat(
    data: ChatMessage,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """
    Send a message to an AI agent and get a reply.

    Use this to test the AI agent from Swagger without needing
    a real phone number or Twilio account.

    Simulates exactly what happens when a customer sends an SMS.
    The conversation is saved to the database just like a real SMS.

    How to test a full booking conversation:
        1. Send: "Hi, I'd like to book a haircut"
        2. Send: "Tomorrow at 10am"
        3. Send: "Yes, confirm the booking"

    Use conversation_id to continue an existing conversation.
    Leave it empty to start a new one or continue the most recent.
    """

    # verify agent belongs to this business
    agent = db.query(AIAgent).filter(
        AIAgent.id == data.agent_id,
    ).first()

    if not agent:
        raise NotFoundException(detail="Agent not found")

    if agent.business_id != current.business_id:
        raise ForbiddenException(detail="You do not have access to this agent")

    current.require(rbac.P_MESSAGE_CUSTOMERS, agent_id=agent.id)
    return _process_chat_message(db, data, agent)


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC CHAT — for web chat widget on booking page
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/public",
    response_model=ChatMessageResponse,
    status_code=200,
    summary="Send a message to the AI agent (public — no auth)",
)
def public_chat(
    data: ChatMessage,
    db: Session = Depends(get_db),
):
    """
    Public chat endpoint — no authentication required.

    Used for embedding a chat widget on the customer-facing booking page.
    Only works with active agents.

    The customer identifies themselves by their phone number.
    If they are a new customer they are created automatically.
    """

    # only active agents for public access
    agent = db.query(AIAgent).filter(
        AIAgent.id == data.agent_id,
        AIAgent.active == True,
    ).first()

    if not agent:
        raise NotFoundException(detail="Agent not found or inactive")

    return _process_chat_message(db, data, agent)


# ─────────────────────────────────────────────────────────────────────────────
# GET CONVERSATION HISTORY
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationHistoryResponse,
    status_code=200,
    summary="Get full conversation history",
)
def get_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """
    Get the full message history for a conversation.

    Useful for reviewing what was said during a test session
    or for the dashboard to show conversation logs.
    """

    log = db.query(ConversationLog).filter(
        ConversationLog.id == conversation_id,
    ).first()

    if not log:
        raise NotFoundException(detail="Conversation not found")

    # verify conversation belongs to this business via agent
    agent = db.query(AIAgent).filter(AIAgent.id == log.agent_id).first()
    if not agent or agent.business_id != current.business_id:
        raise ForbiddenException(detail="You do not have access to this conversation")

    current.require(rbac.P_VIEW_CUSTOMERS, agent_id=agent.id)

    from app.models.models import Customer
    customer = db.query(Customer).filter(Customer.id == log.customer_id).first()

    messages = log.messages or []

    return ConversationHistoryResponse(
        conversation_id=log.id,
        agent_id=log.agent_id,
        agent_name=agent.agent_name if agent else "Unknown",
        customer_id=log.customer_id,
        customer_name=customer.name if customer else "Unknown",
        customer_phone=log.customer_phone,
        channel=log.channel,
        messages=messages,
        total_messages=len(messages),
        created_at=log.created_at,
    )


@router.get(
    "/conversations/",
    status_code=200,
    summary="Get all conversations for an agent",
)
def get_conversations(
    agent_id: int,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """
    Get all conversation logs for an agent.
    Useful for reviewing all customer interactions.
    """

    agent = db.query(AIAgent).filter(AIAgent.id == agent_id).first()
    if not agent:
        raise NotFoundException(detail="Agent not found")
    if agent.business_id != current.business_id:
        raise ForbiddenException(detail="You do not have access to this agent")

    current.require(rbac.P_VIEW_CUSTOMERS, agent_id=agent_id)

    from app.models.models import Customer
    logs = db.query(ConversationLog).filter(
        ConversationLog.agent_id == agent_id,
    ).order_by(ConversationLog.created_at.desc()).all()

    results = []
    for log in logs:
        customer = db.query(Customer).filter(Customer.id == log.customer_id).first()
        messages = log.messages or []
        results.append({
            "conversation_id": log.id,
            "agent_id": log.agent_id,
            "customer_id": log.customer_id,
            "customer_name": customer.name if customer else "Unknown",
            "customer_phone": log.customer_phone,
            "channel": log.channel,
            "total_messages": len(messages),
            "last_message": messages[-1]["content"][:100] if messages else None,
            "created_at": log.created_at,
        })

    return {
        "conversations": results,
        "total": len(results),
    }


@router.delete(
    "/conversations/{conversation_id}",
    status_code=200,
    summary="Clear a conversation (start fresh)",
)
def clear_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
):
    """
    Clear all messages from a conversation.
    The conversation record stays but the message history is wiped.
    Useful during testing to start a fresh conversation.
    """

    log = db.query(ConversationLog).filter(
        ConversationLog.id == conversation_id,
    ).first()

    if not log:
        raise NotFoundException(detail="Conversation not found")

    agent = db.query(AIAgent).filter(AIAgent.id == log.agent_id).first()
    if not agent or agent.business_id != current.business_id:
        raise ForbiddenException(detail="You do not have access to this conversation")

    current.require(rbac.P_MESSAGE_CUSTOMERS, agent_id=agent.id)

    log.messages = []
    db.commit()

    return {"message": f"Conversation {conversation_id} cleared successfully"}


# ─────────────────────────────────────────────────────────────────────────────
# INTERNAL HELPER
# ─────────────────────────────────────────────────────────────────────────────

def _process_chat_message(db, data: ChatMessage, agent: AIAgent) -> ChatMessageResponse:
    from app.services.ai_agent_service import (
        _get_or_create_conversation,
        _build_system_prompt,
        _run_ai,
    )

    # get or create customer — exactly like SMS
    customer = customer_service.get_or_create_customer(
        db=db,
        phone=data.customer_phone,
        name=data.customer_name or "Customer",
        business_id=agent.business_id,
    )

    # get or create conversation — exactly like SMS
    log = _get_or_create_conversation(db, agent, customer)

    # load existing messages
    messages = log.messages or []

    # append new user message
    messages.append({
        "role": "user",
        "content": data.message,
        "ts": datetime.utcnow().isoformat(),
    })

    # build system prompt
    system_prompt = _build_system_prompt(agent, customer)

    # convert to provider format
    provider_messages = [
        {"role": m["role"], "content": m["content"]}
        for m in messages
    ]

    # run AI
    reply = _run_ai(
        db=db,
        agent=agent,
        customer=customer,
        system_prompt=system_prompt,
        messages=provider_messages,
    )

    # append reply
    messages.append({
        "role": "assistant",
        "content": reply,
        "ts": datetime.utcnow().isoformat(),
    })

    # save
    from sqlalchemy.orm.attributes import flag_modified

    log.messages = messages
    flag_modified(log, "messages")
    db.add(log)
    db.commit()

    return ChatMessageResponse(
        conversation_id=log.id,
        customer_id=customer.id,
        customer_name=customer.name,
        agent_id=agent.id,
        agent_name=agent.agent_name,
        message_sent=data.message,
        reply=reply,
        timestamp=datetime.utcnow(),
    )