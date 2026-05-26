"""
app/utils/twilio_utils.py

Twilio helper functions.

Handles:
    - Sending SMS messages
    - Validating Twilio webhook signatures (security)
    - Formatting phone numbers
"""

from twilio.rest import Client
from twilio.request_validator import RequestValidator
from loguru import logger
from app.core.config import settings


def get_twilio_client() -> Client:
    """Return an authenticated Twilio client."""
    return Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)


def send_sms(to: str, body: str, from_number: str = None) -> bool:
    """
    Send an SMS message via Twilio.

    Args:
        to:          recipient phone number e.g. "+12025551234"
        body:        message text
        from_number: sender number

    Returns:
        True if sent successfully, False if failed
    """
    # TEMPORARY: SMS sending disabled during local testing to avoid messaging real numbers.
    logger.info(f"[SMS SUPPRESSED] to={to} from={from_number} body={body!r}")
    return True

    # try:
    #     client = get_twilio_client()
    #     message = client.messages.create(
    #         to=to,
    #         from_=from_number,
    #         body=body,
    #     )
    #     logger.info(f"SMS sent: to={to} sid={message.sid}")
    #     return True
    # except Exception as e:
    #     logger.error(f"SMS failed: to={to} error={e}")
    #     return False


def validate_twilio_signature(
    url: str,
    params: dict,
    signature: str,
) -> bool:
    """
    Validate that a webhook request genuinely came from Twilio.

    Twilio signs every webhook request with your auth token.
    We verify the signature to prevent fake requests.

    Args:
        url:       the full URL of the webhook endpoint
        params:    the POST parameters from the request
        signature: the X-Twilio-Signature header value

    Returns:
        True if the signature is valid, False if fake
    """
    validator = RequestValidator(settings.TWILIO_AUTH_TOKEN)
    return validator.validate(url, params, signature)


def format_phone(phone: str) -> str:
    """
    Ensure phone number is in E.164 format e.g. +12025551234
    Strips spaces and dashes. Adds + if missing.
    """
    cleaned = phone.strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if not cleaned.startswith("+"):
        cleaned = "+" + cleaned
    return cleaned