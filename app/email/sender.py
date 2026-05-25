import base64
import logging
from dataclasses import dataclass
from typing import Optional

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

RESEND_API = "https://api.resend.com/emails"


@dataclass
class Attachment:
    filename: str
    content_bytes: bytes
    content_type: str = "text/csv"


def _resend_payload(
    *,
    to: list[str],
    subject: str,
    html: Optional[str],
    text: Optional[str],
    attachments: Optional[list[Attachment]],
    from_email: str,
    from_name: Optional[str] = None,
) -> dict:
    sender = f"{from_name} <{from_email}>" if from_name else from_email
    payload: dict = {"from": sender, "to": to, "subject": subject}
    if html:
        payload["html"] = html
    if text:
        payload["text"] = text
    if attachments:
        payload["attachments"] = [
            {
                "filename": a.filename,
                "content": base64.b64encode(a.content_bytes).decode("ascii"),
                "content_type": a.content_type,
            }
            for a in attachments
        ]
    return payload


def send_email(
    *,
    to: list[str],
    subject: str,
    html: Optional[str] = None,
    text: Optional[str] = None,
    attachments: Optional[list[Attachment]] = None,
    retries: int = 1,
    http_client: Optional[httpx.Client] = None,
) -> dict:
    """Send via Resend. Retries once on failure. Raises on final failure."""
    settings = get_settings()
    if not settings.RESEND_API_KEY:
        raise RuntimeError("RESEND_API_KEY not configured")

    payload = _resend_payload(
        to=to,
        subject=subject,
        html=html,
        text=text,
        attachments=attachments,
        from_email=settings.FROM_EMAIL,
        from_name=settings.FROM_NAME,
    )
    headers = {
        "Authorization": f"Bearer {settings.RESEND_API_KEY}",
        "Content-Type": "application/json",
    }

    client = http_client or httpx.Client(timeout=30.0)
    last_error: Optional[Exception] = None
    try:
        for attempt in range(retries + 1):
            try:
                resp = client.post(RESEND_API, json=payload, headers=headers)
                if resp.status_code >= 400:
                    raise RuntimeError(f"Resend {resp.status_code}: {resp.text}")
                return resp.json()
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning("Resend send failed (attempt %d): %s", attempt + 1, exc)
        assert last_error is not None
        raise last_error
    finally:
        if http_client is None:
            client.close()


def send_admin_alert(*, subject: str, text: str) -> None:
    """Best-effort alert to ADMIN_EMAIL. Never raises (caller usually doesn't want a cascade)."""
    settings = get_settings()
    if not settings.ADMIN_EMAIL or not settings.RESEND_API_KEY:
        logger.warning("admin alert suppressed (missing config): %s", subject)
        return
    try:
        send_email(to=[settings.ADMIN_EMAIL], subject=subject, text=text, retries=1)
    except Exception:  # noqa: BLE001
        logger.exception("admin alert failed: %s", subject)
