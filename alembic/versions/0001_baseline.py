"""baseline schema

Revision ID: 0001_baseline
Revises:
Create Date: 2026-05-22

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("company_name", sa.Text(), nullable=False),
        sa.Column("domain", sa.Text(), nullable=False, unique=True),
        sa.Column("industry", sa.Text()),
        sa.Column("country", sa.Text()),
        sa.Column("tier", sa.Text()),
        sa.Column("max_contacts_per_run", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notes", sa.Text()),
        sa.Column("source_row_id", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "targeting_profiles",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False, unique=True),
        sa.Column("titles", sa.JSON()),
        sa.Column("seniorities", sa.JSON()),
        sa.Column("departments", sa.JSON()),
        sa.Column("locations", sa.JSON()),
        sa.Column("keywords", sa.JSON()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    op.create_table(
        "company_targeting",
        sa.Column(
            "company_id",
            sa.String(length=36),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "targeting_profile_id",
            sa.String(length=36),
            sa.ForeignKey("targeting_profiles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    op.create_table(
        "reps",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("email", sa.Text(), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("timezone", sa.Text(), nullable=False, server_default="UTC"),
        sa.Column("team", sa.Text()),
        sa.Column("daily_lead_cap", sa.Integer()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "routing_rules",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("conditions", sa.JSON(), nullable=False),
        sa.Column("assigned_rep_email", sa.Text(), nullable=False),
        sa.Column("assigned_rep_name", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "leads",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("company_id", sa.String(length=36), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("apollo_person_id", sa.Text(), nullable=False, unique=True),
        sa.Column("full_name", sa.Text()),
        sa.Column("first_name", sa.Text()),
        sa.Column("last_name", sa.Text()),
        sa.Column("title", sa.Text()),
        sa.Column("seniority", sa.Text()),
        sa.Column("department", sa.Text()),
        sa.Column("linkedin_url", sa.Text()),
        sa.Column("email", sa.Text()),
        sa.Column("email_status", sa.Text()),
        sa.Column("person_country", sa.Text()),
        sa.Column("person_city", sa.Text()),
        sa.Column("assigned_rep_email", sa.Text()),
        sa.Column("assigned_rep_name", sa.Text()),
        sa.Column("routing_rule_id", sa.String(length=36), sa.ForeignKey("routing_rules.id")),
        sa.Column("routing_status", sa.Text()),
        sa.Column("delivery_status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("delivered_at", sa.DateTime()),
        sa.Column("date_discovered", sa.DateTime(), nullable=False),
        sa.Column("date_enriched", sa.DateTime()),
        sa.Column("raw_apollo_payload", sa.JSON()),
    )
    op.create_index("ix_leads_rep_status", "leads", ["assigned_rep_email", "delivery_status"])
    op.create_index("ix_leads_company", "leads", ["company_id"])
    op.create_index("ix_leads_date_discovered", "leads", ["date_discovered"])

    op.create_table(
        "do_not_contact",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("email", sa.Text()),
        sa.Column("domain", sa.Text()),
        sa.Column("apollo_person_id", sa.Text()),
        sa.Column("reason", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_dnc_email", "do_not_contact", ["email"])
    op.create_index("ix_dnc_domain", "do_not_contact", ["domain"])
    op.create_index("ix_dnc_apollo_person_id", "do_not_contact", ["apollo_person_id"])

    op.create_table(
        "enrichment_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("run_started_at", sa.DateTime(), nullable=False),
        sa.Column("run_completed_at", sa.DateTime()),
        sa.Column("companies_processed", sa.Integer(), server_default="0"),
        sa.Column("candidates_found", sa.Integer(), server_default="0"),
        sa.Column("new_leads_created", sa.Integer(), server_default="0"),
        sa.Column("contacts_enriched", sa.Integer(), server_default="0"),
        sa.Column("credits_consumed", sa.Integer(), server_default="0"),
        sa.Column("errors", sa.JSON()),
    )

    op.create_table(
        "digest_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("run_date", sa.Date(), nullable=False),
        sa.Column("reps_emailed", sa.Integer(), server_default="0"),
        sa.Column("total_leads_delivered", sa.Integer(), server_default="0"),
        sa.Column("errors", sa.JSON()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "api_call_log",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("called_at", sa.DateTime(), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("http_status", sa.Integer()),
        sa.Column("credits_used", sa.Integer(), server_default="0"),
        sa.Column("request_payload", sa.JSON()),
        sa.Column("response_summary", sa.JSON()),
    )
    op.create_index("ix_api_call_log_called_at", "api_call_log", ["called_at"])


def downgrade() -> None:
    op.drop_index("ix_api_call_log_called_at", table_name="api_call_log")
    op.drop_table("api_call_log")
    op.drop_table("digest_runs")
    op.drop_table("enrichment_runs")
    op.drop_index("ix_dnc_apollo_person_id", table_name="do_not_contact")
    op.drop_index("ix_dnc_domain", table_name="do_not_contact")
    op.drop_index("ix_dnc_email", table_name="do_not_contact")
    op.drop_table("do_not_contact")
    op.drop_index("ix_leads_date_discovered", table_name="leads")
    op.drop_index("ix_leads_company", table_name="leads")
    op.drop_index("ix_leads_rep_status", table_name="leads")
    op.drop_table("leads")
    op.drop_table("routing_rules")
    op.drop_table("reps")
    op.drop_table("company_targeting")
    op.drop_table("targeting_profiles")
    op.drop_table("companies")
