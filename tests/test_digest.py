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


def test_csv_has_segment_column(session):
    """The CSV must include a Segment column derived from companies.industry,
    title-cased for human readability."""
    import csv as csv_lib
    import io

    a = _seed_company(session, "Acme Pharma", "acme.com")
    a.industry = "healthcare"  # stored lowercase per the normalization rule
    session.flush()
    leads = [_make_lead(session, a, rep_email="r@x.com", apollo_person_id="seg-1")]

    d = build_digest("r@x.com", "Rep", leads, datetime(2026, 5, 22, 8, 0))
    rows = list(csv_lib.reader(io.StringIO(d.csv_bytes.decode("utf-8"))))
    header = rows[0]
    body = rows[1]

    assert "Segment" in header
    seg_idx = header.index("Segment")
    # Segment is positioned right after Domain
    assert header.index("Domain") + 1 == seg_idx
    # Value is title-cased (the rep sees "Healthcare", not "healthcare")
    assert body[seg_idx] == "Healthcare"


def test_run_digest_tick_only_rep_scopes_to_one(session, monkeypatch):
    """only_rep=email sends ONLY that rep's digest, skipping all others.
    Bypasses the 08:00 local-time gate automatically."""
    company = _seed_company(session)
    rep_a = Rep(email="a@x.com", name="Alice", timezone="UTC", is_active=True)
    rep_b = Rep(email="b@x.com", name="Bob", timezone="UTC", is_active=True)
    session.add_all([rep_a, rep_b])
    session.flush()

    _make_lead(session, company, rep_email="a@x.com", apollo_person_id="a1")
    _make_lead(session, company, rep_email="a@x.com", apollo_person_id="a2")
    _make_lead(session, company, rep_email="b@x.com", apollo_person_id="b1")

    sent = []

    def fake_send_email(**kwargs):
        sent.append(kwargs["to"])
        return {"ok": True}

    monkeypatch.setattr("app.digest.scheduler.send_email", fake_send_email)

    # 3am UTC — would normally fail the 08:00 gate, but only_rep implies force.
    run = run_digest_tick(
        session, now_utc=datetime(2026, 5, 20, 3, 0), only_rep="a@x.com"
    )

    assert run.reps_emailed == 1
    assert run.total_leads_delivered == 2
    assert sent == [["a@x.com"]]
    # Alice's leads are now delivered, Bob's are still pending.
    delivered_a = [l for l in session.query(Lead).filter_by(assigned_rep_email="a@x.com")]
    assert all(l.delivery_status == "delivered" for l in delivered_a)
    pending_b = [l for l in session.query(Lead).filter_by(assigned_rep_email="b@x.com")]
    assert all(l.delivery_status == "pending" for l in pending_b)


def test_run_digest_tick_only_rep_skips_inactive(session, monkeypatch):
    """If only_rep points at an inactive rep, no email is sent (no error)."""
    company = _seed_company(session)
    session.add(Rep(email="z@x.com", name="Zelda", timezone="UTC", is_active=False))
    session.flush()
    _make_lead(session, company, rep_email="z@x.com", apollo_person_id="z1")

    monkeypatch.setattr("app.digest.scheduler.send_email", lambda **k: {"ok": True})

    run = run_digest_tick(
        session, now_utc=datetime(2026, 5, 20, 8, 0), only_rep="z@x.com"
    )
    assert run.reps_emailed == 0
    assert run.errors == []


def test_csv_segment_blank_when_industry_missing(session):
    """Companies without an industry should still get a row — Segment is blank."""
    import csv as csv_lib
    import io

    a = _seed_company(session, "Mystery Co", "myst.com")
    a.industry = None
    session.flush()
    leads = [_make_lead(session, a, rep_email="r@x.com", apollo_person_id="seg-2")]

    d = build_digest("r@x.com", "Rep", leads, datetime(2026, 5, 22, 8, 0))
    rows = list(csv_lib.reader(io.StringIO(d.csv_bytes.decode("utf-8"))))
    seg_idx = rows[0].index("Segment")
    assert rows[1][seg_idx] == ""


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


def test_force_sends_outside_8am_and_weekday(session, monkeypatch):
    """force=True bypasses the local-hour + weekday gates."""
    rep = Rep(email="ny@x.com", name="NY", timezone="America/New_York", daily_lead_cap=10)
    session.add(rep)
    session.flush()
    company = _seed_company(session)
    _make_lead(session, company, rep_email="ny@x.com", apollo_person_id="f1", email="x@x.com")

    sent = []
    monkeypatch.setattr("app.digest.scheduler.send_email", lambda **k: sent.append(k) or {"ok": True})

    # Sunday 03:00 UTC — usually skipped (weekend + wrong hour).
    now_utc = datetime(2026, 5, 24, 3, 0)
    run_without_force = run_digest_tick(session, now_utc=now_utc, force=False)
    assert run_without_force.reps_emailed == 0
    assert sent == []

    run_with_force = run_digest_tick(session, now_utc=now_utc, force=True)
    assert run_with_force.reps_emailed == 1
    assert run_with_force.total_leads_delivered == 1
    assert len(sent) == 1


def test_force_still_skips_inactive_reps(session, monkeypatch):
    rep = Rep(email="off@x.com", name="Off", timezone="UTC", is_active=False)
    session.add(rep)
    session.flush()
    company = _seed_company(session)
    _make_lead(session, company, rep_email="off@x.com", apollo_person_id="g1")

    sent = []
    monkeypatch.setattr("app.digest.scheduler.send_email", lambda **k: sent.append(k) or {"ok": True})

    now_utc = datetime(2026, 5, 20, 12, 0)
    run = run_digest_tick(session, now_utc=now_utc, force=True)
    assert run.reps_emailed == 0
    assert sent == []


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


def test_linkedin_only_leads_are_included(session, monkeypatch):
    """A pending lead with linkedin_url but no email must be sent in the digest."""
    rep = Rep(email="ny@x.com", name="NY", timezone="America/New_York", daily_lead_cap=10)
    session.add(rep)
    session.flush()
    company = _seed_company(session)

    # One with email, one LinkedIn-only — both should appear
    _make_lead(session, company, rep_email="ny@x.com", apollo_person_id="with-email", email="x@x.com")
    li_lead = Lead(
        company_id=company.id,
        apollo_person_id="linkedin-only",
        full_name="No Email Person",
        title="VP",
        email=None,
        linkedin_url="https://linkedin.com/in/no-email-person",
        email_status="unverified",
        assigned_rep_email="ny@x.com",
        assigned_rep_name="Rep",
        routing_status="rule_matched",
        delivery_status="pending",
        date_discovered=utcnow(),
    )
    session.add(li_lead)
    session.flush()

    sent = []
    monkeypatch.setattr("app.digest.scheduler.send_email", lambda **k: sent.append(k) or {"ok": True})

    now_utc = datetime(2026, 5, 20, 12, 0)  # Wed 08:00 EDT
    run = run_digest_tick(session, now_utc=now_utc)

    assert run.total_leads_delivered == 2
    delivered = session.query(Lead).filter_by(delivery_status="delivered").all()
    delivered_ids = {l.apollo_person_id for l in delivered}
    assert delivered_ids == {"with-email", "linkedin-only"}


def test_leads_without_email_or_linkedin_stay_out(session, monkeypatch):
    """If neither email nor LinkedIn is present, the lead must NOT be sent."""
    rep = Rep(email="ny@x.com", name="NY", timezone="America/New_York")
    session.add(rep)
    session.flush()
    company = _seed_company(session)

    naked = Lead(
        company_id=company.id,
        apollo_person_id="no-channel",
        full_name="Unreachable",
        title="VP",
        email=None,
        linkedin_url=None,
        email_status="unverified",
        assigned_rep_email="ny@x.com",
        assigned_rep_name="Rep",
        routing_status="rule_matched",
        delivery_status="pending",
        date_discovered=utcnow(),
    )
    session.add(naked)
    session.flush()

    monkeypatch.setattr("app.digest.scheduler.send_email", lambda **k: {"ok": True})

    now_utc = datetime(2026, 5, 20, 12, 0)
    run = run_digest_tick(session, now_utc=now_utc)
    assert run.total_leads_delivered == 0


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
