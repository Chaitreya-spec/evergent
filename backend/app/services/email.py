"""Outgoing email, for password reset and address verification.

Delivery is optional. With no SMTP server configured nothing is sent; in
development the message is logged instead so the flow can still be tried,
and anywhere else the absence is logged as a warning — a link is never
written to a production log.
"""

from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)


def email_configured() -> bool:
    return bool(settings.smtp_host)


def _send_sync(message: EmailMessage) -> None:
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as server:  # type: ignore[arg-type]
        if settings.smtp_starttls:
            server.starttls()
        if settings.smtp_username and settings.smtp_password:
            server.login(settings.smtp_username, settings.smtp_password)
        server.send_message(message)


async def send_email(to: str, subject: str, body: str) -> bool:
    """Send a plain-text email. Returns whether it was sent; never raises."""
    if not email_configured():
        if settings.environment == "development":
            log.info("email_not_sent_dev", to=to, subject=subject, body=body)
        else:
            log.warning("email_not_configured", subject=subject)
        return False

    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    try:
        await asyncio.to_thread(_send_sync, message)
        return True
    except Exception as exc:
        log.error("email_failed", subject=subject, error=str(exc)[:200])
        return False
