from app.models import Company, RoutingRule
from app.routing import route_lead


def _make_company(session, **kwargs):
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


def _add_rule(session, priority, name, conditions, email, rep_name="rep"):
    rule = RoutingRule(
        priority=priority,
        name=name,
        conditions=conditions,
        assigned_rep_email=email,
        assigned_rep_name=rep_name,
    )
    session.add(rule)
    session.flush()
    return rule


def test_oil_and_gas_americas_goes_to_sales_us(session):
    _add_rule(
        session,
        10,
        "O&G Americas",
        {
            "company_industry": ["oil and gas"],
            "company_country": ["United States", "Canada", "Mexico"],
        },
        "sales_us@example.com",
    )
    _add_rule(session, 9999, "Fallback", {}, "dan@example.com", "Dan")

    company = _make_company(session, industry="oil and gas", country="United States")
    decision = route_lead(session, company)
    assert decision.assigned_rep_email == "sales_us@example.com"
    assert decision.routing_status == "rule_matched"


def test_priority_order_first_wins(session):
    _add_rule(
        session,
        5,
        "High priority specific",
        {"company_domain": ["bigco.com"]},
        "vip@example.com",
    )
    _add_rule(
        session,
        10,
        "Lower priority broad",
        {"company_industry": ["manufacturing"]},
        "leadgen@example.com",
    )

    company = _make_company(session, domain="bigco.com", industry="manufacturing")
    decision = route_lead(session, company)
    assert decision.assigned_rep_email == "vip@example.com"


def test_no_match_falls_back_to_default(session):
    _add_rule(
        session,
        10,
        "O&G only",
        {"company_industry": ["oil and gas"]},
        "sales@example.com",
    )
    company = _make_company(session, industry="healthcare")
    decision = route_lead(session, company)
    assert decision.routing_status == "fallback"
    assert decision.assigned_rep_email == "dan@example.com"
    assert decision.routing_rule_id is None


def test_inactive_rules_are_skipped(session):
    r = _add_rule(
        session,
        5,
        "Inactive specific",
        {"company_domain": ["bigco.com"]},
        "vip@example.com",
    )
    r.is_active = False
    _add_rule(session, 10, "Broad", {}, "broad@example.com", "B")

    company = _make_company(session, domain="bigco.com")
    decision = route_lead(session, company)
    assert decision.assigned_rep_email == "broad@example.com"


def test_all_conditions_anded(session):
    _add_rule(
        session,
        10,
        "Mfg in US tier1",
        {
            "company_industry": ["manufacturing"],
            "company_country": ["United States"],
            "company_tier": ["tier1"],
        },
        "leadgen@example.com",
    )
    # Wrong tier -> shouldn't match
    company = _make_company(
        session, industry="manufacturing", country="United States", tier="tier2"
    )
    decision = route_lead(session, company)
    assert decision.routing_status == "fallback"
