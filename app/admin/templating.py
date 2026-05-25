from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.admin.auth import current_user
from app.countries import CANONICAL_COUNTRIES
from app.timezones import CANONICAL_TIMEZONES, TIMEZONES_BY_REGION

TEMPLATES_DIR = Path(__file__).parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def render(request: Request, template_name: str, **context):
    """Wrap Jinja2Templates.TemplateResponse with current_user injected."""
    context.setdefault("current_user", current_user(request))
    context.setdefault("canonical_countries", CANONICAL_COUNTRIES)
    context.setdefault("canonical_timezones", CANONICAL_TIMEZONES)
    context.setdefault("timezones_by_region", TIMEZONES_BY_REGION)
    return templates.TemplateResponse(
        request=request, name=template_name, context=context
    )
