"""Acceptance criteria #15 (CRUD), #16 (fallback reassign), #17 (manual triggers).

Reuses the same `client` fixture pattern as test_admin_auth.py.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (
    Base,
    Company,
    DoNotContact,
    Lead,
    Rep,
    RoutingRule,
    TargetingProfile,
)
from app.models.base import utcnow


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db_file = tmp_path / "admin_crud.sqlite"
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


def test_companies_crud_roundtrip(env):
    client, SessionLocal = env

    # Create profile first so we can link
    with SessionLocal() as s:
        s.add(TargetingProfile(name="ld_leadership", titles=[], seniorities=[], departments=[], locations=[], keywords=[]))
        s.commit()

    resp = client.post(
        "/admin/companies",
        data={
            "company_name": "Acme",
            "domain": "acme.com",
            "industry": "manufacturing",
            "country": "United States",
            "tier": "tier1",
            "max_contacts_per_run": "8",
            "targeting_profiles": "ld_leadership",
        },
    )
    assert resp.status_code == 200
    with SessionLocal() as s:
        acme = s.query(Company).filter_by(domain="acme.com").one()
        assert acme.tier == "tier1"
        assert acme.max_contacts_per_run == 8
        assert len(acme.targeting_links) == 1

    # Inline edit: change tier
    resp = client.patch(f"/admin/companies/{acme.id}", data={"tier": "strategic"})
    assert resp.status_code == 200
    with SessionLocal() as s:
        assert s.query(Company).filter_by(domain="acme.com").one().tier == "strategic"

    # Soft-deactivate
    client.patch(f"/admin/companies/{acme.id}", data={"is_active": "false"})
    with SessionLocal() as s:
        assert s.query(Company).filter_by(domain="acme.com").one().is_active is False


def test_companies_bulk_import(env):
    client, SessionLocal = env
    with SessionLocal() as s:
        s.add(TargetingProfile(name="ld_leadership", titles=[], seniorities=[], departments=[], locations=[], keywords=[]))
        s.commit()

    csv_text = (
        "company_name,domain,industry,country,tier,max_contacts_per_run,targeting_profiles\n"
        "Acme,acme.com,manufacturing,United States,strategic,5,ld_leadership\n"
        "BetaCo,beta.com,oil and gas,Canada,,10,ld_leadership\n"
    )
    resp = client.post("/admin/companies/bulk-import", data={"csv_text": csv_text})
    assert resp.status_code == 200
    assert resp.json()["upserted"] == 2


def test_routing_rule_create_and_reorder(env):
    client, SessionLocal = env
    with SessionLocal() as s:
        s.add(Rep(email="r1@x.com", name="R1", timezone="UTC"))
        s.commit()

    client.post(
        "/admin/routing-rules",
        data={
            "name": "Rule A",
            "priority": "10",
            "conditions": '{"company_industry": ["manufacturing"]}',
            "assigned_rep_email": "r1@x.com",
            "assigned_rep_name": "R1",
        },
    )
    client.post(
        "/admin/routing-rules",
        data={
            "name": "Rule B",
            "priority": "20",
            "conditions": "{}",
            "assigned_rep_email": "r1@x.com",
        },
    )
    with SessionLocal() as s:
        rules = s.query(RoutingRule).order_by(RoutingRule.priority).all()
        assert [r.name for r in rules] == ["Rule A", "Rule B"]
        b_id = next(r.id for r in rules if r.name == "Rule B")
        a_id = next(r.id for r in rules if r.name == "Rule A")

    resp = client.post(
        "/admin/routing-rules/reorder",
        json={"order": [str(b_id), str(a_id)]},
    )
    assert resp.status_code == 200
    with SessionLocal() as s:
        new_order = s.query(RoutingRule).order_by(RoutingRule.priority).all()
        assert [r.name for r in new_order] == ["Rule B", "Rule A"]


def test_fallback_reassign_in_one_click(env):
    """AC #16: fallback triage reassigns to any active rep in one click;
    that lead is then included in the rep's next digest."""
    client, SessionLocal = env

    with SessionLocal() as s:
        rep = Rep(email="leadgen@x.com", name="LG", timezone="UTC", is_active=True)
        company = Company(company_name="Co", domain="co.com")
        s.add_all([rep, company])
        s.flush()
        lead = Lead(
            company_id=company.id,
            apollo_person_id="p1",
            email="p1@co.com",
            assigned_rep_email="dan@eonreality.com",
            assigned_rep_name="Dan (Fallback)",
            routing_status="fallback",
            routing_rule_id=None,
            delivery_status="pending",
            date_discovered=utcnow(),
        )
        s.add(lead)
        s.commit()
        lead_id = lead.id

    resp = client.patch(f"/admin/leads/{lead_id}/rep", data={"new_rep": "leadgen@x.com"})
    assert resp.status_code == 200
    assert resp.headers.get("X-Toast")

    with SessionLocal() as s:
        l = s.get(Lead, lead_id)
        assert l.assigned_rep_email == "leadgen@x.com"
        assert l.routing_status == "company_override"
        assert l.delivery_status == "pending"  # still in next digest


def test_lead_suppress_adds_to_dnc(env):
    client, SessionLocal = env
    with SessionLocal() as s:
        company = Company(company_name="X", domain="x.com")
        s.add(company)
        s.flush()
        lead = Lead(
            company_id=company.id,
            apollo_person_id="p2",
            email="p2@x.com",
            delivery_status="pending",
            date_discovered=utcnow(),
        )
        s.add(lead)
        s.commit()
        lead_id = lead.id

    resp = client.post(f"/admin/leads/{lead_id}/suppress")
    assert resp.status_code == 200
    with SessionLocal() as s:
        assert s.get(Lead, lead_id).delivery_status == "skipped"
        dnc = s.query(DoNotContact).filter_by(email="p2@x.com").one()
        assert dnc.apollo_person_id == "p2"


def test_manual_trigger_digest_requires_login(env, monkeypatch):
    client, _ = env
    client.post("/admin/logout")
    resp = client.post("/admin/triggers/digest")
    # /admin/triggers/* uses HTML accept fallthrough -> 303 redirect to login; json clients get 401
    assert resp.status_code in (303, 401)


def test_dashboard_renders_with_data(env):
    client, _ = env
    resp = client.get("/admin/", headers={"Accept": "text/html"})
    assert resp.status_code == 200
    assert "Credits today" in resp.text
    assert "Manual triggers" in resp.text


def test_bulk_import_accepts_messy_real_world_headers(env):
    """Headers like 'Company_Name', 'Domain', 'Segment/Industry', 'Country' should work."""
    client, SessionLocal = env
    csv_text = (
        "Rank,Company_Name,Ticker,Domain,Segment/Industry,Country,Rx Revenue (USD B)\n"
        "1,Eli Lilly,LLY,lilly.com,Healthcare,United States,65.2\n"
        "2,Pfizer,PFE,pfizer.com,Healthcare,United States,62.6\n"
        "3,AstraZeneca,AZN,https://www.astrazeneca.com/,Healthcare,United Kingdom,58.7\n"
    )
    resp = client.post("/admin/companies/bulk-import", data={"csv_text": csv_text})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["upserted"] == 3
    assert data["skipped"] == 0
    assert data["errors"] == []

    with SessionLocal() as s:
        lilly = s.query(Company).filter_by(domain="lilly.com").one()
        assert lilly.company_name == "Eli Lilly"
        # Industry is normalized to lowercase on write to prevent
        # "Healthcare" vs "healthcare" from creating duplicate segments.
        assert lilly.industry == "healthcare"
        assert lilly.country == "United States"

        # URL pasted as domain should normalize to bare apex
        az = s.query(Company).filter_by(domain="astrazeneca.com").one()
        assert az.country == "United Kingdom"


def test_bulk_import_rejects_csv_without_required_columns(env):
    client, _ = env
    csv_text = "Rank,Ticker\n1,LLY\n"
    resp = client.post("/admin/companies/bulk-import", data={"csv_text": csv_text})
    assert resp.status_code == 400
    assert "company_name" in resp.json()["detail"]
    assert "domain" in resp.json()["detail"]


def test_rep_create_rejects_bad_timezone(env):
    client, _ = env
    resp = client.post(
        "/admin/reps",
        data={
            "email": "tzbad@x.com",
            "name": "Bad TZ",
            "timezone": "Eastern Time",  # not IANA
            "team": "sales",
        },
    )
    assert resp.status_code == 400
    assert "not a recognized IANA timezone" in resp.json()["detail"]


def test_rep_create_accepts_valid_timezone(env):
    client, SessionLocal = env
    resp = client.post(
        "/admin/reps",
        data={
            "email": "tzgood@x.com",
            "name": "Good TZ",
            "timezone": "America/New_York",
            "team": "sales",
        },
    )
    assert resp.status_code == 200
    with SessionLocal() as s:
        rep = s.query(Rep).filter_by(email="tzgood@x.com").one()
        assert rep.timezone == "America/New_York"


def test_rep_patch_rejects_bad_timezone(env):
    client, SessionLocal = env
    with SessionLocal() as s:
        rep = Rep(email="tzpatch@x.com", name="X", timezone="UTC", is_active=True)
        s.add(rep)
        s.commit()
        rep_id = rep.id

    resp = client.patch(f"/admin/reps/{rep_id}", data={"timezone": "PST"})
    assert resp.status_code == 400
    assert "PST" in resp.json()["detail"]


def test_runs_page_tabs(env):
    client, _ = env
    resp = client.get("/admin/runs?tab=enrichment", headers={"Accept": "text/html"})
    assert resp.status_code == 200
    assert "Enrichment runs" in resp.text
    resp = client.get("/admin/runs?tab=digest", headers={"Accept": "text/html"})
    assert resp.status_code == 200
    assert "Digest runs" in resp.text
