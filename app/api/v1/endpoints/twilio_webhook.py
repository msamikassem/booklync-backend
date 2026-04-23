"""
app/api/v1/endpoints/twilio_webhook.py

Twilio webhook endpoints.

Twilio calls these URLs when:
    - A customer sends an SMS to the agent's number
    - (Future) A customer calls the agent's number

Security:
    Every request from Twilio includes an X-Twilio-Signature header.
    We validate this signature to ensure the request genuinely came
    from Twilio and not a fake request from somewhere else.

    In development you can disable signature validation by setting
    TWILIO_VALIDATE_SIGNATURES=False in your .env file.

Twilio expects:
    - HTTP 200 response
    - Response within 15 seconds
    - TwiML or empty body for SMS (we send empty — reply is sent separately)
"""

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session
from loguru import logger

from app.db.session import get_db
from app.core.config import settings
from app.services.ai_agent_service import handle_incoming_sms
from app.utils.twilio_utils import validate_twilio_signature, send_sms, format_phone

router = APIRouter()


@router.post(
    "/sms",
    status_code=200,
    summary="Receive incoming SMS from Twilio",
    include_in_schema=False,   # hide from public docs — internal endpoint
)
async def receive_sms(
    request: Request,
    db: Session = Depends(get_db),
    # Twilio sends these form fields with every SMS webhook
    From: str = Form(...),      # customer's phone number
    To: str = Form(...),        # agent's Twilio number (which number received the SMS)
    Body: str = Form(...),      # the SMS text content
    MessageSid: str = Form(...),# Twilio message ID for logging
):
    """
    Receives incoming SMS messages from Twilio.

    Twilio sends a POST request to this URL when a customer
    sends an SMS to any of your Twilio numbers.

    The endpoint:
    1. Validates the request is genuinely from Twilio
    2. Passes the message to the AI agent handler
    3. Sends the AI's reply back via SMS
    4. Returns 200 OK to Twilio

    Configure this URL in your Twilio console:
        Phone Numbers → Your Number → Messaging → Webhook URL
        https://api.yourdomain.com/api/v1/twilio/sms
        HTTP POST
    """

    logger.info(f"SMS webhook: from={From} to={To} sid={MessageSid}")

    # ── Security: validate Twilio signature ───────────────────────────────
    # Skip in development if explicitly disabled
    if settings.APP_ENV == "production":
        signature = request.headers.get("X-Twilio-Signature", "")
        url = str(request.url)
        form_data = dict(await request.form())

        if not validate_twilio_signature(url, form_data, signature):
            logger.warning(f"Invalid Twilio signature for request from {From}")
            raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    # ── Format phone numbers ──────────────────────────────────────────────
    from_number = format_phone(From)
    to_number = format_phone(To)

    # ── Handle the message ────────────────────────────────────────────────
    try:
        ai_reply = handle_incoming_sms(
            db=db,
            to_number=to_number,
            from_number=from_number,
            message_body=Body.strip(),
        )

        # Send the AI's reply back to the customer
        if ai_reply is not None:
            send_sms(
                to=from_number,
                body=ai_reply,
                from_number=to_number,    # reply from the same number they texted
            )

            logger.info(f"SMS replied: to={from_number} preview='{ai_reply[:50]}...'")

    except Exception as e:
        logger.exception(f"Error handling SMS from {From}: {e}")

        # send a fallback message so customer isn't left hanging
        send_sms(
            to=from_number,
            body="Sorry, I'm having trouble right now. Please try again in a moment.",
            from_number=to_number,
        )

    # Twilio expects a 200 response — return empty TwiML
    # (We already sent the reply manually via the API above)
    return Response(
        content='<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
        media_type="application/xml",
    )


@router.get(
    "/health",
    status_code=200,
    summary="Twilio webhook health check",
    include_in_schema=False,
)
def twilio_health():
    """
    Simple health check for the Twilio webhook.
    Twilio can be configured to hit this to verify the endpoint is alive.
    """
    return {"status": "ok", "webhook": "twilio"}