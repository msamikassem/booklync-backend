"""
app/utils/email_utils.py

Thin wrapper around Resend for transactional emails.

Templates live in app/templates/emails/*.html — edit them there.
Placeholders use {{variable_name}} syntax; they are replaced at send time.

All functions return True on success, False on failure — callers should
log failures but not roll back DB state (invite row is committed first).
"""

from pathlib import Path
from loguru import logger

TEMPLATES_DIR = Path(__file__).parent.parent / "templates" / "emails"


def _render(template_name: str, **variables: str) -> str:
    """Load an email HTML template and replace {{placeholder}} tokens."""
    path = TEMPLATES_DIR / template_name
    html = path.read_text(encoding="utf-8")
    for key, value in variables.items():
        html = html.replace(f"{{{{{key}}}}}", value)
    return html


def send_invite_email(
    to_email: str,
    invite_token: str,
    employee_name: str,
    business_name: str,
    api_base_url: str,
) -> bool:
    """Send a dashboard invite email to an employee."""
    try:
        import resend
        from app.core.config import settings

        resend.api_key = settings.RESEND_API_KEY

        accept_url = f"{api_base_url}/accept-invite?token={invite_token}"

        html = _render(
            "invite.html",
            employee_name=employee_name,
            business_name=business_name,
            accept_url=accept_url,
        )

        resend.Emails.send({
            "from": settings.EMAIL_FROM,
            "to": [to_email],
            "subject": f"You're invited to {business_name}'s Booklync dashboard",
            "html": html,
        })

        logger.info(f"Invite email sent to {to_email}")
        return True

    except Exception as e:
        logger.error(f"Failed to send invite email to {to_email}: {e}")
        return False
