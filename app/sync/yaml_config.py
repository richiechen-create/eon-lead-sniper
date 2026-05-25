from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Company, CompanyTargeting, Rep, RoutingRule, TargetingProfile


@dataclass
class SyncReport:
    profiles_upserted: int = 0
    profiles_deactivated: int = 0
    reps_upserted: int = 0
    reps_deactivated: int = 0
    rules_upserted: int = 0
    rules_deactivated: int = 0
    companies_upserted: int = 0
    companies_deactivated: int = 0
    companies_reactivated: int = 0
    company_warnings: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.company_warnings is None:
            self.company_warnings = []

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def _load_yaml(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r") as f:
        data = yaml.safe_load(f) or []
    if not isinstance(data, list):
        raise ValueError(f"{path} must contain a YAML list at the top level")
    return data


def sync_targeting_profiles(session: Session, path: Path) -> tuple[int, int]:
    rows = _load_yaml(path)
    yaml_names = {row["name"] for row in rows}

    existing = {p.name: p for p in session.execute(select(TargetingProfile)).scalars().all()}
    upserted = 0
    for row in rows:
        name = row["name"]
        profile = existing.get(name)
        if profile is None:
            profile = TargetingProfile(name=name)
            session.add(profile)
        profile.titles = row.get("titles", []) or []
        profile.seniorities = row.get("seniorities", []) or []
        profile.departments = row.get("departments", []) or []
        profile.locations = row.get("locations", []) or []
        profile.keywords = row.get("keywords", []) or []
        profile.is_active = True
        upserted += 1

    deactivated = 0
    for name, profile in existing.items():
        if name not in yaml_names and profile.is_active:
            profile.is_active = False
            deactivated += 1
    session.flush()
    return upserted, deactivated


def sync_reps(session: Session, path: Path) -> tuple[int, int]:
    rows = _load_yaml(path)
    yaml_emails = {row["email"] for row in rows}

    existing = {r.email: r for r in session.execute(select(Rep)).scalars().all()}
    upserted = 0
    for row in rows:
        email = row["email"]
        rep = existing.get(email)
        if rep is None:
            rep = Rep(email=email, name=row.get("name", email))
            session.add(rep)
        rep.name = row.get("name", rep.name)
        rep.timezone = row.get("timezone", "UTC")
        rep.team = row.get("team")
        rep.daily_lead_cap = row.get("daily_lead_cap")
        rep.is_active = True
        upserted += 1

    deactivated = 0
    for email, rep in existing.items():
        if email not in yaml_emails and rep.is_active:
            rep.is_active = False
            deactivated += 1
    session.flush()
    return upserted, deactivated


def sync_routing_rules(session: Session, path: Path) -> tuple[int, int]:
    rows = _load_yaml(path)
    yaml_names = {row["name"] for row in rows}

    existing = {r.name: r for r in session.execute(select(RoutingRule)).scalars().all()}
    upserted = 0
    for row in rows:
        name = row["name"]
        rule = existing.get(name)
        if rule is None:
            rule = RoutingRule(
                name=name,
                priority=row["priority"],
                conditions=row.get("conditions", {}) or {},
                assigned_rep_email=row["assigned_rep_email"],
                assigned_rep_name=row.get("assigned_rep_name", row["assigned_rep_email"]),
            )
            session.add(rule)
        else:
            rule.priority = row["priority"]
            rule.conditions = row.get("conditions", {}) or {}
            rule.assigned_rep_email = row["assigned_rep_email"]
            rule.assigned_rep_name = row.get("assigned_rep_name", row["assigned_rep_email"])
        rule.is_active = True
        upserted += 1

    deactivated = 0
    for name, rule in existing.items():
        if name not in yaml_names and rule.is_active:
            rule.is_active = False
            deactivated += 1
    session.flush()
    return upserted, deactivated


def sync_companies(session: Session, path: Path) -> tuple[int, int, int, list[str]]:
    """Bootstrap companies from YAML. Idempotent. After bootstrap the UI is source of truth.

    Behaviour matches the other sync_* helpers: rows present in YAML are upserted
    (re-activating soft-deleted ones); rows missing from YAML are LEFT ALONE
    (we never soft-delete companies via bootstrap, since the operator may have
    added many more via the UI). Removal only happens through the UI.
    """
    warnings: list[str] = []
    rows = _load_yaml(path)
    if not rows:
        return 0, 0, 0, warnings

    profiles_by_name = {
        p.name: p for p in session.execute(select(TargetingProfile)).scalars().all()
    }
    existing = {c.domain: c for c in session.execute(select(Company)).scalars().all()}

    upserted = 0
    reactivated = 0
    for row in rows:
        domain = (row.get("domain") or "").strip().lower()
        if not domain:
            warnings.append("row missing domain, skipped")
            continue
        company = existing.get(domain)
        was_inactive = company is not None and not company.is_active
        if company is None:
            company = Company(domain=domain, company_name=row.get("company_name") or domain)
            session.add(company)
            existing[domain] = company

        company.company_name = row.get("company_name") or company.company_name
        company.industry = row.get("industry")
        company.country = row.get("country")
        company.tier = row.get("tier")
        if row.get("max_contacts_per_run") is not None:
            company.max_contacts_per_run = int(row["max_contacts_per_run"])
        company.notes = row.get("notes")
        company.is_active = True

        if was_inactive:
            reactivated += 1
        upserted += 1

        session.flush()  # ensure id is assigned before linking

        wanted_profiles = row.get("targeting_profiles") or []
        unknown = [p for p in wanted_profiles if p not in profiles_by_name]
        if unknown:
            warnings.append(f"{domain}: unknown targeting profiles {unknown}")

        existing_links = {link.targeting_profile_id: link for link in company.targeting_links}
        wanted_ids = {
            profiles_by_name[name].id for name in wanted_profiles if name in profiles_by_name
        }
        for pid, link in list(existing_links.items()):
            if pid not in wanted_ids:
                session.delete(link)
        for pid in wanted_ids:
            if pid not in existing_links:
                session.add(CompanyTargeting(company_id=company.id, targeting_profile_id=pid))

    session.flush()
    return upserted, 0, reactivated, warnings


def sync_all(session: Session, config_dir: Path) -> SyncReport:
    report = SyncReport()
    report.profiles_upserted, report.profiles_deactivated = sync_targeting_profiles(
        session, config_dir / "targeting_profiles.yaml"
    )
    report.reps_upserted, report.reps_deactivated = sync_reps(
        session, config_dir / "reps.yaml"
    )
    report.rules_upserted, report.rules_deactivated = sync_routing_rules(
        session, config_dir / "routing_rules.yaml"
    )
    (
        report.companies_upserted,
        report.companies_deactivated,
        report.companies_reactivated,
        report.company_warnings,
    ) = sync_companies(session, config_dir / "companies.yaml")
    return report
