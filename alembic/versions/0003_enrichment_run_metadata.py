"""enrichment_runs.run_metadata column

Adds a nullable JSON sidecar so a run can carry extra context — currently
used to tag country-boost runs with {"boost_country": "...", "cap_override": N}.

Revision ID: 0003_enrichment_run_metadata
Revises: 0002_company_rep_assignments
Create Date: 2026-05-26
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_enrichment_run_metadata"
down_revision: Union[str, None] = "0002_company_rep_assignments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "enrichment_runs",
        sa.Column("run_metadata", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("enrichment_runs", "run_metadata")
