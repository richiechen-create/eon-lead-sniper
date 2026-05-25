import json
from typing import Optional

from fastapi import APIRouter, Body, Depends, Form, HTTPException, Request, Response
from sqlalchemy import select

from app.admin.auth import require_admin
from app.admin.templating import render
from app.countries import non_canonical_countries
from app.db import session_scope
from app.models import Company, Rep, RoutingRule
from app.routing.engine import _matches  # type: ignore[attr-defined]

router = APIRouter(prefix="/routing-rules")


def _parse_conditions(raw: str) -> dict:
    if not raw or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"conditions must be valid JSON: {exc}")
    if not isinstance(data, dict):
        raise HTTPException(400, "conditions must be a JSON object")
    countries = data.get("company_country") or []
    if isinstance(countries, list):
        bad = non_canonical_countries(countries)
        if bad:
            msg = (
                "company_country contains unknown country names: "
                + ", ".join(repr(b) for b in bad)
                + ". Use canonical Apollo names."
            )
            raise HTTPException(status_code=400, detail=msg, headers={"X-Toast": msg})
    return data


@router.get("")
def routing_index(request: Request, _user: str = Depends(require_admin)):
    with session_scope() as session:
        rules = list(
            session.execute(
                select(RoutingRule).order_by(RoutingRule.priority.asc(), RoutingRule.created_at.asc())
            ).scalars()
        )
        reps = list(session.execute(select(Rep).where(Rep.is_active == True)).scalars())  # noqa: E712
    return render(request, "routing_rules.html", rules=rules, reps=reps)


@router.post("")
def routing_create(
    response: Response,
    name: str = Form(...),
    priority: int = Form(...),
    conditions: str = Form("{}"),
    assigned_rep_email: str = Form(...),
    assigned_rep_name: str = Form(""),
    _user: str = Depends(require_admin),
):
    with session_scope() as session:
        rule = RoutingRule(
            name=name,
            priority=priority,
            conditions=_parse_conditions(conditions),
            assigned_rep_email=assigned_rep_email,
            assigned_rep_name=assigned_rep_name or assigned_rep_email,
            is_active=True,
        )
        session.add(rule)
    response.headers["X-Toast"] = "Rule added"
    response.headers["HX-Redirect"] = "/admin/routing-rules"
    return {"ok": True}


@router.patch("/{rule_id}")
def routing_update(
    rule_id: str,
    response: Response,
    name: Optional[str] = Form(None),
    priority: Optional[int] = Form(None),
    conditions: Optional[str] = Form(None),
    assigned_rep_email: Optional[str] = Form(None),
    assigned_rep_name: Optional[str] = Form(None),
    is_active: Optional[bool] = Form(None),
    _user: str = Depends(require_admin),
):
    with session_scope() as session:
        rule = session.get(RoutingRule, rule_id)
        if rule is None:
            raise HTTPException(404, "rule not found")
        if name is not None:
            rule.name = name
        if priority is not None:
            rule.priority = priority
        if conditions is not None:
            rule.conditions = _parse_conditions(conditions)
        if assigned_rep_email is not None:
            rule.assigned_rep_email = assigned_rep_email
        if assigned_rep_name is not None:
            rule.assigned_rep_name = assigned_rep_name
        if is_active is not None:
            rule.is_active = is_active
    response.headers["X-Toast"] = "Saved"
    return {"ok": True}


@router.post("/reorder")
def routing_reorder(
    response: Response,
    payload: dict = Body(...),
    _user: str = Depends(require_admin),
):
    """Persist new priorities. Payload: {"order": ["<rule_id>", "<rule_id>", ...]}"""
    order = payload.get("order") or []
    with session_scope() as session:
        for index, rule_id in enumerate(order):
            rule = session.get(RoutingRule, rule_id)
            if rule is not None:
                rule.priority = (index + 1) * 10
    response.headers["X-Toast"] = "Reordered"
    return {"ok": True}


@router.post("/test")
def routing_test(
    payload: dict = Body(...),
    _user: str = Depends(require_admin),
) -> dict:
    """Pick a company, see which active rule matches first."""
    company_id = payload.get("company_id")
    if not company_id:
        raise HTTPException(400, "company_id required")
    with session_scope() as session:
        company = session.get(Company, company_id)
        if company is None:
            raise HTTPException(404, "company not found")
        rules = list(
            session.execute(
                select(RoutingRule)
                .where(RoutingRule.is_active == True)  # noqa: E712
                .order_by(RoutingRule.priority.asc())
            ).scalars()
        )
        for rule in rules:
            if not rule.conditions or _matches(rule.conditions, company):
                return {
                    "matched_rule": rule.name,
                    "rule_id": str(rule.id),
                    "assigned_rep_email": rule.assigned_rep_email,
                }
    return {"matched_rule": None, "assigned_rep_email": None}
