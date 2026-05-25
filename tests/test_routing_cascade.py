"""Spec delta — per-company per-country rep assignments.

AC #9: company override beats matching rule beats fallback.
"""
from app.models import Company, CompanyRepAssignment, Rep, RoutingRule
from app.routing import route_lead


def _seed_rep(session, email, name="R", team="sales"):
    rep = Rep(email=email, name=name, timezone="UTC", team=team, is_active=True)
    session.add(rep)
    session.flush()
    return rep


def _seed_company(session, **kwargs) -> Company:
    company = Company(
        company_name=kwargs.get("name", "Co"),
        domain=kwargs.get("domain", "co.com"),
        industry=kwargs.get("industry"),
        country=kwargs.get("country"),
        tier=kwargs.get("tier"),
    )
    session.add(company)
    session.flush()
    return company


def _add_rule(session, *, priority, name, conditions, email):
    rule = RoutingRule(
        priority=priority,
        name=name,
        conditions=conditions,
        assigned_rep_email=email,
        assigned_rep_name=email,
        is_active=True,
    )
    session.add(rule)
    session.flush()
    return rule


def _add_assignment(session, *, company, lead_country, rep_email):
    a = CompanyRepAssignment(
        company_id=company.id, lead_country=lead_country, rep_email=rep_email
    )
    session.add(a)
    session.flush()
    return a


def test_company_override_beats_rule(session):
    jane = _seed_rep(session, "jane@x.com", name="Jane", team="sales")
    rule_rep = _seed_rep(session, "rule_rep@x.com", name="Rule Rep", team="sales")
    _add_rule(
        session,
        priority=10,
        name="O&G US",
        conditions={"company_industry": ["oil and gas"], "company_country": ["United States"]},
        email=rule_rep.email,
    )
    company = _seed_company(
        session, name="ExxonMobil", domain="exxonmobil.com",
        industry="oil and gas", country="United States",
    )
    _add_assignment(session, company=company, lead_country="United States", rep_email=jane.email)

    decision = route_lead(session, company, lead_country="United States")
    assert decision.assigned_rep_email == "jane@x.com"
    assert decision.routing_status == "company_override"
    assert decision.routing_rule_id is None
    assert decision.assigned_rep_name == "Jane"


def test_country_specific_override_picks_correct_rep(session):
    jane = _seed_rep(session, "jane@x.com", name="Jane")
    mike = _seed_rep(session, "mike@x.com", name="Mike")
    company = _seed_company(session, industry="oil and gas", country="Global")
    _add_assignment(session, company=company, lead_country="United States", rep_email=jane.email)
    _add_assignment(session, company=company, lead_country="United Kingdom", rep_email=mike.email)

    assert route_lead(session, company, lead_country="United States").assigned_rep_email == jane.email
    assert route_lead(session, company, lead_country="United Kingdom").assigned_rep_email == mike.email


def test_wildcard_override_fires_when_no_country_match(session):
    sarah = _seed_rep(session, "sarah@x.com", name="Sarah", team="lead_gen")
    rule_rep = _seed_rep(session, "rule_rep@x.com")
    _add_rule(
        session,
        priority=10,
        name="catch-all",
        conditions={},
        email=rule_rep.email,
    )
    company = _seed_company(session, industry="manufacturing", country="Global")
    _add_assignment(session, company=company, lead_country="*", rep_email=sarah.email)

    # No matching country-specific override -> wildcard kicks in (still company_override).
    decision = route_lead(session, company, lead_country="Vietnam")
    assert decision.assigned_rep_email == "sarah@x.com"
    assert decision.routing_status == "company_override"


def test_country_specific_beats_wildcard(session):
    primary = _seed_rep(session, "primary@x.com", name="Primary")
    backup = _seed_rep(session, "backup@x.com", name="Backup")
    company = _seed_company(session, industry="aerospace")
    _add_assignment(session, company=company, lead_country="*", rep_email=backup.email)
    _add_assignment(session, company=company, lead_country="Germany", rep_email=primary.email)

    decision = route_lead(session, company, lead_country="Germany")
    assert decision.assigned_rep_email == "primary@x.com"


def test_no_override_falls_through_to_rule(session):
    rule_rep = _seed_rep(session, "rep@x.com", name="Rule rep")
    _add_rule(
        session,
        priority=10,
        name="O&G US",
        conditions={"company_industry": ["oil and gas"], "company_country": ["United States"]},
        email=rule_rep.email,
    )
    company = _seed_company(
        session, industry="oil and gas", country="United States",
    )

    decision = route_lead(session, company, lead_country="United States")
    assert decision.assigned_rep_email == "rep@x.com"
    assert decision.routing_status == "rule_matched"


def test_no_override_no_rule_falls_through_to_default(session):
    company = _seed_company(session, industry="healthcare")
    decision = route_lead(session, company, lead_country="Brazil")
    assert decision.routing_status == "fallback"
    assert decision.routing_rule_id is None


def test_override_only_applies_to_its_own_company(session):
    jane = _seed_rep(session, "jane@x.com", name="Jane")
    rule_rep = _seed_rep(session, "rule@x.com", name="Rule Rep")
    _add_rule(
        session,
        priority=10,
        name="Mfg",
        conditions={"company_industry": ["manufacturing"]},
        email=rule_rep.email,
    )
    a = _seed_company(session, name="A", domain="a.com", industry="manufacturing")
    b = _seed_company(session, name="B", domain="b.com", industry="manufacturing")
    _add_assignment(session, company=a, lead_country="United States", rep_email=jane.email)

    # A: override applies
    decision_a = route_lead(session, a, lead_country="United States")
    assert decision_a.assigned_rep_email == "jane@x.com"
    assert decision_a.routing_status == "company_override"

    # B (same segment, no override): falls through to rule
    decision_b = route_lead(session, b, lead_country="United States")
    assert decision_b.assigned_rep_email == "rule@x.com"
    assert decision_b.routing_status == "rule_matched"


def test_override_with_no_lead_country_still_checks_wildcard(session):
    sarah = _seed_rep(session, "sarah@x.com", name="Sarah")
    company = _seed_company(session, industry="manufacturing")
    _add_assignment(session, company=company, lead_country="*", rep_email=sarah.email)

    # If the lead has no country, the country-specific lookup is skipped but
    # the wildcard one should still fire.
    decision = route_lead(session, company, lead_country=None)
    assert decision.assigned_rep_email == "sarah@x.com"
