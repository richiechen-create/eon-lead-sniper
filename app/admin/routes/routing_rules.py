import json
from typing import Optional

from fastapi import APIRouter, Body, Depends, Form, HTTPException, Query, Request, Response
from sqlalchemy import select

from app.admin.auth import require_admin
from app.admin.templating import render
from app.config import get_settings
from app.countries import non_canonical_countries
from app.db import session_scope
from app.models import Company, Rep, RoutingRule
from app.routing.engine import route_lead

router = APIRouter(prefix="/routing-rules")


# Country abbreviations used in auto-derived rule names. Anything not here
# falls back to the full canonical name.
_COUNTRY_ABBREV = {
    "United States": "US",
    "United Kingdom": "UK",
    "United Arab Emirates": "UAE",
    "South Korea": "Korea",
    "Saudi Arabia": "Saudi",
}


def _title(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    return s.title() if s == s.lower() else s


def _short_country(name: str) -> str:
    return _COUNTRY_ABBREV.get(name, name)


def _join_short(values: list[str], *, sep: str = "/", max_items: int = 3) -> str:
    shown = values[:max_items]
    suffix = ""
    if len(values) > max_items:
        suffix = f" +{len(values) - max_items}"
    return sep.join(shown) + suffix


def derive_rule_name(conditions: dict) -> str:
    """Generate a human-readable rule name from its conditions dict.

    Examples:
      {} -> "Catch-all (fallback)"
      {"company_industry": ["oil and gas"], "company_country": ["United States","Canada","Mexico"]}
        -> "Oil and gas · US/Canada/Mexico"
      {"company_industry": ["healthcare"]}
        -> "Healthcare · any country"
    """
    if not isinstance(conditions, dict) or not conditions:
        return "Catch-all (fallback)"

    industries = [x for x in (conditions.get("company_industry") or []) if x]
    countries = [x for x in (conditions.get("company_country") or []) if x]
    tiers = [x for x in (conditions.get("company_tier") or []) if x]
    domains = [x for x in (conditions.get("company_domain") or []) if x]
    lead_countries = [x for x in (conditions.get("lead_country") or []) if x]

    parts: list[str] = []
    if industries:
        parts.append(_join_short([_title(i) for i in industries]))
    if countries:
        parts.append(_join_short([_short_country(c) for c in countries]))
    elif industries and not lead_countries:
        parts.append("any country")
    if lead_countries:
        parts.append("lead in " + _join_short([_short_country(c) for c in lead_countries]))
    if tiers:
        parts.append("tier " + _join_short(tiers))
    if domains:
        parts.append(_join_short(domains))

    return " · ".join(parts) if parts else "Catch-all (fallback)"


def _clean_list(values: list[str] | None) -> list[str]:
    """Strip whitespace, drop empties, dedupe while preserving order."""
    seen: set[str] = set()
    out: list[str] = []
    for v in values or []:
        s = (v or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _build_conditions(
    *,
    industry: list[str],
    country: list[str],
    tier: list[str],
    domain: list[str],
    lead_country: list[str] | None = None,
) -> dict:
    """Compose the `conditions` dict from structured form fields. Drops empties.

    Industry values are lowercased so case differences ("Oil and Gas" vs
    "oil and gas") can't create parallel segments. `_matches()` already
    compares case-insensitively, so this purely keeps the stored data clean.

    `country` maps to `company_country` (the company's HQ).
    `lead_country` is the lead's personal country from Apollo (`person_country`).
    """
    out: dict[str, list[str]] = {}
    ind = [v.lower() for v in _clean_list(industry)]
    cou = _clean_list(country)
    tie = _clean_list(tier)
    dom = _clean_list(domain)
    lcou = _clean_list(lead_country or [])
    if ind:
        out["company_industry"] = ind
    if cou:
        out["company_country"] = cou
    if tie:
        out["company_tier"] = tie
    if dom:
        out["company_domain"] = dom
    if lcou:
        out["lead_country"] = lcou
    return out


def _validate_conditions(cond: dict) -> None:
    for key, label in (("company_country", "Country"), ("lead_country", "Lead country")):
        values = cond.get(key) or []
        if not isinstance(values, list):
            continue
        bad = non_canonical_countries(values)
        if bad:
            msg = (
                f"{label} contains unknown values: "
                + ", ".join(repr(b) for b in bad)
                + ". Pick from the canonical Apollo list."
            )
            raise HTTPException(status_code=400, detail=msg, headers={"X-Toast": msg})


def _parse_conditions(raw: str) -> dict:
    """Backward-compat: parse a raw JSON conditions string."""
    if not raw or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"conditions must be valid JSON: {exc}")
    if not isinstance(data, dict):
        raise HTTPException(400, "conditions must be a JSON object")
    _validate_conditions(data)
    return data


def _resolve_conditions_from_form(
    *,
    industry: list[str],
    country: list[str],
    tier: list[str],
    domain: list[str],
    lead_country: list[str] | None = None,
    conditions_raw: Optional[str],
) -> dict:
    """Prefer structured fields; fall back to raw JSON for backward compatibility."""
    if any([industry, country, tier, domain, lead_country or []]):
        cond = _build_conditions(
            industry=industry,
            country=country,
            tier=tier,
            domain=domain,
            lead_country=lead_country,
        )
        _validate_conditions(cond)
        return cond
    if conditions_raw is not None:
        return _parse_conditions(conditions_raw)
    return {}


def _grouped_rules(rules: list[RoutingRule]) -> tuple[list[tuple[str, list[RoutingRule]]], list[RoutingRule]]:
    """Split active rules into (industry_label, [rules]) groups + collect inactives.

    Active rules with multiple industries are placed in their first listed
    industry's group. Rules with no industry condition land in "No industry filter".
    """
    active = [r for r in rules if r.is_active]
    inactive = [r for r in rules if not r.is_active]

    groups: dict[str, list[RoutingRule]] = {}
    for rule in active:
        industries = (rule.conditions or {}).get("company_industry") or []
        if industries:
            label = _title(industries[0])
        else:
            label = "No industry filter"
        groups.setdefault(label, []).append(rule)

    # Sort groups alphabetically, but pin "No industry filter" to the bottom.
    keyed = sorted(
        groups.keys(),
        key=lambda k: (k == "No industry filter", k.lower()),
    )
    ordered = [(k, groups[k]) for k in keyed]
    return ordered, inactive


@router.get("")
def routing_index(request: Request, _user: str = Depends(require_admin)):
    settings = get_settings()
    with session_scope() as session:
        rules = list(
            session.execute(
                select(RoutingRule).order_by(
                    RoutingRule.priority.asc(), RoutingRule.created_at.asc()
                )
            ).scalars()
        )
        reps = list(
            session.execute(select(Rep).where(Rep.is_active == True)).scalars()  # noqa: E712
        )
        distinct_industries = sorted(
            {
                (i[0] or "").strip()
                for i in session.execute(select(Company.industry).distinct()).all()
                if i[0] and i[0].strip()
            }
        )
        distinct_tiers = sorted(
            {
                (t[0] or "").strip()
                for t in session.execute(select(Company.tier).distinct()).all()
                if t[0] and t[0].strip()
            }
        )

        active_groups, inactive_rules = _grouped_rules(rules)

    return render(
        request,
        "routing_rules.html",
        rules=rules,
        reps=reps,
        active_groups=active_groups,
        inactive_rules=inactive_rules,
        distinct_industries=distinct_industries,
        distinct_tiers=distinct_tiers,
        default_rep_email=settings.DEFAULT_REP_EMAIL,
        derive_rule_name=derive_rule_name,
    )


@router.post("")
def routing_create(
    response: Response,
    name: str = Form(""),
    priority: Optional[int] = Form(None),
    industry: list[str] = Form(default=[]),
    country: list[str] = Form(default=[]),
    tier: list[str] = Form(default=[]),
    domain: list[str] = Form(default=[]),
    lead_country: list[str] = Form(default=[]),
    conditions: Optional[str] = Form(None),  # backward compat JSON
    assigned_rep_email: str = Form(...),
    assigned_rep_name: str = Form(""),
    _user: str = Depends(require_admin),
):
    cond_dict = _resolve_conditions_from_form(
        industry=industry,
        country=country,
        tier=tier,
        domain=domain,
        lead_country=lead_country,
        conditions_raw=conditions,
    )
    final_name = (name or "").strip() or derive_rule_name(cond_dict)

    with session_scope() as session:
        if priority is None:
            # Append at the end of the active list.
            max_priority = session.execute(
                select(RoutingRule.priority).order_by(RoutingRule.priority.desc()).limit(1)
            ).scalar_one_or_none()
            priority = (max_priority or 0) + 10

        rule = RoutingRule(
            name=final_name,
            priority=priority,
            conditions=cond_dict,
            assigned_rep_email=assigned_rep_email,
            assigned_rep_name=(assigned_rep_name or "").strip() or assigned_rep_email,
            is_active=True,
        )
        session.add(rule)
    response.headers["X-Toast"] = "Rule added"
    response.headers["HX-Redirect"] = "/admin/routing-rules"
    return {"ok": True}


@router.patch("/{rule_id}")
def routing_update(
    rule_id: str,
    response: Response,
    name: Optional[str] = Form(None),
    priority: Optional[int] = Form(None),
    industry: Optional[list[str]] = Form(None),
    country: Optional[list[str]] = Form(None),
    tier: Optional[list[str]] = Form(None),
    domain: Optional[list[str]] = Form(None),
    lead_country: Optional[list[str]] = Form(None),
    conditions: Optional[str] = Form(None),
    assigned_rep_email: Optional[str] = Form(None),
    assigned_rep_name: Optional[str] = Form(None),
    is_active: Optional[bool] = Form(None),
    _user: str = Depends(require_admin),
):
    with session_scope() as session:
        rule = session.get(RoutingRule, rule_id)
        if rule is None:
            raise HTTPException(404, "rule not found")

        # MERGE semantics: only touch the keys that were actually submitted.
        # FastAPI gives us None for "not in form" vs. a list for "submitted"
        # (possibly empty / containing ""). This lets the chip widget PATCH
        # just `country=...` without wiping industry/tier/domain.
        #
        # The full-replace path is the legacy `conditions=<JSON>` form field.
        structured_present = any(
            v is not None for v in (industry, country, tier, domain, lead_country)
        )
        if structured_present:
            new_cond = dict(rule.conditions or {})
            updates = {
                "company_industry": industry,
                "company_country": country,
                "company_tier": tier,
                "company_domain": domain,
                "lead_country": lead_country,
            }
            for cond_key, submitted in updates.items():
                if submitted is None:
                    continue  # field not sent — leave existing value untouched
                cleaned = _clean_list(submitted)
                if cond_key == "company_industry":
                    cleaned = [v.lower() for v in cleaned]
                if cleaned:
                    new_cond[cond_key] = cleaned
                else:
                    new_cond.pop(cond_key, None)
            _validate_conditions(new_cond)
            rule.conditions = new_cond
        elif conditions is not None:
            rule.conditions = _parse_conditions(conditions)

        if name is not None:
            rule.name = name.strip() or derive_rule_name(rule.conditions)
        if priority is not None:
            rule.priority = priority
        if assigned_rep_email is not None:
            rule.assigned_rep_email = assigned_rep_email
        if assigned_rep_name is not None:
            rule.assigned_rep_name = assigned_rep_name
        if is_active is not None:
            rule.is_active = is_active
    response.headers["X-Toast"] = "Saved"
    return {"ok": True}


@router.post("/reorder")
def routing_reorder(
    response: Response,
    payload: dict = Body(...),
    _user: str = Depends(require_admin),
):
    """Persist new priorities. Payload: {"order": ["<rule_id>", "<rule_id>", ...]}"""
    order = payload.get("order") or []
    with session_scope() as session:
        for index, rule_id in enumerate(order):
            rule = session.get(RoutingRule, rule_id)
            if rule is not None:
                rule.priority = (index + 1) * 10
    response.headers["X-Toast"] = "Reordered"
    return {"ok": True}


@router.get("/preview")
def routing_preview(
    industry: Optional[str] = Query(None),
    country: Optional[str] = Query(None),
    tier: Optional[str] = Query(None),
    domain: Optional[str] = Query(None),
    lead_country: Optional[str] = Query(None),
    _user: str = Depends(require_admin),
) -> dict:
    """Dry-run routing for a hypothetical lead. No DB write.

    Constructs a transient Company with the given attributes and runs it
    through `route_lead()`. Per-company overrides won't fire (transient
    company has no id), so the result reflects routing-rules + fallback only —
    which is exactly what this page manages.

    `country` here = company HQ; `lead_country` = lead's personal country.
    """
    company = Company(
        company_name="(preview)",
        domain=(domain or "").strip() or "preview.example.com",
        industry=(industry or "").strip() or None,
        country=(country or "").strip() or None,
        tier=(tier or "").strip() or None,
    )
    with session_scope() as session:
        decision = route_lead(
            session, company, lead_country=(lead_country or "").strip() or None
        )
        matched_name = None
        if decision.routing_rule_id is not None:
            rule = session.get(RoutingRule, decision.routing_rule_id)
            matched_name = rule.name if rule is not None else None
    return {
        "assigned_rep_email": decision.assigned_rep_email,
        "assigned_rep_name": decision.assigned_rep_name,
        "routing_rule_id": str(decision.routing_rule_id) if decision.routing_rule_id else None,
        "routing_status": decision.routing_status,
        "matched_rule_name": matched_name,
    }
