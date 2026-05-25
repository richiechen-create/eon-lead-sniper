from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from sqlalchemy import or_, select

from app.admin.auth import require_admin
from app.admin.templating import render
from app.db import session_scope
from app.models import DoNotContact

router = APIRouter(prefix="/do-not-contact")


@router.get("")
def dnc_index(
    request: Request,
    q: Optional[str] = None,
    _user: str = Depends(require_admin),
):
    with session_scope() as session:
        stmt = select(DoNotContact).order_by(DoNotContact.created_at.desc())
        if q:
            like = f"%{q}%"
            stmt = stmt.where(
                or_(
                    DoNotContact.email.ilike(like),
                    DoNotContact.domain.ilike(like),
                    DoNotContact.apollo_person_id.ilike(like),
                    DoNotContact.reason.ilike(like),
                )
            )
        rows = list(session.execute(stmt).scalars())
    return render(request, "do_not_contact.html", rows=rows, q=q or "")


@router.post("")
def dnc_create(
    response: Response,
    email: str = Form(""),
    domain: str = Form(""),
    apollo_person_id: str = Form(""),
    reason: str = Form("manual exclusion"),
    _user: str = Depends(require_admin),
):
    email = email.strip() or None
    domain = domain.strip().lower() or None
    apollo_person_id = apollo_person_id.strip() or None
    if not (email or domain or apollo_person_id):
        raise HTTPException(400, "provide at least one of email, domain, or apollo_person_id")
    with session_scope() as session:
        session.add(
            DoNotContact(
                email=email,
                domain=domain,
                apollo_person_id=apollo_person_id,
                reason=reason,
            )
        )
    response.headers["X-Toast"] = "Added to DNC"
    response.headers["HX-Redirect"] = "/admin/do-not-contact"
    return {"ok": True}


@router.delete("/{dnc_id}")
def dnc_delete(dnc_id: str, response: Response, _user: str = Depends(require_admin)):
    with session_scope() as session:
        row = session.get(DoNotContact, dnc_id)
        if row is None:
            raise HTTPException(404, "not found")
        session.delete(row)
    response.headers["X-Toast"] = "Removed"
    return {"ok": True}
