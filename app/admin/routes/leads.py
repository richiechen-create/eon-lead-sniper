from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import joinedload

from app.admin.auth import require_admin
from app.admin.templating import render
from app.db import session_scope
from app.models import Company, DoNotContact, Lead, Rep
from app.models.base import utcnow

router = APIRouter(prefix="/leads")

PAGE_SIZE = 50


def _build_filter(stmt, *, rep, status, routing_status, company_q, search):
    if rep:
        stmt = stmt.where(Lead.assigned_rep_email == rep)
    if status:
        stmt = stmt.where(Lead.delivery_status == status)
    if routing_status:
        stmt = stmt.where(Lead.routing_status == routing_status)
    if company_q:
        stmt = stmt.join(Company).where(Company.company_name.ilike(f"%{company_q}%"))
    if search:
        like = f"%{search}%"
        stmt = stmt.where(
            or_(
                Lead.full_name.ilike(like),
                Lead.title.ilike(like),
                Lead.email.ilike(like),
            )
        )
    return stmt


@router.get("")
def leads_index(
    request: Request,
    rep: Optional[str] = None,
    status: Optional[str] = None,
    routing_status: Optional[str] = None,
    company: Optional[str] = None,
    search: Optional[str] = None,
    page: int = 1,
    _user: str = Depends(require_admin),
):
    page = max(page, 1)
    with session_scope() as session:
        stmt = select(Lead).options(joinedload(Lead.company)).order_by(Lead.date_discovered.desc())
        stmt = _build_filter(
            stmt,
            rep=rep,
            status=status,
            routing_status=routing_status,
            company_q=company,
            search=search,
        )
        leads = list(
            session.execute(stmt.limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE)).scalars()
        )
        reps = list(session.execute(select(Rep).where(Rep.is_active == True)).scalars())  # noqa: E712

    return render(
        request,
        "leads.html",
        leads=leads,
        reps=reps,
        rep_filter=rep or "",
        status_filter=status or "",
        routing_status_filter=routing_status or "",
        company_filter=company or "",
        search=search or "",
        page=page,
        has_more=len(leads) == PAGE_SIZE,
        fallback_view=False,
    )


@router.get("/fallback")
def leads_fallback(request: Request, _user: str = Depends(require_admin)):
    with session_scope() as session:
        stmt = (
            select(Lead)
            .options(joinedload(Lead.company))
            .where(Lead.routing_status == "fallback")
            .where(Lead.delivery_status == "pending")
            .order_by(Lead.date_discovered.desc())
        )
        leads = list(session.execute(stmt).scalars())
        reps = list(session.execute(select(Rep).where(Rep.is_active == True)).scalars())  # noqa: E712
    return render(
        request,
        "leads.html",
        leads=leads,
        reps=reps,
        rep_filter="",
        status_filter="pending",
        routing_status_filter="fallback",
        company_filter="",
        search="",
        page=1,
        has_more=False,
        fallback_view=True,
    )


@router.patch("/{lead_id}/rep")
def reassign_rep(
    lead_id: str,
    response: Response,
    new_rep: str = Form(...),
    _user: str = Depends(require_admin),
):
    with session_scope() as session:
        lead = session.get(Lead, lead_id)
        if lead is None:
            raise HTTPException(404, "lead not found")
        rep = session.execute(select(Rep).where(Rep.email == new_rep, Rep.is_active == True)).scalar_one_or_none()  # noqa: E712
        if rep is None:
            raise HTTPException(400, "rep not active")
        lead.assigned_rep_email = rep.email
        lead.assigned_rep_name = rep.name
        lead.routing_status = "company_override"
        lead.routing_rule_id = None
    response.headers["X-Toast"] = f"Reassigned to {new_rep}"
    return {"ok": True}


@router.post("/{lead_id}/suppress")
def suppress_lead(
    lead_id: str,
    response: Response,
    reason: str = Form("manual exclusion via admin"),
    _user: str = Depends(require_admin),
):
    with session_scope() as session:
        lead = session.get(Lead, lead_id)
        if lead is None:
            raise HTTPException(404, "lead not found")
        session.add(
            DoNotContact(
                email=lead.email,
                apollo_person_id=lead.apollo_person_id,
                reason=reason,
            )
        )
        lead.delivery_status = "skipped"
    response.headers["X-Toast"] = "Lead suppressed and added to DNC"
    return {"ok": True}


@router.post("/{lead_id}/skip")
def skip_lead(lead_id: str, response: Response, _user: str = Depends(require_admin)):
    with session_scope() as session:
        lead = session.get(Lead, lead_id)
        if lead is None:
            raise HTTPException(404, "lead not found")
        lead.delivery_status = "skipped"
    response.headers["X-Toast"] = "Marked skipped"
    return {"ok": True}
