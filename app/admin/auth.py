import hmac
from datetime import datetime, timedelta
from typing import Optional

from fastapi import HTTPException, Request, status
from fastapi.responses import RedirectResponse

from app.config import get_settings

SESSION_KEY = "admin_user"
SESSION_EXPIRES_KEY = "admin_session_expires_at"
SESSION_TTL = timedelta(days=7)


def verify_credentials(username: str, password: str) -> bool:
    settings = get_settings()
    if not settings.ADMIN_USERNAME or not settings.ADMIN_PASSWORD:
        return False
    return hmac.compare_digest(username, settings.ADMIN_USERNAME) and hmac.compare_digest(
        password, settings.ADMIN_PASSWORD
    )


def login_session(request: Request, username: str) -> None:
    request.session[SESSION_KEY] = username
    request.session[SESSION_EXPIRES_KEY] = (datetime.utcnow() + SESSION_TTL).isoformat()


def logout_session(request: Request) -> None:
    request.session.pop(SESSION_KEY, None)
    request.session.pop(SESSION_EXPIRES_KEY, None)


def current_user(request: Request) -> Optional[str]:
    user = request.session.get(SESSION_KEY)
    if not user:
        return None
    expires_raw = request.session.get(SESSION_EXPIRES_KEY)
    if not expires_raw:
        return None
    try:
        expires = datetime.fromisoformat(expires_raw)
    except ValueError:
        return None
    if datetime.utcnow() > expires:
        logout_session(request)
        return None
    return user


def require_admin(request: Request) -> str:
    user = current_user(request)
    if user is None:
        if "text/html" in (request.headers.get("accept") or ""):
            raise HTTPException(
                status_code=status.HTTP_303_SEE_OTHER,
                headers={"Location": f"/admin/login?next={request.url.path}"},
            )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not logged in")
    return user


def redirect_to_login(next_path: str = "/admin") -> RedirectResponse:
    return RedirectResponse(url=f"/admin/login?next={next_path}", status_code=303)
