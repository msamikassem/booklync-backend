"""
app/schemas/chat.py

Schemas for the direct chat endpoint.

Allows testing the AI agent via HTTP without needing Twilio.
Also useful for a web chat widget on the booking page.
"""

from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class ChatMessage(BaseModel):
    """
    Send a message to the AI agent.
    Exactly mirrors what happens when a customer sends an SMS.
    
    customer_phone identifies the customer.
    The conversation is found or created automatically — just like SMS.
    """
    agent_id: int
    customer_phone: str
    customer_name: Optional[str] = "Customer"
    message: str


class ChatMessageResponse(BaseModel):
    """
    Response from the AI agent.
    """
    conversation_id: int
    customer_id: int
    customer_name: str
    agent_id: int
    agent_name: str
    message_sent: str           # what the customer sent
    reply: str                  # what the AI replied
    timestamp: datetime


class ConversationHistoryResponse(BaseModel):
    """
    Full conversation history for a customer with an agent.
    """
    conversation_id: int
    agent_id: int
    agent_name: str
    customer_id: int
    customer_name: str
    customer_phone: str
    channel: str
    messages: list[dict]
    total_messages: int
    created_at: datetime