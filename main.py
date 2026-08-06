from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException,Header,Request
import stripe
import os

load_dotenv()
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")

app=FastAPI()
TOKEN_LIMIT = 1000

requests={}
tenant_usage={}
tenant_plans = {} 
processed_webhook_events = set()  # add this near your other dicts, at the top of the file


PLAN_LIMITS = {
    "free": 1000,
    "pro": 100000,
}
@app.post("/generate")
def generate(idempotency_key: str = Header(), tenant_id: str = Header(...)):

    if idempotency_key in requests:
        return requests[idempotency_key]

    current_usage = tenant_usage.get(tenant_id, 0)
    input_tokens = 300
    output_tokens = 400
    tokens = input_tokens + output_tokens

    plan = tenant_plans.get(tenant_id, "free")
    limit = PLAN_LIMITS[plan]

    if current_usage + tokens > limit:
        raise HTTPException(status_code=429, detail="Usage quota exceeded")

    tenant_usage[tenant_id] = current_usage + tokens

    cost = 0.1 * tokens

    result = {"tokens_used": tokens, "total_cost": cost}
    requests[idempotency_key] = result
    return result


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

    if event["id"] in processed_webhook_events:
        return {"status": "already processed"}

    processed_webhook_events.add(event["id"])

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        tenant_id = session["metadata"]["tenant_id"]
        print(f"UPGRADING TENANT: {tenant_id}")
        tenant_plans[tenant_id] = "pro"
    else:
        print(f"IGNORED EVENT TYPE: {event['type']}")

    return {"status": "received"}



@app.get("/debug/tenant/{tenant_id}")
def debug_tenant(tenant_id: str):
    return {
        "plan": tenant_plans.get(tenant_id, "free"),
        "usage": tenant_usage.get(tenant_id, 0)
    }