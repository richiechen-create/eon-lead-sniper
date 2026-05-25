from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from sqlalchemy import func, select

from app.admin.auth import require_admin
from app.admin.templating import render
from app.db import session_scope
from app.models import CompanyTargeting, TargetingProfile

router = APIRouter(prefix="/targeting-profiles")


def _split_lines(raw: str) -> list[str]:
    return [line.strip() for line in (raw or "").splitlines() if line.strip()]


def _split_csv(raw: str) -> list[str]:
    return [s.strip() for s in (raw or "").split(",") if s.strip()]


@router.get("")
def profiles_index(request: Request, _user: str = Depends(require_admin)):
    with session_scope() as session:
        profiles = list(
            session.execute(select(TargetingProfile).order_by(TargetingProfile.name)).scalars()
        )
        counts = dict(
            session.execute(
                select(CompanyTargeting.targeting_profile_id, func.count(CompanyTargeting.company_id))
                .group_by(CompanyTargeting.targeting_profile_id)
            ).all()
        )
    return render(request, "profiles.html", profiles=profiles, counts=counts)


@router.post("")
def profiles_create(
    response: Response,
    name: str = Form(...),
    titles: str = Form(""),
    seniorities: str = Form(""),
    departments: str = Form(""),
    locations: str = Form(""),
    keywords: str = Form(""),
    _user: str = Depends(require_admin),
):
    with session_scope() as session:
        existing = session.execute(
            select(TargetingProfile).where(TargetingProfile.name == name)
        ).scalar_one_or_none()
        if existing is not None:
            raise HTTPException(400, "profile name already exists")
        session.add(
            TargetingProfile(
                name=name,
                titles=_split_lines(titles),
                seniorities=_split_csv(seniorities),
                departments=_split_csv(departments),
                locations=_split_csv(locations),
                keywords=_split_csv(keywords),
                is_active=True,
            )
        )
    response.headers["X-Toast"] = "Profile added"
    response.headers["HX-Redirect"] = "/admin/targeting-profiles"
    return {"ok": True}


@router.patch("/{profile_id}")
def profiles_update(
    profile_id: str,
    response: Response,
    titles: Optional[str] = Form(None),
    seniorities: Optional[str] = Form(None),
    departments: Optional[str] = Form(None),
    locations: Optional[str] = Form(None),
    keywords: Optional[str] = Form(None),
    is_active: Optional[bool] = Form(None),
    _user: str = Depends(require_admin),
):
    with session_scope() as session:
        profile = session.get(TargetingProfile, profile_id)
        if profile is None:
            raise HTTPException(404, "profile not found")
        if titles is not None:
            profile.titles = _split_lines(titles)
        if seniorities is not None:
            profile.seniorities = _split_csv(seniorities)
        if departments is not None:
            profile.departments = _split_csv(departments)
        if locations is not None:
            profile.locations = _split_csv(locations)
        if keywords is not None:
            profile.keywords = _split_csv(keywords)
        if is_active is not None:
            profile.is_active = is_active
    response.headers["X-Toast"] = "Saved"
    return {"ok": True}
