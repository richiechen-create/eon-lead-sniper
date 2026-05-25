import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.leads.suppression import is_suppressed
from app.models import Lead
from app.models.base import utcnow


@dataclass
class LeadCandidate:
    apollo_person_id: str
    company_id: uuid.UUID
    company_domain: str
    full_name: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    title: Optional[str] = None
    seniority: Optional[str] = None
    department: Optional[str] = None
    linkedin_url: Optional[str] = None
    email: Optional[str] = None
    email_status: Optional[str] = None
    person_country: Optional[str] = None
    person_city: Optional[str] = None
    raw_apollo_payload: Optional[dict] = None


@dataclass
class RoutingAssignment:
    assigned_rep_email: str
    assigned_rep_name: str
    routing_rule_id: Optional[uuid.UUID]
    routing_status: str  # 'matched' | 'fallback'


def lead_exists(session: Session, apollo_person_id: str) -> bool:
    stmt = select(Lead.id).where(Lead.apollo_person_id == apollo_person_id).limit(1)
    return session.execute(stmt).scalar_one_or_none() is not None


def insert_lead(
    session: Session,
    candidate: LeadCandidate,
    routing: RoutingAssignment,
) -> Optional[Lead]:
    """Insert a new lead, honoring idempotency + suppression. Returns the Lead or None if skipped."""
    if lead_exists(session, candidate.apollo_person_id):
        return None

    dnc = is_suppressed(
        session,
        email=candidate.email,
        domain=candidate.company_domain,
        apollo_person_id=candidate.apollo_person_id,
    )
    if dnc is not None:
        return None

    delivery_status = "pending" if candidate.email else "skipped"

    lead = Lead(
        company_id=candidate.company_id,
        apollo_person_id=candidate.apollo_person_id,
        full_name=candidate.full_name,
        first_name=candidate.first_name,
        last_name=candidate.last_name,
        title=candidate.title,
        seniority=candidate.seniority,
        department=candidate.department,
        linkedin_url=candidate.linkedin_url,
        email=candidate.email,
        email_status=candidate.email_status,
        person_country=candidate.person_country,
        person_city=candidate.person_city,
        assigned_rep_email=routing.assigned_rep_email,
        assigned_rep_name=routing.assigned_rep_name,
        routing_rule_id=routing.routing_rule_id,
        routing_status=routing.routing_status,
        delivery_status=delivery_status,
        date_discovered=utcnow(),
        date_enriched=utcnow() if candidate.email else None,
        raw_apollo_payload=candidate.raw_apollo_payload,
    )
    session.add(lead)
    session.flush()
    return lead
