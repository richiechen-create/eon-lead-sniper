"""Seed the dev SQLite DB with realistic sample data.

Usage:
  python -m scripts.seed_dev          # additive — only inserts what's missing
  python -m scripts.seed_dev --wipe   # drops sqlite file first, fresh start

What it does:
  1. Runs alembic upgrade head to create the schema.
  2. Bootstraps companies/profiles/reps/routing_rules from config/*.yaml
     (the same files prod uses, so it mirrors prod structure).
  3. Inserts fake leads across multiple reps in several delivery_status
     and routing_status values so /admin/leads, the dashboard cards, and
     the digest preview all have data.
  4. Inserts one prior EnrichmentRun and one DigestRun so /admin/runs
     looks populated.

Nothing here calls Apollo or SMTP. It only writes to the local DB.

Run via `make seed` or `make reset` for a one-liner.
"""
import argparse
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

# Force dev config BEFORE any app modules import + cache settings.
os.environ.setdefault("DATABASE_URL", "sqlite:///./lead_engine_dev.sqlite")
os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("APOLLO_API_KEY", "dev-fake")
os.environ.setdefault("SMTP_USERNAME", "")
os.environ.setdefault("SMTP_PASSWORD", "")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "dev")
os.environ.setdefault("SESSION_SECRET", "dev-only-not-secret-32-chars-aaaaaaa")
os.environ.setdefault("INTERNAL_API_KEY", "dev-internal-key")
os.environ.setdefault("DEFAULT_REP_EMAIL", "dan@example.com")

DEV_SQLITE = Path("lead_engine_dev.sqlite")


def _wipe() -> None:
    if DEV_SQLITE.exists():
        DEV_SQLITE.unlink()
        print(f"  Wiped {DEV_SQLITE}")


def _alembic_upgrade() -> None:
    print("→ Running alembic upgrade head")
    env = {**os.environ}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=True,
        env=env,
    )


def _yaml_sync() -> None:
    print("→ Bootstrapping config/*.yaml")
    from app.db import session_scope
    from app.sync.yaml_config import sync_all

    with session_scope() as session:
        report = sync_all(session, Path("config").resolve())
    summary = report.as_dict()
    for key, value in summary.items():
        print(f"  {key}: {value}")


def _seed_leads() -> None:
    """Insert sample leads + a run history row."""
    print("→ Seeding sample leads + run history")
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Company, DigestRun, EnrichmentRun, Lead, Rep
    from app.models.base import utcnow

    now = utcnow()

    sample_leads = [
        # rep_email, company_domain, first, last, title, country, status, routing
        ("dan@eonreality.com",                 "exxonmobil.com", "Sarah",   "Chen",     "VP Learning & Development",      "United States", "pending",   "fallback"),
        ("dan@eonreality.com",                 "shell.com",      "Marcus",  "Weber",    "EHS Director",                   "Netherlands",   "pending",   "fallback"),
        ("dan@eonreality.com",                 "bp.com",         "Priya",   "Sharma",   "Head of Safety Training",        "India",         "pending",   "fallback"),
        ("sales_us@eonreality.com",            "chevron.com",    "James",   "O'Brien",  "Senior L&D Manager",             "United States", "pending",   "rule_matched"),
        ("sales_us@eonreality.com",            "exxonmobil.com", "Aisha",   "Patel",    "Director of EHS",                "United States", "delivered", "rule_matched"),
        ("leadgen_mfg_americas@eonreality.com","ge.com",         "Carlos",  "Mendez",   "VP Training",                    "Brazil",        "pending",   "rule_matched"),
        ("leadgen_mfg_americas@eonreality.com","boeing.com",     "Yuki",    "Tanaka",   "Director Talent Development",    "Japan",         "delivered", "rule_matched"),
        ("leadgen_mfg_americas@eonreality.com","siemens.com",    "Elena",   "Volkov",   "Chief Learning Officer",         "Germany",       "skipped",   "rule_matched"),
    ]

    with session_scope() as session:
        existing = session.execute(select(Lead).limit(1)).scalar_one_or_none()
        if existing is not None:
            print("  (leads already exist — skipping lead seed)")
        else:
            # Need a company for each domain. Fall back to ANY active company
            # if the prod domain isn't in our YAML bootstrap.
            companies = list(session.execute(select(Company).where(Company.is_active == True)).scalars())  # noqa: E712
            if not companies:
                print("  No companies in DB — run YAML sync first. Skipping leads.")
                return
            by_domain = {c.domain: c for c in companies}

            inserted = 0
            for i, (rep_email, domain, first, last, title, country, status, routing) in enumerate(sample_leads):
                company = by_domain.get(domain) or companies[i % len(companies)]
                # Find rep's name; fall back to email if no Rep row exists.
                rep = session.execute(select(Rep).where(Rep.email == rep_email)).scalar_one_or_none()
                rep_name = rep.name if rep else rep_email
                discovered = now - timedelta(days=(7 - i // 2), hours=(i % 12))
                lead = Lead(
                    company_id=company.id,
                    apollo_person_id=f"dev-seed-{i}-{uuid.uuid4().hex[:8]}",
                    full_name=f"{first} {last}",
                    first_name=first,
                    last_name=last,
                    title=title,
                    seniority="vp" if "VP" in title else ("director" if "Director" in title else "manager"),
                    department="Learning & Development",
                    linkedin_url=f"https://linkedin.com/in/{first.lower()}-{last.lower().replace(chr(39),'')}-dev",
                    email=f"{first.lower()}.{last.lower().replace(chr(39),'')}@{company.domain}",
                    email_status="verified",
                    person_country=country,
                    assigned_rep_email=rep_email,
                    assigned_rep_name=rep_name,
                    routing_status=routing,
                    delivery_status=status,
                    date_discovered=discovered,
                    date_enriched=discovered,
                    delivered_at=(discovered + timedelta(hours=1)) if status == "delivered" else None,
                )
                session.add(lead)
                inserted += 1
            print(f"  Inserted {inserted} sample leads")

        # Run history rows for /admin/runs.
        if session.execute(select(EnrichmentRun).limit(1)).scalar_one_or_none() is None:
            session.add(
                EnrichmentRun(
                    run_started_at=now - timedelta(days=1, hours=2),
                    run_completed_at=now - timedelta(days=1, hours=1, minutes=45),
                    companies_processed=12,
                    candidates_found=84,
                    new_leads_created=6,
                    contacts_enriched=5,
                    credits_consumed=5,
                    errors=[],
                )
            )
            print("  Inserted 1 sample EnrichmentRun")
        if session.execute(select(DigestRun).limit(1)).scalar_one_or_none() is None:
            session.add(
                DigestRun(
                    run_date=(now - timedelta(days=1)).date(),
                    reps_emailed=2,
                    total_leads_delivered=3,
                    errors=[],
                )
            )
            print("  Inserted 1 sample DigestRun")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wipe", action="store_true", help="Drop the sqlite file before seeding")
    args = parser.parse_args()

    if args.wipe:
        print("→ Wiping dev DB")
        _wipe()

    _alembic_upgrade()
    _yaml_sync()
    _seed_leads()

    print()
    print("Dev DB ready: lead_engine_dev.sqlite")
    print("Start the server with:  make dev")
    print("Login at http://localhost:8000/admin/login  (admin / dev)")


if __name__ == "__main__":
    main()
