from typing import Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import DoNotContact


def is_suppressed(
    session: Session,
    *,
    email: Optional[str],
    domain: Optional[str],
    apollo_person_id: Optional[str],
) -> Optional[DoNotContact]:
    """Return the matching DoNotContact row if the candidate is suppressed, else None.

    A row matches if ANY of (email, domain, apollo_person_id) on the DNC row equals
    the corresponding argument. Empty values on either side never match.
    """
    clauses = []
    if email:
        clauses.append(DoNotContact.email == email)
    if domain:
        clauses.append(DoNotContact.domain == domain)
    if apollo_person_id:
        clauses.append(DoNotContact.apollo_person_id == apollo_person_id)
    if not clauses:
        return None
    stmt = select(DoNotContact).where(or_(*clauses)).limit(1)
    return session.execute(stmt).scalar_one_or_none()
