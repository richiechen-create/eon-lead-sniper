from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.countries import non_canonical_countries
from app.models import ApiCallLog, Lead


def purge_old_api_call_log(session: Session, *, older_than_days: int = 30) -> int:
    cutoff = datetime.utcnow() - timedelta(days=older_than_days)
    result = session.execute(delete(ApiCallLog).where(ApiCallLog.called_at < cutoff))
    return result.rowcount or 0


def scan_country_drift(session: Session, *, log: bool = True) -> dict:
    """Find distinct lead.person_country values not in the canonical list.

    Returns a summary dict; optionally records the result in api_call_log so the
    daily admin summary can report it. Non-destructive — existing rows untouched.
    """
    distinct = list(
        session.execute(
            select(Lead.person_country).where(Lead.person_country.is_not(None)).distinct()
        ).scalars()
    )
    bad = non_canonical_countries(distinct)

    leads_affected = 0
    if bad:
        leads_affected = int(
            session.execute(
                select(func.count(Lead.id)).where(Lead.person_country.in_(bad))
            ).scalar_one()
            or 0
        )

    summary = {
        "bad_countries": bad,
        "leads_affected": leads_affected,
        "distinct_seen": len(distinct),
    }

    if log:
        session.add(
            ApiCallLog(
                endpoint="country_drift",
                http_status=200,
                credits_used=0,
                request_payload={"distinct_seen": len(distinct)},
                response_summary=summary,
            )
        )
        session.flush()
    return summary
