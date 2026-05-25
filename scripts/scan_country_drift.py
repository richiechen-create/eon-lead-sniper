"""Daily country-drift scan.

Surveys distinct lead.person_country values and logs any that don't match the
Apollo-canonical list to api_call_log (endpoint='country_drift'). Surfaced in
the admin daily summary and the dashboard data-hygiene widget.

Usage: python -m scripts.scan_country_drift
"""
import json

from app.db import session_scope
from app.maintenance import scan_country_drift


def main() -> None:
    with session_scope() as session:
        result = scan_country_drift(session, log=True)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
