import csv
import io
from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from sqlalchemy import select

from app.admin.auth import require_admin
from app.admin.templating import render, templates
from app.countries import is_canonical_country
from app.db import session_scope
from app.models import (
    Company,
    CompanyRepAssignment,
    CompanyTargeting,
    Rep,
    RoutingRule,
    TargetingProfile,
)

router = APIRouter(prefix="/companies")


def _split_profile_names(raw: str) -> list[str]:
    return [p.strip() for p in (raw or "").replace(",", ";").split(";") if p.strip()]


def _segment_key(industry: str | None) -> str:
    """Group key: lower-cased, trimmed. Empty industries fall into the '' bucket."""
    return (industry or "").strip().lower()


def _segment_label(industry: str | None) -> str:
    """Display label: trimmed; title-case if all lowercase, else preserved."""
    s = (industry or "").strip()
    if not s:
        return "(no segment)"
    if s.islower():
        return s.title()
    return s


def _build_segment_team_map(session) -> dict[str, tuple[str, str | None]]:
    """For each industry referenced by an active routing rule, return
    (rep_email, team) of the highest-priority matching rule.
    Returns dict keyed by the lower-cased industry string.
    """
    rules = list(
        session.execute(
            select(RoutingRule)
            .where(RoutingRule.is_active == True)  # noqa: E712
            .order_by(RoutingRule.priority.asc(), RoutingRule.created_at.asc())
        ).scalars()
    )
    reps_by_email = {r.email: r for r in session.execute(select(Rep)).scalars()}

    out: dict[str, tuple[str, str | None]] = {}
    for rule in rules:
        industries = (rule.conditions or {}).get("company_industry") or []
        if not isinstance(industries, list):
            continue
        for industry in industries:
            key = _segment_key(industry)
            if key in out:
                continue  # first (highest priority) wins
            rep = reps_by_email.get(rule.assigned_rep_email)
            team = rep.team if rep is not None else None
            out[key] = (rule.assigned_rep_email, team)
    return out


@router.get("")
def companies_index(request: Request, _user: str = Depends(require_admin)):
    with session_scope() as session:
        companies = list(
            session.execute(select(Company).order_by(Company.company_name)).scalars()
        )
        profiles = list(
            session.execute(
                select(TargetingProfile).where(TargetingProfile.is_active == True)  # noqa: E712
            ).scalars()
        )
        segment_team = _build_segment_team_map(session)

        # Per-company assignment summary (country -> rep_email).
        assignments = list(
            session.execute(select(CompanyRepAssignment)).scalars()
        )
        assignments_by_company: dict = defaultdict(list)
        for a in assignments:
            assignments_by_company[a.company_id].append(a)

        # Group companies by segment key.
        by_key: dict[str, list] = defaultdict(list)
        label_by_key: dict[str, str] = {}
        for c in companies:
            key = _segment_key(c.industry)
            label_by_key[key] = _segment_label(c.industry)
            linked_profiles = [
                link.profile for link in c.targeting_links if link.profile.is_active
            ]
            assignment_pairs = [
                (a.lead_country, a.rep_email)
                for a in assignments_by_company.get(c.id, [])
            ]
            by_key[key].append(
                {
                    "company": c,
                    "profile_names": [p.name for p in linked_profiles],
                    "linked_profiles": linked_profiles,
                    "linked_profile_ids": {p.id for p in linked_profiles},
                    "assignments": assignment_pairs,
                }
            )

        # Ensure every segment referenced by routing rules has an entry,
        # even if there are no companies yet (AC #7 — zero-active panels still render).
        for key in segment_team:
            if key not in by_key:
                by_key[key] = []
                label_by_key[key] = _segment_label(key)

        segments = []
        for key in sorted(by_key.keys(), key=lambda k: label_by_key[k].lower()):
            rows = by_key[key]
            active_count = sum(1 for r in rows if r["company"].is_active)
            assigned = segment_team.get(key)
            segments.append(
                {
                    "key": key,
                    "label": label_by_key[key],
                    "rows": rows,
                    "active_count": active_count,
                    "team": (assigned[1] if assigned else None),
                    "assigned_rep_email": (assigned[0] if assigned else None),
                }
            )

        distinct_industries = sorted({s["label"] for s in segments if s["key"]})

    return render(
        request,
        "companies.html",
        segments=segments,
        profiles=profiles,
        distinct_industries=distinct_industries,
    )


@router.post("")
def companies_create(
    response: Response,
    company_name: str = Form(...),
    domain: str = Form(...),
    industry: str = Form(""),
    country: str = Form(""),
    tier: str = Form(""),
    max_contacts_per_run: int = Form(10),
    targeting_profiles: str = Form(""),
    _user: str = Depends(require_admin),
):
    domain = domain.strip().lower()
    # Strip protocol + trailing slash if the operator pasted a URL.
    for prefix in ("https://", "http://"):
        if domain.startswith(prefix):
            domain = domain[len(prefix):]
    domain = domain.rstrip("/").split("/")[0]
    if domain.startswith("www."):
        domain = domain[4:]

    country = (country or "").strip()
    if country and not is_canonical_country(country):
        msg = f"'{country}' is not a recognized country. Pick one from the dropdown."
        raise HTTPException(status_code=400, detail=msg, headers={"X-Toast": msg})

    with session_scope() as session:
        existing = session.execute(
            select(Company).where(Company.domain == domain)
        ).scalar_one_or_none()
        if existing is not None:
            msg = (
                f"{existing.company_name} ({domain}) is already in the system"
                + ("" if existing.is_active else " — currently inactive")
            )
            raise HTTPException(
                status_code=400,
                detail=msg,
                headers={"X-Toast": msg},
            )
        company = Company(
            company_name=company_name.strip(),
            domain=domain,
            industry=(industry or "").strip() or None,
            country=(country or "").strip() or None,
            tier=(tier or "").strip() or None,
            max_contacts_per_run=max_contacts_per_run,
            is_active=True,
        )
        session.add(company)
        session.flush()
        _link_profiles(session, company, _split_profile_names(targeting_profiles))
    response.headers["X-Toast"] = "Company added"
    response.headers["HX-Redirect"] = "/admin/companies"
    return {"ok": True}


@router.patch("/{company_id}")
def companies_update(
    company_id: str,
    response: Response,
    is_active: Optional[bool] = Form(None),
    tier: Optional[str] = Form(None),
    max_contacts_per_run: Optional[int] = Form(None),
    targeting_profiles: Optional[str] = Form(None),
    company_name: Optional[str] = Form(None),
    industry: Optional[str] = Form(None),
    country: Optional[str] = Form(None),
    _user: str = Depends(require_admin),
):
    industry_changed = False
    with session_scope() as session:
        company = session.get(Company, company_id)
        if company is None:
            raise HTTPException(404, "company not found")
        if is_active is not None:
            company.is_active = is_active
        if tier is not None:
            company.tier = (tier or "").strip() or None
        if max_contacts_per_run is not None:
            company.max_contacts_per_run = max_contacts_per_run
        if company_name is not None:
            company.company_name = company_name.strip() or company.company_name
        if country is not None:
            country_clean = (country or "").strip()
            if country_clean and not is_canonical_country(country_clean):
                msg = f"'{country_clean}' is not a recognized country."
                raise HTTPException(
                    status_code=400, detail=msg, headers={"X-Toast": msg}
                )
            company.country = country_clean or None
        if industry is not None:
            new_industry = (industry or "").strip() or None
            if _segment_key(new_industry) != _segment_key(company.industry):
                industry_changed = True
            company.industry = new_industry
        if targeting_profiles is not None:
            _link_profiles(session, company, _split_profile_names(targeting_profiles))
    response.headers["X-Toast"] = "Saved"
    if industry_changed:
        # Force the page to re-render so the row lands in the new panel.
        response.headers["HX-Refresh"] = "true"
    return {"ok": True}


# Map common real-world CSV header variants → our canonical column keys.
# Lookup is done after lowercasing + stripping the incoming header.
_HEADER_ALIASES: dict[str, str] = {
    "company": "company_name",
    "company name": "company_name",
    "company_name": "company_name",
    "name": "company_name",
    "domain": "domain",
    "website": "domain",
    "url": "domain",
    "industry": "industry",
    "segment": "industry",
    "segment/industry": "industry",
    "industry/segment": "industry",
    "sector": "industry",
    "country": "country",
    "hq country": "country",
    "headquarters": "country",
    "tier": "tier",
    "max_contacts_per_run": "max_contacts_per_run",
    "max contacts": "max_contacts_per_run",
    "max contacts per run": "max_contacts_per_run",
    "targeting_profiles": "targeting_profiles",
    "targeting profiles": "targeting_profiles",
    "profiles": "targeting_profiles",
    "notes": "notes",
}


def _normalize_header(h: str | None) -> str | None:
    """Lowercase + strip a header, then map to a canonical key if aliased."""
    if not h:
        return None
    key = h.strip().lower()
    return _HEADER_ALIASES.get(key, key)


def _normalize_row(row: dict) -> dict:
    """Return a new dict keyed by canonical column names. Unknown columns dropped."""
    out: dict[str, str] = {}
    for orig_key, val in row.items():
        key = _normalize_header(orig_key)
        if key is None:
            continue
        # Don't overwrite a value already set from an earlier alias on the same row.
        if key in _HEADER_ALIASES.values() and key not in out:
            out[key] = val
    return out


@router.post("/bulk-import")
def companies_bulk_import(
    response: Response,
    csv_text: str = Form(...),
    _user: str = Depends(require_admin),
):
    reader = csv.DictReader(io.StringIO(csv_text))
    if reader.fieldnames is None:
        raise HTTPException(400, "CSV is empty or missing a header row")
    normalized_headers = {_normalize_header(h) for h in reader.fieldnames}
    required = {"company_name", "domain"}
    if not required.issubset(normalized_headers):
        raise HTTPException(
            400,
            "CSV must include at least 'company_name' and 'domain' columns "
            "(case-insensitive; aliases like 'Company Name', 'Website' also work)",
        )

    upserted = 0
    skipped = 0
    errors: list[str] = []
    with session_scope() as session:
        profiles_by_name = {
            p.name: p for p in session.execute(select(TargetingProfile)).scalars()
        }
        existing = {c.domain: c for c in session.execute(select(Company)).scalars()}
        # rowno starts at 2 because row 1 is the CSV header.
        for rowno, raw_row in enumerate(reader, start=2):
            row = _normalize_row(raw_row)

            # Normalize domain like the single-add endpoint: strip protocol,
            # leading www., trailing path/slash.
            raw_domain = (row.get("domain") or "").strip().lower()
            for prefix in ("https://", "http://"):
                if raw_domain.startswith(prefix):
                    raw_domain = raw_domain[len(prefix):]
            raw_domain = raw_domain.rstrip("/").split("/")[0]
            if raw_domain.startswith("www."):
                raw_domain = raw_domain[4:]
            domain = raw_domain

            if not domain:
                skipped += 1
                errors.append(f"row {rowno}: missing domain")
                continue
            country = (row.get("country") or "").strip()
            if country and not is_canonical_country(country):
                skipped += 1
                errors.append(f"row {rowno} ({domain}): unknown country '{country}'")
                continue
            company = existing.get(domain)
            if company is None:
                company = Company(
                    domain=domain, company_name=row.get("company_name") or domain
                )
                session.add(company)
                existing[domain] = company
            company.company_name = row.get("company_name") or company.company_name
            company.industry = (row.get("industry") or None)
            company.country = country or None
            company.tier = (row.get("tier") or None)
            try:
                company.max_contacts_per_run = int(row.get("max_contacts_per_run") or 10)
            except ValueError:
                pass
            company.is_active = True
            session.flush()

            wanted = _split_profile_names(row.get("targeting_profiles", ""))
            _link_profiles_by_name(session, company, wanted, profiles_by_name)
            upserted += 1

    toast = f"Imported {upserted}, skipped {skipped}"
    if errors:
        toast += f" — {len(errors)} error(s)"
    response.headers["X-Toast"] = toast
    response.headers["HX-Redirect"] = "/admin/companies"
    return {"upserted": upserted, "skipped": skipped, "errors": errors}


def _link_profiles(session, company: Company, profile_names: list[str]) -> None:
    profiles = {
        p.name: p
        for p in session.execute(
            select(TargetingProfile).where(TargetingProfile.name.in_(profile_names))
        ).scalars()
    }
    _link_profiles_by_name(session, company, profile_names, profiles)


def _link_profiles_by_name(session, company: Company, profile_names: list[str], lookup: dict) -> None:
    wanted_ids = {lookup[n].id for n in profile_names if n in lookup}
    existing_links = {link.targeting_profile_id: link for link in company.targeting_links}
    for pid, link in list(existing_links.items()):
        if pid not in wanted_ids:
            session.delete(link)
    for pid in wanted_ids:
        if pid not in existing_links:
            session.add(CompanyTargeting(company_id=company.id, targeting_profile_id=pid))


def _render_profile_pills(request: Request, session, company: Company):
    linked_profiles = [
        link.profile for link in company.targeting_links if link.profile.is_active
    ]
    return templates.TemplateResponse(
        request=request,
        name="_profiles_pills.html",
        context={"company": company, "linked_profiles": linked_profiles},
    )


@router.post("/{company_id}/profiles/toggle")
def companies_profile_toggle(
    company_id: str,
    request: Request,
    profile_id: str = Form(...),
    _user: str = Depends(require_admin),
):
    with session_scope() as session:
        company = session.get(Company, company_id)
        if company is None:
            raise HTTPException(404, "company not found")
        profile = session.get(TargetingProfile, profile_id)
        if profile is None or not profile.is_active:
            raise HTTPException(400, "profile not active")

        existing = next(
            (
                link
                for link in company.targeting_links
                if link.targeting_profile_id == profile.id
            ),
            None,
        )
        if existing is not None:
            session.delete(existing)
        else:
            session.add(
                CompanyTargeting(company_id=company.id, targeting_profile_id=profile.id)
            )
        session.flush()
        session.refresh(company)
        return _render_profile_pills(request, session, company)


@router.delete("/{company_id}/profiles/{profile_id}")
def companies_profile_remove(
    company_id: str,
    profile_id: str,
    request: Request,
    _user: str = Depends(require_admin),
):
    with session_scope() as session:
        company = session.get(Company, company_id)
        if company is None:
            raise HTTPException(404, "company not found")
        link = next(
            (l for l in company.targeting_links if str(l.targeting_profile_id) == profile_id),
            None,
        )
        if link is not None:
            session.delete(link)
            session.flush()
            session.refresh(company)
        return _render_profile_pills(request, session, company)
