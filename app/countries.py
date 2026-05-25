"""Canonical country list aligned with Apollo's classification.

Source: ISO 3166-1 short English names via `pycountry`, with overrides for
Apollo's preferred forms where they diverge.

Verified Apollo deviations get appended to APOLLO_OVERRIDES as the country-
drift maintenance job surfaces them. Manual code edit + redeploy to update —
no DB-side vocabulary table, since this is a tiny static list.
"""
import pycountry

# Apollo deviations from ISO 3166-1.
# Format: ISO short name -> Apollo's preferred form.
APOLLO_OVERRIDES: dict[str, str] = {
    "Korea, Republic of": "South Korea",
    "Korea, Democratic People's Republic of": "North Korea",
    "Russian Federation": "Russia",
    "Iran, Islamic Republic of": "Iran",
    "Viet Nam": "Vietnam",
    "Syrian Arab Republic": "Syria",
    "Czechia": "Czech Republic",
    "Türkiye": "Turkey",
    "Tanzania, United Republic of": "Tanzania",
    "Lao People's Democratic Republic": "Laos",
    "Bolivia, Plurinational State of": "Bolivia",
    "Venezuela, Bolivarian Republic of": "Venezuela",
    "Moldova, Republic of": "Moldova",
    "Taiwan, Province of China": "Taiwan",
    "Hong Kong": "Hong Kong",
    "Brunei Darussalam": "Brunei",
    "Congo, The Democratic Republic of the": "Democratic Republic of the Congo",
    "Congo": "Republic of the Congo",
    "Côte d'Ivoire": "Ivory Coast",
    "Cabo Verde": "Cape Verde",
    "Eswatini": "Swaziland",
    "Macedonia, the former Yugoslav Republic of": "North Macedonia",
    "Micronesia, Federated States of": "Micronesia",
    "Palestine, State of": "Palestine",
}


def _build_canonical_list() -> list[str]:
    names = []
    for country in pycountry.countries:
        names.append(APOLLO_OVERRIDES.get(country.name, country.name))
    return sorted(set(names))


CANONICAL_COUNTRIES: list[str] = _build_canonical_list()
_CANONICAL_LOOKUP: set[str] = set(CANONICAL_COUNTRIES)


def is_canonical_country(value: str | None) -> bool:
    """True if value matches an Apollo-canonical country name. Empty/None is treated as valid (means 'no country')."""
    if value is None or value == "":
        return True
    return value in _CANONICAL_LOOKUP


def non_canonical_countries(values) -> list[str]:
    """Return the subset of values that are NOT canonical (preserves input order, deduplicates)."""
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        if v is None or v == "" or v in _CANONICAL_LOOKUP:
            continue
        if v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out
