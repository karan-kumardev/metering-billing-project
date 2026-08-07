"""
Tests for the metering & billing engine.
Run with: docker compose exec api pytest
Requires the app + Postgres to be running (docker compose up -d).
"""
import uuid
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def unique_key():
    return str(uuid.uuid4())


def test_generate_returns_cost_breakdown():
    tenant = f"test-{uuid.uuid4()}"
    response = client.post(
        "/generate",
        headers={"Idempotency-Key": unique_key(), "Tenant-Id": tenant},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["input_tokens"] == 300
    assert body["cached_input_tokens"] == 100
    assert body["output_tokens"] == 400
    assert body["reasoning_tokens"] == 50
    # 300/1000*0.01 + 100/1000*0.005 + 450/1000*0.03 = 0.003 + 0.0005 + 0.0135 = 0.017
    assert abs(body["total_cost"] - 0.017) < 1e-9


def test_duplicate_idempotency_key_returns_identical_result():
    tenant = f"test-{uuid.uuid4()}"
    key = unique_key()

    first = client.post("/generate", headers={"Idempotency-Key": key, "Tenant-Id": tenant})
    second = client.post("/generate", headers={"Idempotency-Key": key, "Tenant-Id": tenant})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()


def test_duplicate_request_does_not_double_count_usage():
    tenant = f"test-{uuid.uuid4()}"
    key = unique_key()

    client.post("/generate", headers={"Idempotency-Key": key, "Tenant-Id": tenant})
    client.post("/generate", headers={"Idempotency-Key": key, "Tenant-Id": tenant})

    usage = client.get("/usage", headers={"Tenant-Id": tenant}).json()
    # one request's worth of tokens (300+400=700), not two
    assert usage["used"] == 700


def test_quota_exceeded_returns_429():
    tenant = f"test-{uuid.uuid4()}"

    # first call: 700 tokens, under the 1000 free-plan limit
    r1 = client.post("/generate", headers={"Idempotency-Key": unique_key(), "Tenant-Id": tenant})
    assert r1.status_code == 200

    # second call: another 700 tokens -> 1400 total, over the limit
    r2 = client.post("/generate", headers={"Idempotency-Key": unique_key(), "Tenant-Id": tenant})
    assert r2.status_code == 429
    assert "quota" in r2.json()["detail"].lower()


def test_usage_endpoint_reports_plan_used_limit_cost():
    tenant = f"test-{uuid.uuid4()}"
    client.post("/generate", headers={"Idempotency-Key": unique_key(), "Tenant-Id": tenant})

    usage = client.get("/usage", headers={"Tenant-Id": tenant}).json()
    assert usage["plan"] == "free"
    assert usage["limit"] == 1000
    assert usage["used"] == 700
    assert abs(usage["total_cost"] - 0.017) < 1e-9


def test_new_tenant_defaults_to_free_plan_with_zero_usage():
    tenant = f"test-{uuid.uuid4()}"
    usage = client.get("/usage", headers={"Tenant-Id": tenant}).json()
    assert usage["plan"] == "free"
    assert usage["used"] == 0


def test_forged_webhook_signature_rejected():
    response = client.post(
        "/webhooks/stripe",
        headers={"stripe-signature": "t=12345,v1=totallyfakesignature"},
        json={"type": "checkout.session.completed", "data": {"object": {"metadata": {"tenant_id": "someone"}}}},
    )
    assert response.status_code == 400


def test_missing_headers_return_422():
    # missing Idempotency-Key and Tenant-Id entirely
    response = client.post("/generate")
    assert response.status_code == 422
