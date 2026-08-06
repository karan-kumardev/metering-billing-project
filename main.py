from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException,Header

load_dotenv()

app=FastAPI()
TOKEN_LIMIT = 1000

requests={}
tenant_usage={}

@app.post("/generate")
def generate(idempotency_key: str = Header(), tenant_id: str = Header(...)):

    if idempotency_key in requests:
        return requests[idempotency_key]

    current_usage = tenant_usage.get(tenant_id, 0)
    input_tokens = 300
    output_tokens = 400
    tokens = input_tokens + output_tokens

    if current_usage + tokens > TOKEN_LIMIT:
        raise HTTPException(status_code=429, detail="Usage quota exceeded")

    tenant_usage[tenant_id] = current_usage + tokens

    cost = 0.1 * tokens 

    result = {"tokens_used": tokens, "total_cost": cost}
    requests[idempotency_key] = result
    return result