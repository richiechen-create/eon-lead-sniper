import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Optional

from app.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class Attachment:
    filename: str
    content_bytes: bytes
    content_type: str = "text/csv"


def _build_message(
    *,
    to: list[str],
    subject: str,
    html: Optional[str],
    text: Optional[str],
    attachments: Optional[list[Attachment]],
    from_email: str,
    from_name: Optional[str],
) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{from_email}>" if from_name else from_email
    msg["To"] = ", ".join(to)

    # Plain-text body is required; HTML attaches as alternative.
    msg.set_content(text or "(no plain-text body)")
    if html:
        msg.add_alternative(html, subtype="html")

    for a in attachments or []:
        maintype, _, subtype = (a.content_type or "application/octet-stream").partition("/")
        msg.add_attachment(
            a.content_bytes,
            maintype=maintype or "application",
            subtype=subtype or "octet-stream",
            filename=a.filename,
        )
    return msg


def send_email(
    *,
    to: list[str],
    subject: str,
    html: Optional[str] = None,
    text: Optional[str] = None,
    attachments: Optional[list[Attachment]] = None,
    retries: int = 1,
) -> dict:
    """Send via SMTP (Gmail with App Password). Retries once on failure."""
    settings = get_settings()
    if not (settings.SMTP_HOST and settings.SMTP_USERNAME and settings.SMTP_PASSWORD):
        raise RuntimeError(
            "SMTP not configured: set SMTP_HOST, SMTP_USERNAME, SMTP_PASSWORD"
        )

    msg = _build_message(
        to=to,
        subject=subject,
        html=html,
        text=text,
        attachments=attachments,
        from_email=settings.FROM_EMAIL,
        from_name=settings.FROM_NAME,
    )

    last_error: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=30) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
                server.send_message(msg)
            return {"ok": True, "to": to}
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning("SMTP send failed (attempt %d): %s", attempt + 1, exc)
    assert last_error is not None
    raise last_error


def send_admin_alert(*, subject: str, text: str) -> None:
    """Best-effort alert to ADMIN_EMAIL. Never raises (caller usually doesn't want a cascade)."""
    settings = get_settings()
    if not settings.ADMIN_EMAIL or not settings.SMTP_USERNAME:
        logger.warning("admin alert suppressed (missing config): %s", subject)
        return
    try:
        send_email(to=[settings.ADMIN_EMAIL], subject=subject, text=text, retries=1)
    except Exception:  # noqa: BLE001
        logger.exception("admin alert failed: %s", subject)
