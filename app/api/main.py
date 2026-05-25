from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from app.admin.routes import router as admin_router
from app.config import get_settings
from app.db import session_scope
from app.digest.admin_summary import send_admin_summary
from app.digest.scheduler import run_digest_tick
from app.maintenance import purge_old_api_call_log
from app.tasks.enrichment import run_enrichment


def require_internal_key(x_internal_key: str | None = Header(default=None)) -> None:
    expected = get_settings().INTERNAL_API_KEY
    if not expected or x_internal_key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or missing X-Internal-Key"
        )


app = FastAPI(title="EON Lead Sniper", version="0.1.0")

_settings = get_settings()
app.add_middleware(
    SessionMiddleware,
    secret_key=_settings.SESSION_SECRET,
    session_cookie="lead_engine_session",
    max_age=60 * 60 * 24 * 7,
    same_site="lax",
    https_only=_settings.APP_ENV == "prod",
)
app.include_router(admin_router)


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/admin", status_code=307)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/run/enrichment", dependencies=[Depends(require_internal_key)])
def run_enrichment_endpoint() -> dict:
    with session_scope() as session:
        summary = run_enrichment(session)
    return {
        "run_id": summary.run_id,
        "companies_processed": summary.companies_processed,
        "candidates_found": summary.candidates_found,
        "new_leads_created": summary.new_leads_created,
        "contacts_enriched": summary.contacts_enriched,
        "credits_consumed": summary.credits_consumed,
        "halted_by_budget": summary.halted_by_budget,
        "error_count": len(summary.errors),
    }


@app.post("/run/digest", dependencies=[Depends(require_internal_key)])
def run_digest_endpoint(send_admin: bool = False) -> dict:
    with session_scope() as session:
        run = run_digest_tick(session)
        admin_payload = None
        if send_admin:
            admin = send_admin_summary(session)
            admin_payload = {
                "credits_month": admin.credits_month,
                "credits_limit": admin.credits_limit,
                "reps_emailed": admin.reps_emailed,
                "leads_delivered": admin.leads_delivered,
            }
    return {
        "digest_run_id": str(run.id),
        "reps_emailed": run.reps_emailed,
        "total_leads_delivered": run.total_leads_delivered,
        "errors": run.errors or [],
        "admin_summary": admin_payload,
    }


@app.post("/maintenance/purge-api-log", dependencies=[Depends(require_internal_key)])
def purge_endpoint(older_than_days: int = 30) -> dict:
    if older_than_days < 1:
        raise HTTPException(status_code=400, detail="older_than_days must be >= 1")
    with session_scope() as session:
        deleted = purge_old_api_call_log(session, older_than_days=older_than_days)
    return {"deleted": deleted}
