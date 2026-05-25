from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app.admin.auth import login_session, logout_session, verify_credentials
from app.admin.templating import render

router = APIRouter()


@router.get("/login")
def login_page(request: Request, next: str = "/admin"):
    return render(request, "login.html", next=next, error=None)


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/admin"),
):
    if not verify_credentials(username, password):
        return render(
            request,
            "login.html",
            next=next,
            error="Invalid username or password.",
        )
    login_session(request, username)
    safe_next = next if next.startswith("/admin") else "/admin"
    return RedirectResponse(url=safe_next, status_code=303)


@router.post("/logout")
def logout(request: Request):
    logout_session(request)
    return RedirectResponse(url="/admin/login", status_code=303)
