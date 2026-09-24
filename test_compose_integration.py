"""Acceptance test for a running Agent Relay deployment.

Set RELAY_INTEGRATION_BASE_URL to opt in. This keeps the regular unit suite
self-contained while allowing the same protocol flow to exercise Compose.
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest


BASE_URL = os.getenv("RELAY_INTEGRATION_BASE_URL")
pytestmark = pytest.mark.skipif(not BASE_URL, reason="RELAY_INTEGRATION_BASE_URL is not set")


def register(client: httpx.Client, name: str) -> tuple[dict, dict[str, str]]:
    response = client.post("/api/v1/agents", json={"name": name})
    assert response.status_code == 201
    data = response.json()
    return data, {"Authorization": f"Bearer {data['token']}"}


def test_two_agents_exchange_task_and_sender_reads_result():
    run_id = uuid.uuid4().hex
    with httpx.Client(base_url=BASE_URL, timeout=10) as client:
        _sender, sender_headers = register(client, f"compose-sender-{run_id}")
        recipient, recipient_headers = register(client, f"compose-recipient-{run_id}")

        sent = client.post(
            "/api/v1/tasks",
            headers={**sender_headers, "Idempotency-Key": f"compose-{run_id}"},
            json={"to": recipient["agent_id"], "input": "hello from compose"},
        )
        assert sent.status_code == 201
        assert sent.json()["status"] == "queued"
        task_id = sent.json()["task_id"]

        claim = client.post(
            "/api/v1/tasks/claim",
            headers=recipient_headers,
            json={"worker_id": "compose-test-worker", "wait_seconds": 0},
        )
        assert claim.status_code == 200
        assert claim.json()["task_id"] == task_id

        completed = client.post(
            f"/api/v1/tasks/{task_id}/complete",
            headers=recipient_headers,
            json={
                "claim_token": claim.json()["claim_token"],
                "output": "HELLO FROM COMPOSE",
            },
        )
        assert completed.status_code == 200
        assert completed.json()["status"] == "completed"

        sender_view = client.get(f"/api/v1/tasks/{task_id}", headers=sender_headers)
        assert sender_view.status_code == 200
        assert sender_view.json()["status"] == "completed"
        assert sender_view.json()["output"] == "HELLO FROM COMPOSE"

        dashboard = client.get("/")
        assert dashboard.status_code == 200
        assert "Agent Relay" in dashboard.text

        dashboard_tasks = client.get("/api/v1/tasks?direction=sent", headers=sender_headers)
        assert dashboard_tasks.status_code == 200
        assert any(item["task_id"] == task_id for item in dashboard_tasks.json()["items"])
