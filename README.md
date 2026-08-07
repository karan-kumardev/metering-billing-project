# Usage Metering & Billing Engine

A backend service that meters AI/API usage per tenant, enforces plan quotas, calculates real token-based
costs, and integrates Stripe (test mode) for subscription upgrades — with idempotent metering and
signature-verified, deduplicated webhooks.

Built as a FlyRank Internship capstone (Backend Track).

## What this system does

- Every call to `POST /generate` records a usage event (simulated AI token usage) for a tenant.
- The same request retried with the same `Idempotency-Key` returns the original result — never double-counted.
- Usage is checked against the tenant's plan quota before being allowed; over-limit requests are rejected with `429`.
- Cost is calculated using real AI-token pricing rules: cached input tokens are cheaper than fresh input,
  reasoning tokens are billed as output tokens, and categories are priced separately then summed as money.
- Customers upgrade from Free → Pro through Stripe Checkout (test mode). A signature-verified webhook
  updates the tenant's plan — forged or duplicate webhook events are rejected/ignored safely.
- `GET /usage` gives a tenant their current plan, usage, limit, and total cost.

## Architecture

```
Client ─► POST /generate (Idempotency-Key, Tenant-Id headers)
   ├─ idempotency check (usage_events.idempotency_key) → duplicate? return original
   ├─ tenant lookup / auto-create (tenants table)
   ├─ usage rollup: SUM(tokens) from usage_events for this tenant
   ├─ quota check vs. PLAN_LIMITS[plan] → over limit? 429
   ├─ cost calculation: input / cached_input / output+reasoning priced separately
   └─ INSERT usage_events → return breakdown

Client ─► POST /create-checkout-session (Tenant-Id header)
   └─► Stripe Checkout Session (mode=subscription, metadata.tenant_id) → checkout_url

Stripe ─signed webhook─► POST /webhooks/stripe
   ├─ verify signature (invalid → 400)
   ├─ dedupe by event.id (processed_webhook_events table) → seen before? no-op
   └─ checkout.session.completed → upsert tenants.plan = 'pro'

Client ─► GET /usage (Tenant-Id header)
   └─► { plan, used, limit, total_cost }
```

### Data model

- **tenants** — `id` (PK), `plan` (`free` | `pro`, default `free`)
- **usage_events** — one row per billable action: `tenant_id`, `idempotency_key` (UNIQUE), token
  breakdown (`input_tokens`, `cached_input_tokens`, `output_tokens`, `reasoning_tokens`), `total_cost`, `created_at`
- **processed_webhook_events** — `event_id` (PK) — every Stripe event ID we've already handled

## Run it

Requirements: Docker Desktop, a free Stripe account (test/sandbox mode), the Stripe CLI.

1. Clone the repo and move into it:
   ```
   git clone <your-repo-url>
   cd flyrank-capstone-metering-billing
   ```
2. Copy the example environment file and fill in your own Stripe test keys:
   ```
   cp .env.example .env
   ```
   You need `STRIPE_SECRET_KEY` and `STRIPE_PUBLISHABLE_KEY` from your Stripe Dashboard (test mode), plus a
   Price ID for a recurring "Pro" plan product (create one via Dashboard or `stripe prices create`).
3. Start the stack:
   ```
   docker compose up -d --build
   ```
4. In a separate terminal, start the Stripe webhook listener (needed for local development):
   ```
   stripe listen --forward-to localhost:8000/webhooks/stripe
   ```
   Copy the `whsec_...` secret it prints into your `.env` as `STRIPE_WEBHOOK_SECRET`, then restart the app
   (`docker compose up -d --build`) so it picks up the value.
5. Visit `http://localhost:8000/docs` to confirm it's running.

### Seed / demo flow

```
# Make a billable call
curl -X POST http://localhost:8000/generate -H "Idempotency-Key: demo1" -H "Tenant-Id: demoTenant"

# Retry the same request — proves idempotency (identical response, no new row)
curl -X POST http://localhost:8000/generate -H "Idempotency-Key: demo1" -H "Tenant-Id: demoTenant"

# Check usage
curl http://localhost:8000/usage -H "Tenant-Id: demoTenant"

# Start a real Stripe test-mode upgrade
curl -X POST http://localhost:8000/create-checkout-session -H "Tenant-Id: demoTenant"
# open the returned checkout_url, pay with 4242 4242 4242 4242 / any future expiry / any CVC

# Confirm the upgrade landed
curl http://localhost:8000/usage -H "Tenant-Id: demoTenant"
```

### Run tests

```
docker compose exec api pytest
```

## API reference

| Method | Path | Auth/Headers | Purpose |
|---|---|---|---|
| POST | `/generate` | `Idempotency-Key`, `Tenant-Id` | Simulated billable AI call; meters usage, enforces quota, returns cost breakdown |
| GET | `/usage` | `Tenant-Id` | Returns `{plan, used, limit, total_cost}` |
| POST | `/create-checkout-session` | `Tenant-Id` | Creates a Stripe Checkout session for the Pro plan |
| GET | `/success` | — | Checkout success redirect target |
| POST | `/webhooks/stripe` | `Stripe-Signature` | Stripe webhook receiver — verifies signature, dedupes, upgrades plan |

## Limitations (honest)

- Only two plans (Free/Pro) and one simulated billable action, per the assignment's realistic-scope guidance —
  not a general-purpose metering product.
- Token usage in `/generate` is simulated with fixed numbers, not a real AI model call — the pricing math is
  real, the token counts are not.
- No invoicing, proration, or overage billing — explicitly out of core scope.
- `Tenant-Id` is a trusted header with no authentication layer in front of it; a production system would
  derive tenant identity from a verified auth token, not a client-supplied header.
- Webhook secret rotates every time `stripe listen` restarts during local development — documented above,
  not an issue in a real deployed webhook endpoint with a fixed URL.
