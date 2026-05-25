import uuid
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Company, CompanyRepAssignment, Rep, RoutingRule

_CONDITION_KEYS = ("company_industry", "company_country", "company_tier", "company_domain")


@dataclass
class RoutingDecision:
    assigned_rep_email: str
    assigned_rep_name: str
    routing_rule_id: Optional[uuid.UUID]
    routing_status: str  # 'company_override' | 'rule_matched' | 'fallback'


def _matches(conditions: dict, company: Company) -> bool:
    """All present condition keys must match (AND). Unknown keys are ignored."""
    if not isinstance(conditions, dict):
        return False
    if "company_industry" in conditions:
        allowed = [str(x).strip().lower() for x in (conditions["company_industry"] or [])]
        if (company.industry or "").strip().lower() not in allowed:
            return False
    if "company_country" in conditions:
        allowed = conditions["company_country"] or []
        if (company.country or "") not in allowed:
            return False
    if "company_tier" in conditions:
        allowed = conditions["company_tier"] or []
        if (company.tier or "") not in allowed:
            return False
    if "company_domain" in conditions:
        allowed = conditions["company_domain"] or []
        if (company.domain or "") not in allowed:
            return False
    return True


def _lookup_rep(session: Session, email: str) -> Optional[Rep]:
    return session.execute(select(Rep).where(Rep.email == email)).scalar_one_or_none()


def _company_override(
    session: Session, company: Company, lead_country: Optional[str]
) -> Optional[CompanyRepAssignment]:
    """Find a per-company assignment: exact-country first, then '*' wildcard."""
    if lead_country:
        override = session.execute(
            select(CompanyRepAssignment).where(
                CompanyRepAssignment.company_id == company.id,
                CompanyRepAssignment.lead_country == lead_country,
            )
        ).scalar_one_or_none()
        if override is not None:
            return override
    return session.execute(
        select(CompanyRepAssignment).where(
            CompanyRepAssignment.company_id == company.id,
            CompanyRepAssignment.lead_country == "*",
        )
    ).scalar_one_or_none()


def route_lead(
    session: Session,
    company: Company,
    lead_country: Optional[str] = None,
) -> RoutingDecision:
    """Cascaded routing:

    1. Per-company per-country override (exact country, then '*' wildcard).
    2. Active routing rule, priority ASC.
    3. Fallback to DEFAULT_REP_EMAIL.

    `lead_country` is the person's country from Apollo, not the company's country.
    """
    # 1. Per-company override
    override = _company_override(session, company, lead_country)
    if override is not None:
        rep = _lookup_rep(session, override.rep_email)
        rep_name = rep.name if rep is not None else override.rep_email
        return RoutingDecision(
            assigned_rep_email=override.rep_email,
            assigned_rep_name=rep_name,
            routing_rule_id=None,
            routing_status="company_override",
        )

    # 2. Segment-level rule
    rules = session.execute(
        select(RoutingRule)
        .where(RoutingRule.is_active == True)  # noqa: E712
        .order_by(RoutingRule.priority.asc(), RoutingRule.created_at.asc())
    ).scalars().all()
    for rule in rules:
        if not rule.conditions or _matches(rule.conditions, company):
            return RoutingDecision(
                assigned_rep_email=rule.assigned_rep_email,
                assigned_rep_name=rule.assigned_rep_name,
                routing_rule_id=rule.id,
                routing_status="rule_matched",
            )

    # 3. Fallback
    settings = get_settings()
    return RoutingDecision(
        assigned_rep_email=settings.DEFAULT_REP_EMAIL,
        assigned_rep_name=settings.DEFAULT_REP_NAME,
        routing_rule_id=None,
        routing_status="fallback",
    )
