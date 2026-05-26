"""country-boost.md AC #9 (test 2): cap_override caps new_in_this_company
to the override value instead of the row's stored max_contacts_per_run."""
from app.models import Company, CompanyTargeting, Lead, RoutingRule, TargetingProfile
from app.tasks.enrichment import run_enrichment
from tests._fakes import FakeApolloClient, FakePerson


def _seed(session, *, stored_cap: int):
    profile = TargetingProfile(
        name="ld_leadership",
        titles=["VP Learning"],
        seniorities=["vp"],
        departments=[],
        locations=[],
        keywords=[],
    )
    session.add(profile)
    session.flush()

    company = Company(
        company_name="Acme",
        domain="acme.com",
        industry="manufacturing",
        country="United States",
        max_contacts_per_run=stored_cap,
    )
    session.add(company)
    session.flush()
    session.add(CompanyTargeting(company_id=company.id, targeting_profile_id=profile.id))

    session.add(
        RoutingRule(
            priority=10,
            name="Mfg US",
            conditions={"company_industry": ["manufacturing"]},
            assigned_rep_email="leadgen@example.com",
            assigned_rep_name="LeadGen",
        )
    )
    session.flush()


def _people(n: int) -> list[FakePerson]:
    return [FakePerson(id=f"p{i}", email=f"p{i}@acme.com") for i in range(n)]


def test_cap_override_caps_below_stored_value(session):
    """Stored cap is 50, override is 20 → exactly 20 leads inserted."""
    _seed(session, stored_cap=50)
    client = FakeApolloClient(session, {"acme.com": _people(40)})

    summary = run_enrichment(session, apollo_client=client, cap_override=20)

    assert summary.new_leads_created == 20
    assert session.query(Lead).count() == 20


def test_cap_override_caps_above_stored_value(session):
    """Stored cap is 5, override is 15 → override wins, 15 leads inserted."""
    _seed(session, stored_cap=5)
    client = FakeApolloClient(session, {"acme.com": _people(40)})

    summary = run_enrichment(session, apollo_client=client, cap_override=15)

    assert summary.new_leads_created == 15


def test_no_cap_override_uses_stored_value(session):
    """Sanity: without cap_override, the stored max_contacts_per_run rules."""
    _seed(session, stored_cap=3)
    client = FakeApolloClient(session, {"acme.com": _people(10)})

    summary = run_enrichment(session, apollo_client=client)

    assert summary.new_leads_created == 3
