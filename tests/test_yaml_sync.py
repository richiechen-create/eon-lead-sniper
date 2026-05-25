from pathlib import Path

from app.models import Rep, RoutingRule, TargetingProfile
from app.sync.yaml_config import sync_all


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


def test_sync_all_yaml(tmp_path: Path, session):
    _write(
        tmp_path / "targeting_profiles.yaml",
        """
- name: ld_leadership
  titles: [VP Learning]
  seniorities: [vp]
- name: ehs_leadership
  titles: [Director EHS]
  seniorities: [director]
""",
    )
    _write(
        tmp_path / "reps.yaml",
        """
- email: dan@example.com
  name: Dan
  team: ops
  timezone: Asia/Singapore
- email: sales_us@example.com
  name: TBD
  team: sales
  timezone: America/New_York
  daily_lead_cap: 15
""",
    )
    _write(
        tmp_path / "routing_rules.yaml",
        """
- priority: 10
  name: O&G Americas
  conditions:
    company_industry: [oil and gas]
    company_country: [United States]
  assigned_rep_email: sales_us@example.com
  assigned_rep_name: TBD
- priority: 9999
  name: Fallback
  conditions: {}
  assigned_rep_email: dan@example.com
  assigned_rep_name: Dan
""",
    )

    report = sync_all(session, tmp_path)
    assert report.profiles_upserted == 2
    assert report.reps_upserted == 2
    assert report.rules_upserted == 2

    assert session.query(TargetingProfile).count() == 2
    rep = session.query(Rep).filter_by(email="sales_us@example.com").one()
    assert rep.daily_lead_cap == 15
    assert rep.timezone == "America/New_York"

    # Remove the O&G rule; re-sync deactivates it (no hard delete).
    _write(
        tmp_path / "routing_rules.yaml",
        """
- priority: 9999
  name: Fallback
  conditions: {}
  assigned_rep_email: dan@example.com
  assigned_rep_name: Dan
""",
    )
    report2 = sync_all(session, tmp_path)
    assert report2.rules_deactivated == 1
    og = session.query(RoutingRule).filter_by(name="O&G Americas").one()
    assert og.is_active is False
