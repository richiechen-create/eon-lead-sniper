# CLAUDE.md — lead_engine / EON Bullseye

Memory file for future Claude sessions. Read this first before doing anything in
this repo so you don't have to re-derive the design from chat history.

## What this is

**Project:** EON Bullseye — automated lead discovery + enrichment + delivery
for EON Reality.
**Owner:** Richie (Chief of Staff at EON Reality, c.richie@gmail.com).
**Audience for the UI:** one operator (Richie). Reps never log in — they receive
daily digest emails and action leads in their own Gmail / CRMs.
**Backend package:** still named `lead_engine/` on disk. Only user-facing
strings carry the "EON Bullseye" brand. Don't rename modules.

**Brand colors** (Tailwind extend keys in templates): navy `#1A1F4D`, crimson
`#8C1D3E`, navyTint `#EEF0FA`, crimsonTint `#FBEEF2`.

## Stack at a glance

- Python 3.11+, FastAPI, SQLAlchemy 2.x, Alembic, Postgres (Neon/Supabase in
  prod, sqlite fallback for local).
- HTMX + Jinja2 + Tailwind CDN admin UI (no JS build pipeline).
- Apollo.io for People Search + People Match (Professional plan, 10,190
  credits/month; we budget 9,500 with a 500 buffer).
- Resend for email; Render for hosting (one web + three cron services).
- Auth: single admin username/password → signed session cookie (Starlette
  `SessionMiddleware`), 7-day expiry. Cron endpoints gated by `X-Internal-Key`
  header — bypasses session auth.

## Run it

```bash
uv venv --python 3.11 && source .venv/bin/activate
uv pip install -e ".[dev]"
cp .env.example .env  # then fill in secrets
alembic upgrade head
python -m scripts.sync_config   # bootstrap from config/*.yaml
pytest
uvicorn app.api.main:app --reload
```

Visit `http://localhost:8000/admin` → redirects to `/admin/login`.

## File map (the ones that matter)

| Path | Role |
|---|---|
| `app/api/main.py` | FastAPI app, SessionMiddleware, cron POST endpoints (`/run/enrichment`, `/run/digest`, `/maintenance/purge-api-log`). `/health` is public. |
| `app/admin/auth.py` | `verify_credentials`, `require_admin`, session helpers. `hmac.compare_digest` for the password. |
| `app/admin/routes/` | One module per page (dashboard, leads, companies, assignments, profiles, reps, routing_rules, do_not_contact, runs, triggers, auth). |
| `app/admin/templates/` | Jinja templates. `_assignment_editor.html` and `_profiles_pills.html` are HTMX partials. |
| `app/apollo/client.py` | `ApolloClient` — X-Api-Key header, `search_people()` generator, `match_person()` always sends `reveal_phone_number=False`. Logs every call to `api_call_log`. |
| `app/apollo/budget.py` | `credits_used_this_month()`, `budget_status()` → `(used, limit, exhausted)`. Sums `enrichment_runs.credits_consumed` for the current calendar month. |
| `app/routing/engine.py` | `route_lead(session, company, lead_country)` → `RoutingDecision`. Cascade is implemented here. |
| `app/leads/insert.py` | `lead_exists()`, `insert_lead()`. Idempotency + DNC check happen inside `insert_lead`. |
| `app/leads/suppression.py` | `is_suppressed()` — matches DNC rows by email OR domain OR apollo_person_id. |
| `app/tasks/enrichment.py` | The big orchestrator. `run_enrichment(session)` → loops active companies, calls Apollo, routes + inserts. Fail-soft per company. Budget guard before each company AND before each match call. |
| `app/digest/builder.py` | `build_digest()` — text + HTML + CSV payload. Groups by company alphabetically. |
| `app/digest/scheduler.py` | `run_digest_tick()` — hourly. Emails a rep only when their local hour is 08 on a weekday (Mon=0..Sun=6, skip 5/6). On success flips `delivery_status` to `delivered`. |
| `app/digest/admin_summary.py` | `send_admin_summary()` — invoked once a day with `--admin-summary` flag. |
| `app/email/sender.py` | Resend wrapper. `send_email()`, `send_admin_alert()`, `Attachment`. |
| `app/sync/yaml_config.py` | Bootstrap from `config/*.yaml`. Companies are NOT soft-deleted by YAML (only via UI); profiles/reps/rules ARE soft-deleted when absent from YAML. |
| `app/models/entities.py` | All SQLAlchemy models in one file. |
| `app/countries.py` | `CANONICAL_COUNTRIES` built from `pycountry` + `APOLLO_OVERRIDES`. `is_canonical_country()`, `non_canonical_countries()`. |
| `app/maintenance.py` | `purge_old_api_call_log(session, older_than_days)`. |
| `app/config.py` | `Settings` (pydantic-settings) + `get_settings()` (lru_cached). |
| `scripts/run_enrichment.py` | Cron entrypoint. `python -m scripts.run_enrichment`. |
| `scripts/run_digest.py` | Cron entrypoint. Accepts `--admin-summary`. |
| `scripts/sync_config.py` | One-shot YAML → DB bootstrap. |
| `scripts/scan_country_drift.py` | Reports `leads.person_country` values not in `CANONICAL_COUNTRIES`. |
| `scripts/purge_api_log.py` | Daily cleanup. |
| `alembic/versions/0001_baseline.py` | Initial schema. |
| `alembic/versions/0002_company_rep_assignments.py` | Adds `company_rep_assignments` for the per-company per-country override. |
| `render.yaml` | One web service, three cron services. Cron uses `lead-engine-shared` env group. |

## Data model essentials

All models live in `app/models/entities.py`.

- `companies` — `domain` is unique. `is_active` toggles enrichment inclusion.
  `max_contacts_per_run` caps NEW lead inserts per run (default 10).
- `targeting_profiles` — JSON arrays of `titles`, `seniorities`, `departments`,
  `locations`, `keywords`. `is_active` true/false.
- `company_targeting` — m:n join (company × targeting_profile).
- `company_rep_assignments` — per-company per-country override.
  `UNIQUE(company_id, lead_country)`. `lead_country = '*'` is the wildcard.
  Takes precedence over routing_rules.
- `reps` — `email` unique, `timezone` for digest scheduling, optional
  `daily_lead_cap`, `is_active` to opt out (also doubles as OOO workaround).
- `routing_rules` — `priority` ASC (lower = earlier), `conditions` is JSON with
  AND semantics across keys: `company_industry`, `company_country`,
  `company_tier`, `company_domain`. Title is for discovery only, never
  routing.
- `leads` — `apollo_person_id` UNIQUE (the dedupe key).
  `delivery_status` in `{pending, delivered, skipped}` (skipped when no email
  was found). `routing_status` in
  `{company_override, rule_matched, fallback}` per the routing engine. **Note**:
  the docstring in `app/leads/insert.py:RoutingAssignment` says `'matched' |
  'fallback'` — that's stale; the column accepts free text and the engine
  emits the three values above. Manual reassignment from the UI sets
  `routing_status='company_override'`.
- `do_not_contact` — match by email, domain, or apollo_person_id (any one).
- `enrichment_runs` — counters + `credits_consumed` (drives the monthly budget
  guard) + `errors` JSON list.
- `digest_runs` — daily summary of digest activity.
- `api_call_log` — every Apollo POST is logged here. Purged daily after 30
  days.

## Routing cascade — the algorithm

Implemented in `app/routing/engine.py:route_lead()`. First match wins.

1. **Per-company per-country override** (`company_rep_assignments`): exact
   `lead_country` match first, then `lead_country = '*'` wildcard. Yields
   `routing_status='company_override'`, `routing_rule_id=None`.
2. **Segment rule** (`routing_rules` where `is_active=True`, ordered by
   `priority ASC, created_at ASC`). A rule matches if its `conditions` are
   empty OR all present keys match. Yields `routing_status='rule_matched'`
   plus `routing_rule_id`.
3. **Fallback** to `settings.DEFAULT_REP_EMAIL` (default
   `dan@eonreality.com`). Yields `routing_status='fallback'`.

**Subtlety to remember:** the segment-rule stage uses the **company's**
attributes (industry/country/tier/domain). Only the company-override stage
looks at `lead_country` (person's country from Apollo). So segment rules can't
route on the lead's country directly — that's intentional, but operators may
expect otherwise.

## Apollo integration contracts

- Always use `X-Api-Key` header. Never put the key in URL params.
- `match_person()` always sends `reveal_phone_number=False`. There's a runtime
  `assert` plus a test (`test_apollo_no_phone.py`) to keep it that way.
- Credits: each `/people/match` call that returns a non-empty email counts as
  1 credit. `/mixed_people/search` is treated as 0 credits (caveat below).
- Retry logic: 429 with exponential backoff up to 5 times; 5xx retried once.
  Network errors raise `ApolloError`.
- Budget guard runs before every company AND before every match call inside a
  company. On exhaustion: log error, send admin alert, halt enrichment cleanly.

**Open item from the spec:** confirm with Apollo support **in writing**
whether `/mixed_people/search` consumes credits on the Professional plan with
master keys. If yes, update `app/apollo/budget.py` to include search calls.

## Daily flow

| Job | Schedule (UTC) | What it does |
|---|---|---|
| Enrichment | 04:00 | `scripts.run_enrichment` → loops active companies, search+match, insert leads. |
| Digest tick | hourly | `scripts.run_digest` → for each active rep, if their local hour is 08 and it's a weekday, build + send digest. |
| Digest + admin summary | 23:00 | Same as hourly but with `--admin-summary` to also email Richie the daily roll-up. |
| Purge api_call_log | 04:30 | `scripts.purge_api_log` → drops rows older than 30 days. |

Locally / via API the same can be triggered via `POST /run/enrichment`,
`POST /run/digest?send_admin=true`, `POST /maintenance/purge-api-log` — all
require `X-Internal-Key`. The UI has manual trigger buttons on
`/admin/runs` (gated by session auth, not the internal key).

## Idempotency + suppression

- Idempotency: `leads.apollo_person_id UNIQUE`. `lead_exists()` is checked
  **before** the match call (saves credits) and again inside `insert_lead`.
- Suppression: `is_suppressed()` is consulted before any match call (by
  domain + apollo_person_id, since email isn't known yet) and again inside
  `insert_lead` (by all three).
- A new email arriving at the same `apollo_person_id` (job change) is
  **silently deduped out** in v1. Spec calls this out as an accepted gap.

## Admin UI map

All pages live under `/admin` and require login.

| Page | What it does |
|---|---|
| `/admin` | Dashboard: credit gauge, pending-per-rep, 7-day trends, manual run buttons. |
| `/admin/leads` | Filter/search; inline reassign / suppress / skip. |
| `/admin/leads/fallback` | Triage queue for `routing_status='fallback' AND delivery_status='pending'`. One-click rep reassignment. |
| `/admin/companies` | CRUD; inline edit `tier`, `max_contacts_per_run`, `is_active`. Bulk import via pasted CSV. Profiles cell is clickable popover. Per-company rep assignments live here as expandable editor. |
| `/admin/targeting-profiles` | CRUD targeting profiles. |
| `/admin/reps` | CRUD reps. Inline edit name/team/timezone/daily_cap/active. |
| `/admin/routing-rules` | CRUD; drag to reorder priority. Conditions as JSON. |
| `/admin/do-not-contact` | Suppression list. |
| `/admin/runs` | Enrichment + digest history with errors. |

UI rendering quirks (locked in by spec deltas):
- Reps cell: 0 → "uses segment rules" muted; 1 → existing `Country→Name` pill;
  2+ → single pill "`{N} reps assigned ▾`" (do not list individual reps).
- Profiles cell: clickable everywhere; popover with checkboxes that
  toggle `company_targeting` via HTMX. Empty state opens popover too.
- Country inputs everywhere are searchable combobox bound to
  `CANONICAL_COUNTRIES`. `*` wildcard pinned at top of the dropdown ONLY
  inside the rep-assignment editor.

## Tests — what's enforced

Run `pytest`. Key assertions:

- `test_apollo_no_phone.py` — no `reveal_phone_number=True` on any call;
  `X-Api-Key` header present.
- `test_routing.py` + `test_routing_cascade.py` — priority order, AND
  semantics, inactive rules skipped, fallback, company override + wildcard.
- `test_idempotency.py` — no dupes on rerun; DNC blocks insert.
- `test_digest.py` — per-timezone scheduling, weekend skip, daily cap honored.
- `test_yaml_sync.py` + `test_companies_yaml.py` — soft-delete behavior;
  companies absent from YAML are NOT soft-deleted (different from
  profiles/reps/rules).
- `test_admin_auth.py` — `/admin/*` requires session; cron POST paths still
  work with `X-Internal-Key` even when logged out.
- `test_admin_crud.py` — full CRUD coverage + manual triggers.

## Env vars (full list)

From `app/config.py`. Defaults shown when blank is OK locally.

| Var | Default | Notes |
|---|---|---|
| `APOLLO_API_KEY` | "" | Required in prod. |
| `DATABASE_URL` | `sqlite:///./lead_engine.sqlite` | Use `postgresql+psycopg2://…` in prod. |
| `RESEND_API_KEY` | "" | Required in prod. |
| `FROM_EMAIL` | `leadengine@leadengine.eonreality.com` | Verified subdomain — do NOT send from main domain. |
| `FROM_NAME` | `EON Bullseye` | |
| `ADMIN_EMAIL` | `admin@eonreality.com` | Where admin alerts and daily summary go. |
| `DEFAULT_REP_EMAIL` | `dan@eonreality.com` | Fallback recipient. |
| `DEFAULT_REP_NAME` | `Dan (Fallback)` | |
| `APP_ENV` | `dev` | `dev` or `prod`. `prod` flips session cookie to https_only. |
| `CREDIT_BUDGET_MONTHLY` | `9500` | Halt threshold. 500 buffer under the 10,190 plan limit. |
| `APOLLO_BASE_URL` | `https://api.apollo.io/api/v1` | |
| `APOLLO_MAX_PAGES` | 10 | Per company per run. |
| `APOLLO_PER_PAGE` | 25 | |
| `ADMIN_USERNAME` | `admin` | Replace in prod. |
| `ADMIN_PASSWORD` | `change-me` | Replace in prod. |
| `SESSION_SECRET` | dev placeholder | 32+ random bytes in prod. `python -c "import secrets; print(secrets.token_hex(32))"`. |
| `INTERNAL_API_KEY` | dev placeholder | Required header on cron endpoints. |

## Gotchas + things to remember

- The `routing_status` comment in `app/leads/insert.py` is stale (`'matched' |
  'fallback'`). The engine actually emits
  `'company_override' | 'rule_matched' | 'fallback'`. Don't follow the stale
  comment.
- Manual reassignment from the UI sets `routing_status='company_override'`
  (not a new status) — this is intentional so audit queries stay clean.
- `max_contacts_per_run` caps NEW lead inserts, NOT API calls. Dedupes don't
  count against it.
- `_matches()` uses lowercase comparison for `company_industry` only; other
  keys are case-sensitive — important when an operator types "USA" vs
  "United States". The country dropdown is the mitigation.
- Bootstrap YAML does NOT soft-delete companies when they're absent (UI is
  source of truth). It DOES soft-delete missing profiles/reps/rules.
- Digest scheduler emails ONLY when local hour == 8 on a weekday. If the cron
  misses the 8-o-clock tick for a timezone, that rep gets nothing that day.
  Run on Render's hourly cron with confidence; locally, just call
  `run_digest_tick(session, now_utc=…)` with a contrived time.
- `pycountry` + `APOLLO_OVERRIDES` is the only canonical country source.
  Update the override map in `app/countries.py` when the daily drift scan
  surfaces a new Apollo form. Redeploy.

## Out of scope for v1 (don't accidentally build these)

- Multi-user UI / rep-facing dashboard
- OAuth / SSO
- Outreach automation, sequences, LinkedIn
- Reply / open / click / bounce tracking
- Hunter.io secondary verification
- Phone enrichment (explicitly disabled)
- CRM sync (HubSpot, Salesforce)
- Job-changer re-routing
- OOO / vacation handling (workaround: `reps.is_active = false`)

## When you (future Claude) make changes here

- Read this file first.
- If a change touches the routing cascade, update `app/routing/engine.py`
  AND `test_routing*.py` AND the table in this file.
- If you add a new model field, generate a new Alembic migration
  (`alembic revision -m "…"` then edit the auto-generated file — env.py is
  already wired to the models).
- Brand: navy `#1A1F4D` / crimson `#8C1D3E`. Backend folder stays
  `lead_engine/`; user-facing copy stays "EON Bullseye".
- Don't introduce phone enrichment under any framing.
- If you touch the Apollo client, re-run `test_apollo_no_phone.py` —
  it's the safety net for the no-phone invariant.

## History — what the spec deltas asked for (latest takes priority)

1. Companies page: panels grouped by segment (industry), add-company under each.
2. Per-company per-country rep assignment (the cascade above).
3. Brand rename to EON Bullseye + navy/crimson palette.
4. Add-country form in rep editor collapsed behind "+ Add country assignment".
5. Profiles cell becomes a clickable popover with checkbox toggles via HTMX.
6. Multi-rep display collapses to "`{N} reps assigned ▾`"; single-rep stays
   as `Country→Name`.
7. Country dropdown sourced from `CANONICAL_COUNTRIES` everywhere; bulk-CSV
   import rejects non-canonical values row by row.

A team-briefing deck (`eon-lead-sniper-team-briefing.pptx`) also exists in
the chat artifacts (outputs folder) — friendly Pokémon-style ELI10 slide
included.
