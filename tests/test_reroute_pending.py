"""POST /admin/triggers/reroute-pending re-runs the routing cascade for
existing pending leads. Used after the operator changes routing rules.

Manual reassignments (routing_status='company_override') are preserved —
only rule_matched and fallback leads are re-evaluated.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Company, Lead, Rep, RoutingRule
from app.models.base import utcnow


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db_file = tmp_path / "reroute.sqlite"
    eng = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(eng)
    SessionLocal = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False, future=True)

    import app.db as _db
    monkeypatch.setattr(_db, "engine", eng)
    monkeypatch.setattr(_db, "SessionLocal", SessionLocal)

    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "secret")
    monkeypatch.setenv("DEFAULT_REP_EMAIL", "dan@example.com")
    from app.config import get_settings
    get_settings.cache_clear()

    from app.api.main import app
    client = TestClient(app, follow_redirects=False)
    client.post("/admin/login", data={"username": "admin", "password": "secret"})
    return client, SessionLocal


def test_reroute_picks_up_new_rule(env):
    """A pending lead currently routed to Dan (fallback) should land on the new
    rep after a matching rule is added and reroute is triggered.
    """
    client, SessionLocal = env
    with SessionLocal() as s:
        s.add_all([
            Rep(email="jeetesh@x.com", name="Jeetesh", timezone="UTC", is_active=True),
        ])
        company = Company(company_name="BP", domain="bp.com", industry="oil and gas")
        s.add(company)
        s.flush()

        # Existing fallback lead from before the rule existed
        s.add(Lead(
            company_id=company.id,
            apollo_person_id="p1",
            full_name="Alice",
            email="alice@bp.com",
            person_country="United Kingdom",
            assigned_rep_email="dan@example.com",
            assigned_rep_name="Dan (Fallback)",
            routing_status="fallback",
            delivery_status="pending",
            date_discovered=utcnow(),
        ))

        # New rule the operator just added
        s.add(RoutingRule(
            name="UK leads -> Jeetesh",
            priority=10,
            conditions={"lead_country": ["United Kingdom"]},
            assigned_rep_email="jeetesh@x.com",
            assigned_rep_name="Jeetesh",
            is_active=True,
        ))
        s.commit()

    resp = client.post("/admin/triggers/reroute-pending")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["considered"] == 1
    assert data["updated"] == 1
    assert data["unchanged"] == 0

    with SessionLocal() as s:
        lead = s.query(Lead).filter_by(apollo_person_id="p1").one()
        assert lead.assigned_rep_email == "jeetesh@x.com"
        assert lead.routing_status == "rule_matched"


def test_reroute_preserves_manual_company_override(env):
    """A lead the operator has manually reassigned must NOT be moved by reroute."""
    client, SessionLocal = env
    with SessionLocal() as s:
        s.add_all([
            Rep(email="picked@x.com", name="Picked", timezone="UTC", is_active=True),
            Rep(email="auto@x.com", name="Auto", timezone="UTC", is_active=True),
        ])
        company = Company(company_name="X", domain="x.com", industry="oil and gas")
        s.add(company)
        s.flush()
        s.add(Lead(
            company_id=company.id,
            apollo_person_id="p1",
            full_name="A",
            email="a@x.com",
            person_country="United Kingdom",
            assigned_rep_email="picked@x.com",
            assigned_rep_name="Picked",
            routing_status="company_override",  # manual reassignment
            delivery_status="pending",
            date_discovered=utcnow(),
        ))
        # A rule that would otherwise route UK leads to auto@x.com
        s.add(RoutingRule(
            name="UK -> Auto",
            priority=10,
            conditions={"lead_country": ["United Kingdom"]},
            assigned_rep_email="auto@x.com",
            assigned_rep_name="Auto",
            is_active=True,
        ))
        s.commit()

    resp = client.post("/admin/triggers/reroute-pending")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # company_override lead is skipped, so considered=0
    assert data["considered"] == 0
    assert data["updated"] == 0

    with SessionLocal() as s:
        lead = s.query(Lead).filter_by(apollo_person_id="p1").one()
        # Untouched
        assert lead.assigned_rep_email == "picked@x.com"
        assert lead.routing_status == "company_override"


def test_reroute_skips_delivered_leads(env):
    """Delivered leads must not be re-routed (the rep already got the email)."""
    client, SessionLocal = env
    with SessionLocal() as s:
        s.add_all([
            Rep(email="old@x.com", name="Old", timezone="UTC", is_active=True),
            Rep(email="new@x.com", name="New", timezone="UTC", is_active=True),
        ])
        company = Company(company_name="X", domain="x.com", industry="oil and gas")
        s.add(company)
        s.flush()
        s.add(Lead(
            company_id=company.id,
            apollo_person_id="p1",
            email="a@x.com",
            assigned_rep_email="old@x.com",
            assigned_rep_name="Old",
            routing_status="rule_matched",
            delivery_status="delivered",  # already sent
            date_discovered=utcnow(),
        ))
        s.add(RoutingRule(
            name="Send to New",
            priority=10,
            conditions={},
            assigned_rep_email="new@x.com",
            assigned_rep_name="New",
            is_active=True,
        ))
        s.commit()

    resp = client.post("/admin/triggers/reroute-pending")
    assert resp.status_code == 200
    data = resp.json()
    assert data["considered"] == 0  # delivered leads aren't considered

    with SessionLocal() as s:
        lead = s.query(Lead).filter_by(apollo_person_id="p1").one()
        assert lead.assigned_rep_email == "old@x.com"


def test_reroute_counts_unchanged_correctly(env):
    """Pending leads already routed to the right rep should count as unchanged."""
    client, SessionLocal = env
    with SessionLocal() as s:
        s.add(Rep(email="r@x.com", name="R", timezone="UTC", is_active=True))
        company = Company(company_name="X", domain="x.com", industry="oil and gas")
        s.add(company)
        s.flush()
        rule = RoutingRule(
            name="catch-all",
            priority=10,
            conditions={},
            assigned_rep_email="r@x.com",
            assigned_rep_name="R",
            is_active=True,
        )
        s.add(rule)
        s.flush()
        s.add(Lead(
            company_id=company.id,
            apollo_person_id="p1",
            email="a@x.com",
            assigned_rep_email="r@x.com",  # already correct
            assigned_rep_name="R",
            routing_rule_id=rule.id,
            routing_status="rule_matched",
            delivery_status="pending",
            date_discovered=utcnow(),
        ))
        s.commit()

    resp = client.post("/admin/triggers/reroute-pending")
    assert resp.status_code == 200
    data = resp.json()
    assert data["considered"] == 1
    assert data["updated"] == 0
    assert data["unchanged"] == 1
