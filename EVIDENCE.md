# Evidence

One proof per Definition-of-Done checkbox. All output below is from real runs against the live system.

## Metering

**A billable action creates exactly one usage event, even under retries — deduplicated by idempotency key.**

```
$ curl -X POST http://localhost:8000/generate -H "Idempotency-Key: dbtest1" -H "Tenant-Id: tenantC"
{"input_tokens":300,"cached_input_tokens":100,"output_tokens":400,"reasoning_tokens":50,"total_cost":0.017}

$ curl -X POST http://localhost:8000/generate -H "Idempotency-Key: dbtest1" -H "Tenant-Id: tenantC"
{"input_tokens":300,"cached_input_tokens":100,"output_tokens":400,"reasoning_tokens":50,"total_cost":0.017}

$ curl -X POST http://localhost:8000/generate -H "Idempotency-Key: dbtest1" -H "Tenant-Id: tenantC"
{"input_tokens":300,"cached_input_tokens":100,"output_tokens":400,"reasoning_tokens":50,"total_cost":0.017}
```
Three identical calls with the same idempotency key, all identical responses. Verified this survives a full
server restart (in-memory version could not — this is why the DB migration mattered): the third call above
was made after restarting the `api` container between calls.

## Quotas

**Usage is checked against the tenant's plan; requests over the limit are rejected. Responses carry correct
status codes and a message explaining why.**

```
$ curl -X POST http://localhost:8000/generate -H "Idempotency-Key: dbtest2" -H "Tenant-Id: tenantC"
{"detail":"Usage quota exceeded"}
```
`tenantC` had already recorded 700 tokens (from `dbtest1`). A second, different request (700 more tokens)
correctly returns `429` with a clear reason before it would have pushed them to 1400 — over the free plan's
1000-token limit.

## Cost calculation

**AI token pricing handles cached input tokens, reasoning tokens, and output pricing correctly.**

```
$ curl -X POST http://localhost:8000/generate -H "Idempotency-Key: pricing1" -H "Tenant-Id: tenantB"
{"input_tokens":300,"cached_input_tokens":100,"output_tokens":400,"reasoning_tokens":50,"total_cost":0.017}
```
Manual verification:
- input: 300/1000 × $0.01 = $0.003
- cached input: 100/1000 × $0.005 = $0.0005
- output + reasoning: (400+50)/1000 × $0.03 = $0.0135
- total: 0.003 + 0.0005 + 0.0135 = **0.017** ✓ matches API output exactly

## Stripe integration

**Subscription checkout works end-to-end in Stripe test mode. Webhooks verify signatures, ignore duplicate
events, and update tenant plan/status.**

```
$ curl -X POST http://localhost:8000/create-checkout-session -H "Tenant-Id: tenantD"
{"checkout_url":"https://checkout.stripe.com/c/pay/cs_test_..."}
```
Checkout completed in browser with Stripe test card 4242 4242 4242 4242. Server log from the live run:
```
api-1  | IGNORED EVENT TYPE: customer.created
api-1  | IGNORED EVENT TYPE: charge.succeeded
api-1  | IGNORED EVENT TYPE: invoice.finalized
api-1  | UPGRADING TENANT: tenantD
api-1  | IGNORED EVENT TYPE: invoice.paid
api-1  | IGNORED EVENT TYPE: payment_method.attached
api-1  | IGNORED EVENT TYPE: customer.updated
```
Followed by:
```
$ curl http://localhost:8000/usage -H "Tenant-Id: tenantD"
{"plan":"pro", ...}
```
Confirms: real Checkout session → real webhook delivery → signature verified → correct event type isolated
from the noise of related Stripe events → tenant plan flipped.

**Forged webhook rejected:**
```
$ curl -i -X POST http://localhost:8000/webhooks/stripe -H "stripe-signature: t=12345,v1=totallyfakesignature" -d "{...fake checkout.session.completed for tenantA...}"
HTTP/1.1 400 Bad Request
{"detail":"Invalid signature"}
```
Confirmed via `/debug/tenant/tenantA` afterward that no plan change occurred.

**Duplicate webhook event ignored:**
A real `checkout.session.completed` event was resent via `stripe events resend <event_id>` without
restarting the server between deliveries. Second delivery returned `{"status": "already processed"}` and
produced no second `UPGRADING TENANT` log line — the `processed_webhook_events` table's primary-key
constraint plus the pre-check prevented reprocessing.

## Data model

**Database includes tenants, plans, subscriptions, and usage events; customer data isolated per tenant.**

```sql
\dt
             List of relations
 Schema |          Name              | Type  |  Owner
--------+-----------------------------+-------+----------
 public | tenants                     | table | postgres
 public | usage_events                | table | postgres
 public | processed_webhook_events    | table | postgres
```
`usage_events.tenant_id` references `tenants.id`; `usage_events.idempotency_key` is `UNIQUE` (a second,
database-enforced layer of duplicate protection beyond the application-level check).

## Tests

See `test_main.py`. Run with `docker compose exec api pytest`.
