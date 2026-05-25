from datetime import datetime, timedelta

from app.digest.builder import build_digest
from app.digest.scheduler import run_digest_tick
from app.models import Company, Lead, Rep
from app.models.base import utcnow


def _seed_company(session, name="Acme", domain="acme.com"):
    company = Company(company_name=name, domain=domain)
    session.add(company)
    session.flush()
    return company


def _make_lead(session, company, *, rep_email, **kwargs):
    lead = Lead(
        company_id=company.id,
        apollo_person_id=kwargs.get("apollo_person_id", f"id-{utcnow().timestamp()}"),
        full_name=kwargs.get("full_name", "Jane Doe"),
        title=kwargs.get("title", "VP Learning"),
        email=kwargs.get("email", "jane@acme.com"),
        email_status="verified",
        assigned_rep_email=rep_email,
        assigned_rep_name="Rep",
        routing_status="matched",
        delivery_status=kwargs.get("delivery_status", "pending"),
        date_discovered=utcnow(),
    )
    session.add(lead)
    session.flush()
    return lead


def test_build_digest_groups_by_company_sorted(session):
    a = _seed_company(session, "Zeta Corp", "zeta.com")
    b = _seed_company(session, "Alpha Inc", "alpha.com")
    leads = [
        _make_lead(session, a, rep_email="r@x.com", apollo_person_id="1"),
        _make_lead(session, b, rep_email="r@x.com", apollo_person_id="2"),
        _make_lead(session, a, rep_email="r@x.com", apollo_person_id="3", full_name="Joe Smith"),
    ]
    d = build_digest("r@x.com", "Rep Person", leads, datetime(2026, 5, 22, 8, 0))
    assert d is not None
    # Alpha must come before Zeta alphabetically.
    alpha_pos = d.text.index("Alpha Inc")
    zeta_pos = d.text.index("Zeta Corp")
    assert alpha_pos < zeta_pos
    assert d.subject.startswith("3 new leads ready")
    # CSV has 3 data rows + 1 header.
    csv_text = d.csv_bytes.decode("utf-8").strip().splitlines()
    assert len(csv_text) == 4


def test_build_digest_returns_none_when_no_leads():
    assert build_digest("r@x.com", "Rep", [], datetime(2026, 5, 22, 8, 0)) is None


def test_scheduler_skips_reps_outside_local_8am(session, monkeypatch):
    # Wednesday 08:00 UTC. Rep is in NY (UTC-4 in May -> 04:00 local). Not eligible.
    rep = Rep(email="ny@x.com", name="NY Rep", timezone="America/New_York")
    session.add(rep)
    session.flush()
    company = _seed_company(session)
    _make_lead(session, company, rep_email="ny@x.com")

    # Replace the send to avoid real email
    sent = []

    def fake_send_email(**kwargs):
        sent.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr("app.digest.scheduler.send_email", fake_send_email)

    now_utc = datetime(2026, 5, 20, 8, 0)
    run = run_digest_tick(session, now_utc=now_utc)
    assert run.reps_emailed == 0
    assert sent == []


def test_scheduler_sends_at_rep_local_8am_weekday(session, monkeypatch):
    # 12:00 UTC on a Wednesday in May -> 08:00 in New York (EDT).
    rep = Rep(email="ny@x.com", name="NY Rep", timezone="America/New_York", daily_lead_cap=10)
    session.add(rep)
    session.flush()
    company = _seed_company(session)
    _make_lead(session, company, rep_email="ny@x.com", apollo_person_id="lid1")
    _make_lead(session, company, rep_email="ny@x.com", apollo_person_id="lid2")

    sent = []

    def fake_send_email(**kwargs):
        sent.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr("app.digest.scheduler.send_email", fake_send_email)

    now_utc = datetime(2026, 5, 20, 12, 0)  # Wed 12:00 UTC -> Wed 08:00 EDT
    run = run_digest_tick(session, now_utc=now_utc)
    assert run.reps_emailed == 1
    assert run.total_leads_delivered == 2
    assert len(sent) == 1
    delivered = session.query(Lead).filter_by(delivery_status="delivered").all()
    assert len(delivered) == 2


def test_scheduler_respects_daily_lead_cap(session, monkeypatch):
    rep = Rep(email="cap@x.com", name="Cap", timezone="America/New_York", daily_lead_cap=1)
    session.add(rep)
    session.flush()
    company = _seed_company(session)
    _make_lead(session, company, rep_email="cap@x.com", apollo_person_id="c1")
    _make_lead(session, company, rep_email="cap@x.com", apollo_person_id="c2")

    monkeypatch.setattr("app.digest.scheduler.send_email", lambda **k: {"ok": True})

    now_utc = datetime(2026, 5, 20, 12, 0)
    run = run_digest_tick(session, now_utc=now_utc)
    assert run.total_leads_delivered == 1
    # Excess remains pending.
    pending = session.query(Lead).filter_by(delivery_status="pending").count()
    assert pending == 1


def test_weekend_skipped(session, monkeypatch):
    rep = Rep(email="wk@x.com", name="W", timezone="America/New_York")
    session.add(rep)
    session.flush()
    company = _seed_company(session)
    _make_lead(session, company, rep_email="wk@x.com")

    monkeypatch.setattr("app.digest.scheduler.send_email", lambda **k: {"ok": True})

    # Saturday 12:00 UTC -> 08:00 EDT, but Saturday is skipped.
    now_utc = datetime(2026, 5, 23, 12, 0)
    run = run_digest_tick(session, now_utc=now_utc)
    assert run.reps_emailed == 0


def test_zero_leads_no_email(session, monkeypatch):
    rep = Rep(email="ny@x.com", name="NY", timezone="America/New_York")
    session.add(rep)
    session.flush()
    sent = []
    monkeypatch.setattr(
        "app.digest.scheduler.send_email",
        lambda **k: sent.append(k) or {"ok": True},
    )
    now_utc = datetime(2026, 5, 20, 12, 0)
    run = run_digest_tick(session, now_utc=now_utc)
    assert run.reps_emailed == 0
    assert sent == []
