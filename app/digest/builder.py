import csv
import io
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from app.models import Lead


@dataclass
class CompanyGroup:
    company_name: str
    domain: str
    leads: list[Lead]


@dataclass
class BuiltDigest:
    rep_email: str
    rep_first_name: str
    subject: str
    html: str
    text: str
    csv_bytes: bytes
    csv_filename: str
    lead_ids: list  # uuids — caller flips delivery_status on success


def _group_leads_by_company(leads: list[Lead]) -> list[CompanyGroup]:
    groups: dict[str, CompanyGroup] = {}
    for lead in leads:
        company = lead.company
        key = (company.company_name or "").lower()
        if key not in groups:
            groups[key] = CompanyGroup(
                company_name=company.company_name or company.domain,
                domain=company.domain,
                leads=[],
            )
        groups[key].leads.append(lead)
    # Alphabetical by company name.
    return sorted(groups.values(), key=lambda g: g.company_name.lower())


def _format_date(d: datetime) -> str:
    return d.strftime("%a %b %-d") if hasattr(datetime, "strftime") else d.strftime("%a %b %d")


def _build_text(rep_first_name: str, groups: list[CompanyGroup], total_leads: int) -> str:
    lines = [
        f"Hi {rep_first_name},",
        "",
        f"{total_leads} new enriched leads are ready for your outreach today, "
        f"across {len(groups)} companies.",
        "",
    ]
    for group in groups:
        lines.append("=" * 50)
        lines.append(f"{group.company_name} ({group.domain})")
        lines.append("=" * 50)
        for lead in group.leads:
            lines.append(f"- {lead.full_name or '(name unknown)'} - {lead.title or ''}")
            if lead.email:
                lines.append(f"  Email: {lead.email}")
            if lead.linkedin_url:
                lines.append(f"  LinkedIn: {lead.linkedin_url}")
        lines.append("")
    lines.append("All leads attached as CSV.")
    lines.append("")
    lines.append("-- EON Bullseye")
    return "\n".join(lines)


def _build_html(rep_first_name: str, groups: list[CompanyGroup], total_leads: int) -> str:
    parts = [
        "<html><body style=\"font-family: -apple-system, system-ui, sans-serif; color: #1f2328;\">",
        f"<p>Hi {rep_first_name},</p>",
        f"<p>{total_leads} new enriched leads are ready for your outreach today, "
        f"across {len(groups)} companies.</p>",
    ]
    for group in groups:
        parts.append(
            f'<h3 style="border-bottom:1px solid #d0d7de; padding-bottom:4px;">'
            f"{_html_escape(group.company_name)} "
            f'<span style="color:#57606a; font-weight:normal;">'
            f"({_html_escape(group.domain)})</span></h3>"
        )
        parts.append("<ul>")
        for lead in group.leads:
            name = _html_escape(lead.full_name or "(name unknown)")
            title = _html_escape(lead.title or "")
            parts.append(f"<li><strong>{name}</strong> &mdash; {title}<br/>")
            if lead.email:
                parts.append(
                    f'Email: <a href="mailto:{_html_escape(lead.email)}">'
                    f"{_html_escape(lead.email)}</a><br/>"
                )
            if lead.linkedin_url:
                parts.append(
                    f'LinkedIn: <a href="{_html_escape(lead.linkedin_url)}">'
                    f"{_html_escape(lead.linkedin_url)}</a>"
                )
            parts.append("</li>")
        parts.append("</ul>")
    parts.append("<p>All leads attached as CSV.</p>")
    parts.append('<p style="color:#57606a;">-- EON Bullseye</p>')
    parts.append("</body></html>")
    return "".join(parts)


def _html_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _build_csv(leads: list[Lead]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "Company",
            "Domain",
            "Full Name",
            "Title",
            "Seniority",
            "Department",
            "Email",
            "LinkedIn URL",
            "Date Discovered",
        ]
    )
    for lead in leads:
        company = lead.company
        writer.writerow(
            [
                company.company_name or "",
                company.domain or "",
                lead.full_name or "",
                lead.title or "",
                lead.seniority or "",
                lead.department or "",
                lead.email or "",
                lead.linkedin_url or "",
                lead.date_discovered.strftime("%Y-%m-%d") if lead.date_discovered else "",
            ]
        )
    return buf.getvalue().encode("utf-8")


def build_digest(rep_email: str, rep_name: str, leads: list[Lead], local_date: datetime) -> Optional[BuiltDigest]:
    if not leads:
        return None
    rep_first_name = (rep_name or rep_email).split()[0]
    groups = _group_leads_by_company(leads)
    total = len(leads)
    subject = f"{total} new leads ready - {_format_date(local_date)}"
    text = _build_text(rep_first_name, groups, total)
    html = _build_html(rep_first_name, groups, total)
    csv_bytes = _build_csv(leads)
    csv_filename = f"leads_{local_date.strftime('%Y-%m-%d')}.csv"
    return BuiltDigest(
        rep_email=rep_email,
        rep_first_name=rep_first_name,
        subject=subject,
        html=html,
        text=text,
        csv_bytes=csv_bytes,
        csv_filename=csv_filename,
        lead_ids=[lead.id for lead in leads],
    )
