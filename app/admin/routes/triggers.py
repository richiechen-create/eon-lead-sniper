from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select

from app.admin.auth import require_admin
from app.config import get_settings
from app.db import session_scope
from app.digest.admin_summary import send_admin_summary
from app.digest.builder import build_digest
from app.digest.scheduler import _pending_leads_for_rep, run_digest_tick
from app.email.sender import Attachment, send_email
from app.models import Company, EnrichmentRun, Rep
from app.tasks.enrichment import run_enrichment

router = APIRouter(prefix="/triggers")


@router.post("/enrichment")
def trigger_enrichment(
    industry: Optional[str] = None,
    _user: str = Depends(require_admin),
) -> dict:
    """Manually trigger an enrichment run.

    Pass ?industry=oil%20and%20gas (or a comma-separated list) to restrict to
    one or more segments. Without the param, runs across all active companies.
    """
    industries: Optional[list[str]] = None
    if industry:
        industries = [i.strip() for i in industry.split(",") if i.strip()]
    with session_scope() as session:
        summary = run_enrichment(session, industries=industries)
    return {
        "run_id": summary.run_id,
        "scope": industries or "all",
        "companies_processed": summary.companies_processed,
        "new_leads_created": summary.new_leads_created,
        "contacts_enriched": summary.contacts_enriched,
        "credits_consumed": summary.credits_consumed,
        "halted_by_budget": summary.halted_by_budget,
        "error_count": len(summary.errors),
    }


@router.post("/digest")
def trigger_digest(send_admin: bool = False, _user: str = Depends(require_admin)) -> dict:
    with session_scope() as session:
        run = run_digest_tick(session)
        if send_admin:
            send_admin_summary(session)
    return {
        "digest_run_id": str(run.id),
        "reps_emailed": run.reps_emailed,
        "total_leads_delivered": run.total_leads_delivered,
        "errors": run.errors or [],
    }


@router.post("/test-digest")
def trigger_test_digest(
    rep: Optional[str] = None,
    _user: str = Depends(require_admin),
) -> dict:
    """Send a faithful preview of one rep's next digest to ADMIN_EMAIL.

    Behavior:
      - If `rep` (email) is provided, previews that rep's digest. The rep must
        be active and must have at least one pending+enriched lead.
      - If `rep` is omitted, picks the first active rep (alphabetical by email)
        who has at least one pending+enriched lead.
      - Uses the same lead filter as the production scheduler
        (`_pending_leads_for_rep`): pending status + has email, ordered by
        date_discovered ASC, honoring daily_lead_cap.
      - Sends to ADMIN_EMAIL with `[TEST]` subject prefix.
      - Does NOT flip delivery_status, does NOT create a DigestRun row.
      - If no rep qualifies, returns {"sent": False, ...} without sending.
    """
    settings = get_settings()
    to_email = settings.ADMIN_EMAIL
    if not to_email:
        raise HTTPException(
            status_code=400,
            detail="ADMIN_EMAIL not configured. Set it in env vars first.",
            headers={"X-Toast": "ADMIN_EMAIL not configured"},
        )

    requested_rep = (rep or "").strip().lower() or None

    with session_scope() as session:
        selected_rep = None
        selected_leads: list = []

        if requested_rep is not None:
            target = session.execute(
                select(Rep).where(Rep.email == requested_rep)
            ).scalar_one_or_none()
            if target is None:
                msg = f"Rep {requested_rep} not found."
                raise HTTPException(
                    status_code=404, detail=msg, headers={"X-Toast": msg}
                )
            if not target.is_active:
                msg = f"Rep {requested_rep} is inactive."
                raise HTTPException(
                    status_code=400, detail=msg, headers={"X-Toast": msg}
                )
            leads = _pending_leads_for_rep(session, target)
            if not leads:
                return {
                    "sent": False,
                    "reason": f"{requested_rep} has no pending leads with email",
                    "preview_rep": requested_rep,
                    "leads": 0,
                }
            selected_rep = target
            selected_leads = leads
        else:
            active_reps = list(
                session.execute(
                    select(Rep)
                    .where(Rep.is_active == True)  # noqa: E712
                    .order_by(Rep.email)
                ).scalars()
            )
            for r in active_reps:
                leads = _pending_leads_for_rep(session, r)
                if leads:
                    selected_rep = r
                    selected_leads = leads
                    break

            if selected_rep is None:
                return {
                    "sent": False,
                    "reason": "no leads to preview",
                    "preview_rep": None,
                    "leads": 0,
                }

        digest = build_digest(
            selected_rep.email, selected_rep.name, selected_leads, datetime.utcnow()
        )
        if digest is None:
            raise HTTPException(500, "could not build digest")

        preview_rep_email = selected_rep.email
        lead_count = len(selected_leads)

    try:
        send_email(
            to=[to_email],
            subject="[TEST] " + digest.subject,
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
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail=f"SMTP send failed: {exc}",
            headers={"X-Toast": f"SMTP send failed: {exc}"},
        )

    return {
        "sent": True,
        "sent_to": to_email,
        "preview_rep": preview_rep_email,
        "leads": lead_count,
    }


@router.get("/enrichment/status")
def enrichment_status(
    industry: Optional[str] = None,
    _user: str = Depends(require_admin),
) -> dict:
    """Snapshot of the most recent enrichment run for the live progress UI.

    The orchestrator updates `companies_processed`, `new_leads_created`, and
    `credits_consumed` on the run row inside its loop via `session.flush()`,
    so polling this endpoint every 2-ish seconds gives a real-time view of
    progress for an in-flight run.

    `total_active_companies` is the denominator for an X / Y progress bar.
    Optional `industry` filter mirrors the per-segment trigger.
    """
    with session_scope() as session:
        latest = session.execute(
            select(EnrichmentRun).order_by(EnrichmentRun.run_started_at.desc()).limit(1)
        ).scalar_one_or_none()

        total_stmt = select(func.count(Company.id)).where(Company.is_active == True)  # noqa: E712
        if industry:
            industries = [i.strip().lower() for i in industry.split(",") if i.strip()]
            if industries:
                total_stmt = total_stmt.where(
                    func.lower(func.trim(Company.industry)).in_(industries)
                )
        total_active = int(session.execute(total_stmt).scalar_one() or 0)

        if latest is None:
            return {
                "in_progress": False,
                "total_active_companies": total_active,
                "run": None,
            }

        return {
            "in_progress": latest.run_completed_at is None,
            "total_active_companies": total_active,
            "run": {
                "id": str(latest.id),
                "started_at": latest.run_started_at.isoformat() if latest.run_started_at else None,
                "completed_at": latest.run_completed_at.isoformat() if latest.run_completed_at else None,
                "companies_processed": latest.companies_processed or 0,
                "new_leads_created": latest.new_leads_created or 0,
                "contacts_enriched": latest.contacts_enriched or 0,
                "candidates_found": latest.candidates_found or 0,
                "credits_consumed": latest.credits_consumed or 0,
                "errors_count": len(latest.errors or []),
            },
        }
