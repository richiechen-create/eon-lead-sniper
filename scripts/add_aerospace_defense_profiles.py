"""Idempotently insert the 3 aerospace & defense targeting profiles.

Per add-aerospace-defense-profiles.md. Run with:

  python -m scripts.add_aerospace_defense_profiles

Does NOT touch config/targeting_profiles.yaml (YAML sync would later
deactivate profiles missing from YAML — UI/script-added profiles must
live outside the YAML to survive). Does NOT modify existing profiles.

Re-running is safe: each profile is checked by name first; existing rows
are left untouched and logged as "skipped".
"""
from sqlalchemy import select

from app.db import session_scope
from app.models import TargetingProfile


PROFILES: list[dict] = [
    {
        "name": "ld_leadership_aerospace_defense",
        "titles": [
            "Chief Learning Officer",
            "Chief People Officer",
            "VP Learning and Development",
            "VP Talent Development",
            "VP People & Culture",
            "VP Customer Services and Training",
            "Head of Learning",
            "Head of Operator Training",
            "Director of Learning and Development",
            "Director of Talent Development",
            "Director of Technical Training",
            "Director of Pilot Training",
            "Director of Aircrew Training",
            "Director of MRO Training",
            "Director of Maintenance Training",
            "Director of Customer Training",
            "Director of Production Training",
            "Director of Engineering Training",
            "Director of Workforce Development",
            "Director of Skills Development",
            "Director of Apprenticeships",
        ],
        "seniorities": ["c_suite", "vp", "head", "director"],
        "departments": ["Human Resources", "Customer Services", "Training", "Engineering"],
        "locations": [],
        "keywords": [],
    },
    {
        "name": "safety_airworthiness_leadership",
        "titles": [
            "Chief Safety Officer",
            "Chief Quality Officer",
            "Chief Engineer",
            "VP Safety",
            "VP Flight Safety",
            "VP Quality",
            "VP Mission Assurance",
            "VP Manufacturing Quality",
            "VP EHS",
            "Head of Quality",
            "Head of Airworthiness",
            "Director of Quality Assurance",
            "Director of Aviation Safety",
            "Director of Flight Safety",
            "Director of System Safety",
            "Director of Mission Assurance",
            "Director of Airworthiness",
            "Director of Certification",
            "Director of Continuing Airworthiness",
            "Director of AS9100 Quality",
            "Director of Supplier Quality",
            "Director of ITAR Compliance",
            "Director of Export Control",
            "Director of Defense Compliance",
            "Director of Regulatory Affairs",
            "Director of EHS Manufacturing",
        ],
        "seniorities": ["c_suite", "vp", "head", "director"],
        "departments": ["Quality", "Safety", "Compliance", "Engineering", "Regulatory"],
        "locations": [],
        "keywords": [],
    },
    {
        "name": "manufacturing_engineering_leadership",
        "titles": [
            "Chief Operating Officer",
            "Chief Engineer",
            "Chief Manufacturing Officer",
            "VP Manufacturing",
            "VP Production",
            "VP Industrial Operations",
            "VP Engineering",
            "VP Engineering Excellence",
            "VP MRO Operations",
            "VP Operations",
            "Head of Production",
            "Head of Manufacturing",
            "Director of Manufacturing Excellence",
            "Director of Operations",
            "Director of Final Assembly",
            "Director of MRO",
            "Director of Industrialization",
            "Director of Continuous Improvement",
            "Director of Engineering Capability",
            "Director of Production Engineering",
            "Director of Plant Operations",
        ],
        "seniorities": ["c_suite", "vp", "head", "director"],
        "departments": ["Manufacturing", "Operations", "Engineering"],
        "locations": [],
        "keywords": [],
    },
]


def main() -> None:
    created = 0
    skipped = 0
    with session_scope() as session:
        for spec in PROFILES:
            existing = session.execute(
                select(TargetingProfile).where(TargetingProfile.name == spec["name"])
            ).scalar_one_or_none()
            if existing is not None:
                print(f"  skipped (already exists): {spec['name']}")
                skipped += 1
                continue
            session.add(
                TargetingProfile(
                    name=spec["name"],
                    titles=spec["titles"],
                    seniorities=spec["seniorities"],
                    departments=spec["departments"],
                    locations=spec["locations"],
                    keywords=spec["keywords"],
                    is_active=True,
                )
            )
            print(f"  created: {spec['name']}")
            created += 1
    print()
    print(f"Done — created {created}, skipped {skipped}.")


if __name__ == "__main__":
    main()
