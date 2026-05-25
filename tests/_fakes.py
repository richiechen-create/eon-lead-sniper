"""Fake Apollo client + helpers shared across tests."""
from dataclasses import dataclass
from typing import Iterable

from app.apollo.client import MatchResult


@dataclass
class FakePerson:
    id: str
    name: str = "Jane Doe"
    first_name: str = "Jane"
    last_name: str = "Doe"
    title: str = "VP Learning"
    seniority: str = "vp"
    linkedin_url: str = "https://linkedin.com/in/jane"
    email: str | None = "jane@example.com"
    email_status: str = "verified"


class FakeApolloClient:
    """Implements the same surface as ApolloClient but reads from a fixed dataset.

    `people_by_domain`: dict[domain, list[FakePerson]]
    `phone_calls`: collected payloads for assertions
    """

    def __init__(self, session, people_by_domain: dict[str, list[FakePerson]]):
        self.session = session
        self.people_by_domain = people_by_domain
        self.phone_calls: list[dict] = []
        self.match_calls: list[str] = []

    def close(self) -> None:
        pass

    def __enter__(self) -> "FakeApolloClient":
        return self

    def __exit__(self, *_args) -> None:
        return None

    def search_people(self, query) -> Iterable[dict]:
        people = self.people_by_domain.get(query.domain, [])
        for p in people:
            yield {
                "id": p.id,
                "name": p.name,
                "first_name": p.first_name,
                "last_name": p.last_name,
                "title": p.title,
                "seniority": p.seniority,
                "linkedin_url": p.linkedin_url,
            }

    def match_person(self, apollo_person_id: str) -> MatchResult:
        self.match_calls.append(apollo_person_id)
        # Locate the person in any domain.
        for people in self.people_by_domain.values():
            for p in people:
                if p.id == apollo_person_id:
                    return MatchResult(
                        apollo_person_id=p.id,
                        email=p.email,
                        email_status=p.email_status,
                        credits_used=1 if p.email else 0,
                        raw={
                            "id": p.id,
                            "email": p.email,
                            "email_status": p.email_status,
                            "name": p.name,
                            "title": p.title,
                        },
                    )
        return MatchResult(
            apollo_person_id=apollo_person_id,
            email=None,
            email_status="unverified",
            credits_used=0,
            raw={"id": apollo_person_id},
        )
