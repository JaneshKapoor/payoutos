# Playto Payout Engine

A minimal but production-shaped clone of the payout engine that sits behind Playto Pay — the system that holds merchant balances in INR paise, accepts payout requests, and walks them through a state machine until they either settle or refund.

> Built for the Playto Founding Engineer Challenge 2026 by [Janesh Kapoor](https://github.com/JaneshKapoor).

## What this repo proves

| Property | Where it lives |
|---|---|
| Money is integer paise everywhere — never float, never `Decimal` | `apps/ledger/models.py`, `apps/payouts/models.py` (BigIntegerField) |
| Balance is derived from the ledger, not stored | `apps/ledger/services.get_balance` (single SQL `SUM`) |
| Concurrent payouts cannot overdraw | `apps/payouts/services.request_payout` — `SELECT FOR UPDATE` on the merchant row |
| Idempotency via `Idempotency-Key` header, scoped per merchant, 24h TTL | `apps/payouts/services._lookup_idempotency` + unique constraint on `(merchant, key)` |
| State machine with hard rejection of illegal transitions | `apps/payouts/state_machine.py` |
| Atomic refund on failure | `apps/payouts/services.transition_to` (state flip + reversal entry in one tx) |
| Stuck-payout detection + exponential retry, capped | `apps/payouts/tasks.scan_stuck_payouts` |

The detailed walkthrough of each is in [`EXPLAINER.md`](EXPLAINER.md).

---

## Stack

- **Backend**: Django 5 + Django REST Framework
- **Database**: PostgreSQL 16 (locking semantics matter — see `EXPLAINER.md`)
- **Worker / scheduler**: Celery + Celery Beat, Redis broker
- **Frontend**: React 18 + Vite + Tailwind CSS

---

## Run it locally

### Option A: docker compose (everything wired up)

```bash
docker compose up --build
```

This spins up Postgres, Redis, the Django API on `:8000`, a Celery worker, Celery beat, and the Vite dev server on `:5173`. Migrations and seeding run automatically on first boot.

Open <http://localhost:5173> for the dashboard. The API is at <http://localhost:8000/api/v1/>.

### Option B: run each piece yourself

You'll need Python 3.12, Node 20, Postgres, and Redis on the host.

```bash
# 1. Postgres + Redis
# (start them however you normally do — brew services, docker, etc.)
createdb payoutos

# 2. Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # tweak DATABASE_URL / REDIS_URL if needed
python manage.py migrate
python manage.py seed
python manage.py runserver 8000

# 3. Worker + beat (in two more terminals)
celery -A payoutengine worker --loglevel=info --concurrency=2
celery -A payoutengine beat   --loglevel=info

# 4. Frontend
cd frontend
npm install
npm run dev
```

### Reset everything

```bash
python manage.py seed --reset
```

---

## API

All endpoints are under `/api/v1/`. The "auth" for this demo is the `X-Merchant-Id` header — not how you'd ship it to production, but it lets the dashboard switch merchants without a login flow.

### `POST /api/v1/payouts`

Create a payout. Funds are held immediately.

**Headers**
```
X-Merchant-Id: <merchant uuid>
Idempotency-Key: <uuid the client mints>
Content-Type: application/json
```

**Body**
```json
{
  "amount_paise": 30000,
  "bank_account_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
}
```

**Responses**
- `201 Created` — payout queued.
- `200 OK` + `Idempotent-Replayed: true` — same key as a prior request; returns the original payout.
- `409 Conflict` — same key, prior request still running.
- `422 Unprocessable Entity` — `insufficient_funds` / `idempotency_key_conflict` (key reused with different body) / `invalid_bank_account`.

```json
{
  "id": "...",
  "amount_paise": 30000,
  "state": "pending",
  "attempts": 0,
  "created_at": "2026-04-29T12:34:56Z"
}
```

### `GET /api/v1/payouts/list`
Lists payouts for the merchant in the `X-Merchant-Id` header.

### `GET /api/v1/payouts/{id}`
Single-payout detail.

### `GET /api/v1/merchants`
Lists merchants (for the demo merchant switcher).

### `GET /api/v1/merchants/{id}/balance`
```json
{
  "available_paise": 1234500,
  "held_paise": 30000,
  "settled_paise": 1204500,
  "lifetime_credits_paise": 1500000,
  "lifetime_debits_paise": 265500
}
```

### `GET /api/v1/merchants/{id}/ledger`
Append-only ledger entries, newest first.

### `GET /api/v1/merchants/{id}/bank-accounts`
Bank accounts on file.

---

## Tests

```bash
cd backend
DATABASE_URL=postgres://postgres:postgres@localhost:5432/payoutos \
  python manage.py test apps.payouts.tests
```

The two flagship tests:

- **`test_concurrency.py`** — spawns two threads, both try to take 60 rupees out of a 100-rupee balance. Asserts exactly one wins and the other gets `InsufficientFunds`. Skips on SQLite (locking semantics differ); needs Postgres.
- **`test_idempotency.py`** — same key + same body returns the same payout (no second debit, no second payout row). Same key + different body raises `IdempotencyKeyConflict`. Same key across two merchants → both succeed (keys are merchant-scoped).
- **`test_state_machine.py`** — fast unit check that every illegal transition (especially `failed → completed` and any backwards move) is rejected.

---

## Deployment

The repo is set up to deploy to **Render** or **Railway** with minimal config. The pieces:

- Web service: build with `pip install -r backend/requirements.txt`, run `python manage.py migrate && python manage.py seed && gunicorn payoutengine.wsgi --chdir backend`.
- Worker: same image, run `celery -A payoutengine worker -l info`.
- Beat: same image, run `celery -A payoutengine beat -l info`.
- Postgres: managed instance.
- Redis: managed instance.
- Frontend: `npm run build` and serve as a static site (`frontend/dist`), with `VITE_API_URL` pointing at the API service URL.

A `render.yaml` is included for the click-to-deploy path on Render. The actual deployment uses Railway (backend) + Vercel (frontend) because Render's free tier no longer supports background workers.

Live URLs:
- **Dashboard:** https://payoutos.vercel.app
- **API:** https://api-production-b04f.up.railway.app/api/v1/

The dashboard has 3 seeded merchants — pick one from the top-right dropdown to view balance, ledger, and request payouts. Worker + beat are running on Railway, so payouts move through the state machine in real time.

---

## Repo layout

```
backend/
  payoutengine/         # Django project (settings, urls, celery app)
  apps/
    merchants/          # Merchant + BankAccount models, dashboard read APIs
    ledger/             # LedgerEntry (append-only), get_balance()
    payouts/            # Payout, IdempotencyKey, state machine, services, tasks, tests
  scripts/              # (seed lives in apps/merchants/management/commands)
  Dockerfile

frontend/
  src/
    components/         # BalanceCard, PayoutForm, PayoutTable, LedgerTable, MerchantSelector
    api.js              # fetch wrapper, mints Idempotency-Key UUIDs
    format.js           # paise <-> rupees at the UI edge only
  Dockerfile

docker-compose.yml      # postgres + redis + api + worker + beat + frontend
EXPLAINER.md            # the five questions, answered with code
```
