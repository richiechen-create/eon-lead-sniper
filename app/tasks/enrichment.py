import logging
import traceback
from dataclasses import dataclass
from typing import Iterable, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.apollo import ApolloClient, ApolloError
from app.apollo.budget import budget_status
from app.apollo.client import SearchQuery
from app.config import get_settings
from app.email.sender import send_admin_alert
from app.leads.insert import LeadCandidate, RoutingAssignment, insert_lead, lead_exists
from app.leads.suppression import is_suppressed
from app.models import Company, EnrichmentRun, TargetingProfile
from app.models.base import utcnow
from app.routing import route_lead

logger = logging.getLogger(__name__)


@dataclass
class EnrichmentSummary:
    run_id: str
    companies_processed: int
    candidates_found: int
    new_leads_created: int
    contacts_enriched: int
    credits_consumed: int
    errors: list
    halted_by_budget: bool = False


def _union_profiles(profiles: list[TargetingProfile]) -> tuple[list[str], list[str], list[str], list[str]]:
    titles: list[str] = []
    seniorities: list[str] = []
    locations: list[str] = []
    departments: list[str] = []
    seen_t: set[str] = set()
    seen_s: set[str] = set()
    seen_l: set[str] = set()
    seen_d: set[str] = set()
    for p in profiles:
        for t in p.titles or []:
            if t not in seen_t:
                seen_t.add(t)
                titles.append(t)
        for s in p.seniorities or []:
            if s not in seen_s:
                seen_s.add(s)
                seniorities.append(s)
        for loc in p.locations or []:
            if loc not in seen_l:
                seen_l.add(loc)
                locations.append(loc)
        for d in p.departments or []:
            if d not in seen_d:
                seen_d.add(d)
                departments.append(d)
    return titles, seniorities, locations, departments


def _candidate_from_apollo(person: dict, company: Company) -> LeadCandidate:
    return LeadCandidate(
        apollo_person_id=person.get("id") or "",
        company_id=company.id,
        company_domain=company.domain,
        full_name=person.get("name"),
        first_name=person.get("first_name"),
        last_name=person.get("last_name"),
        title=person.get("title"),
        seniority=person.get("seniority"),
        department=(person.get("departments") or [None])[0]
        if isinstance(person.get("departments"), list)
        else person.get("department"),
        linkedin_url=person.get("linkedin_url"),
        person_country=person.get("country"),
        person_city=person.get("city"),
        raw_apollo_payload=person,
    )


def run_enrichment(
    session: Session,
    *,
    apollo_client: Optional[ApolloClient] = None,
    industries: Optional[Iterable[str]] = None,
) -> EnrichmentSummary:
    """Run enrichment across active companies.

    `industries` (optional): if provided, restrict to companies whose
    `industry` matches one of the given values (case-insensitive, trimmed).
    Useful for scoping a manual run to a single segment.
    """
    settings = get_settings()
    run = EnrichmentRun(run_started_at=utcnow(), errors=[])
    session.add(run)
    session.flush()

    owns_client = apollo_client is None
    client = apollo_client or ApolloClient(session)

    candidates_found = 0
    new_leads_created = 0
    contacts_enriched = 0
    credits_consumed = 0
    halted_by_budget = False

    try:
        stmt = (
            select(Company)
            .where(Company.is_active == True)  # noqa: E712
            .order_by(Company.company_name)
        )
        if industries:
            keys = {(i or "").strip().lower() for i in industries if i}
            if keys:
                stmt = stmt.where(func.lower(func.trim(Company.industry)).in_(keys))
        companies = session.execute(stmt).scalars().all()

        for company in companies:
            # Budget guard — checked before every company AND before every match call.
            used, limit, exhausted = budget_status(session)
            if exhausted:
                halted_by_budget = True
                msg = f"monthly credit budget exhausted: {used}/{limit}"
                logger.warning(msg)
                run.errors.append({"company": company.domain, "error": msg})
                _safe_admin_alert(
                    subject="EON Lead Sniper: monthly Apollo credit budget exhausted",
                    text=f"Halted enrichment. Credits used this month: {used}/{limit}.",
                )
                break

            try:
                count_for_company, enriched_for_company, credits_for_company = _process_company(
                    session, client, company, run
                )
                candidates_found += count_for_company
                contacts_enriched += enriched_for_company
                credits_consumed += credits_for_company
                # We count "new leads created" via row deltas inside _process_company,
                # which appends to run.new_leads_created directly.
            except Exception as exc:  # noqa: BLE001 - we want fail-soft per company
                tb = traceback.format_exc(limit=4)
                logger.exception("company %s failed", company.domain)
                run.errors = (run.errors or []) + [
                    {"company": company.domain, "error": str(exc), "traceback": tb}
                ]
                session.flush()

            run.companies_processed = (run.companies_processed or 0) + 1
            session.flush()

        new_leads_created = run.new_leads_created or 0

    except Exception as exc:  # noqa: BLE001 - top-level crash
        tb = traceback.format_exc()
        run.errors = (run.errors or []) + [{"top_level": str(exc), "traceback": tb}]
        _safe_admin_alert(
            subject="EON Lead Sniper: enrichment run crashed",
            text=f"Enrichment run {run.id} crashed.\n\n{tb}",
        )
        raise
    finally:
        run.candidates_found = candidates_found
        run.contacts_enriched = contacts_enriched
        run.credits_consumed = credits_consumed
        run.run_completed_at = utcnow()
        session.flush()
        if owns_client:
            client.close()

    return EnrichmentSummary(
        run_id=str(run.id),
        companies_processed=run.companies_processed or 0,
        candidates_found=candidates_found,
        new_leads_created=new_leads_created,
        contacts_enriched=contacts_enriched,
        credits_consumed=credits_consumed,
        errors=run.errors or [],
        halted_by_budget=halted_by_budget,
    )


def _process_company(
    session: Session,
    client: ApolloClient,
    company: Company,
    run: EnrichmentRun,
) -> tuple[int, int, int]:
    profiles = [link.profile for link in company.targeting_links if link.profile.is_active]
    if not profiles:
        return 0, 0, 0

    titles, seniorities, locations, departments = _union_profiles(profiles)
    if not titles and not seniorities and not departments:
        return 0, 0, 0

    query = SearchQuery(
        domain=company.domain,
        titles=titles,
        seniorities=seniorities,
        locations=locations,
        departments=departments,
    )

    candidates_found = 0
    contacts_enriched = 0
    credits_consumed = 0
    new_in_this_company = 0
    cap = company.max_contacts_per_run or 10

    for person in client.search_people(query):
        candidates_found += 1
        apollo_id = person.get("id")
        if not apollo_id:
            continue

        # Cap new leads per company per run (note: this caps NEW inserts, not API calls).
        if new_in_this_company >= cap:
            break

        # Skip dedupes early — saves a match call.
        if lead_exists(session, apollo_id):
            continue

        dnc = is_suppressed(
            session,
            email=None,
            domain=company.domain,
            apollo_person_id=apollo_id,
        )
        if dnc is not None:
            continue

        # Budget guard before each enrichment call.
        used, limit, exhausted = budget_status(session)
        if exhausted:
            break

        try:
            match = client.match_person(apollo_id)
        except ApolloError as exc:
            run.errors = (run.errors or []) + [
                {"company": company.domain, "apollo_person_id": apollo_id, "error": str(exc)}
            ]
            session.flush()
            continue

        candidate = _candidate_from_apollo(person, company)
        candidate.email = match.email
        candidate.email_status = match.email_status
        candidate.raw_apollo_payload = {"search": person, "match": match.raw}

        # Route per-lead: per-company per-country override first, then segment rule, then fallback.
        lead_country = match.raw.get("country") or person.get("country")
        routing = route_lead(session, company, lead_country=lead_country)

        lead = insert_lead(session, candidate, RoutingAssignment(
            assigned_rep_email=routing.assigned_rep_email,
            assigned_rep_name=routing.assigned_rep_name,
            routing_rule_id=routing.routing_rule_id,
            routing_status=routing.routing_status,
        ))
        credits_consumed += match.credits_used
        # Reflect credits live so the next iteration's budget check is accurate.
        run.credits_consumed = (run.credits_consumed or 0) + match.credits_used

        if lead is not None:
            new_in_this_company += 1
            run.new_leads_created = (run.new_leads_created or 0) + 1
            if match.email:
                contacts_enriched += 1
            session.flush()

    return candidates_found, contacts_enriched, credits_consumed


def _safe_admin_alert(*, subject: str, text: str) -> None:
    try:
        send_admin_alert(subject=subject, text=text)
    except Exception:  # noqa: BLE001
        logger.exception("failed to send admin alert")
