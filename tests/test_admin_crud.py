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


def test_bulk_segment_profile_link_and_unlink(env):
    """Operator bulk-applies a targeting profile across every active company
    in a segment, then bulk-removes it. Idempotent both ways."""
    from app.models import Company, TargetingProfile

    client, SessionLocal = env

    # Seed 3 healthcare + 1 mining company, plus 1 profile.
    with SessionLocal() as s:
        prof = TargetingProfile(
            name="ld_leadership",
            titles=["VP Learning"],
            seniorities=["vp"],
            departments=[],
            locations=[],
            keywords=[],
        )
        s.add(prof)
        s.flush()
        prof_id = prof.id
        for i in range(3):
            s.add(Company(
                company_name=f"Pharma{i}", domain=f"pharma{i}.com",
                industry="healthcare", country="United States", is_active=True,
            ))
        s.add(Company(
            company_name="MineCo", domain="mineco.com",
            industry="mining", country="United States", is_active=True,
        ))
        s.commit()

    # LINK ld_leadership across all 3 healthcare companies.
    resp = client.post(
        "/admin/companies/segment-profiles/bulk",
        data={"segment_key": "healthcare", "action": "link", "profile_ids": [str(prof_id)]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["action"] == "link"
    assert body["companies_count"] == 3
    assert body["links_changed"] == 3

    with SessionLocal() as s:
        # Mining company should NOT have the link
        mineco = s.query(Company).filter_by(domain="mineco.com").one()
        assert len(mineco.targeting_links) == 0
        for i in range(3):
            ph = s.query(Company).filter_by(domain=f"pharma{i}.com").one()
            assert {l.targeting_profile_id for l in ph.targeting_links} == {prof_id}

    # Re-running LINK is a no-op: links_changed == 0.
    resp = client.post(
        "/admin/companies/segment-profiles/bulk",
        data={"segment_key": "healthcare", "action": "link", "profile_ids": [str(prof_id)]},
    )
    assert resp.json()["links_changed"] == 0

    # UNLINK reverses it.
    resp = client.post(
        "/admin/companies/segment-profiles/bulk",
        data={"segment_key": "healthcare", "action": "unlink", "profile_ids": [str(prof_id)]},
    )
    assert resp.json()["links_changed"] == 3

    with SessionLocal() as s:
        for i in range(3):
            ph = s.query(Company).filter_by(domain=f"pharma{i}.com").one()
            assert len(ph.targeting_links) == 0


def test_bulk_segment_profile_rejects_bad_action(env):
    client, _ = env
    resp = client.post(
        "/admin/companies/segment-profiles/bulk",
        data={
            "segment_key": "healthcare",
            "action": "wipe-all",  # invalid
            "profile_ids": ["00000000-0000-0000-0000-000000000000"],
        },
    )
    assert resp.status_code == 400


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


def test_rep_email_change_cascades_to_references(env):
    """Editing a rep's email must update all places that reference the old email:
    leads.assigned_rep_email, company_rep_assignments.rep_email, routing_rules.assigned_rep_email.
    """
    from app.models import CompanyRepAssignment, Lead, RoutingRule
    from app.models.base import utcnow

    client, SessionLocal = env

    with SessionLocal() as s:
        rep = Rep(email="old@x.com", name="Pranav", timezone="UTC", is_active=True)
        s.add(rep)
        s.flush()
        rep_id = rep.id

        company = Company(company_name="X", domain="x.com")
        s.add(company)
        s.flush()

        s.add(Lead(
            company_id=company.id,
            apollo_person_id="p1",
            email="lead@x.com",
            assigned_rep_email="old@x.com",
            delivery_status="pending",
            date_discovered=utcnow(),
        ))
        s.add(CompanyRepAssignment(
            company_id=company.id, lead_country="India", rep_email="old@x.com"
        ))
        s.add(RoutingRule(
            name="r",
            priority=10,
            conditions={},
            assigned_rep_email="old@x.com",
            assigned_rep_name="Pranav",
            is_active=True,
        ))
        s.commit()

    resp = client.patch(f"/admin/reps/{rep_id}", data={"email": "new@x.com"})
    assert resp.status_code == 200, resp.text

    with SessionLocal() as s:
        assert s.get(Rep, rep_id).email == "new@x.com"
        assert s.query(Lead).filter_by(apollo_person_id="p1").one().assigned_rep_email == "new@x.com"
        assert s.query(CompanyRepAssignment).one().rep_email == "new@x.com"
        assert s.query(RoutingRule).filter_by(name="r").one().assigned_rep_email == "new@x.com"
        # Nothing left at the old email
        assert s.query(Lead).filter_by(assigned_rep_email="old@x.com").count() == 0


def test_rep_email_change_rejects_clash(env):
    client, SessionLocal = env
    with SessionLocal() as s:
        s.add_all([
            Rep(email="taken@x.com", name="Other", timezone="UTC", is_active=True),
            Rep(email="me@x.com", name="Me", timezone="UTC", is_active=True),
        ])
        s.commit()
        me_id = s.query(Rep).filter_by(email="me@x.com").one().id

    resp = client.patch(f"/admin/reps/{me_id}", data={"email": "taken@x.com"})
    assert resp.status_code == 400
    assert "taken@x.com" in resp.json()["detail"]


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


# ===== leads-grouped-by-rep spec — AC #12 + AC #13 =========================


def _seed_grouped_leads(SessionLocal):
    """Seed: Dan with 5 fallback-pending leads, Mats with 2 rule_matched-pending,
    plus 1 skipped lead for Dan to test no-email count."""
    from app.models import Lead
    from app.models.base import utcnow

    with SessionLocal() as s:
        s.add_all([
            Rep(email="dan@eonreality.com", name="Dan", timezone="UTC", is_active=True),
            Rep(email="mats@eonreality.com", name="Mats", timezone="UTC", is_active=True),
        ])
        s.flush()

        company = Company(company_name="Co", domain="co.com")
        s.add(company)
        s.flush()
        company_id = company.id

        for i in range(5):
            s.add(Lead(
                company_id=company_id,
                apollo_person_id=f"dan-p{i}",
                email=f"dan-p{i}@co.com",
                assigned_rep_email="dan@eonreality.com",
                assigned_rep_name="Dan",
                routing_status="fallback",
                delivery_status="pending",
                date_discovered=utcnow(),
            ))
        for i in range(2):
            s.add(Lead(
                company_id=company_id,
                apollo_person_id=f"mats-p{i}",
                email=f"mats-p{i}@co.com",
                assigned_rep_email="mats@eonreality.com",
                assigned_rep_name="Mats",
                routing_status="rule_matched",
                delivery_status="pending",
                date_discovered=utcnow(),
            ))
        # One skipped no-email lead for Dan
        s.add(Lead(
            company_id=company_id,
            apollo_person_id="dan-skipped",
            email=None,
            assigned_rep_email="dan@eonreality.com",
            assigned_rep_name="Dan",
            routing_status="fallback",
            delivery_status="skipped",
            date_discovered=utcnow(),
        ))
        s.commit()


def test_bulk_reassign_filter_updates_all_matching_leads(env):
    """AC #12: bulk-reassign 5 fallback leads from Dan to Mats updates all 5
    and sets routing_status='company_override' on each. Returns {'updated': 5}."""
    from app.models import ApiCallLog, Lead

    client, SessionLocal = env
    _seed_grouped_leads(SessionLocal)

    resp = client.post(
        "/admin/leads/bulk-reassign",
        json={
            "new_rep_email": "mats@eonreality.com",
            "filter": {
                "assigned_rep_email": "dan@eonreality.com",
                "routing_status": "fallback",
                "delivery_status": "pending",
            },
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"updated": 5, "new_rep": "mats@eonreality.com"}

    with SessionLocal() as s:
        moved = s.query(Lead).filter_by(
            assigned_rep_email="mats@eonreality.com",
            routing_status="company_override",
        ).all()
        # 5 newly moved + 2 Mats already had (but those are rule_matched, not company_override)
        assert len(moved) == 5
        # Dan's pending fallback bucket is now empty
        assert s.query(Lead).filter_by(
            assigned_rep_email="dan@eonreality.com",
            routing_status="fallback",
            delivery_status="pending",
        ).count() == 0
        # The skipped Dan lead is untouched (filter required pending)
        assert s.query(Lead).filter_by(
            apollo_person_id="dan-skipped"
        ).one().assigned_rep_email == "dan@eonreality.com"
        # Audit row exists
        assert s.query(ApiCallLog).filter_by(
            endpoint="/admin/leads/bulk-reassign"
        ).count() == 1


def test_bulk_reassign_explicit_lead_ids(env):
    from app.models import Lead

    client, SessionLocal = env
    _seed_grouped_leads(SessionLocal)

    with SessionLocal() as s:
        ids = [str(l.id) for l in s.query(Lead).filter(
            Lead.apollo_person_id.in_(["dan-p0", "dan-p1"])
        ).all()]

    resp = client.post(
        "/admin/leads/bulk-reassign",
        json={"new_rep_email": "mats@eonreality.com", "lead_ids": ids},
    )
    assert resp.status_code == 200
    assert resp.json()["updated"] == 2


def test_bulk_reassign_rejects_inactive_rep(env):
    client, SessionLocal = env
    _seed_grouped_leads(SessionLocal)
    # Mark Mats inactive
    with SessionLocal() as s:
        s.query(Rep).filter_by(email="mats@eonreality.com").update({"is_active": False})
        s.commit()

    resp = client.post(
        "/admin/leads/bulk-reassign",
        json={
            "new_rep_email": "mats@eonreality.com",
            "filter": {"assigned_rep_email": "dan@eonreality.com"},
        },
    )
    assert resp.status_code == 400


def test_bulk_reassign_requires_lead_ids_or_filter(env):
    client, _ = env
    resp = client.post(
        "/admin/leads/bulk-reassign",
        json={"new_rep_email": "mats@eonreality.com"},
    )
    assert resp.status_code == 400


def test_bulk_reassign_rejects_unsupported_filter_keys(env):
    client, SessionLocal = env
    _seed_grouped_leads(SessionLocal)
    resp = client.post(
        "/admin/leads/bulk-reassign",
        json={
            "new_rep_email": "mats@eonreality.com",
            "filter": {"email": "anything@x.com"},
        },
    )
    assert resp.status_code == 400


def test_grouped_view_renders_with_counts(env):
    """AC #13: GET /admin/leads renders sections with counts matching the DB."""
    client, SessionLocal = env
    _seed_grouped_leads(SessionLocal)

    resp = client.get("/admin/leads", headers={"Accept": "text/html"})
    assert resp.status_code == 200
    text = resp.text
    # Both rep emails appear (one section each)
    assert "dan@eonreality.com" in text
    assert "mats@eonreality.com" in text
    # Fallback banner says 5 leads
    assert "5 leads" in text or "5</strong>" in text
    # Chips render with totals: 8 actionable (5 fallback + 2 matched + 1 skipped), 7 pending, 5 fallback, 1 skipped
    assert "All 8" in text
    assert "Pending 7" in text
    assert "Fallback 5" in text
    assert "Skipped 1" in text
    # The "Reassign all 5 fallback to…" dropdown only appears for Dan's section
    assert "Reassign all 5 fallback" in text


def test_single_rep_filter_still_flat_table(env):
    """AC #8: ?rep=X keeps the existing flat-table view (no grouping)."""
    client, SessionLocal = env
    _seed_grouped_leads(SessionLocal)

    resp = client.get("/admin/leads?rep=mats@eonreality.com", headers={"Accept": "text/html"})
    assert resp.status_code == 200
    # Flat view doesn't render the chips banner ("All N" pill is grouped-only)
    assert "All 8" not in resp.text
    # But shows Mats's leads
    assert "mats-p0@co.com" in resp.text


def test_routing_status_dropdown_has_correct_options(env):
    """AC #7: the routing_status select lists the three current values, no stale 'matched'."""
    client, _ = env
    resp = client.get("/admin/leads", headers={"Accept": "text/html"})
    assert resp.status_code == 200
    assert "Company override" in resp.text
    assert "Matched by rule" in resp.text
    assert "Fallback to Dan" in resp.text
    # Stale value should be gone
    assert ">matched<" not in resp.text
