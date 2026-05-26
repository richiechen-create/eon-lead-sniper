"""country-boost.md AC #9 (test 1): person_country_override forces
SearchQuery.locations to [country], regardless of what's in the profile."""
from app.models import Company, CompanyTargeting, RoutingRule, TargetingProfile
from app.tasks.enrichment import run_enrichment
from tests._fakes import FakeApolloClient, FakePerson


class RecordingApollo(FakeApolloClient):
    """FakeApolloClient that records every SearchQuery it sees."""

    def __init__(self, session, people_by_domain):
        super().__init__(session, people_by_domain)
        self.search_queries: list = []

    def search_people(self, query):
        self.search_queries.append(query)
        yield from super().search_people(query)


def _seed(session, *, profile_locations):
    profile = TargetingProfile(
        name="ld_leadership",
        titles=["VP Learning"],
        seniorities=["vp"],
        departments=[],
        locations=profile_locations,
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


def test_country_override_replaces_profile_locations(session):
    """When person_country_override is set, every SearchQuery.locations is
    exactly [country], regardless of the underlying profile's locations."""
    _seed(session, profile_locations=["United States", "Canada", "Mexico"])
    client = RecordingApollo(session, {"acme.com": [FakePerson(id="p1")]})

    summary = run_enrichment(
        session, apollo_client=client, person_country_override="India"
    )

    assert summary.companies_processed == 1
    assert len(client.search_queries) == 1
    q = client.search_queries[0]
    assert q.locations == ["India"], (
        f"locations should be exactly ['India'], got {q.locations!r}"
    )


def test_country_override_overrides_empty_profile_locations(session):
    """Even when the profile has no locations at all, the override still
    populates locations=[country] in the query."""
    _seed(session, profile_locations=[])
    client = RecordingApollo(session, {"acme.com": [FakePerson(id="p2")]})

    run_enrichment(session, apollo_client=client, person_country_override="Singapore")

    assert client.search_queries[0].locations == ["Singapore"]


def test_no_override_preserves_profile_locations(session):
    """Sanity: without the override, locations come from the profile unchanged."""
    _seed(session, profile_locations=["United States", "Canada"])
    client = RecordingApollo(session, {"acme.com": [FakePerson(id="p3")]})

    run_enrichment(session, apollo_client=client)

    assert client.search_queries[0].locations == ["United States", "Canada"]


def test_country_override_stamps_run_metadata(session):
    """The EnrichmentRun row should carry run_metadata.boost_country so the
    Run history page can render the boost chip."""
    from app.models import EnrichmentRun

    _seed(session, profile_locations=[])
    client = RecordingApollo(session, {"acme.com": [FakePerson(id="p4")]})
    summary = run_enrichment(
        session, apollo_client=client, person_country_override="India", cap_override=5
    )

    run = session.query(EnrichmentRun).filter_by(id=summary.run_id).one()
    assert run.run_metadata == {"boost_country": "India", "cap_override": 5}
