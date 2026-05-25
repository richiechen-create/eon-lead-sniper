"""Tests for the structured routing-rules editor + preview endpoint.

Per the routing-defaults redesign spec ACs #10 and #11.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.admin.routes.routing_rules import derive_rule_name
from app.models import Base, Rep, RoutingRule


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db_file = tmp_path / "routing.sqlite"
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


# ----- derive_rule_name ----------------------------------------------------

def test_auto_name_oil_and_gas_americas():
    cond = {
        "company_industry": ["oil and gas"],
        "company_country": ["United States", "Canada", "Mexico"],
    }
    assert derive_rule_name(cond) == "Oil And Gas · US/Canada/Mexico"


def test_auto_name_healthcare_any_country():
    assert derive_rule_name({"company_industry": ["healthcare"]}) == "Healthcare · any country"


def test_auto_name_uk_norway_netherlands():
    """The misleading 'Oil & gas JP' rule from the spec — auto-name reveals the truth."""
    cond = {
        "company_industry": ["oil and gas"],
        "company_country": ["United Kingdom", "Norway", "Netherlands"],
    }
    name = derive_rule_name(cond)
    assert "UK" in name and "Norway" in name and "Netherlands" in name
    assert "JP" not in name


def test_auto_name_empty_is_catchall():
    assert derive_rule_name({}) == "Catch-all (fallback)"


def test_auto_name_truncates_long_lists():
    cond = {
        "company_country": ["United States", "Canada", "Mexico", "Brazil", "Argentina"],
    }
    name = derive_rule_name(cond)
    assert "+2" in name  # 5 countries → show 3 + " +2"


# ----- POST /admin/routing-rules with structured form data (AC #10) ---------

def test_create_rule_with_structured_form_data(env):
    client, SessionLocal = env
    with SessionLocal() as s:
        s.add(Rep(email="rep@x.com", name="R", timezone="UTC", is_active=True))
        s.commit()

    body = (
        "industry=oil and gas"
        "&country=United States"
        "&assigned_rep_email=rep@x.com"
    )
    resp = client.post(
        "/admin/routing-rules",
        content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200, resp.text
    with SessionLocal() as s:
        rule = s.query(RoutingRule).one()
        assert rule.conditions == {
            "company_industry": ["oil and gas"],
            "company_country": ["United States"],
        }
        # Empty name → auto-derived
        assert "Oil And Gas" in rule.name
        assert "US" in rule.name


def test_create_rule_with_multi_value_country(env):
    """Multiple country=... values produce a list in conditions."""
    client, SessionLocal = env
    with SessionLocal() as s:
        s.add(Rep(email="rep@x.com", name="R", timezone="UTC", is_active=True))
        s.commit()

    body = (
        "industry=manufacturing"
        "&country=United States"
        "&country=Canada"
        "&country=Mexico"
        "&assigned_rep_email=rep@x.com"
    )
    resp = client.post(
        "/admin/routing-rules",
        content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200, resp.text
    with SessionLocal() as s:
        rule = s.query(RoutingRule).one()
        assert rule.conditions["company_country"] == ["United States", "Canada", "Mexico"]


def test_create_rule_empty_conditions_is_catchall(env):
    client, SessionLocal = env
    with SessionLocal() as s:
        s.add(Rep(email="rep@x.com", name="R", timezone="UTC", is_active=True))
        s.commit()

    body = "assigned_rep_email=rep@x.com"
    resp = client.post(
        "/admin/routing-rules",
        content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200, resp.text
    with SessionLocal() as s:
        rule = s.query(RoutingRule).one()
        assert rule.conditions == {}
        assert "Catch-all" in rule.name


def test_create_rule_rejects_non_canonical_country(env):
    client, SessionLocal = env
    with SessionLocal() as s:
        s.add(Rep(email="rep@x.com", name="R", timezone="UTC", is_active=True))
        s.commit()

    body = "country=USA&assigned_rep_email=rep@x.com"  # USA is not canonical
    resp = client.post(
        "/admin/routing-rules",
        content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 400
    assert "USA" in resp.json()["detail"]


def test_create_rule_backward_compat_with_json(env):
    """A POST with the old `conditions` JSON string still works."""
    client, SessionLocal = env
    with SessionLocal() as s:
        s.add(Rep(email="rep@x.com", name="R", timezone="UTC", is_active=True))
        s.commit()

    body = (
        'name=Manual'
        '&conditions={"company_industry":["healthcare"]}'
        '&assigned_rep_email=rep@x.com'
    )
    resp = client.post(
        "/admin/routing-rules",
        content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200, resp.text
    with SessionLocal() as s:
        rule = s.query(RoutingRule).one()
        assert rule.conditions == {"company_industry": ["healthcare"]}
        assert rule.name == "Manual"


# ----- PATCH with structured field replaces conditions correctly ------------

def test_patch_rule_with_structured_field_replaces_conditions(env):
    client, SessionLocal = env
    with SessionLocal() as s:
        s.add(Rep(email="rep@x.com", name="R", timezone="UTC", is_active=True))
        rule = RoutingRule(
            name="initial",
            priority=10,
            conditions={"company_industry": ["oil and gas"]},
            assigned_rep_email="rep@x.com",
            assigned_rep_name="R",
            is_active=True,
        )
        s.add(rule)
        s.commit()
        rule_id = rule.id

    body = "country=United States&country=Canada"
    resp = client.patch(
        f"/admin/routing-rules/{rule_id}",
        content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200, resp.text
    with SessionLocal() as s:
        rule = s.get(RoutingRule, rule_id)
        # industry not submitted → cleared. country is the only condition now.
        assert rule.conditions == {"company_country": ["United States", "Canada"]}


# ----- GET /admin/routing-rules/preview (AC #11) ---------------------------

def test_preview_returns_matched_rule(env):
    client, SessionLocal = env
    with SessionLocal() as s:
        s.add(Rep(email="oil@x.com", name="OilRep", timezone="UTC", is_active=True))
        s.add(RoutingRule(
            name="O&G US",
            priority=10,
            conditions={
                "company_industry": ["oil and gas"],
                "company_country": ["United States"],
            },
            assigned_rep_email="oil@x.com",
            assigned_rep_name="OilRep",
            is_active=True,
        ))
        s.commit()

    resp = client.get(
        "/admin/routing-rules/preview",
        params={"industry": "oil and gas", "country": "United States"},
    )
    assert resp.status_code == 200, resp.text
    d = resp.json()
    assert d["assigned_rep_email"] == "oil@x.com"
    assert d["routing_status"] == "rule_matched"
    assert d["matched_rule_name"] == "O&G US"


def test_preview_falls_back_to_default_when_no_rule_matches(env):
    client, _ = env
    resp = client.get(
        "/admin/routing-rules/preview",
        params={"industry": "aerospace"},
    )
    assert resp.status_code == 200, resp.text
    d = resp.json()
    assert d["assigned_rep_email"] == "dan@example.com"
    assert d["routing_status"] == "fallback"
    assert d["routing_rule_id"] is None


def test_preview_inactive_rule_does_not_fire(env):
    client, SessionLocal = env
    with SessionLocal() as s:
        s.add(Rep(email="oil@x.com", name="OilRep", timezone="UTC", is_active=True))
        s.add(RoutingRule(
            name="O&G US (off)",
            priority=10,
            conditions={"company_industry": ["oil and gas"]},
            assigned_rep_email="oil@x.com",
            assigned_rep_name="OilRep",
            is_active=False,
        ))
        s.commit()

    resp = client.get(
        "/admin/routing-rules/preview",
        params={"industry": "oil and gas"},
    )
    assert resp.status_code == 200, resp.text
    d = resp.json()
    # Inactive rule skipped → fallback
    assert d["routing_status"] == "fallback"
