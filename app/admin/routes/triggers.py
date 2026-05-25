from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.admin.auth import require_admin
from app.config import get_settings
from app.db import session_scope
from app.digest.admin_summary import send_admin_summary
from app.digest.builder import build_digest
from app.digest.scheduler import run_digest_tick
from app.email.sender import Attachment, send_email
from app.models import Company, Lead
from app.models.base import utcnow
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
def trigger_test_digest(_user: str = Depends(require_admin)) -> dict:
    """Send a sample digest to ADMIN_EMAIL.

    Uses real leads with an email address if any exist (does NOT flip their
    delivery_status — safe to call repeatedly). Falls back to a synthetic
    sample if the DB has no leads yet, so the operator can preview the format.
    Subject is prefixed with [TEST] so it can't be mistaken for a real digest.
    """
    settings = get_settings()
    to_email = settings.ADMIN_EMAIL
    if not to_email:
        raise HTTPException(
            status_code=400,
            detail="ADMIN_EMAIL not configured. Set it in env vars first.",
            headers={"X-Toast": "ADMIN_EMAIL not configured"},
        )

    with session_scope() as session:
        leads = list(
            session.execute(
                select(Lead)
                .options(joinedload(Lead.company))
                .where(Lead.email.is_not(None))
                .order_by(Lead.date_discovered.desc())
                .limit(10)
            ).scalars()
        )
        synthetic = False
        if not leads:
            # No real leads yet — build a transient sample so the operator
            # can still preview the format. Nothing is persisted.
            fake_company = Company(
                company_name="Acme Corp (sample)", domain="acme.example.com"
            )
            fake_lead = Lead(
                apollo_person_id="sample-1",
                full_name="Jane Doe",
                title="VP Learning & Development",
                seniority="vp",
                department="Human Resources",
                linkedin_url="https://linkedin.com/in/janedoe",
                email="jane.doe@acme.example.com",
                date_discovered=utcnow(),
            )
            fake_lead.company = fake_company
            leads = [fake_lead]
            synthetic = True

        # Use the first lead's rep info for "Hi {rep_first_name}".
        first_rep_email = leads[0].assigned_rep_email or to_email
        first_rep_name = leads[0].assigned_rep_name or "there"

        digest = build_digest(first_rep_email, first_rep_name, leads, datetime.utcnow())
        if digest is None:
            raise HTTPException(500, "could not build digest")

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
        "sent_to": to_email,
        "leads_in_digest": len(leads),
        "synthetic": synthetic,
    }
