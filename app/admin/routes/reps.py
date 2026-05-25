import json
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from sqlalchemy import func, select

from app.admin.auth import require_admin
from app.admin.templating import render
from app.db import session_scope
from app.models import CompanyRepAssignment, Lead, Rep, RoutingRule

router = APIRouter(prefix="/reps")


@router.get("")
def reps_index(request: Request, _user: str = Depends(require_admin)):
    with session_scope() as session:
        reps = list(session.execute(select(Rep).order_by(Rep.email)).scalars())
        pending = dict(
            session.execute(
                select(Lead.assigned_rep_email, func.count(Lead.id))
                .where(Lead.delivery_status == "pending")
                .group_by(Lead.assigned_rep_email)
            ).all()
        )
    return render(request, "reps.html", reps=reps, pending=pending)


@router.post("")
def reps_create(
    response: Response,
    email: str = Form(...),
    name: str = Form(...),
    timezone: str = Form("UTC"),
    team: str = Form(""),
    daily_lead_cap: Optional[int] = Form(None),
    _user: str = Depends(require_admin),
):
    with session_scope() as session:
        existing = session.execute(select(Rep).where(Rep.email == email)).scalar_one_or_none()
        if existing is not None:
            raise HTTPException(400, "rep email already exists")
        session.add(
            Rep(
                email=email.strip().lower(),
                name=name,
                timezone=timezone,
                team=team or None,
                daily_lead_cap=daily_lead_cap,
                is_active=True,
            )
        )
    response.headers["X-Toast"] = "Rep added"
    response.headers["HX-Redirect"] = "/admin/reps"
    return {"ok": True}


@router.patch("/{rep_id}")
def reps_update(
    rep_id: str,
    response: Response,
    timezone: Optional[str] = Form(None),
    team: Optional[str] = Form(None),
    daily_lead_cap: Optional[str] = Form(None),
    is_active: Optional[bool] = Form(None),
    name: Optional[str] = Form(None),
    _user: str = Depends(require_admin),
):
    with session_scope() as session:
        rep = session.get(Rep, rep_id)
        if rep is None:
            raise HTTPException(404, "rep not found")
        if name is not None:
            rep.name = name
        if timezone is not None:
            rep.timezone = timezone or "UTC"
        if team is not None:
            rep.team = team or None
        if daily_lead_cap is not None:
            rep.daily_lead_cap = int(daily_lead_cap) if daily_lead_cap.strip() else None
        if is_active is not None:
            rep.is_active = is_active
    response.headers["X-Toast"] = "Saved"
    return {"ok": True}


@router.delete("/{rep_id}")
def reps_delete(
    rep_id: str,
    response: Response,
    _user: str = Depends(require_admin),
):
    """Smart delete: hard-delete if nothing references this rep, otherwise soft-deactivate.

    Anything referencing the rep is detected via:
      - leads.assigned_rep_email
      - company_rep_assignments.rep_email
      - routing_rules.assigned_rep_email
    """
    with session_scope() as session:
        rep = session.get(Rep, rep_id)
        if rep is None:
            raise HTTPException(404, "rep not found")
        email = rep.email

        lead_refs = int(
            session.execute(
                select(func.count(Lead.id)).where(Lead.assigned_rep_email == email)
            ).scalar_one()
            or 0
        )
        assignment_refs = int(
            session.execute(
                select(func.count(CompanyRepAssignment.id)).where(
                    CompanyRepAssignment.rep_email == email
                )
            ).scalar_one()
            or 0
        )
        rule_refs = int(
            session.execute(
                select(func.count(RoutingRule.id)).where(
                    RoutingRule.assigned_rep_email == email
                )
            ).scalar_one()
            or 0
        )
        total_refs = lead_refs + assignment_refs + rule_refs

        if total_refs == 0:
            session.delete(rep)
            response.headers["X-Toast"] = f"Removed {email}"
            response.headers["HX-Redirect"] = "/admin/reps"
            return {"deleted": True}

        rep.is_active = False
        parts = []
        if lead_refs:
            parts.append(f"{lead_refs} lead(s)")
        if assignment_refs:
            parts.append(f"{assignment_refs} per-company assignment(s)")
        if rule_refs:
            parts.append(f"{rule_refs} routing rule(s)")
        message = (
            f"{email} is referenced by {' and '.join(parts)}, so it was deactivated "
            "instead of removed. Reactivate via the active/inactive pill, or remove the "
            "references first and try again."
        )
        # HX-Trigger fires a custom client event that JS handles with alert() + reload.
        # This survives the page redirect (the toast doesn't).
        response.headers["HX-Trigger"] = json.dumps({"rep-deactivated": {"message": message}})
        return {
            "deactivated": True,
            "message": message,
            "lead_refs": lead_refs,
            "assignment_refs": assignment_refs,
            "rule_refs": rule_refs,
        }
