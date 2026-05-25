"""IANA timezone list for UI dropdowns + server-side validation.

Sourced from `zoneinfo.available_timezones()` (Python 3.9+). UTC is pinned at
the top of the list; everything else is alphabetical. Keep `is_valid_timezone`
strict — `reps.timezone` drives the digest scheduler (see
`app/digest/scheduler.py`), and a misspelled tz silently falls back to UTC,
which is exactly the silent-failure mode the country dropdown was added to
prevent.
"""
from functools import lru_cache
from zoneinfo import ZoneInfo, available_timezones


@lru_cache(maxsize=1)
def canonical_timezones() -> list[str]:
    zones = sorted(available_timezones())
    if "UTC" in zones:
        zones.remove("UTC")
    return ["UTC"] + zones


CANONICAL_TIMEZONES: list[str] = canonical_timezones()
_TZ_LOOKUP: set[str] = set(CANONICAL_TIMEZONES)


@lru_cache(maxsize=1)
def timezones_by_region() -> list[tuple[str, list[str]]]:
    """Group IANA timezones by their first path segment (Africa, America, ...).

    Returned as a list of (region_label, [tz_names]) tuples in display order,
    with UTC as its own pinned group at the top.
    """
    groups: dict[str, list[str]] = {}
    for tz in CANONICAL_TIMEZONES:
        if tz == "UTC":
            continue
        region = tz.split("/", 1)[0] if "/" in tz else "Other"
        groups.setdefault(region, []).append(tz)
    ordered_regions = sorted(groups.keys())
    out: list[tuple[str, list[str]]] = [("UTC", ["UTC"])]
    out.extend((region, sorted(groups[region])) for region in ordered_regions)
    return out


TIMEZONES_BY_REGION: list[tuple[str, list[str]]] = timezones_by_region()


def is_valid_timezone(value: str | None) -> bool:
    """True if value is a recognized IANA timezone. Empty/None treats as 'use default'."""
    if value is None or value == "":
        return True
    if value in _TZ_LOOKUP:
        return True
    # Belt-and-suspenders: try to construct ZoneInfo in case available_timezones()
    # missed something on this platform.
    try:
        ZoneInfo(value)
        return True
    except Exception:
        return False
