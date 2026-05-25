from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.apollo.budget import budget_status
from app.config import get_settings
from app.email.sender import send_email
from app.maintenance import scan_country_drift
from app.models import DigestRun, EnrichmentRun, Lead


@dataclass
class AdminSummary:
    run_date: date
    companies_processed: int
    new_leads_created: int
    contacts_enriched: int
    credits_today: int
    credits_month: int
    credits_limit: int
    reps_emailed: int
    leads_delivered: int
    leads_skipped_today: int
    skipped_7d_avg: float
    matched_today: int
    fallback_today: int
    apollo_errors: int
    email_errors: int
    drift_countries: list[str] = None  # type: ignore[assignment]
    drift_leads_affected: int = 0

    def __post_init__(self) -> None:
        if self.drift_countries is None:
            self.drift_countries = []

    def to_text(self) -> str:
        drift_block = ""
        if self.drift_countries:
            sample = ", ".join(self.drift_countries[:5])
            extra = (
                f" (+{len(self.drift_countries) - 5} more)"
                if len(self.drift_countries) > 5
                else ""
            )
            drift_block = (
                "\nDATA HYGIENE\n"
                f"  Non-canonical countries in leads: {len(self.drift_countries)} "
                f"({self.drift_leads_affected} lead(s) affected)\n"
                f"    Examples: {sample}{extra}\n"
                "    Fix by editing APOLLO_OVERRIDES in app/countries.py and redeploying.\n"
            )

        return (
            f"EON Lead Sniper Daily Summary - {self.run_date.isoformat()}\n\n"
            "ENRICHMENT\n"
            f"  Companies processed:    {self.companies_processed}\n"
            f"  New leads created:      {self.new_leads_created}\n"
            f"  Contacts enriched:      {self.contacts_enriched}\n"
            f"  Credits consumed today: {self.credits_today}\n"
            f"  Credits used this month: {self.credits_month:,} / {self.credits_limit:,}\n\n"
            "DIGESTS\n"
            f"  Reps emailed:           {self.reps_emailed}\n"
            f"  Leads delivered:        {self.leads_delivered}\n"
            f"  Leads skipped (no email): {self.leads_skipped_today}\n"
            f"  Skipped trend (7-day avg): {self.skipped_7d_avg:.1f}\n\n"
            "ROUTING\n"
            f"  Matched by rule:        {self.matched_today}\n"
            f"  Fallback to Dan:        {self.fallback_today}\n\n"
            "ERRORS\n"
            f"  Apollo timeouts:        {self.apollo_errors}\n"
            f"  Email send failures:    {self.email_errors}\n"
            f"{drift_block}\n"
            "(Full logs available in DB)\n"
        )


def _start_of_day(d: date) -> datetime:
    return datetime.combine(d, datetime.min.time())


def _today_runs(session: Session, today: date) -> list[EnrichmentRun]:
    start = _start_of_day(today)
    end = start + timedelta(days=1)
    stmt = select(EnrichmentRun).where(
        EnrichmentRun.run_started_at >= start,
        EnrichmentRun.run_started_at < end,
    )
    return list(session.execute(stmt).scalars().all())


def _digests_today(session: Session, today: date) -> list[DigestRun]:
    stmt = select(DigestRun).where(DigestRun.run_date == today)
    return list(session.execute(stmt).scalars().all())


def collect(session: Session, today: Optional[date] = None) -> AdminSummary:
    today = today or datetime.utcnow().date()
    start_today = _start_of_day(today)
    end_today = start_today + timedelta(days=1)

    runs = _today_runs(session, today)
    companies_processed = sum(r.companies_processed or 0 for r in runs)
    new_leads_created = sum(r.new_leads_created or 0 for r in runs)
    contacts_enriched = sum(r.contacts_enriched or 0 for r in runs)
    credits_today = sum(r.credits_consumed or 0 for r in runs)
    apollo_errors = sum(len(r.errors or []) for r in runs)

    credits_month, credits_limit, _ = budget_status(session, now=datetime.utcnow())

    digests = _digests_today(session, today)
    reps_emailed = sum(d.reps_emailed or 0 for d in digests)
    leads_delivered = sum(d.total_leads_delivered or 0 for d in digests)
    email_errors = sum(len(d.errors or []) for d in digests)

    leads_skipped_today = int(
        session.execute(
            select(func.count(Lead.id)).where(
                Lead.date_discovered >= start_today,
                Lead.date_discovered < end_today,
                Lead.delivery_status == "skipped",
            )
        ).scalar_one()
        or 0
    )

    seven_days_ago = start_today - timedelta(days=7)
    skipped_total_7d = int(
        session.execute(
            select(func.count(Lead.id)).where(
                Lead.date_discovered >= seven_days_ago,
                Lead.date_discovered < start_today,
                Lead.delivery_status == "skipped",
            )
        ).scalar_one()
        or 0
    )
    skipped_7d_avg = skipped_total_7d / 7.0

    matched_today = int(
        session.execute(
            select(func.count(Lead.id)).where(
                Lead.date_discovered >= start_today,
                Lead.date_discovered < end_today,
                Lead.routing_status == "matched",
            )
        ).scalar_one()
        or 0
    )
    fallback_today = int(
        session.execute(
            select(func.count(Lead.id)).where(
                Lead.date_discovered >= start_today,
                Lead.date_discovered < end_today,
                Lead.routing_status == "fallback",
            )
        ).scalar_one()
        or 0
    )

    drift = scan_country_drift(session, log=True)

    return AdminSummary(
        run_date=today,
        companies_processed=companies_processed,
        new_leads_created=new_leads_created,
        contacts_enriched=contacts_enriched,
        credits_today=credits_today,
        credits_month=credits_month,
        credits_limit=credits_limit,
        reps_emailed=reps_emailed,
        leads_delivered=leads_delivered,
        leads_skipped_today=leads_skipped_today,
        skipped_7d_avg=skipped_7d_avg,
        matched_today=matched_today,
        fallback_today=fallback_today,
        apollo_errors=apollo_errors,
        email_errors=email_errors,
        drift_countries=drift["bad_countries"],
        drift_leads_affected=drift["leads_affected"],
    )


def send_admin_summary(session: Session, today: Optional[date] = None) -> AdminSummary:
    settings = get_settings()
    summary = collect(session, today=today)
    if settings.ADMIN_EMAIL and settings.SMTP_USERNAME and settings.SMTP_PASSWORD:
        send_email(
            to=[settings.ADMIN_EMAIL],
            subject=f"EON Lead Sniper Daily Summary - {summary.run_date.isoformat()}",
            text=summary.to_text(),
            retries=1,
        )
    return summary
