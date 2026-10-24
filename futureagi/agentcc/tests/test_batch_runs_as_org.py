"""Console Batch Jobs run as the caller's org.

submit-batch mints a 24 h key for the caller's org, names it to the gateway in
x-agentcc-batch-key, records the batch (AgentccBatchJob), and get/cancel name the
org to the gateway; the minted key is revoked at the batch's first terminal status.
"""

import hashlib
import uuid
from datetime import timedelta

import httpx
import pytest
from django.utils import timezone

from agentcc.models import AgentccAPIKey
from agentcc.services.gateway_client import GatewayClient, GatewayClientError
from agentcc.tests.batch_support import *  # noqa: F403  (fixtures)
from agentcc.tests.batch_support import (
    BATCH_BODY,
    GATEWAY,
)


def _job_model():
    from agentcc.models import AgentccBatchJob

    return AgentccBatchJob


def _submit(client):
    response = client.post(f"{GATEWAY}/submit-batch/", BATCH_BODY, format="json")
    assert response.status_code == 200, response.content
    return response


@pytest.mark.integration
def test_submit_mints_an_org_key_and_names_it_to_the_gateway(
    auth_client, organization, user, fake_gateway
):
    before = timezone.now()
    response = _submit(auth_client)
    result = response.json()["result"]

    creates = fake_gateway.named("create_key")
    assert len(creates) == 1, fake_gateway.calls
    create = creates[0]
    assert create["name"].startswith("console-batch-")
    uuid.UUID(create["name"][len("console-batch-") :])
    assert create["metadata"]["org_id"] == str(organization.id)
    key_id, raw = next(iter(fake_gateway.minted.items()))

    submits = fake_gateway.named("submit_batch")
    assert len(submits) == 1
    assert submits[0]["batch_key"] == raw
    assert submits[0]["requests"] == BATCH_BODY["requests"]
    assert submits[0]["max_concurrency"] == 2

    # The raw key never reaches the console.
    assert raw not in response.content.decode()
    assert result["org_id"] == str(organization.id)

    # The key is the org's, stored by hash with a 24 h expiry, so the
    # gateway's key sync keeps it (and later carries its expiry).
    row = AgentccAPIKey.no_workspace_objects.get(gateway_key_id=key_id)
    assert row.organization_id == organization.id
    assert row.name == create["name"]
    assert row.key_hash == hashlib.sha256(raw.encode()).hexdigest()
    assert row.status == AgentccAPIKey.ACTIVE
    assert row.user_id == user.id
    assert (
        before + timedelta(hours=24) - timedelta(minutes=5)
        <= row.expires_at
        <= timezone.now() + timedelta(hours=24)
    )

    job = _job_model().objects.get(gateway_batch_id=result["batch_id"])
    assert job.organization_id == organization.id
    assert job.gateway_key_id == key_id
    assert job.status == "processing"
    assert job.notified is False


@pytest.mark.integration
def test_get_and_cancel_name_the_org_and_another_org_gets_404(
    auth_client, organization, fake_gateway, other_org_client
):
    batch_id = _submit(auth_client).json()["result"]["batch_id"]

    got = auth_client.get(f"{GATEWAY}/get-batch/", {"batch_id": batch_id})
    assert got.status_code == 200, got.content
    assert fake_gateway.named("get_batch")[-1] == {
        "batch_id": batch_id,
        "org_id": str(organization.id),
    }

    _, other = other_org_client
    calls_before = list(fake_gateway.calls)
    assert other.get(f"{GATEWAY}/get-batch/", {"batch_id": batch_id}).status_code == 404
    assert (
        other.post(
            f"{GATEWAY}/cancel-batch/", {"batch_id": batch_id}, format="json"
        ).status_code
        == 404
    )
    assert fake_gateway.calls == calls_before, "another org reached the gateway"

    cancelled = auth_client.post(
        f"{GATEWAY}/cancel-batch/", {"batch_id": batch_id}, format="json"
    )
    assert cancelled.status_code == 200, cancelled.content
    assert cancelled.json()["result"]["status"] == "cancelled"
    assert fake_gateway.named("cancel_batch")[-1] == {
        "batch_id": batch_id,
        "org_id": str(organization.id),
    }


@pytest.mark.integration
def test_first_terminal_status_revokes_the_minted_key_once(
    auth_client, fake_gateway, delivered
):
    batch_id = _submit(auth_client).json()["result"]["batch_id"]
    key_id = next(iter(fake_gateway.minted))

    fake_gateway.next_status = "processing"
    auth_client.get(f"{GATEWAY}/get-batch/", {"batch_id": batch_id})
    assert fake_gateway.revoked == []

    fake_gateway.next_status = "completed"
    for _ in range(2):
        got = auth_client.get(f"{GATEWAY}/get-batch/", {"batch_id": batch_id})
        assert got.status_code == 200
        assert got.json()["result"]["status"] == "completed"
    assert fake_gateway.revoked == [key_id]
    row = AgentccAPIKey.no_workspace_objects.get(gateway_key_id=key_id)
    assert row.status == AgentccAPIKey.REVOKED
    job = _job_model().objects.get(gateway_batch_id=batch_id)
    assert job.status == "completed"
    assert job.key_revoked_at is not None


@pytest.mark.integration
def test_a_refused_submit_revokes_the_minted_key_and_records_nothing(
    auth_client, fake_gateway
):
    fake_gateway.submit_error = GatewayClientError(
        "Gateway returned 401: invalid_api_key", status_code=401
    )
    response = auth_client.post(f"{GATEWAY}/submit-batch/", BATCH_BODY, format="json")
    assert response.status_code == 400
    key_id = next(iter(fake_gateway.minted))
    assert fake_gateway.revoked == [key_id]
    assert (
        AgentccAPIKey.no_workspace_objects.get(gateway_key_id=key_id).status
        == AgentccAPIKey.REVOKED
    )
    assert not _job_model().objects.exists()


@pytest.mark.integration
def test_an_empty_submit_mints_nothing(auth_client, fake_gateway):
    response = auth_client.post(
        f"{GATEWAY}/submit-batch/", {"requests": []}, format="json"
    )
    assert response.status_code == 400
    assert fake_gateway.calls == []


def test_gateway_client_sends_the_batch_key_and_the_org(monkeypatch):
    """The real GatewayClient on an in-memory transport: the batch key goes in
    x-agentcc-batch-key on submit only, next to the admin token; get and
    cancel name the org in org_id."""
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"batch_id": "b1", "status": "processing"})

    real_client = httpx.Client

    def in_memory_client(**kwargs):
        return real_client(
            transport=httpx.MockTransport(handler), trust_env=False, **kwargs
        )

    monkeypatch.setattr(
        "agentcc.services.gateway_client.httpx.Client", in_memory_client
    )
    client = GatewayClient("http://gateway.invalid", admin_token="admin-token-test")

    client.submit_batch([{"model": "m"}], 3, batch_key="sk-agentcc-raw-test")
    client.get_batch("b1", org_id="org-1")
    client.cancel_batch("b1", org_id="org-1")
    client.health_check()

    submit, get, cancel, health = seen
    assert submit.method == "POST" and submit.url.path == "/-/batches"
    assert submit.headers["x-agentcc-batch-key"] == "sk-agentcc-raw-test"
    assert submit.headers["authorization"] == "Bearer admin-token-test"
    assert get.url.path == "/-/batches/b1"
    assert dict(get.url.params) == {"batch_id": "b1", "org_id": "org-1"}
    assert cancel.method == "POST" and cancel.url.path == "/-/batches/b1/cancel"
    assert dict(cancel.url.params) == {"batch_id": "b1", "org_id": "org-1"}
    for request in (get, cancel, health):
        assert "x-agentcc-batch-key" not in request.headers
        assert request.headers["authorization"] == "Bearer admin-token-test"
