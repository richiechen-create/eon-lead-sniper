from datetime import datetime
from typing import Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import EnrichmentRun


def credits_used_this_month(session: Session, now: datetime | None = None) -> int:
    """Sum of credits_consumed across enrichment_runs started in the current calendar month."""
    now = now or datetime.utcnow()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    stmt = select(func.coalesce(func.sum(EnrichmentRun.credits_consumed), 0)).where(
        EnrichmentRun.run_started_at >= start
    )
    return int(session.execute(stmt).scalar_one() or 0)


def budget_status(session: Session, now: datetime | None = None) -> Tuple[int, int, bool]:
    """Returns (used, limit, exhausted)."""
    settings = get_settings()
    used = credits_used_this_month(session, now=now)
    return used, settings.CREDIT_BUDGET_MONTHLY, used >= settings.CREDIT_BUDGET_MONTHLY
