import json
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from sqlalchemy import func, select

from app.admin.auth import require_admin
from app.admin.templating import render
from app.db import session_scope
from app.models import Company, CompanyRepAssignment, Lead, Rep, RoutingRule
from app.timezones import is_valid_timezone

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
    tz = (timezone or "UTC").strip() or "UTC"
    if not is_valid_timezone(tz):
        msg = f"'{tz}' is not a recognized IANA timezone. Pick one from the dropdown."
        raise HTTPException(status_code=400, detail=msg, headers={"X-Toast": msg})

    with session_scope() as session:
        existing = session.execute(select(Rep).where(Rep.email == email)).scalar_one_or_none()
        if existing is not None:
            raise HTTPException(400, "rep email already exists")
        session.add(
            Rep(
                email=email.strip().lower(),
                name=name,
                timezone=tz,
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
    email: Optional[str] = Form(None),
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

        if email is not None:
            new_email = email.strip().lower()
            if not new_email or "@" not in new_email:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid email",
                    headers={"X-Toast": "Invalid email"},
                )
            if new_email != rep.email:
                # Ensure the new email isn't already taken by another rep.
                clash = session.execute(
                    select(Rep).where(Rep.email == new_email)
                ).scalar_one_or_none()
                if clash is not None and clash.id != rep.id:
                    msg = f"Another rep already has email {new_email}"
                    raise HTTPException(
                        status_code=400, detail=msg, headers={"X-Toast": msg}
                    )
                old_email = rep.email
                # Cascade the email change everywhere it's referenced.
                lead_updates = (
                    session.query(Lead)
                    .filter(Lead.assigned_rep_email == old_email)
                    .update({Lead.assigned_rep_email: new_email})
                )
                cra_updates = (
                    session.query(CompanyRepAssignment)
                    .filter(CompanyRepAssignment.rep_email == old_email)
                    .update({CompanyRepAssignment.rep_email: new_email})
                )
                rule_updates = (
                    session.query(RoutingRule)
                    .filter(RoutingRule.assigned_rep_email == old_email)
                    .update({RoutingRule.assigned_rep_email: new_email})
                )
                rep.email = new_email
                response.headers["X-Toast"] = (
                    f"Renamed to {new_email}. Updated {lead_updates} lead(s), "
                    f"{cra_updates} assignment(s), {rule_updates} rule(s)."
                )

        if name is not None:
            rep.name = name
        if timezone is not None:
            tz = (timezone or "UTC").strip() or "UTC"
            if not is_valid_timezone(tz):
                msg = f"'{tz}' is not a recognized IANA timezone."
                raise HTTPException(
                    status_code=400, detail=msg, headers={"X-Toast": msg}
                )
            rep.timezone = tz
        if team is not None:
            rep.team = team or None
        if daily_lead_cap is not None:
            rep.daily_lead_cap = int(daily_lead_cap) if daily_lead_cap.strip() else None
        if is_active is not None:
            rep.is_active = is_active

    response.headers.setdefault("X-Toast", "Saved")
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
        rule_rows = list(
            session.execute(
                select(RoutingRule.name, RoutingRule.is_active).where(
                    RoutingRule.assigned_rep_email == email
                )
            ).all()
        )
        assignment_rows = list(
            session.execute(
                select(Company.company_name, CompanyRepAssignment.lead_country)
                .join(Company, Company.id == CompanyRepAssignment.company_id)
                .where(CompanyRepAssignment.rep_email == email)
            ).all()
        )
        rule_refs = len(rule_rows)
        assignment_refs = len(assignment_rows)
        total_refs = lead_refs + assignment_refs + rule_refs

        if total_refs == 0:
            session.delete(rep)
            response.headers["X-Toast"] = f"Removed {email}"
            response.headers["HX-Redirect"] = "/admin/reps"
            return {"deleted": True}

        rep.is_active = False

        parts: list[str] = []
        if lead_refs:
            parts.append(f"{lead_refs} lead(s)")
        if rule_refs:
            names = [
                f"\"{n}\"{'' if active else ' (inactive)'}"
                for n, active in rule_rows
            ]
            parts.append(
                f"{rule_refs} routing rule(s): " + ", ".join(names[:5])
                + (f", +{rule_refs - 5} more" if rule_refs > 5 else "")
            )
        if assignment_refs:
            pairs = [
                f"{cn} ({country if country != '*' else 'any'})"
                for cn, country in assignment_rows
            ]
            parts.append(
                f"{assignment_refs} per-company assignment(s): " + ", ".join(pairs[:5])
                + (f", +{assignment_refs - 5} more" if assignment_refs > 5 else "")
            )

        message = (
            f"{email} was deactivated instead of removed because it is referenced by:\n\n"
            + "\n".join(f"  • {p}" for p in parts)
            + "\n\nTo fully remove this rep, edit Routing defaults / per-company "
            "assignments to point elsewhere, or accept the deactivation (digests stop, "
            "but historical leads keep their attribution)."
        )
        response.headers["HX-Trigger"] = json.dumps({"rep-deactivated": {"message": message}})
        return {
            "deactivated": True,
            "message": message,
            "lead_refs": lead_refs,
            "assignment_refs": assignment_refs,
            "rule_refs": rule_refs,
            "rules": [n for n, _ in rule_rows],
            "assignments": [
                {"company": cn, "lead_country": country} for cn, country in assignment_rows
            ],
        }
