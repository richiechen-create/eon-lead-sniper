from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, or_, select

from app.admin.auth import require_admin
from app.admin.templating import render
from app.apollo.budget import budget_status
from app.db import session_scope
from app.maintenance import scan_country_drift
from app.models import Company, DigestRun, EnrichmentRun, Lead, Rep

router = APIRouter()


@router.get("/")
def dashboard_root(request: Request, _user: str = Depends(require_admin)):
    today = datetime.utcnow().date()
    start_today = datetime.combine(today, datetime.min.time())
    week_ago = start_today - timedelta(days=7)

    with session_scope() as session:
        runs_today = list(
            session.execute(
                select(EnrichmentRun).where(EnrichmentRun.run_started_at >= start_today)
            ).scalars()
        )
        digests_today = list(
            session.execute(select(DigestRun).where(DigestRun.run_date == today)).scalars()
        )

        credits_today = sum(r.credits_consumed or 0 for r in runs_today)
        credits_month, credits_limit, _ = budget_status(session)
        companies_processed_today = sum(r.companies_processed or 0 for r in runs_today)
        new_leads_today = sum(r.new_leads_created or 0 for r in runs_today)
        contacts_enriched_today = sum(r.contacts_enriched or 0 for r in runs_today)
        reps_emailed_today = sum(d.reps_emailed or 0 for d in digests_today)
        leads_delivered_today = sum(d.total_leads_delivered or 0 for d in digests_today)

        pending_per_rep = list(
            session.execute(
                select(Lead.assigned_rep_email, func.count(Lead.id))
                .where(Lead.delivery_status == "pending")
                .group_by(Lead.assigned_rep_email)
                .order_by(func.count(Lead.id).desc())
            )
        )

        leads_per_day = list(
            session.execute(
                select(func.date(Lead.date_discovered), func.count(Lead.id))
                .where(Lead.date_discovered >= week_ago)
                .group_by(func.date(Lead.date_discovered))
                .order_by(func.date(Lead.date_discovered))
            )
        )

        fallback_rate = 0.0
        last_week_total = int(
            session.execute(
                select(func.count(Lead.id)).where(Lead.date_discovered >= week_ago)
            ).scalar_one()
            or 0
        )
        if last_week_total > 0:
            last_week_fallback = int(
                session.execute(
                    select(func.count(Lead.id)).where(
                        Lead.date_discovered >= week_ago,
                        Lead.routing_status == "fallback",
                    )
                ).scalar_one()
                or 0
            )
            fallback_rate = round(100 * last_week_fallback / last_week_total, 1)

        recent_errors = []
        for r in sorted(runs_today, key=lambda x: x.run_started_at, reverse=True)[:3]:
            for e in (r.errors or [])[-3:]:
                recent_errors.append({"when": r.run_started_at, "source": "enrichment", "detail": e})
        for d in digests_today:
            for e in (d.errors or [])[-3:]:
                recent_errors.append({"when": d.created_at, "source": "digest", "detail": e})

        active_reps = int(
            session.execute(
                select(func.count(Rep.id)).where(Rep.is_active == True)  # noqa: E712
            ).scalar_one()
            or 0
        )

        drift = scan_country_drift(session, log=False)

        # Distinct industries among active companies, with count, for the
        # per-segment enrichment dropdown.
        seg_rows = list(
            session.execute(
                select(Company.industry, func.count(Company.id))
                .where(Company.is_active == True)  # noqa: E712
                .group_by(Company.industry)
                .order_by(Company.industry)
            ).all()
        )
        segments = []
        for industry, count in seg_rows:
            label = (industry or "").strip() or "(no segment)"
            segments.append({"label": label, "count": int(count)})

        # Active reps with at least one pending+deliverable lead — for the
        # test-digest rep picker dropdown. Deliverable means email OR
        # linkedin_url (same filter as the digest scheduler).
        rep_lead_counts = dict(
            session.execute(
                select(Lead.assigned_rep_email, func.count(Lead.id))
                .where(Lead.delivery_status == "pending")
                .where(or_(Lead.email.is_not(None), Lead.linkedin_url.is_not(None)))
                .group_by(Lead.assigned_rep_email)
            ).all()
        )
        active_rep_rows = list(
            session.execute(
                select(Rep.email, Rep.name)
                .where(Rep.is_active == True)  # noqa: E712
                .order_by(Rep.email)
            ).all()
        )
        test_digest_reps = [
            {"email": email, "name": name, "count": int(rep_lead_counts[email])}
            for email, name in active_rep_rows
            if rep_lead_counts.get(email)
        ]

    return render(
        request,
        "dashboard.html",
        credits_today=credits_today,
        credits_month=credits_month,
        credits_limit=credits_limit,
        credits_pct=round(100 * credits_month / max(credits_limit, 1), 1),
        companies_processed_today=companies_processed_today,
        new_leads_today=new_leads_today,
        contacts_enriched_today=contacts_enriched_today,
        reps_emailed_today=reps_emailed_today,
        leads_delivered_today=leads_delivered_today,
        pending_per_rep=pending_per_rep,
        leads_per_day=leads_per_day,
        fallback_rate=fallback_rate,
        recent_errors=recent_errors[-10:],
        active_reps=active_reps,
        today=today,
        drift_country_count=len(drift["bad_countries"]),
        drift_leads_affected=drift["leads_affected"],
        drift_examples=drift["bad_countries"][:5],
        segments=segments,
        test_digest_reps=test_digest_reps,
    )
