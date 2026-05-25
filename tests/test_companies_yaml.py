from pathlib import Path

from app.models import Company, TargetingProfile
from app.sync.yaml_config import sync_companies


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


def test_companies_yaml_upserts_and_links_profiles(tmp_path: Path, session):
    session.add(
        TargetingProfile(
            name="ld_leadership",
            titles=[],
            seniorities=[],
            departments=[],
            locations=[],
            keywords=[],
        )
    )
    session.flush()

    _write(
        tmp_path / "companies.yaml",
        """
- company_name: Acme
  domain: acme.com
  industry: manufacturing
  country: United States
  tier: strategic
  max_contacts_per_run: 5
  targeting_profiles: [ld_leadership]
""",
    )
    upserted, deactivated, reactivated, warnings = sync_companies(
        session, tmp_path / "companies.yaml"
    )
    assert upserted == 1
    assert warnings == []

    acme = session.query(Company).filter_by(domain="acme.com").one()
    assert acme.industry == "manufacturing"
    assert acme.max_contacts_per_run == 5
    assert len(acme.targeting_links) == 1


def test_companies_yaml_does_not_soft_delete_extras(tmp_path: Path, session):
    """A company in DB but missing from YAML must NOT be deactivated by bootstrap.

    Operator may have added it via the UI; bootstrap is one-way.
    """
    session.add(Company(company_name="UI-added", domain="ui.com", is_active=True))
    session.flush()

    _write(
        tmp_path / "companies.yaml",
        "- company_name: Acme\n  domain: acme.com\n  industry: mfg\n  country: US\n",
    )
    sync_companies(session, tmp_path / "companies.yaml")

    ui_co = session.query(Company).filter_by(domain="ui.com").one()
    assert ui_co.is_active is True


def test_companies_yaml_unknown_profile_warns(tmp_path: Path, session):
    _write(
        tmp_path / "companies.yaml",
        "- company_name: A\n  domain: a.com\n  industry: x\n  country: US\n"
        "  targeting_profiles: [missing_profile]\n",
    )
    upserted, _, _, warnings = sync_companies(session, tmp_path / "companies.yaml")
    assert upserted == 1
    assert any("missing_profile" in w for w in warnings)
