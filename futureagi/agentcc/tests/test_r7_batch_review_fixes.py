"""Batch loss, bounded polling, and transport header behavior.

1. get-batch for a batch the gateway no longer holds (404). An open batch
   is immediately finished as failed (outcome unknown), as the poll does.
   A finished batch whose results the gateway dropped reads its recorded
   status. Both answer 200 so the console stops polling. Delegated batch
   enablement requires a single gateway replica or batch affinity.
2. poll_open_batches asks about the least recently polled batch first (a
   backlog of lost batches no longer starves newer ones), stops at its time
   budget (under the activity's 300 s limit), and stops at the first answer
   that shows the gateway unreachable: minted keys past their lifetime are
   still revoked locally, and the gateway is asked again by the next run.
3. GatewayClient._request: a caller's headers never replace the admin token
   or the JSON content type, whatever their case.
"""

import hashlib
from datetime import timedelta

import httpx
import pytest
from django.utils import timezone

from accounts.models.service_grant import ServiceGrant
from agentcc.models import AgentccAPIKey
from agentcc.models.webhook import AgentccWebhook, AgentccWebhookEvent
from agentcc.serializers.contracts import GatewayBatchDetailResultSerializer
from agentcc.services.gateway_client import GatewayClient, GatewayClientError
from agentcc.tests.r7_batch_support import *  # noqa: F403  (fixtures)
from agentcc.tests.r7_batch_support import BATCH_BODY, GATEWAY
from agentcc.tests.test_webhook_encrypted_fixtures import (
    make_webhook,
)
from agentcc.tests.test_webhook_encrypted_fixtures import (
    webhook_encryption_key as webhook_encryption_key,
)


def _job_model():
    from agentcc.models import AgentccBatchJob

    return AgentccBatchJob


def _lost():
    return GatewayClientError(
        'Gateway returned 404: {"error":"batch not found"}', status_code=404
    )


def _unreachable():
    return GatewayClientError(
        "Cannot connect to gateway at http://gateway.invalid/-/batches/x: refused"
    )


def _hook(organization):
    return make_webhook(
        manager=AgentccWebhook.no_workspace_objects,
        organization=organization,
        name="r7-batch-review-hook",
        url="https://hooks.invalid/r7-batch-review",
        events=["batch.completed"],
    )


def _events(hook):
    return list(AgentccWebhookEvent.no_workspace_objects.filter(webhook=hook))


def _submit(client):
    response = client.post(f"{GATEWAY}/submit-batch/", BATCH_BODY, format="json")
    assert response.status_code == 200, response.content
    return response.json()["result"]["batch_id"]


def _get(client, batch_id):
    return client.get(f"{GATEWAY}/get-batch/", {"batch_id": batch_id})


def _in_contract(result):
    serializer = GatewayBatchDetailResultSerializer(data=result)
    assert serializer.is_valid(), serializer.errors


def _age(batch_id, hours):
    _job_model().objects.filter(gateway_batch_id=batch_id).update(
        created_at=timezone.now() - timedelta(hours=hours)
    )


# --- 1. get-batch for a batch the gateway no longer holds ---------------


@pytest.mark.integration
def test_get_batch_finishes_a_lost_batch_past_its_key_lifetime_as_failed(
    auth_client, organization, fake_gateway, delivered
):
    hook = _hook(organization)
    batch_id = _submit(auth_client)
    assert fake_gateway.minted == {}
    assert ServiceGrant.objects.filter(kind="batch").count() == 1
    grant = ServiceGrant.objects.get()
    fake_gateway.get_error = _lost()
    _age(batch_id, 25)

    response = _get(auth_client, batch_id)
    assert response.status_code == 200, response.content
    result = response.json()["result"]
    assert result["status"] == "failed"
    assert result["outcome"] == "unknown"
    assert "no longer holds" in result["error"]
    assert result["batch_id"] == batch_id
    assert result["org_id"] == str(organization.id)
    assert result["total"] == 2
    assert "results" not in result and "summary" not in result
    _in_contract(result)

    # Finished once, as the poll would: one event, delivered; the key revoked.
    events = _events(hook)
    assert len(events) == 1
    assert events[0].payload["status"] == "failed"
    assert events[0].payload["outcome"] == "unknown"
    assert [p["body"]["status"] for p in delivered] == ["failed"]
    grant.refresh_from_db()
    assert grant.revoked_at is not None
    first_revocation = grant.revoked_at
    assert fake_gateway.minted == {} and fake_gateway.revoked == []
    job = _job_model().objects.get(gateway_batch_id=batch_id)
    assert job.notified is True and job.status == "failed"

    # The next read answers the same, and nothing is sent or revoked again.
    again = _get(auth_client, batch_id)
    assert again.status_code == 200, again.content
    assert again.json()["result"]["status"] == "failed"
    from agentcc.services.batch_jobs import poll_open_batches

    poll_open_batches(client=fake_gateway)
    assert len(_events(hook)) == 1
    grant.refresh_from_db()
    assert grant.revoked_at == first_revocation
    assert fake_gateway.revoked == []


@pytest.mark.integration
def test_get_batch_reads_a_finished_batch_the_gateway_dropped_as_recorded(
    auth_client, organization, fake_gateway, delivered
):
    batch_id = _submit(auth_client)
    fake_gateway.next_status = "completed"
    assert _get(auth_client, batch_id).json()["result"]["status"] == "completed"

    # The gateway's hour passed (or it restarted): it answers 404.
    fake_gateway.get_error = _lost()
    response = _get(auth_client, batch_id)
    assert response.status_code == 200, response.content
    result = response.json()["result"]
    assert result["status"] == "completed"
    assert result["batch_id"] == batch_id
    assert result["org_id"] == str(organization.id)
    assert result["completed_at"]
    assert "no longer holds" in result["error"]
    assert "outcome" not in result
    assert "results" not in result and "summary" not in result
    _in_contract(result)


@pytest.mark.integration
def test_get_batch_other_gateway_answers_are_unchanged(
    auth_client, organization, fake_gateway, delivered
):
    """A missing batch is explicitly lost even before expiry; other gateway
    errors retain their error response rather than inventing an outcome."""
    hook = _hook(organization)
    batch_id = _submit(auth_client)
    fake_gateway.get_error = _lost()
    response = _get(auth_client, batch_id)
    assert response.status_code == 200
    assert response.json()["result"]["outcome"] == "unknown"
    assert "no longer holds" in response.json()["result"]["error"]
    assert _job_model().objects.get(gateway_batch_id=batch_id).notified is True
    assert len(_events(hook)) == 1

    fake_gateway.get_error = None
    fake_gateway.next_status = "completed"
    assert _get(auth_client, batch_id).status_code == 200
    fake_gateway.get_error = GatewayClientError(
        "Gateway returned 500: boom", status_code=500
    )
    assert _get(auth_client, batch_id).status_code == 400


# --- 2. poll_open_batches: fairness, time budget, unreachable gateway -----


def _open_job(organization, batch_id, *, created_hours_ago, polled_hours_ago):
    now = timezone.now()
    job = _job_model().objects.create(
        organization=organization,
        gateway_batch_id=batch_id,
        gateway_key_id=f"key-{batch_id}",
        total=1,
    )
    _job_model().objects.filter(pk=job.pk).update(
        created_at=now - timedelta(hours=created_hours_ago),
        updated_at=now - timedelta(hours=polled_hours_ago),
    )
    return job


def _held(fake, organization, batch_id):
    fake.batches[batch_id] = {
        "org_id": str(organization.id),
        "status": "processing",
        "batch_key": None,
    }


def _asked(fake):
    return [args["batch_id"] for args in fake.named("get_batch")]


@pytest.mark.integration
def test_poll_asks_about_the_least_recently_polled_batch_first(
    db, organization, fake_gateway
):
    """A batch the gateway lost stays open for 24 h; it was re-polled at the
    head of the queue every minute, so a backlog larger than one run's limit
    starved every newer batch."""
    from agentcc.services.batch_jobs import poll_open_batches

    _open_job(organization, "batch-r7-lost", created_hours_ago=2, polled_hours_ago=2)
    _open_job(organization, "batch-r7-newer", created_hours_ago=1, polled_hours_ago=1)
    _held(fake_gateway, organization, "batch-r7-newer")

    poll_open_batches(client=fake_gateway, limit=1)
    poll_open_batches(client=fake_gateway, limit=1)
    assert _asked(fake_gateway) == ["batch-r7-lost", "batch-r7-newer"]


@pytest.mark.integration
def test_poll_stops_at_the_first_unreachable_gateway_answer(
    db, organization, fake_gateway
):
    from agentcc.services.batch_jobs import poll_open_batches

    stale = _open_job(
        organization, "batch-r7-stale", created_hours_ago=25, polled_hours_ago=30
    )
    row = AgentccAPIKey.no_workspace_objects.create(
        organization=organization,
        workspace=None,
        gateway_key_id=stale.gateway_key_id,
        key_hash=hashlib.sha256(b"sk-agentcc-r7-batch-review-test").hexdigest(),
        name="console-batch-r7-review",
        status=AgentccAPIKey.ACTIVE,
    )
    for n in range(3):
        _open_job(
            organization,
            f"batch-r7-u{n}",
            created_hours_ago=1,
            polled_hours_ago=3 - n,
        )
    fake_gateway.get_error = _unreachable()

    counts = poll_open_batches(client=fake_gateway)
    assert _asked(fake_gateway) == ["batch-r7-stale"]
    assert counts["stopped"] == "unreachable"
    # The past-lifetime key is revoked here at once (no key sync hands it
    # back); the gateway is not asked while it is unreachable, and the job
    # keeps its pending revoke for the next run.
    row.refresh_from_db()
    assert row.status == AgentccAPIKey.REVOKED
    assert fake_gateway.named("revoke_key") == []
    stale.refresh_from_db()
    assert stale.key_revoked_at is None

    fake_gateway.get_error = None
    for name in ("batch-r7-stale", "batch-r7-u0", "batch-r7-u1", "batch-r7-u2"):
        _held(fake_gateway, organization, name)
    counts = poll_open_batches(client=fake_gateway)
    assert counts["stopped"] is None
    assert fake_gateway.revoked == [stale.gateway_key_id]
    stale.refresh_from_db()
    assert stale.key_revoked_at is not None


@pytest.mark.integration
def test_poll_stops_when_its_time_budget_is_spent(db, organization, fake_gateway):
    """Each gateway call may take up to the client's 10 s timeout and the
    activity is killed at 300 s, so a run stops at its own budget first."""
    from agentcc.services.batch_jobs import poll_open_batches

    for n in range(4):
        name = f"batch-r7-t{n}"
        _open_job(organization, name, created_hours_ago=1, polled_hours_ago=4 - n)
        _held(fake_gateway, organization, name)

    clock = {"now": 1000.0}
    answer = fake_gateway.get_batch

    def slow_get_batch(batch_id, org_id=None):
        clock["now"] += 50.0
        return answer(batch_id, org_id=org_id)

    fake_gateway.get_batch = slow_get_batch
    counts = poll_open_batches(
        client=fake_gateway, time_budget=120.0, clock=lambda: clock["now"]
    )
    assert _asked(fake_gateway) == ["batch-r7-t0", "batch-r7-t1", "batch-r7-t2"]
    assert counts["checked"] == 3
    assert counts["stopped"] == "time_budget"

    # The next run starts with the batch this one did not reach.
    poll_open_batches(client=fake_gateway, limit=1)
    assert _asked(fake_gateway)[-1] == "batch-r7-t3"


# --- 3. GatewayClient._request headers ----------------------------------


def _in_memory(monkeypatch, seen):
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


def test_request_headers_never_replace_the_admin_token_or_content_type(monkeypatch):
    seen = []
    _in_memory(monkeypatch, seen)
    caller_headers = {
        "Authorization": "Bearer caller-token-test",
        "authorization": "Bearer caller-lowercase-test",
        "Content-Type": "text/plain",
        "content-type": "text/csv",
        "x-agentcc-batch-key": "sk-agentcc-raw-test",
    }

    GatewayClient("http://gateway.invalid", admin_token="admin-token-test")._request(
        "POST", "/-/batches", json_body={"requests": []}, headers=caller_headers
    )
    GatewayClient("http://gateway.invalid", admin_token="")._request(
        "POST", "/-/batches", json_body={"requests": []}, headers=caller_headers
    )

    with_token, without_token = seen
    assert with_token.headers.get_list("authorization") == ["Bearer admin-token-test"]
    assert with_token.headers.get_list("content-type") == ["application/json"]
    assert with_token.headers["x-agentcc-batch-key"] == "sk-agentcc-raw-test"
    # No admin token configured: the caller's Authorization is still not sent.
    assert without_token.headers.get_list("authorization") == []
    assert without_token.headers.get_list("content-type") == ["application/json"]
    assert without_token.headers["x-agentcc-batch-key"] == "sk-agentcc-raw-test"
