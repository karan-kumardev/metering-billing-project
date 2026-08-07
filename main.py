from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException,Header,Request
import stripe
import os
import psycopg

load_dotenv()
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")

app=FastAPI()


PRICING = {
    "input_per_1k": 0.01,
    "cached_input_per_1k": 0.005,
    "output_per_1k": 0.03,
}

PLAN_LIMITS = {
    "free": 1000,
    "pro": 100000,
}

DATABASE_URL=os.environ.get("DATABASE_URL")
conn=psycopg.connect(DATABASE_URL)
cursor=conn.cursor()


cursor.execute("""CREATE TABLE IF NOT EXISTS tenants (
    id TEXT PRIMARY KEY,
    plan TEXT NOT NULL DEFAULT 'free');
    """)

cursor.execute("""CREATE TABLE IF NOT EXISTS usage_events (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    idempotency_key TEXT NOT NULL UNIQUE,
    input_tokens INTEGER NOT NULL,
    cached_input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    reasoning_tokens INTEGER NOT NULL,
    total_cost NUMERIC(10, 6) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);""")


cursor.execute("""CREATE TABLE IF NOT EXISTS processed_webhook_events (
                    event_id TEXT PRIMARY KEY
);""")

conn.commit()

@app.post("/generate")
def generate(idempotency_key: str = Header(), tenant_id: str = Header(...)):

    input_tokens = 300
    cached_input_tokens = 100
    output_tokens = 400
    reasoning_tokens = 50
    tokens = input_tokens + output_tokens

    existing = cursor.execute(
        "SELECT * FROM usage_events WHERE idempotency_key = %s", (idempotency_key,)
    ).fetchone()

    if existing:
        return {
            "input_tokens": existing[3],
            "cached_input_tokens": existing[4],
            "output_tokens": existing[5],
            "reasoning_tokens": existing[6],
            "total_cost": float(existing[7]),
        }

    tenant_row = cursor.execute(
        "SELECT * FROM tenants WHERE id = %s", (tenant_id,)
    ).fetchone()

    tokens_row = cursor.execute(
        "SELECT SUM(input_tokens + output_tokens) FROM usage_events WHERE tenant_id = %s",
        (tenant_id,)
    ).fetchone()
    current_usage = tokens_row[0] if tokens_row[0] is not None else 0

    if not tenant_row:
        cursor.execute("INSERT INTO tenants (id, plan) VALUES (%s, %s)", (tenant_id, 'free'))
        conn.commit()
        plan = "free"
    else:
        plan = tenant_row[1]

    limit = PLAN_LIMITS[plan]

    if current_usage + tokens > limit:
        raise HTTPException(status_code=429, detail="Usage quota exceeded")

    input_cost = (input_tokens / 1000) * PRICING["input_per_1k"]
    cached_input_cost = (cached_input_tokens / 1000) * PRICING["cached_input_per_1k"]
    output_cost = ((output_tokens + reasoning_tokens) / 1000) * PRICING["output_per_1k"]
    cost = input_cost + cached_input_cost + output_cost

    cursor.execute(
        """INSERT INTO usage_events 
           (tenant_id, idempotency_key, input_tokens, cached_input_tokens, output_tokens, reasoning_tokens, total_cost)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (tenant_id, idempotency_key, input_tokens, cached_input_tokens, output_tokens, reasoning_tokens, round(cost, 6))
    )
    conn.commit()

    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "total_cost": round(cost, 6),
    }



@app.post("/create-checkout-session")
def create_checkout_session(tenant_id: str = Header(...)):
    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{
            "price": "price_1U1PKUBXLHf32tHdAuTdPn6u",
            "quantity": 1,
        }],
        success_url="http://localhost:8000/success",
        cancel_url="http://localhost:8000/cancel",
        metadata={"tenant_id": tenant_id},
    )
    return {"checkout_url": session.url}


@app.get("/success")
def success():
    return {"message": "Payment successful! Your plan will update shortly."}    

@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    existing_event = cursor.execute(
        "SELECT * FROM processed_webhook_events WHERE event_id = %s", (event["id"],)
    ).fetchone()

    if existing_event:
        return {"status": "already processed"}

    cursor.execute("INSERT INTO processed_webhook_events (event_id) VALUES (%s)", (event["id"],))
    conn.commit()

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        tenant_id = session["metadata"]["tenant_id"]
        print(f"UPGRADING TENANT: {tenant_id}")

        cursor.execute(
            """INSERT INTO tenants (id, plan) VALUES (%s, %s)
               ON CONFLICT (id) DO UPDATE SET plan = %s""",
            (tenant_id, "pro", "pro")
        )
        conn.commit()
    else:
        print(f"IGNORED EVENT TYPE: {event['type']}")

    return {"status": "received"}

@app.get("/debug/tenant/{tenant_id}")
def debug_tenant(tenant_id: str):
    tenant_row = cursor.execute("SELECT * FROM tenants WHERE id = %s", (tenant_id,)).fetchone()
    plan = tenant_row[1] if tenant_row else "free"

    usage_row = cursor.execute(
        "SELECT SUM(input_tokens + output_tokens) FROM usage_events WHERE tenant_id = %s",
        (tenant_id,)
    ).fetchone()
    usage = usage_row[0] if usage_row[0] is not None else 0

    return {"plan": plan, "usage": usage}



@app.get("/usage")
def get_usage(tenant_id: str = Header(...)):
    tenant_row = cursor.execute("SELECT * FROM tenants WHERE id = %s", (tenant_id,)).fetchone()
    plan = tenant_row[1] if tenant_row else "free"
    limit = PLAN_LIMITS[plan]

    usage_row = cursor.execute(
        "SELECT SUM(input_tokens + output_tokens), SUM(total_cost) FROM usage_events WHERE tenant_id = %s",
        (tenant_id,)
    ).fetchone()

    used = usage_row[0] if usage_row[0] is not None else 0
    total_cost = float(usage_row[1]) if usage_row[1] is not None else 0.0

    return {
        "plan": plan,
        "used": used,
        "limit": limit,
        "total_cost": round(total_cost, 6),
    }