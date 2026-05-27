from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import joinedload

from app.admin.auth import require_admin
from app.admin.templating import render
from app.config import get_settings
from app.db import session_scope
from app.models import ApiCallLog, Company, DoNotContact, Lead, Rep

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


def _build_grouped_view(session, *, status, routing_status, company_q, search):
    """Server-side group-by-rep for the default leads view.

    Pulls all pending+skipped leads (filtered), buckets them by
    assigned_rep_email, and returns a sorted list of section dicts. The
    DEFAULT_REP_EMAIL bucket is pinned first when it has fallback leads;
    other sections sort by pending_count desc.
    """
    settings = get_settings()
    default_email = (settings.DEFAULT_REP_EMAIL or "").lower()

    # Default the grouped view to "actionable" leads (pending + skipped). The
    # chips above the sections let the operator narrow further.
    stmt = (
        select(Lead)
        .options(joinedload(Lead.company))
        .where(Lead.delivery_status.in_(["pending", "skipped"]))
        .order_by(Lead.date_discovered.desc())
    )
    stmt = _build_filter(
        stmt,
        rep=None,  # grouping by rep — never filter by a single rep here
        status=status,
        routing_status=routing_status,
        company_q=company_q,
        search=search,
    )
    all_leads = list(session.execute(stmt).scalars())

    # Bucket. The key is the lowercased email so case-typos don't split groups.
    buckets: dict[str, dict] = {}
    for lead in all_leads:
        key = (lead.assigned_rep_email or "(unassigned)").lower()
        if key not in buckets:
            buckets[key] = {
                "rep_email": lead.assigned_rep_email or "(unassigned)",
                "rep_name": lead.assigned_rep_name or "",
                "leads": [],
                "pending_count": 0,
                "fallback_count": 0,
                "skipped_count": 0,
                "no_email_count": 0,
            }
        b = buckets[key]
        b["leads"].append(lead)
        if lead.delivery_status == "pending":
            b["pending_count"] += 1
        if lead.delivery_status == "skipped":
            b["skipped_count"] += 1
        # fallback_count counts ACTIONABLE fallback leads only (pending). A
        # skipped lead is already off the digest path — counting it would
        # mislead the operator into thinking the section has more to triage.
        if lead.routing_status == "fallback" and lead.delivery_status == "pending":
            b["fallback_count"] += 1
        if not lead.email:
            b["no_email_count"] += 1

    sections = list(buckets.values())

    def _sort_key(section):
        # Pin DEFAULT_REP_EMAIL first when it has fallback leads; otherwise
        # sort by pending_count desc, then email for stability.
        is_default = section["rep_email"].lower() == default_email
        pinned = -1 if (is_default and section["fallback_count"] > 0) else 0
        return (pinned, -section["pending_count"], section["rep_email"])

    sections.sort(key=_sort_key)

    fallback_total = sum(s["fallback_count"] for s in sections)
    pending_total = sum(s["pending_count"] for s in sections)
    skipped_total = sum(s["skipped_count"] for s in sections)
    all_total = pending_total + skipped_total

    return sections, {
        "all_total": all_total,
        "pending_total": pending_total,
        "fallback_total": fallback_total,
        "skipped_total": skipped_total,
    }


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
        reps = list(
            session.execute(select(Rep).where(Rep.is_active == True).order_by(Rep.email)).scalars()  # noqa: E712
        )

        # Two modes:
        #   - `?rep=X` set → flat single-rep table (existing behavior)
        #   - no rep → grouped-by-rep view (new default)
        if rep:
            stmt = (
                select(Lead)
                .options(joinedload(Lead.company))
                .order_by(Lead.date_discovered.desc())
            )
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
            return render(
                request,
                "leads.html",
                leads=leads,
                sections=None,
                totals=None,
                reps=reps,
                rep_filter=rep,
                status_filter=status or "",
                routing_status_filter=routing_status or "",
                company_filter=company or "",
                search=search or "",
                page=page,
                has_more=len(leads) == PAGE_SIZE,
                fallback_view=False,
                grouped_view=False,
            )

        sections, totals = _build_grouped_view(
            session,
            status=status,
            routing_status=routing_status,
            company_q=company,
            search=search,
        )
        return render(
            request,
            "leads.html",
            leads=None,
            sections=sections,
            totals=totals,
            reps=reps,
            rep_filter="",
            status_filter=status or "",
            routing_status_filter=routing_status or "",
            company_filter=company or "",
            search=search or "",
            page=1,
            has_more=False,
            fallback_view=False,
            grouped_view=True,
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
        reps = list(session.execute(select(Rep).where(Rep.is_active == True).order_by(Rep.email)).scalars())  # noqa: E712
    return render(
        request,
        "leads.html",
        leads=leads,
        sections=None,
        totals=None,
        reps=reps,
        rep_filter="",
        status_filter="pending",
        routing_status_filter="fallback",
        company_filter="",
        search="",
        page=1,
        has_more=False,
        fallback_view=True,
        grouped_view=False,
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


# -- Bulk reassign ----------------------------------------------------------


class BulkReassignBody(BaseModel):
    """JSON body for bulk-reassign.

    Either `lead_ids` (explicit list) OR `filter` (server-side query) is
    required. `new_rep_email` is always required.
    """

    new_rep_email: str
    lead_ids: Optional[list[str]] = None
    # filter dict supports: assigned_rep_email, routing_status, delivery_status
    filter: Optional[dict] = None


@router.post("/bulk-reassign")
def bulk_reassign(
    body: BulkReassignBody,
    response: Response,
    _user: str = Depends(require_admin),
):
    """Reassign a batch of leads to a different rep in one statement.

    Accepts either explicit `lead_ids` or a `filter` dict matching one or
    more of: assigned_rep_email, routing_status, delivery_status. Sets
    `routing_status='company_override'` on each updated row (matches the
    per-lead reassign convention). Logs an `api_call_log` row for audit.
    """
    target_email = (body.new_rep_email or "").strip().lower()
    if not target_email:
        raise HTTPException(400, "new_rep_email is required")

    if not body.lead_ids and not body.filter:
        raise HTTPException(400, "either lead_ids or filter is required")

    with session_scope() as session:
        rep = session.execute(
            select(Rep).where(Rep.email == target_email, Rep.is_active == True)  # noqa: E712
        ).scalar_one_or_none()
        if rep is None:
            raise HTTPException(400, f"rep {target_email} is not active")

        stmt = update(Lead).values(
            assigned_rep_email=rep.email,
            assigned_rep_name=rep.name,
            routing_status="company_override",
            routing_rule_id=None,
        )

        if body.lead_ids:
            stmt = stmt.where(Lead.id.in_(body.lead_ids))
        else:
            f = body.filter or {}
            allowed = {"assigned_rep_email", "routing_status", "delivery_status"}
            bad_keys = set(f.keys()) - allowed
            if bad_keys:
                raise HTTPException(400, f"unsupported filter keys: {sorted(bad_keys)}")
            if "assigned_rep_email" in f:
                stmt = stmt.where(Lead.assigned_rep_email == f["assigned_rep_email"])
            if "routing_status" in f:
                stmt = stmt.where(Lead.routing_status == f["routing_status"])
            if "delivery_status" in f:
                stmt = stmt.where(Lead.delivery_status == f["delivery_status"])

        result = session.execute(stmt)
        updated = result.rowcount or 0

        # Audit trail — reuses api_call_log even though this isn't an Apollo
        # call. The schema allows any endpoint string and gives Richie a
        # paper trail without inventing a new table.
        session.add(
            ApiCallLog(
                endpoint="/admin/leads/bulk-reassign",
                http_status=200,
                credits_used=0,
                request_payload={
                    "new_rep_email": target_email,
                    "lead_ids": body.lead_ids,
                    "filter": body.filter,
                },
                response_summary={"updated": int(updated)},
            )
        )

    response.headers["X-Toast"] = f"Reassigned {updated} leads to {target_email}"
    return {"updated": int(updated), "new_rep": target_email}


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
