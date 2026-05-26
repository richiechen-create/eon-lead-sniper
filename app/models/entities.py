import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDStr, new_uuid, utcnow


class Company(Base, TimestampMixin):
    __tablename__ = "companies"

    id: Mapped[uuid.UUID] = mapped_column(UUIDStr, primary_key=True, default=new_uuid)
    company_name: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    industry: Mapped[Optional[str]] = mapped_column(Text)
    country: Mapped[Optional[str]] = mapped_column(Text)
    tier: Mapped[Optional[str]] = mapped_column(Text)
    max_contacts_per_run: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    source_row_id: Mapped[Optional[str]] = mapped_column(Text)

    targeting_links: Mapped[list["CompanyTargeting"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    rep_assignments: Mapped[list["CompanyRepAssignment"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    leads: Mapped[list["Lead"]] = relationship(back_populates="company")


class TargetingProfile(Base):
    __tablename__ = "targeting_profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUIDStr, primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    titles: Mapped[list] = mapped_column(JSON, default=list)
    seniorities: Mapped[list] = mapped_column(JSON, default=list)
    departments: Mapped[list] = mapped_column(JSON, default=list)
    locations: Mapped[list] = mapped_column(JSON, default=list)
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    company_links: Mapped[list["CompanyTargeting"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )


class CompanyRepAssignment(Base, TimestampMixin):
    """Per-company per-country rep override. Takes precedence over routing_rules.

    `lead_country = '*'` is a wildcard that applies when no country-specific
    row matches.
    """
    __tablename__ = "company_rep_assignments"

    id: Mapped[uuid.UUID] = mapped_column(UUIDStr, primary_key=True, default=new_uuid)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUIDStr, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    lead_country: Mapped[str] = mapped_column(Text, nullable=False)
    rep_email: Mapped[str] = mapped_column(Text, nullable=False)

    company: Mapped["Company"] = relationship(back_populates="rep_assignments")

    __table_args__ = (
        UniqueConstraint("company_id", "lead_country", name="uq_cra_company_country"),
        Index("ix_cra_company", "company_id"),
    )


class CompanyTargeting(Base):
    __tablename__ = "company_targeting"

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUIDStr, ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True
    )
    targeting_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUIDStr, ForeignKey("targeting_profiles.id", ondelete="CASCADE"), primary_key=True
    )

    company: Mapped[Company] = relationship(back_populates="targeting_links")
    profile: Mapped[TargetingProfile] = relationship(back_populates="company_links")


class Rep(Base):
    __tablename__ = "reps"

    id: Mapped[uuid.UUID] = mapped_column(UUIDStr, primary_key=True, default=new_uuid)
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    timezone: Mapped[str] = mapped_column(Text, nullable=False, default="UTC")
    team: Mapped[Optional[str]] = mapped_column(Text)
    daily_lead_cap: Mapped[Optional[int]] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class RoutingRule(Base):
    __tablename__ = "routing_rules"

    id: Mapped[uuid.UUID] = mapped_column(UUIDStr, primary_key=True, default=new_uuid)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    conditions: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    assigned_rep_email: Mapped[str] = mapped_column(Text, nullable=False)
    assigned_rep_name: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[uuid.UUID] = mapped_column(UUIDStr, primary_key=True, default=new_uuid)
    company_id: Mapped[uuid.UUID] = mapped_column(UUIDStr, ForeignKey("companies.id"), nullable=False)
    apollo_person_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    full_name: Mapped[Optional[str]] = mapped_column(Text)
    first_name: Mapped[Optional[str]] = mapped_column(Text)
    last_name: Mapped[Optional[str]] = mapped_column(Text)
    title: Mapped[Optional[str]] = mapped_column(Text)
    seniority: Mapped[Optional[str]] = mapped_column(Text)
    department: Mapped[Optional[str]] = mapped_column(Text)
    linkedin_url: Mapped[Optional[str]] = mapped_column(Text)
    email: Mapped[Optional[str]] = mapped_column(Text)
    email_status: Mapped[Optional[str]] = mapped_column(Text)  # verified|likely|unverified
    person_country: Mapped[Optional[str]] = mapped_column(Text)
    person_city: Mapped[Optional[str]] = mapped_column(Text)
    assigned_rep_email: Mapped[Optional[str]] = mapped_column(Text)
    assigned_rep_name: Mapped[Optional[str]] = mapped_column(Text)
    routing_rule_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUIDStr, ForeignKey("routing_rules.id"))
    routing_status: Mapped[Optional[str]] = mapped_column(Text)  # matched|fallback
    delivery_status: Mapped[str] = mapped_column(Text, default="pending", nullable=False)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    date_discovered: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    date_enriched: Mapped[Optional[datetime]] = mapped_column(DateTime)
    raw_apollo_payload: Mapped[Optional[dict]] = mapped_column(JSON)

    company: Mapped[Company] = relationship(back_populates="leads")

    __table_args__ = (
        Index("ix_leads_rep_status", "assigned_rep_email", "delivery_status"),
        Index("ix_leads_company", "company_id"),
        Index("ix_leads_date_discovered", "date_discovered"),
    )


class DoNotContact(Base):
    __tablename__ = "do_not_contact"

    id: Mapped[uuid.UUID] = mapped_column(UUIDStr, primary_key=True, default=new_uuid)
    email: Mapped[Optional[str]] = mapped_column(Text)
    domain: Mapped[Optional[str]] = mapped_column(Text)
    apollo_person_id: Mapped[Optional[str]] = mapped_column(Text)
    reason: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (
        Index("ix_dnc_email", "email"),
        Index("ix_dnc_domain", "domain"),
        Index("ix_dnc_apollo_person_id", "apollo_person_id"),
    )


class EnrichmentRun(Base):
    __tablename__ = "enrichment_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUIDStr, primary_key=True, default=new_uuid)
    run_started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    run_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    companies_processed: Mapped[int] = mapped_column(Integer, default=0)
    candidates_found: Mapped[int] = mapped_column(Integer, default=0)
    new_leads_created: Mapped[int] = mapped_column(Integer, default=0)
    contacts_enriched: Mapped[int] = mapped_column(Integer, default=0)
    credits_consumed: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[list] = mapped_column(JSON, default=list)
    # Free-form sidecar for run context (e.g. boost runs tag this with
    # {"boost_country": "India", "cap_override": 10}).
    # Named `run_metadata` because `metadata` is reserved by SQLAlchemy's Base.
    run_metadata: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)


class DigestRun(Base):
    __tablename__ = "digest_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUIDStr, primary_key=True, default=new_uuid)
    run_date: Mapped[date] = mapped_column(Date, nullable=False)
    reps_emailed: Mapped[int] = mapped_column(Integer, default=0)
    total_leads_delivered: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class ApiCallLog(Base):
    __tablename__ = "api_call_log"

    id: Mapped[uuid.UUID] = mapped_column(UUIDStr, primary_key=True, default=new_uuid)
    called_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    http_status: Mapped[Optional[int]] = mapped_column(Integer)
    credits_used: Mapped[int] = mapped_column(Integer, default=0)
    request_payload: Mapped[Optional[dict]] = mapped_column(JSON)
    response_summary: Mapped[Optional[dict]] = mapped_column(JSON)

    __table_args__ = (Index("ix_api_call_log_called_at", "called_at"),)
