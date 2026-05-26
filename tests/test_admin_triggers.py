"""country-boost.md AC #9 (test 3): POST /admin/triggers/enrichment-boost
with country=India&cap_override=10 returns 200 with the run summary."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, EnrichmentRun


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db_file = tmp_path / "admin_triggers.sqlite"
    url = f"sqlite:///{db_file}"
    eng = create_engine(url, connect_args={"check_same_thread": False}, future=True)
    Base.metadata.create_all(eng)
    SessionLocal = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False, future=True)

    import app.db as _db
    monkeypatch.setattr(_db, "engine", eng)
    monkeypatch.setattr(_db, "SessionLocal", SessionLocal)

    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "secret")
    monkeypatch.setenv("INTERNAL_API_KEY", "cron-token")
    from app.config import get_settings
    get_settings.cache_clear()

    from app.api.main import app
    client = TestClient(app, follow_redirects=False)
    client.post("/admin/login", data={"username": "admin", "password": "secret"})
    return client, SessionLocal


def test_boost_endpoint_returns_200_with_run_summary(env):
    """Posting to the boost endpoint with country=India and cap_override=10
    returns 200 with the run summary shape."""
    client, SessionLocal = env

    # No active companies seeded — enrichment will be a no-op but the route
    # should still create a run row and return a summary.
    resp = client.post(
        "/admin/triggers/enrichment-boost",
        data={"country": "India", "cap_override": "10"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "run_id" in body
    assert body["scope"]["boost_country"] == "India"
    assert body["scope"]["cap_override"] == 10
    assert body["companies_processed"] == 0
    assert body["new_leads_created"] == 0
    assert body["credits_consumed"] == 0
    assert body["halted_by_budget"] is False

    # The run row should be persisted with run_metadata tagged.
    with SessionLocal() as s:
        run = s.query(EnrichmentRun).one()
        assert run.run_metadata == {"boost_country": "India", "cap_override": 10}


def test_boost_endpoint_rejects_non_canonical_country(env):
    """Country must be canonical — typo'd or wrong forms get a 400 + X-Toast."""
    client, _ = env

    resp = client.post(
        "/admin/triggers/enrichment-boost",
        data={"country": "Indja", "cap_override": "10"},
    )

    assert resp.status_code == 400
    assert "Indja" in resp.json()["detail"]
    assert resp.headers.get("X-Toast")


def test_boost_endpoint_rejects_non_numeric_cap(env):
    """cap_override must parse to int."""
    client, _ = env

    resp = client.post(
        "/admin/triggers/enrichment-boost",
        data={"country": "India", "cap_override": "lots"},
    )

    assert resp.status_code == 400


def test_boost_endpoint_accepts_no_cap_override(env):
    """cap_override is optional — leaving it out should still work and
    leave run_metadata.cap_override=None."""
    client, SessionLocal = env

    resp = client.post(
        "/admin/triggers/enrichment-boost",
        data={"country": "India"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["scope"]["cap_override"] is None

    with SessionLocal() as s:
        run = s.query(EnrichmentRun).one()
        assert run.run_metadata == {"boost_country": "India", "cap_override": None}


def test_boost_endpoint_requires_login(env):
    """Unauthenticated callers get 401/303 just like any other /admin/* route."""
    client, _ = env
    client.post("/admin/logout")

    resp = client.post(
        "/admin/triggers/enrichment-boost",
        data={"country": "India"},
    )
    assert resp.status_code in (303, 401)
