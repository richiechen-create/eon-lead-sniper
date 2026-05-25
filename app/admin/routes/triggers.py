from fastapi import APIRouter, Depends

from app.admin.auth import require_admin
from app.db import session_scope
from app.digest.admin_summary import send_admin_summary
from app.digest.scheduler import run_digest_tick
from app.tasks.enrichment import run_enrichment

router = APIRouter(prefix="/triggers")


@router.post("/enrichment")
def trigger_enrichment(_user: str = Depends(require_admin)) -> dict:
    with session_scope() as session:
        summary = run_enrichment(session)
    return {
        "run_id": summary.run_id,
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
