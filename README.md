# EON Bullseye

Backend service + single-operator admin UI for the EON Reality lead generation
ops stack. (Backend package is still named `lead_engine/` — only user-visible
strings carry the brand name.) Discovers prospects across a maintained company list, enriches
verified work emails via Apollo (email only, never phone), routes each lead to
one rep by sector + country, and delivers daily digests + a daily admin summary.

Reps action delivered leads in their own Gmail + CRMs. The UI is for the
operator (Chief of Staff), not for reps.

---

## Quick start (local)

Requirements: **Python 3.11+** and Postgres (Neon, Supabase, or local).

```bash
uv venv --python 3.11
source .venv/bin/activate
uv pip install -e ".[dev]"
```

```bash
cp .env.example .env
```

Edit `.env` — at minimum, set:

- `DATABASE_URL` (e.g. Neon pooled connection string with `postgresql+psycopg2://` prefix)
- `ADMIN_USERNAME` and `ADMIN_PASSWORD` (your UI login)
- `SESSION_SECRET` (32+ random bytes — `python -c "import secrets; print(secrets.token_hex(32))"`)
- `INTERNAL_API_KEY` (random string, used by cron jobs only)

For local dev without Apollo/Resend keys you can leave the rest blank.

```bash
alembic upgrade head
python -m scripts.sync_config
pytest
uvicorn app.api.main:app --reload
```

Open **http://localhost:8000/admin** — you'll be redirected to the login form,
then into the dashboard.

---

## The admin UI

| Page | Purpose |
|---|---|
| `/admin` | Dashboard: credit gauge, pending-per-rep, 7-day trends, manual trigger buttons |
| `/admin/leads` | Browser with filters (rep, status, routing status, company, date), free-text search, inline rep reassign, suppress, skip |
| `/admin/leads/fallback` | Triage queue: all `routing_status=fallback, delivery_status=pending` leads, one-click rep reassignment |
| `/admin/companies` | CRUD; inline edit `tier`, `max_contacts_per_run`, active; bulk import via pasted CSV |
| `/admin/targeting-profiles` | CRUD profiles (titles / seniorities / departments / locations / keywords) |
| `/admin/reps` | CRUD; inline edit name, team, timezone, daily cap, active |
| `/admin/routing-rules` | CRUD; drag-to-reorder priority; conditions as JSON |
| `/admin/do-not-contact` | Suppression list; add by email / domain / apollo_person_id |
| `/admin/runs` | Enrichment + digest run history with errors |

UI stack: HTMX + Jinja2 + Tailwind CDN (no build pipeline). Auth: HTTP form
post → signed session cookie via Starlette `SessionMiddleware`, 7-day expiry.

---

## Cron-only API endpoints

These are NOT browsable from the UI. They require the `X-Internal-Key`
header so the cron jobs work regardless of UI auth state (acceptance #18).

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness probe (no auth) |
| POST | `/run/enrichment` | Daily enrichment run |
| POST | `/run/digest?send_admin=true` | Hourly digest tick |
| POST | `/maintenance/purge-api-log` | Purge api_call_log entries older than N days |

Render cron jobs invoke the equivalent scripts directly (`python -m
scripts.run_enrichment`) which bypass HTTP — no header needed there.

---

## What runs when (in production)

| Job | Schedule | Command |
|---|---|---|
| Enrichment | daily 04:00 UTC | `python -m scripts.run_enrichment` |
| Digest hourly tick | hourly every day | `python -m scripts.run_digest` |
| Digest + admin summary | 23:00 UTC daily | `python -m scripts.run_digest --admin-summary` |
| api_call_log purge | daily 04:30 UTC | `python -m scripts.purge_api_log` |

The hourly tick checks each active rep's local time. Only reps whose local
hour is currently **08** on a weekday receive their digest.

---

## Configuration

### Env vars
See `.env.example`. Required at runtime:
- `APOLLO_API_KEY`, `DATABASE_URL`, `RESEND_API_KEY`, `FROM_EMAIL`, `ADMIN_EMAIL`,
  `DEFAULT_REP_EMAIL`
- `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `SESSION_SECRET`, `INTERNAL_API_KEY`

`CREDIT_BUDGET_MONTHLY` defaults to **9500** (leaves a 500-credit buffer below
the Apollo Professional ceiling of 10,190).

### Bootstrap YAML (one-time)
On first deploy, seed the DB from `config/`:

```bash
python -m scripts.sync_config
```

This upserts everything in:
- `config/targeting_profiles.yaml`
- `config/reps.yaml`
- `config/routing_rules.yaml`
- `config/companies.yaml`

**After bootstrap, the UI is the source of truth.** YAML can stay in the repo
as a versioned backup, but expect it to drift from live DB once the operator
starts editing.

---

## Architecture

```
lead_engine/
├── alembic/                         migrations
├── app/
│   ├── api/main.py                  FastAPI app + middleware + cron endpoints
│   ├── admin/                       UI: auth, templates, routes/, templating
│   ├── apollo/                      Apollo client + budget guard
│   ├── routing/engine.py            sector+country routing (deterministic)
│   ├── leads/                       suppression + idempotent insert
│   ├── digest/                      digest builder, hourly scheduler, admin summary
│   ├── sync/yaml_config.py          YAML → DB bootstrap
│   ├── tasks/enrichment.py          end-to-end enrichment orchestration
│   ├── email/sender.py              Resend integration
│   ├── models/                      SQLAlchemy 2.x models
│   ├── maintenance.py               api_call_log purge
│   └── config.py                    pydantic-settings env loader
├── config/                          bootstrap YAML
├── scripts/                         cron entrypoints
└── tests/                           pytest suite
```

### Core principles (enforced in code, asserted in tests)

1. **Idempotent.** Dedupe on `apollo_person_id`. Running enrichment twice
   produces zero duplicates. (`tests/test_idempotency.py`)
2. **Email-only enrichment.** `reveal_phone_number` is hardcoded `False` on
   every `/people/match` call. (`tests/test_apollo_no_phone.py`)
3. **Fail soft.** A single company's Apollo failure logs to
   `enrichment_runs.errors` and the next company proceeds.
4. **Audit everything.** Every Apollo call hits `api_call_log`. Every credit,
   every routing decision, every digest send is persisted.
5. **Suppression-first.** `do_not_contact` is consulted by email, domain, and
   `apollo_person_id` before any insert — and before any match call when the
   id is known.

### Routing — cascaded
Each lead is routed in this order. First match wins.

1. **Per-company per-country override** (`company_rep_assignments`). Exact-country
   match first, then `'*'` wildcard. Yields `routing_status='company_override'`.
2. **Segment rule** (`routing_rules`, priority ASC). Yields
   `routing_status='rule_matched'` plus `routing_rule_id`.
3. **Fallback** to `DEFAULT_REP_EMAIL`. Yields `routing_status='fallback'`.

Conditions in routing rules only see sector + country + tier + domain. Title is
for discovery only.

Manual reassignment from the leads UI sets `routing_status='company_override'`.

#### Audit: how were last week's leads routed?

```sql
SELECT
    routing_status,
    COUNT(*) AS n,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct
FROM leads
WHERE date_discovered >= NOW() - INTERVAL '7 days'
GROUP BY routing_status
ORDER BY n DESC;
```

To see which rules fired most often:

```sql
SELECT rr.name, COUNT(*) AS leads_routed
FROM leads l
JOIN routing_rules rr ON rr.id = l.routing_rule_id
WHERE l.date_discovered >= NOW() - INTERVAL '30 days'
GROUP BY rr.name
ORDER BY leads_routed DESC;
```

### Credit budget guard
Before every match call, the orchestrator sums
`enrichment_runs.credits_consumed` across the current calendar month. If the
running total exceeds `CREDIT_BUDGET_MONTHLY`, the run halts and `ADMIN_EMAIL`
is paged.

---

## Tests

```bash
pytest
```

Coverage:
- `test_apollo_no_phone.py` — AC #4 (no phone reveals); `X-Api-Key` header
- `test_routing.py` — priority order, AND semantics, fallback, inactive rules
- `test_idempotency.py` — AC #3 (no duplicates) + do_not_contact behavior
- `test_digest.py` — per-timezone scheduling, daily cap, weekend skip
- `test_yaml_sync.py`, `test_companies_yaml.py` — bootstrap soft-delete round-trip
- `test_admin_auth.py` — AC #14 (auth gates `/admin/*`), AC #18 (cron path unaffected)
- `test_admin_crud.py` — AC #15 (CRUD across all entities), AC #16 (one-click fallback reassign), AC #17 (manual triggers)

---

## Deploy

`render.yaml` defines one web service + three cron jobs. Connect the repo to
Render via Blueprint, set the secret env vars in the dashboard, and Render
handles the rest.

Sending domain: configure SPF / DKIM / DMARC on a dedicated subdomain
(`leadengine.eonreality.com` recommended). Do **not** send from the main
corporate domain.

---

## Operator setup checklist (§16 of the spec)

- [ ] Apollo master API key
- [ ] Resend API key
- [ ] Postgres `DATABASE_URL` (Neon or Supabase)
- [ ] Verified sending subdomain (SPF + DKIM + DMARC)
- [ ] Render account
- [ ] `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `SESSION_SECRET`, `INTERNAL_API_KEY`
- [ ] `config/*.yaml` seeded with real profiles / reps / rules / initial companies
- [ ] **Confirmation from Apollo support, in writing**: does
      `/mixed_people/search` consume credits on the Professional plan with
      master keys? If yes, update `app/apollo/budget.py` to include search
      calls in the monthly total.

---

## Out of scope for v1

- Multi-user UI / rep-facing dashboard
- OAuth / SSO (single admin password is the v1 design)
- Outreach automation, sequence sending, LinkedIn touches
- Reply / open / click / bounce tracking
- Hunter.io secondary verification
- Phone enrichment (explicitly disabled)
- CRM sync (HubSpot / Salesforce)
- Job-changer re-routing (a known `apollo_person_id` at a new company is
  deduped out — accepted gap for v1)
- OOO / vacation handling (workaround: toggle `reps.is_active=false`)
