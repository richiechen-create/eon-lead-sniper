"""Manual digest tick.

Hourly cron entrypoint. Pass --admin-summary on the last tick of the day.

Usage: python -m scripts.run_digest [--admin-summary]
"""
import argparse
import json

from app.db import session_scope
from app.digest.admin_summary import send_admin_summary
from app.digest.scheduler import run_digest_tick


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--admin-summary",
        action="store_true",
        help="Also send the admin daily summary (run this on the final tick of the day).",
    )
    args = parser.parse_args()

    with session_scope() as session:
        run = run_digest_tick(session)
        admin = None
        if args.admin_summary:
            summary = send_admin_summary(session)
            admin = {
                "credits_month": summary.credits_month,
                "credits_limit": summary.credits_limit,
                "leads_delivered": summary.leads_delivered,
            }

    print(
        json.dumps(
            {
                "digest_run_id": str(run.id),
                "reps_emailed": run.reps_emailed,
                "total_leads_delivered": run.total_leads_delivered,
                "errors": run.errors or [],
                "admin_summary": admin,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
