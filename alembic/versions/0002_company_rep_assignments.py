"""company_rep_assignments + routing_status rename

Revision ID: 0002_company_rep_assignments
Revises: 0001_baseline
Create Date: 2026-05-22

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_company_rep_assignments"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "company_rep_assignments",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "company_id",
            sa.String(length=36),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("lead_country", sa.Text(), nullable=False),
        sa.Column("rep_email", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("company_id", "lead_country", name="uq_cra_company_country"),
    )
    op.create_index("ix_cra_company", "company_rep_assignments", ["company_id"])

    # Rename existing routing_status='matched' -> 'rule_matched'. Spec extends
    # the enum to {company_override, rule_matched, fallback}.
    op.execute("UPDATE leads SET routing_status = 'rule_matched' WHERE routing_status = 'matched'")


def downgrade() -> None:
    op.execute("UPDATE leads SET routing_status = 'matched' WHERE routing_status = 'rule_matched'")
    op.drop_index("ix_cra_company", table_name="company_rep_assignments")
    op.drop_table("company_rep_assignments")
