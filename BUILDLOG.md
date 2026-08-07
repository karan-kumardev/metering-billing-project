# Build Log — AI Usage

Honest record of where AI (Claude) helped, where it was wrong or I was wrong, and what actually changed.
This project was built with me writing the code myself in most cases, with Claude reviewing for bugs and
explaining concepts rather than writing finished code outright.

## Where AI helped

- **Explaining new concepts before building them.** Idempotency, Stripe Checkout/webhooks, and the
  fetch→parse pattern for a two-pass system were all new to me. Claude explained the concept in plain
  language first, then I wrote the code myself.
- **Catching real bugs in code I wrote.** Most of the actual debugging in this project was: I write a
  function, Claude reads it and points out a specific bug without fixing it for me, I fix it, we retest.
  Examples: a missing `conn.commit()`, a stray `:` after a non-block statement, an `UPDATE` that silently
  matched zero rows because the tenant didn't exist yet, storing one value but returning a different one
  from the idempotency cache (so the second call to `/generate` returned a different shape than the first).
- **Diagnosing environment/tooling issues**, e.g. `psycopg` hanging when trying to reach `db` from outside
  Docker, a duplicate route definition silently shadowing the one I'd actually edited, and the Stripe CLI
  not being on PATH after a `winget install` that silently failed.
- **Providing the initial Dockerfile/compose.yaml scaffold**, matching a pattern from an earlier track
  assignment (A3) that I'd already used and understood.

## Where AI was wrong, or I had to correct the approach

- An early version of the webhook plan-upgrade logic used a plain `UPDATE tenants SET plan = ... WHERE id = ...`.
  This silently did nothing for a tenant that had never called `/generate` (no row existed yet to update).
  I had to actually test this against a genuinely new tenant to catch it — the fix was switching to an
  `INSERT ... ON CONFLICT (id) DO UPDATE` upsert instead.
- I initially had two `@app.post("/webhooks/stripe")` route definitions in the file at once (an old version
  left in place while iterating on a new one). FastAPI silently matched the first, older one, so newly added
  print statements never appeared no matter how many times I edited and restarted. Found via
  `findstr /n "webhooks/stripe" main.py` — a reminder that "the code runs with no errors" doesn't mean
  "the code I'm looking at is the code that's running."

## What I changed vs. what was suggested

- Kept the Postgres/Docker pattern from A3 rather than adopting SQLite, since I already understood the
  container networking model (`db` as hostname, not `localhost`) from that earlier assignment.
- The cost formula and pricing categories (`input_per_1k`, `cached_input_per_1k`, `output_per_1k`) are my
  own chosen rates — the *structure* (three separate categories, reasoning billed as output, summed as
  money not tokens) came from the assignment brief itself, which Claude helped me translate into an actual
  formula.
