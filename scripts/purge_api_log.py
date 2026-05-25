"""Daily purge of api_call_log entries older than 30 days.

Usage: python -m scripts.purge_api_log [--older-than-days 30]
"""
import argparse
import json

from app.db import session_scope
from app.maintenance import purge_old_api_call_log


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--older-than-days", type=int, default=30)
    args = parser.parse_args()
    with session_scope() as session:
        deleted = purge_old_api_call_log(session, older_than_days=args.older_than_days)
    print(json.dumps({"deleted": deleted}, indent=2))


if __name__ == "__main__":
    main()
