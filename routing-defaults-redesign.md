# Spec Delta: Make Routing defaults page operator-friendly

The current `/admin/routing-rules` page asks the operator to:
- hand-edit raw JSON for the `conditions` field
- type arbitrary priority numbers
- invent rule names by hand
- mentally separate active vs. inactive rules

This causes silent failures and misleading labels. Live example today:
the rule called **"Oil & gas JP"** has conditions
`["United Kingdom","Norway","Netherlands"]` — the name lies about the
geography. A future operator reading the page believes JP routing exists when
in fact UK/Norway/NL leads are being routed to that rep.

This delta is a UI-only rewrite. The routing engine, schema, and routing
semantics are untouched.

---

## Why

Three concrete pains:

1. **Names drift out of sync with conditions** (the JP/UK example above).
2. **Easy to silently break routing** — a stray comma in the JSON either
   404s the patch or, worse, saves a malformed dict that `_matches()`
   evaluates oddly.
3. **Hard to scan** — flat table, every rule looks the same, inactive rules
   clutter the live list, no sense of which step of the routing cascade this
   page represents.

---

## Changes

### 1. Structured editor — kill the JSON textarea

For each rule, expose four labelled cells instead of one Conditions JSON cell:

| Industry | Country | Tier | Domain |
|---|---|---|---|

Each is a multi-pill input (chips with `×` to remove individually). Compact
display when not focused.

- **Industry**: free text with autocomplete from existing distinct values in
  `companies.industry`.
- **Country**: searchable combobox bound to `CANONICAL_COUNTRIES` (same
  component as everywhere else, per `apollo-country-dropdown.md`).
- **Tier**: free text with autocomplete from existing tiers.
- **Domain**: free text.

The backend keeps the existing `conditions` JSON column. The UI constructs it
server-side from the structured form fields. `app/routing/engine.py:_matches()`
and the rest of the routing semantics are untouched.

### 2. Auto-derived rule names

Default the `name` field to a label generated from the conditions, e.g.:

| Conditions | Auto-name |
|---|---|
| `{"company_industry": ["oil and gas"], "company_country": ["United States","Canada","Mexico"]}` | **Oil and gas · US/Canada/Mexico** |
| `{"company_industry": ["healthcare"]}` | **Healthcare · any country** |
| `{"company_country": ["Italy","Uganda"], "company_industry": ["oil and gas"]}` | **Oil and gas · Italy/Uganda** |
| `{}` | **Catch-all (fallback)** |

The operator can still type a custom name, but:
- New rules ship with the auto-name pre-filled.
- If the operator typed a custom name AND the conditions are subsequently
  changed, show a small `ⓘ` next to the name with a one-click **Reset to
  auto** link. This catches the drift that produced "Oil & gas JP".

Country abbreviations for compact display: use a small map for the obvious
ones (`United States → US`, `United Kingdom → UK`, etc.). Otherwise use the
full canonical name. Limit displayed country list in the auto-name to 3 with
`…+N` if longer.

### 3. Drag-only ordering — hide the priority number

Today operators see a Priority column with editable numbers. Remove the
visible number. Rules render in priority order with drag handles only. On
drop, the server normalizes priorities to `10, 20, 30, …` (this is already
what `/admin/routing-rules/reorder` does behind the scenes — just stop
exposing the raw number to the operator).

### 4. Group by industry

Mirror the Companies page UX: section headers per industry. Within a section,
rules in priority order with drag handles. Sections render in alphabetical
order. Rules with no `company_industry` condition (catch-alls) live in a
"No industry filter" section at the bottom of the active list, just above
the Inactive section.

### 5. Collapse inactive rules

Move deactivated rules into a collapsed `▾ Inactive (N)` section at the very
bottom of the page. They don't clutter the live view but stay one click away
for reactivation.

### 6. Cascade explainer at the top

Replace the existing crimson banner with a 3-step strip showing the cascade:

```
1. Per-company assignment   →   2. The rules below   →   3. Fallback to {DEFAULT_REP_EMAIL}
   (Companies page)                  (this page)             (Reps page)
```

Each step links to where it's managed. Makes it obvious that this page is
step 2 of 3.

### 7. Inline "Test a lead" preview (lightweight)

A small panel at the top right of the page:

```
Test routing
Industry: [oil and gas]   Country: [United States]   Tier: [—]   Domain: [—]
→ Would route to: mats@eonreality.com
  Matched rule: Oil and gas · US/Canada/Mexico (priority 30)
```

Live as the operator types. Hits `GET /admin/routing-rules/preview` (new),
which constructs a transient `Company` (no DB write) and calls
`route_lead()` against it. Returns the resulting rep + rule.

Note: segment rules don't look at lead_country (only `company_country`), so
this preview takes no `lead_country` field — that's intentional.

---

## Backend changes

- **`app/admin/routes/routing_rules.py`**:
  - POST and PATCH accept *either*:
    - Structured form fields: `industry`, `country`, `tier`, `domain` (each
      may appear multiple times as form-encoded lists); OR
    - A raw `conditions` JSON string (backward compatible).
  - The endpoint constructs the `conditions` dict from structured fields,
    drops empty arrays, then writes the same JSON column. If raw JSON is
    sent, validate it parses as a dict before saving.
  - New endpoint: `GET /admin/routing-rules/preview?industry=...&country=...&tier=...&domain=...`
    → returns `{ "assigned_rep_email": "...", "assigned_rep_name": "...", "routing_rule_id": "...|null", "routing_status": "rule_matched|fallback" }`. No DB write.
  - Pass distinct-industry + distinct-tier autocomplete lists to the
    template alongside the existing rules + reps.

- **`app/admin/templates/routing_rules.html`**: substantial rewrite per the
  above.

- **`app/routing/engine.py`**: **no changes**. Same semantics.

- **`app/models/entities.py`**: **no changes**. Same schema.

---

## Migration / data

- Existing rule rows in the DB render fine through the new editor — the UI
  just splits the existing JSON dict into pills per condition key.
- Existing operator-typed `name` values stay as-is until the operator clicks
  **Reset to auto**. The misleading "Oil & gas JP" rule keeps its current
  name until reset.
- No Alembic migration needed.

---

## Acceptance criteria

1. `/admin/routing-rules` renders without showing any raw JSON to the
   operator (textarea is gone, fields render as pills).
2. Adding a new rule via the form produces a `routing_rules` row with a
   valid `conditions` JSON dict (empty `{}` if no conditions selected).
3. Editing any structured field (industry/country/tier/domain) PATCH-updates
   the row's `conditions` JSON correctly — verified by reading the row back
   and inspecting the JSON.
4. Removing all conditions from a rule yields `conditions = {}` — that rule
   becomes a catch-all (matches everything per `_matches()`).
5. The visible "priority number" column is gone. Drag-reorder still works
   and normalizes priorities to `10, 20, 30, …` on drop.
6. Inactive rules render only under a `▾ Inactive (N)` section, collapsed
   by default. The active list shows no inactive rows.
7. The auto-derived rule name on the existing rule with conditions
   `{"company_industry": ["oil and gas"], "company_country": ["United Kingdom","Norway","Netherlands"]}`
   reads "Oil and gas · UK/Norway/Netherlands" when the operator clicks
   "Reset to auto" — never the misleading "JP".
8. The "Test a lead" preview returns the same rep that
   `app/routing/engine.py:route_lead()` picks for a synthetic Company with
   those attributes.
9. All existing tests in `tests/test_routing*.py` pass unchanged — the
   rewrite is UI-only, routing semantics are unchanged.
10. New test: posting form data `industry=oil and gas&country=United States`
    to `POST /admin/routing-rules` creates a rule with
    `conditions = {"company_industry": ["oil and gas"], "company_country": ["United States"]}`.
11. New test: `GET /admin/routing-rules/preview?industry=oil and gas&country=United States`
    returns the rep+rule that the routing engine would actually pick.

---

## Out of scope

- Changing routing semantics (cascade order, AND-within-rule, first-match,
  priority ASC). Still per CLAUDE.md:
  - Per-company override → segment rule → fallback
  - AND across condition keys within a rule
  - First match wins, ordered by `priority ASC, created_at ASC`
- Adding new condition keys (still only `company_industry`,
  `company_country`, `company_tier`, `company_domain`).
- Editing per-company assignments here (they belong on the Companies page).
- Per-rep workload visibility (already on Reps page).
