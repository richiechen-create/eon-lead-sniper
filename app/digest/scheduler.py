"""Hourly digest dispatcher.

Each hourly cron tick:
  - Finds active reps whose local time has just crossed 08:00 (current local hour == 8).
  - Excludes weekends (Mon=0 ... Sun=6; skip 5 and 6 in rep local time).
  - Builds + sends a digest per eligible rep.
  - Flips delivered leads to delivery_status='delivered'.
  - Once per day (after the last weekday rep's slot, defined as 23:00 UTC), sends the admin summary.
"""
import logging
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.digest.builder import BuiltDigest, build_digest
from app.email.sender import Attachment, send_email, send_admin_alert
from app.models import DigestRun, Lead, Rep
from app.models.base import utcnow

logger = logging.getLogger(__name__)


def _local_now(tz_name: str, now_utc: datetime) -> datetime:
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
    return now_utc.replace(tzinfo=timezone.utc).astimezone(tz)


def _is_eligible(rep: Rep, now_utc: datetime, *, force: bool = False) -> Optional[datetime]:
    """Return the rep's local datetime if it's a weekday and the local hour is 8, else None.

    When `force=True`, the weekday + 8AM checks are skipped — every active rep
    is treated as eligible. Used for the dashboard's manual "Run digest now"
    button so the operator doesn't need to wait for the scheduled tick.
    """
    local = _local_now(rep.timezone or "UTC", now_utc)
    if force:
        return local
    if local.weekday() >= 5:  # Sat=5, Sun=6
        return None
    if local.hour != 8:
        return None
    return local


def _pending_leads_for_rep(session: Session, rep: Rep) -> list[Lead]:
    """Pending leads the rep can actually act on — email OR LinkedIn present.

    LinkedIn-only leads are still deliverable: the rep can reach out via
    LinkedIn from the digest. The Apollo people-match step often returns
    LinkedIn URLs even when the email is withheld, so this widens the funnel
    without compromising deliverability.
    """
    cap = rep.daily_lead_cap
    stmt = (
        select(Lead)
        .where(Lead.assigned_rep_email == rep.email)
        .where(Lead.delivery_status == "pending")
        .where(or_(Lead.email.is_not(None), Lead.linkedin_url.is_not(None)))
        .order_by(Lead.date_discovered.asc())
    )
    if cap is not None and cap > 0:
        stmt = stmt.limit(cap)
    return list(session.execute(stmt).scalars().all())


def _send_digest(digest: BuiltDigest) -> None:
    send_email(
        to=[digest.rep_email],
        subject=digest.subject,
        html=digest.html,
        text=digest.text,
        attachments=[
            Attachment(
                filename=digest.csv_filename,
                content_bytes=digest.csv_bytes,
                content_type="text/csv",
            )
        ],
        retries=1,
    )


def run_digest_tick(
    session: Session,
    now_utc: Optional[datetime] = None,
    *,
    force: bool = False,
    only_rep: Optional[str] = None,
) -> DigestRun:
    """Hourly digest dispatch.

    `force=True` sends to every active rep with pending+deliverable leads,
    ignoring local-hour and weekday gates. Used by the dashboard's manual
    button — cron scripts always use the default (force=False).

    `only_rep` (email): when set, scopes the run to that single rep instead
    of looping all actives. Implies `force=True` behavior for that rep (the
    operator wouldn't manually trigger a single-rep send if they wanted the
    schedule gate to apply). Useful for staggering sends when some reps
    have only 1-2 leads and others have many — operator can hold the small
    ones, send the full ones.
    """
    now_utc = now_utc or datetime.utcnow()
    run = DigestRun(run_date=now_utc.date(), errors=[])
    session.add(run)
    session.flush()

    rep_stmt = select(Rep).where(Rep.is_active == True)  # noqa: E712
    if only_rep:
        rep_stmt = rep_stmt.where(Rep.email == only_rep.strip().lower())
    reps = session.execute(rep_stmt).scalars().all()

    # Single-rep mode always bypasses the time gate — manual override is the
    # whole point of this code path.
    if only_rep:
        force = True

    reps_emailed = 0
    total_delivered = 0

    for rep in reps:
        local = _is_eligible(rep, now_utc, force=force)
        if local is None:
            continue
        try:
            leads = _pending_leads_for_rep(session, rep)
        except Exception as exc:  # noqa: BLE001
            run.errors = (run.errors or []) + [{"rep": rep.email, "error": str(exc)}]
            continue
        if not leads:
            continue  # 0 leads after cap -> no email

        # Build can raise (e.g. orphaned lead → company is None). Catch
        # per-rep so one bad lead doesn't kill the whole tick / endpoint.
        try:
            digest = build_digest(rep.email, rep.name, leads, local)
        except Exception as exc:  # noqa: BLE001
            logger.exception("digest build failed for %s", rep.email)
            run.errors = (run.errors or []) + [
                {"rep": rep.email, "stage": "build", "error": str(exc)}
            ]
            continue
        if digest is None:
            continue
        try:
            _send_digest(digest)
        except Exception as exc:  # noqa: BLE001
            logger.exception("digest send failed for %s", rep.email)
            run.errors = (run.errors or []) + [
                {"rep": rep.email, "stage": "send", "error": str(exc)}
            ]
            _safe_admin_alert(
                subject=f"EON Bullseye: digest send failed for {rep.email}",
                text=f"Failed to send digest for {rep.email}: {exc}",
            )
            continue

        # On success, flip those leads to delivered.
        now = utcnow()
        for lead in leads:
            lead.delivery_status = "delivered"
            lead.delivered_at = now
        reps_emailed += 1
        total_delivered += len(leads)
        session.flush()

    run.reps_emailed = reps_emailed
    run.total_leads_delivered = total_delivered
    session.flush()
    return run


def _safe_admin_alert(*, subject: str, text: str) -> None:
    try:
        send_admin_alert(subject=subject, text=text)
    except Exception:  # noqa: BLE001
        logger.exception("admin alert failed")
