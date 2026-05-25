from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from app.admin.auth import require_admin
from app.admin.templating import render
from app.db import session_scope
from app.models import DigestRun, EnrichmentRun

router = APIRouter(prefix="/runs")


@router.get("")
def runs_index(request: Request, tab: str = "enrichment", _user: str = Depends(require_admin)):
    with session_scope() as session:
        if tab == "digest":
            digest_runs = list(
                session.execute(
                    select(DigestRun).order_by(DigestRun.created_at.desc()).limit(100)
                ).scalars()
            )
            enrichment_runs = []
        else:
            tab = "enrichment"
            enrichment_runs = list(
                session.execute(
                    select(EnrichmentRun).order_by(EnrichmentRun.run_started_at.desc()).limit(100)
                ).scalars()
            )
            digest_runs = []
    return render(
        request,
        "runs.html",
        tab=tab,
        enrichment_runs=enrichment_runs,
        digest_runs=digest_runs,
    )
