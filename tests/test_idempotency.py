"""Acceptance criterion #4: running enrichment twice creates zero duplicate leads."""
from app.models import (
    Company,
    CompanyTargeting,
    DoNotContact,
    Lead,
    RoutingRule,
    TargetingProfile,
)
from app.tasks.enrichment import run_enrichment
from tests._fakes import FakeApolloClient, FakePerson


def _seed(session):
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


def test_two_runs_produce_no_duplicates(session):
    _seed(session)
    people = {
        "acme.com": [
            FakePerson(id="p1", name="Jane Doe", email="jane@acme.com"),
            FakePerson(id="p2", name="John Roe", email="john@acme.com"),
        ]
    }

    client = FakeApolloClient(session, people)
    s1 = run_enrichment(session, apollo_client=client)
    assert s1.new_leads_created == 2
    assert s1.contacts_enriched == 2
    assert s1.credits_consumed == 2

    # Second run with the SAME data: nothing new.
    client2 = FakeApolloClient(session, people)
    s2 = run_enrichment(session, apollo_client=client2)
    assert s2.new_leads_created == 0
    assert s2.contacts_enriched == 0
    # No match calls should occur on dedupe — saves credits.
    assert client2.match_calls == []
    assert s2.credits_consumed == 0

    total_leads = session.query(Lead).count()
    assert total_leads == 2


def test_do_not_contact_blocks_insert(session):
    _seed(session)
    session.add(DoNotContact(email="jane@acme.com", reason="manual"))
    session.flush()

    people = {
        "acme.com": [
            FakePerson(id="p1", name="Jane Doe", email="jane@acme.com"),
            FakePerson(id="p2", name="John Roe", email="john@acme.com"),
        ]
    }
    client = FakeApolloClient(session, people)
    summary = run_enrichment(session, apollo_client=client)
    # John should be inserted, Jane suppressed.
    emails = {l.email for l in session.query(Lead).all()}
    assert "jane@acme.com" not in emails
    assert "john@acme.com" in emails
    assert summary.new_leads_created == 1


def test_do_not_contact_by_apollo_id_prevents_match_call(session):
    _seed(session)
    session.add(DoNotContact(apollo_person_id="p1", reason="dnc-by-id"))
    session.flush()

    people = {"acme.com": [FakePerson(id="p1", email="jane@acme.com")]}
    client = FakeApolloClient(session, people)
    summary = run_enrichment(session, apollo_client=client)
    assert summary.new_leads_created == 0
    # Suppression by apollo_person_id must short-circuit BEFORE the match call.
    assert client.match_calls == []


def test_lead_without_email_but_with_linkedin_is_pending(session):
    """No email but LinkedIn URL present → still deliverable in the digest."""
    _seed(session)
    people = {"acme.com": [FakePerson(id="px", email=None, email_status="unverified")]}
    client = FakeApolloClient(session, people)
    summary = run_enrichment(session, apollo_client=client)
    assert summary.new_leads_created == 1
    lead = session.query(Lead).filter_by(apollo_person_id="px").one()
    assert lead.email is None
    assert lead.linkedin_url  # FakePerson defaults this to a real URL
    assert lead.delivery_status == "pending"


def test_lead_without_email_and_without_linkedin_is_skipped(session):
    """No email AND no LinkedIn → un-reachable, status='skipped'."""
    _seed(session)
    people = {
        "acme.com": [
            FakePerson(id="py", email=None, email_status="unverified", linkedin_url=None)
        ]
    }
    client = FakeApolloClient(session, people)
    summary = run_enrichment(session, apollo_client=client)
    assert summary.new_leads_created == 1
    lead = session.query(Lead).filter_by(apollo_person_id="py").one()
    assert lead.email is None
    assert lead.linkedin_url is None
    assert lead.delivery_status == "skipped"


def test_max_contacts_per_run_caps_inserts(session):
    _seed(session)
    company = session.query(Company).one()
    company.max_contacts_per_run = 1
    session.flush()

    people = {
        "acme.com": [
            FakePerson(id="p1", email="a@acme.com"),
            FakePerson(id="p2", email="b@acme.com"),
            FakePerson(id="p3", email="c@acme.com"),
        ]
    }
    client = FakeApolloClient(session, people)
    summary = run_enrichment(session, apollo_client=client)
    assert summary.new_leads_created == 1
    # Only the first matched -> only one enrichment credit spent.
    assert summary.credits_consumed == 1
