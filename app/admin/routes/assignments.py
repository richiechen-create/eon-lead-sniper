"""Per-company rep assignments (cascading override above segment rules)."""
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from sqlalchemy import func, select

from app.admin.auth import require_admin
from app.admin.templating import render
from app.countries import is_canonical_country
from app.db import session_scope
from app.models import Company, CompanyRepAssignment, Rep

router = APIRouter(prefix="/companies/{company_id}/assignments")


def _load_company_or_404(session, company_id: str) -> Company:
    company = session.get(Company, company_id)
    if company is None:
        raise HTTPException(404, "company not found")
    return company


def _active_reps(session) -> list[Rep]:
    return list(
        session.execute(
            select(Rep).where(Rep.is_active == True).order_by(Rep.team, Rep.name)  # noqa: E712
        ).scalars()
    )


def _grouped_reps(reps: list[Rep]) -> dict[str, list[Rep]]:
    out: dict[str, list[Rep]] = {}
    for r in reps:
        key = r.team or "other"
        out.setdefault(key, []).append(r)
    return out


def _render_editor(request: Request, session, company: Company):
    assignments = list(
        session.execute(
            select(CompanyRepAssignment)
            .where(CompanyRepAssignment.company_id == company.id)
            .order_by(CompanyRepAssignment.lead_country)
        ).scalars()
    )
    reps = _active_reps(session)
    reps_by_email = {r.email: r for r in reps}

    rows = []
    for a in assignments:
        rep = reps_by_email.get(a.rep_email)
        rows.append(
            {
                "assignment": a,
                "rep_name": rep.name if rep else a.rep_email,
                "team": rep.team if rep else None,
                "orphaned": rep is None,
            }
        )

    return render(
        request,
        "_assignment_editor.html",
        company=company,
        rows=rows,
        reps_by_team=_grouped_reps(reps),
    )


@router.get("")
def assignments_editor(
    company_id: str,
    request: Request,
    _user: str = Depends(require_admin),
):
    with session_scope() as session:
        company = _load_company_or_404(session, company_id)
        return _render_editor(request, session, company)


@router.post("")
def assignments_add(
    company_id: str,
    request: Request,
    lead_country: str = Form(...),
    rep_email: str = Form(...),
    _user: str = Depends(require_admin),
):
    lead_country = lead_country.strip()
    rep_email = rep_email.strip().lower()
    if not lead_country or not rep_email:
        raise HTTPException(400, "lead_country and rep_email required")
    if lead_country != "*" and not is_canonical_country(lead_country):
        msg = (
            f"'{lead_country}' is not a recognized country. "
            "Pick one from the dropdown, or use * for any country."
        )
        raise HTTPException(status_code=400, detail=msg, headers={"X-Toast": msg})

    with session_scope() as session:
        company = _load_company_or_404(session, company_id)
        rep = session.execute(
            select(Rep).where(Rep.email == rep_email, Rep.is_active == True)  # noqa: E712
        ).scalar_one_or_none()
        if rep is None:
            raise HTTPException(400, "rep not active")

        existing = session.execute(
            select(CompanyRepAssignment).where(
                CompanyRepAssignment.company_id == company.id,
                CompanyRepAssignment.lead_country == lead_country,
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.rep_email = rep_email
        else:
            session.add(
                CompanyRepAssignment(
                    company_id=company.id, lead_country=lead_country, rep_email=rep_email
                )
            )
        session.flush()
        return _render_editor(request, session, company)


@router.delete("/{assignment_id}")
def assignments_delete(
    company_id: str,
    assignment_id: str,
    request: Request,
    _user: str = Depends(require_admin),
):
    with session_scope() as session:
        company = _load_company_or_404(session, company_id)
        a = session.get(CompanyRepAssignment, assignment_id)
        if a is None or a.company_id != company.id:
            raise HTTPException(404, "assignment not found")
        session.delete(a)
        session.flush()
        return _render_editor(request, session, company)


@router.post("/apply-to-segment")
def assignments_apply_to_segment(
    company_id: str,
    response: Response,
    _user: str = Depends(require_admin),
):
    """Upsert every (lead_country, rep_email) pair from this company to all
    other active companies that share the same industry."""
    with session_scope() as session:
        source = _load_company_or_404(session, company_id)
        if not source.industry:
            raise HTTPException(400, "source company has no industry — set one first")

        source_assignments = list(
            session.execute(
                select(CompanyRepAssignment).where(
                    CompanyRepAssignment.company_id == source.id
                )
            ).scalars()
        )
        if not source_assignments:
            raise HTTPException(400, "this company has no assignments to apply")

        source_key = (source.industry or "").strip().lower()
        siblings = [
            c
            for c in session.execute(
                select(Company).where(Company.is_active == True)  # noqa: E712
            ).scalars()
            if c.id != source.id and (c.industry or "").strip().lower() == source_key
        ]

        applied_companies = 0
        for sib in siblings:
            existing_by_country = {
                a.lead_country: a
                for a in session.execute(
                    select(CompanyRepAssignment).where(
                        CompanyRepAssignment.company_id == sib.id
                    )
                ).scalars()
            }
            changed = False
            for src in source_assignments:
                existing = existing_by_country.get(src.lead_country)
                if existing is None:
                    session.add(
                        CompanyRepAssignment(
                            company_id=sib.id,
                            lead_country=src.lead_country,
                            rep_email=src.rep_email,
                        )
                    )
                    changed = True
                elif existing.rep_email != src.rep_email:
                    existing.rep_email = src.rep_email
                    changed = True
            if changed:
                applied_companies += 1
        session.flush()

    response.headers["X-Toast"] = (
        f"Applied {len(source_assignments)} assignment(s) to {applied_companies} other compan{'y' if applied_companies == 1 else 'ies'} in segment"
    )
    response.headers["HX-Refresh"] = "true"
    return {
        "applied_to": applied_companies,
        "assignments_per_company": len(source_assignments),
    }
