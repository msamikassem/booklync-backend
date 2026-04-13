"""
app/schemas/voice.py

Pydantic schemas for Vapi voice integration.

Vapi sends three types of webhooks:
    1. assistant-request  — sent when a call arrives, we return the assistant config
    2. message            — sent during the call with transcript updates and tool calls
    3. status-update      — sent for call lifecycle events (started, ended, etc.)

Vapi docs: https://docs.vapi.ai/api-reference/webhooks

The assistant config we return tells Vapi:
    - Which AI model to use
    - What voice to use (ElevenLabs, Deepgram, etc.)
    - The system prompt
    - The first message to speak when the call connects
    - Which tools the AI can call
    - Our server URL for tool calls and transcripts
"""

from pydantic import BaseModel
from typing import Optional, Any


# ─────────────────────────────────────────────────────────────────────────────
# INBOUND WEBHOOK SCHEMAS — data Vapi sends TO us
# ─────────────────────────────────────────────────────────────────────────────

class VapiCall(BaseModel):
    """Vapi call object included in every webhook payload."""
    id: str
    type: Optional[str] = None
    status: Optional[str] = None
    phoneNumberId: Optional[str] = None

    # The number that received the call (our agent's number)
    # Vapi nests this inside the call object
    toNumber: Optional[str] = None
    fromNumber: Optional[str] = None

    class Config:
        extra = "allow"     # Vapi adds many extra fields — ignore them safely


class VapiMessage(BaseModel):
    """A single message in the Vapi conversation transcript."""
    role: str               # "user", "assistant", "tool", "system"
    message: Optional[str] = None
    content: Optional[str] = None  # some Vapi versions use content instead of message
    time: Optional[float] = None
    endTime: Optional[float] = None
    duration: Optional[float] = None

    class Config:
        extra = "allow"


class VapiWebhookPayload(BaseModel):
    """
    The root payload Vapi sends to all our webhook endpoints.

    message.type determines which kind of event this is:
        "assistant-request"   → call arrived, return assistant config
        "transcript"          → real-time transcript chunk (we ignore these)
        "conversation-update" → full transcript update, we process these
        "status-update"       → call lifecycle event
        "function-call"       → AI wants to call a tool (if using server tools)
        "end-of-call-report"  → call ended with full summary
    """
    message: dict           # raw dict — structure varies by type, we parse manually

    class Config:
        extra = "allow"


class VapiAssistantRequest(BaseModel):
    """
    Parsed assistant-request payload.
    Sent by Vapi when a call arrives — we must return assistant config.
    """
    call: VapiCall

    class Config:
        extra = "allow"


class VapiConversationUpdate(BaseModel):
    """
    Parsed conversation-update payload.
    Contains the latest customer message we need to process.
    """
    call: VapiCall
    messages: list[VapiMessage] = []

    class Config:
        extra = "allow"


# ─────────────────────────────────────────────────────────────────────────────
# OUTBOUND SCHEMAS — data we send BACK to Vapi
# ─────────────────────────────────────────────────────────────────────────────

class VapiVoiceConfig(BaseModel):
    """
    Voice configuration for the assistant.
    Supports ElevenLabs, Deepgram, PlayHT, etc.
    """
    provider: str = "11labs"          # ElevenLabs — best quality for customer service
    voiceId: str = "rachel"           # ElevenLabs voice ID
    stability: float = 0.5
    similarityBoost: float = 0.75
    optimizeStreamingLatency: int = 3  # 0-4, higher = lower latency, slightly lower quality


class VapiModelConfig(BaseModel):
    """
    AI model configuration sent to Vapi.
    Vapi uses this to run the AI on their side for STT→LLM→TTS pipeline.
    We use server-side tools so Vapi calls our webhook for tool execution.
    """
    provider: str
    model: str
    systemPrompt: str
    temperature: float = 0.7
    maxTokens: int = 150              # keep voice responses SHORT


class VapiServerConfig(BaseModel):
    """
    Server configuration — tells Vapi where to send webhooks.
    """
    url: str
    timeoutSeconds: int = 20


class VapiAssistantConfig(BaseModel):
    """
    Full assistant configuration returned to Vapi on assistant-request.

    This dynamically configures the entire AI assistant for this call
    based on the agent's settings in our database.
    No hardcoded assistants in the Vapi dashboard — everything is dynamic.
    """
    name: str
    model: dict                        # VapiModelConfig as dict
    voice: dict                        # VapiVoiceConfig as dict
    firstMessage: str                  # spoken when call connects
    serverUrl: str                     # our webhook URL for tool calls
    serverUrlSecret: Optional[str] = None  # optional shared secret for validation
    recordingEnabled: bool = True
    endCallFunctionEnabled: bool = True   # AI can hang up when done
    dialKeypadFunctionEnabled: bool = False

    # How long to wait for customer to speak before hanging up
    silenceTimeoutSeconds: int = 30
    maxDurationSeconds: int = 600      # 10 minute max call duration

    # What to say if the call exceeds max duration
    endCallMessage: str = "Thank you for calling. Goodbye!"

    # What to say if there's an error
    backgroundDenoisingEnabled: bool = True


class VapiToolCallResponse(BaseModel):
    """
    Response to a Vapi function/tool call webhook.
    We execute the tool and return the result.
    """
    result: str                        # JSON string of the tool result


class VapiMessageResponse(BaseModel):
    """
    Response to a Vapi conversation-update webhook.
    Contains the text we want the AI to speak.
    """
    response: Optional[str] = None    # if set, Vapi injects this as assistant message


class VapiFallbackConfig(BaseModel):
    """
    Fallback assistant config returned when no agent is found for a number.
    Vapi requires a valid response — we can't return a 404 or it breaks the call.
    """
    name: str = "Receptionist"
    firstMessage: str = "Thank you for calling. Unfortunately this number is not currently active. Please try again later."
    model: dict = {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "systemPrompt": "You are a polite receptionist. Tell the caller this number is not active and end the call.",
        "maxTokens": 50,
    }
    voice: dict = {
        "provider": "11labs",
        "voiceId": "rachel",
    }
    endCallFunctionEnabled: bool = True