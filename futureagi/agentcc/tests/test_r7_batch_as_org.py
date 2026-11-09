"""Console batches carry a registered person grant and a durable org link.

The legacy transport remains covered separately for pre-cutover jobs. New
submissions never mint keys; terminal states revoke the registered grant.
"""

import httpx
import pytest
from django.utils import timezone

from accounts.models import OrgApiKey
from accounts.models.service_grant import ServiceGrant
from agentcc.models import AgentccAPIKey
from agentcc.services.gateway_client import GatewayClient, GatewayClientError
from agentcc.tests.r7_batch_support import *  # noqa: F403  (fixtures)
from agentcc.tests.r7_batch_support import (
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
def test_submit_registers_a_person_grant_and_names_it_to_the_gateway(
    auth_client, organization, user, workspace, fake_gateway
):
    before = int(timezone.now().timestamp())
    response = _submit(auth_client)
    result = response.json()["result"]
    assert fake_gateway.named("create_key") == []
    assert fake_gateway.minted == {}
    assert OrgApiKey._base_manager.count() == 0
    assert AgentccAPIKey._base_manager.count() == 0
    submits = fake_gateway.named("submit_batch")
    assert len(submits) == 1
    delegation = submits[0]["delegation"]
    assert "batch_key" not in submits[0]
    assert submits[0]["requests"] == BATCH_BODY["requests"]
    assert submits[0]["max_concurrency"] == 2
    assert delegation.token not in response.content.decode()
    assert result["org_id"] == str(organization.id)
    row = ServiceGrant.objects.get(pk=delegation.claims["id"])
    assert row.org == organization.id
    assert row.user == user.id
    assert row.workspace == workspace.id
    assert row.kind == "batch"
    assert row.scope == "eval"
    assert row.aud == "fa-eval-grant"
    assert row.revoked_at is None
    assert before <= row.iat <= int(timezone.now().timestamp())
    assert row.exp - row.iat == 86400
    assert row.registry_row()["sub"] == "batch:" + str(row.batch_ref)
    job = _job_model().objects.get(gateway_batch_id=result["batch_id"])
    assert job.pk == row.batch_ref
    assert job.organization_id == organization.id
    assert job.created_by_id == user.id
    assert job.gateway_key_id == ""
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
def test_first_terminal_status_revokes_the_registered_grant_once(
    auth_client, fake_gateway, delivered
):
    batch_id = _submit(auth_client).json()["result"]["batch_id"]
    assert fake_gateway.minted == {}
    assert ServiceGrant.objects.filter(kind="batch").count() == 1
    row = ServiceGrant.objects.get()
    fake_gateway.next_status = "processing"
    auth_client.get(f"{GATEWAY}/get-batch/", {"batch_id": batch_id})
    row.refresh_from_db()
    assert row.revoked_at is None
    fake_gateway.next_status = "completed"
    revocations = []
    for _ in range(2):
        got = auth_client.get(f"{GATEWAY}/get-batch/", {"batch_id": batch_id})
        assert got.status_code == 200
        assert got.json()["result"]["status"] == "completed"
        row.refresh_from_db()
        revocations.append(row.revoked_at)
    assert revocations[0] is not None and revocations[0] == revocations[1]
    assert fake_gateway.revoked == []
    assert AgentccAPIKey._base_manager.count() == 0
    job = _job_model().objects.get(gateway_batch_id=batch_id)
    assert job.status == "completed"
    assert job.key_revoked_at is not None


@pytest.mark.integration
def test_a_refused_submit_revokes_the_grant_and_keeps_a_failed_audit_row(
    auth_client, fake_gateway
):
    fake_gateway.submit_error = GatewayClientError(
        "Gateway returned 401: service_envelope_rejected", status_code=401
    )
    response = auth_client.post(f"{GATEWAY}/submit-batch/", BATCH_BODY, format="json")
    assert response.status_code == 400
    assert fake_gateway.minted == {} and fake_gateway.revoked == []
    assert AgentccAPIKey._base_manager.count() == 0
    assert ServiceGrant.objects.get().revoked_at is not None
    job = _job_model().objects.get()
    assert job.status == "failed" and job.notified is True
    assert job.finished_at is not None and job.gateway_key_id == ""


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
