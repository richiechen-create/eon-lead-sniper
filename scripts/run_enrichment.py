"""Manual enrichment trigger.

Usage: python -m scripts.run_enrichment
"""
import json

from app.db import session_scope
from app.tasks.enrichment import run_enrichment


def main() -> None:
    with session_scope() as session:
        summary = run_enrichment(session)
    print(
        json.dumps(
            {
                "run_id": summary.run_id,
                "companies_processed": summary.companies_processed,
                "candidates_found": summary.candidates_found,
                "new_leads_created": summary.new_leads_created,
                "contacts_enriched": summary.contacts_enriched,
                "credits_consumed": summary.credits_consumed,
                "halted_by_budget": summary.halted_by_budget,
                "error_count": len(summary.errors),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
