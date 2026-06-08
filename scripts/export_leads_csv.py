"""Standalone leads-to-CSV exporter. Hits the DB directly — useful when
the admin UI is down (Render outage, redeploy, etc.).

Usage (writes CSV to stdout — pipe to a file):

  # All leads with email, every segment
  DATABASE_URL='postgresql://...' .venv/bin/python -m scripts.export_leads_csv > leads.csv

  # Only one segment
  .venv/bin/python -m scripts.export_leads_csv --segment "oil and gas" > leads-og.csv

  # Include LinkedIn-only / no-email leads too
  .venv/bin/python -m scripts.export_leads_csv --include-no-email > leads-full.csv

  # Filter to one rep or one status
  .venv/bin/python -m scripts.export_leads_csv --rep dan@eonreality.com --status pending > dans-pending.csv

DATABASE_URL is read from the environment (sourced from .env automatically
by pydantic-settings the same way the app does).
"""
import argparse
import csv
import sys

from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from app.db import session_scope
from app.models import Company, Lead


COLUMNS = [
    "Segment", "Company", "Domain",
    "First Name", "Last Name", "Title",
    "Seniority", "Department",
    "Country", "City",
    "Email", "LinkedIn URL",
    "Routing Status", "Delivery Status",
    "Assigned Rep Email", "Assigned Rep Name",
    "Date Discovered",
]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--segment", default=None, help="Filter to one segment (case-insensitive, e.g. 'oil and gas')")
    p.add_argument("--rep", default=None, help="Filter to one rep email")
    p.add_argument("--status", default=None, choices=["pending", "delivered", "skipped"])
    p.add_argument("--routing-status", default=None, choices=["company_override", "rule_matched", "fallback"])
    p.add_argument("--include-no-email", action="store_true",
                   help="Include LinkedIn-only / no-contact leads (default: drop them)")
    args = p.parse_args()

    with session_scope() as session:
        stmt = (
            select(Lead)
            .options(joinedload(Lead.company))
            .join(Company, Lead.company_id == Company.id)
            .order_by(
                func.lower(func.trim(Company.industry)).asc().nulls_last(),
                Lead.date_discovered.desc(),
            )
        )
        if args.segment:
            stmt = stmt.where(func.lower(func.trim(Company.industry)) == args.segment.strip().lower())
        if args.rep:
            stmt = stmt.where(Lead.assigned_rep_email == args.rep)
        if args.status:
            stmt = stmt.where(Lead.delivery_status == args.status)
        if args.routing_status:
            stmt = stmt.where(Lead.routing_status == args.routing_status)
        if not args.include_no_email:
            stmt = stmt.where(Lead.email.is_not(None), Lead.email != "")

        leads = list(session.execute(stmt).scalars())

        writer = csv.writer(sys.stdout)
        writer.writerow(COLUMNS)
        for lead in leads:
            c = lead.company
            seg = ((c.industry if c else "") or "").title()
            writer.writerow([
                seg,
                (c.company_name if c else "") or "",
                (c.domain if c else "") or "",
                lead.first_name or "",
                lead.last_name or "",
                lead.title or "",
                lead.seniority or "",
                lead.department or "",
                lead.person_country or "",
                lead.person_city or "",
                lead.email or "",
                lead.linkedin_url or "",
                lead.routing_status or "",
                lead.delivery_status or "",
                lead.assigned_rep_email or "",
                lead.assigned_rep_name or "",
                lead.date_discovered.strftime("%Y-%m-%d") if lead.date_discovered else "",
            ])

    print(f"\n{len(leads)} rows exported", file=sys.stderr)


if __name__ == "__main__":
    main()
