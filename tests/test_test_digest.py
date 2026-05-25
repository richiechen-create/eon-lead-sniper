"""Regression test for /admin/triggers/test-digest.

Bug: previously the endpoint sent every pending+enriched lead to whichever rep
happened to own the first one. After this fix the test-digest faithfully
previews ONE rep's actual digest — same filter as the production scheduler.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Company, Lead, Rep
from app.models.base import utcnow


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db_file = tmp_path / "test_digest.sqlite"
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
    monkeypatch.setenv("ADMIN_EMAIL", "admin@example.com")
    monkeypatch.setenv("INTERNAL_API_KEY", "cron-token")
    from app.config import get_settings
    get_settings.cache_clear()

    from app.api.main import app
    client = TestClient(app, follow_redirects=False)
    client.post("/admin/login", data={"username": "admin", "password": "secret"})
    return client, SessionLocal


def _seed_three_reps_with_distinct_leads(SessionLocal):
    """Three active reps, each with their own pending+enriched leads + some noise."""
    with SessionLocal() as s:
        # Reps (alphabetical by email so we know the first one selected)
        aaron = Rep(email="aaron@x.com", name="Aaron", timezone="UTC", is_active=True)
        bea = Rep(email="bea@x.com", name="Bea", timezone="UTC", is_active=True)
        carl = Rep(email="carl@x.com", name="Carl", timezone="UTC", is_active=True)
        s.add_all([aaron, bea, carl])

        company_a = Company(company_name="AlphaCo", domain="alpha.com")
        company_b = Company(company_name="BetaCo", domain="beta.com")
        company_c = Company(company_name="CarbonCo", domain="carbon.com")
        s.add_all([company_a, company_b, company_c])
        s.flush()

        def _lead(*, person_id, email, name, rep_email, company_id,
                  delivery_status="pending"):
            return Lead(
                apollo_person_id=person_id,
                company_id=company_id,
                full_name=name,
                title="Director",
                email=email,
                email_status="verified" if email else "unverified",
                assigned_rep_email=rep_email,
                assigned_rep_name=rep_email,
                routing_status="rule_matched",
                delivery_status=delivery_status,
                date_discovered=utcnow(),
            )

        # Aaron — 2 pending+email leads (should show in his digest)
        s.add(_lead(person_id="a1", email="alice@alpha.com", name="Alice", rep_email="aaron@x.com", company_id=company_a.id))
        s.add(_lead(person_id="a2", email="adam@alpha.com", name="Adam", rep_email="aaron@x.com", company_id=company_a.id))
        # Aaron — 1 already-delivered (should NOT show)
        s.add(_lead(person_id="a3", email="ann@alpha.com", name="Ann", rep_email="aaron@x.com", company_id=company_a.id, delivery_status="delivered"))
        # Aaron — 1 skipped no-email (should NOT show)
        s.add(_lead(person_id="a4", email=None, name="Anon", rep_email="aaron@x.com", company_id=company_a.id, delivery_status="skipped"))

        # Bea — 3 pending+email leads (would show in her digest, but she's not picked)
        s.add(_lead(person_id="b1", email="bob@beta.com", name="Bob", rep_email="bea@x.com", company_id=company_b.id))
        s.add(_lead(person_id="b2", email="brian@beta.com", name="Brian", rep_email="bea@x.com", company_id=company_b.id))
        s.add(_lead(person_id="b3", email="bella@beta.com", name="Bella", rep_email="bea@x.com", company_id=company_b.id))

        # Carl — 1 pending+email lead
        s.add(_lead(person_id="c1", email="cara@carbon.com", name="Cara", rep_email="carl@x.com", company_id=company_c.id))

        s.commit()


def test_test_digest_only_includes_chosen_reps_leads(env, monkeypatch):
    client, SessionLocal = env
    _seed_three_reps_with_distinct_leads(SessionLocal)

    sent: list[dict] = []

    def fake_send_email(**kwargs):
        sent.append(kwargs)
        return {"ok": True}

    # Patch the import that triggers.py actually uses.
    monkeypatch.setattr("app.admin.routes.triggers.send_email", fake_send_email)

    resp = client.post("/admin/triggers/test-digest")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["sent"] is True
    # Aaron sorts first alphabetically and has pending+enriched leads.
    assert data["preview_rep"] == "aaron@x.com"
    assert data["leads"] == 2  # Aaron's two pending+email leads, not all 6

    # Exactly one email was sent.
    assert len(sent) == 1
    email = sent[0]
    assert email["to"] == ["admin@example.com"]
    assert email["subject"].startswith("[TEST] ")

    body = (email.get("html") or "") + "\n" + (email.get("text") or "")

    # Aaron's leads must appear
    assert "Alice" in body
    assert "Adam" in body
    assert "alice@alpha.com" in body
    assert "adam@alpha.com" in body

    # Other reps' leads must NOT appear
    for foreign in ("Bob", "Brian", "Bella", "Cara",
                    "bob@beta.com", "brian@beta.com", "bella@beta.com", "cara@carbon.com"):
        assert foreign not in body, f"foreign lead leaked into digest: {foreign}"

    # Already-delivered + skipped leads must NOT appear
    assert "Ann" not in body
    assert "Anon" not in body


def test_test_digest_does_not_mutate_lead_status(env, monkeypatch):
    client, SessionLocal = env
    _seed_three_reps_with_distinct_leads(SessionLocal)
    monkeypatch.setattr("app.admin.routes.triggers.send_email", lambda **k: {"ok": True})

    # Snapshot statuses before
    with SessionLocal() as s:
        before = {l.apollo_person_id: (l.delivery_status, l.delivered_at) for l in s.query(Lead).all()}

    resp = client.post("/admin/triggers/test-digest")
    assert resp.status_code == 200

    with SessionLocal() as s:
        after = {l.apollo_person_id: (l.delivery_status, l.delivered_at) for l in s.query(Lead).all()}

    assert before == after, "test-digest must not mutate any lead's delivery_status or delivered_at"


def test_test_digest_does_not_create_digest_run(env, monkeypatch):
    from app.models import DigestRun

    client, SessionLocal = env
    _seed_three_reps_with_distinct_leads(SessionLocal)
    monkeypatch.setattr("app.admin.routes.triggers.send_email", lambda **k: {"ok": True})

    with SessionLocal() as s:
        before = s.query(DigestRun).count()

    resp = client.post("/admin/triggers/test-digest")
    assert resp.status_code == 200

    with SessionLocal() as s:
        after = s.query(DigestRun).count()

    assert before == after


def test_test_digest_returns_no_leads_when_nothing_to_preview(env, monkeypatch):
    client, SessionLocal = env
    # Active rep with no leads, plus a deactivated rep that DOES have a lead.
    with SessionLocal() as s:
        active = Rep(email="active@x.com", name="Active", timezone="UTC", is_active=True)
        inactive = Rep(email="inactive@x.com", name="Inactive", timezone="UTC", is_active=False)
        company = Company(company_name="X", domain="x.com")
        s.add_all([active, inactive, company])
        s.flush()
        s.add(Lead(
            apollo_person_id="i1",
            company_id=company.id,
            email="someone@x.com",
            assigned_rep_email="inactive@x.com",
            delivery_status="pending",
            date_discovered=utcnow(),
        ))
        s.commit()

    sent = []
    monkeypatch.setattr("app.admin.routes.triggers.send_email", lambda **k: sent.append(k) or {"ok": True})

    resp = client.post("/admin/triggers/test-digest")
    assert resp.status_code == 200
    data = resp.json()
    assert data["sent"] is False
    assert data["preview_rep"] is None
    assert data["leads"] == 0
    assert sent == [], "no email should be sent when no rep qualifies"


def test_test_digest_returns_error_when_admin_email_unset(env, monkeypatch):
    client, _ = env
    monkeypatch.setenv("ADMIN_EMAIL", "")
    from app.config import get_settings
    get_settings.cache_clear()

    resp = client.post("/admin/triggers/test-digest")
    assert resp.status_code == 400
    assert "ADMIN_EMAIL" in resp.json()["detail"]


def test_test_digest_with_rep_param_picks_that_rep(env, monkeypatch):
    """If ?rep=email is supplied, preview that rep's leads even if alphabetically later."""
    client, SessionLocal = env
    _seed_three_reps_with_distinct_leads(SessionLocal)

    sent: list[dict] = []
    monkeypatch.setattr(
        "app.admin.routes.triggers.send_email",
        lambda **k: sent.append(k) or {"ok": True},
    )

    resp = client.post("/admin/triggers/test-digest?rep=bea@x.com")
    assert resp.status_code == 200
    data = resp.json()
    assert data["sent"] is True
    assert data["preview_rep"] == "bea@x.com"
    assert data["leads"] == 3

    body = (sent[0].get("html") or "") + "\n" + (sent[0].get("text") or "")
    for name in ("Bob", "Brian", "Bella"):
        assert name in body
    for name in ("Alice", "Adam", "Cara"):
        assert name not in body, f"non-Bea lead leaked: {name}"


def test_test_digest_rep_param_404_for_unknown_rep(env, monkeypatch):
    client, SessionLocal = env
    _seed_three_reps_with_distinct_leads(SessionLocal)
    monkeypatch.setattr("app.admin.routes.triggers.send_email", lambda **k: {"ok": True})

    resp = client.post("/admin/triggers/test-digest?rep=ghost@x.com")
    assert resp.status_code == 404
    assert "ghost@x.com" in resp.json()["detail"]


def test_test_digest_rep_param_400_for_inactive_rep(env, monkeypatch):
    client, SessionLocal = env
    _seed_three_reps_with_distinct_leads(SessionLocal)
    with SessionLocal() as s:
        rep = s.query(Rep).filter_by(email="bea@x.com").one()
        rep.is_active = False
        s.commit()

    monkeypatch.setattr("app.admin.routes.triggers.send_email", lambda **k: {"ok": True})

    resp = client.post("/admin/triggers/test-digest?rep=bea@x.com")
    assert resp.status_code == 400
    assert "inactive" in resp.json()["detail"].lower()


def test_test_digest_rep_param_returns_no_leads_when_rep_has_none(env, monkeypatch):
    """Picking a rep who is active but has no pending+enriched leads → sent=false, no email."""
    client, SessionLocal = env
    with SessionLocal() as s:
        empty_rep = Rep(email="empty@x.com", name="Empty", timezone="UTC", is_active=True)
        s.add(empty_rep)
        s.commit()

    sent: list[dict] = []
    monkeypatch.setattr(
        "app.admin.routes.triggers.send_email",
        lambda **k: sent.append(k) or {"ok": True},
    )

    resp = client.post("/admin/triggers/test-digest?rep=empty@x.com")
    assert resp.status_code == 200
    data = resp.json()
    assert data["sent"] is False
    assert data["preview_rep"] == "empty@x.com"
    assert data["leads"] == 0
    assert sent == []


def test_test_digest_honors_daily_lead_cap(env, monkeypatch):
    """If the rep has a daily_lead_cap of N, only N leads appear in the preview."""
    client, SessionLocal = env
    with SessionLocal() as s:
        rep = Rep(email="capped@x.com", name="Capped", timezone="UTC", is_active=True, daily_lead_cap=2)
        company = Company(company_name="X", domain="x.com")
        s.add_all([rep, company])
        s.flush()
        for i in range(5):
            s.add(Lead(
                apollo_person_id=f"k{i}",
                company_id=company.id,
                full_name=f"Person{i}",
                email=f"p{i}@x.com",
                assigned_rep_email="capped@x.com",
                delivery_status="pending",
                date_discovered=utcnow(),
            ))
        s.commit()

    monkeypatch.setattr("app.admin.routes.triggers.send_email", lambda **k: {"ok": True})

    resp = client.post("/admin/triggers/test-digest")
    assert resp.status_code == 200
    data = resp.json()
    assert data["sent"] is True
    assert data["leads"] == 2  # capped at daily_lead_cap
