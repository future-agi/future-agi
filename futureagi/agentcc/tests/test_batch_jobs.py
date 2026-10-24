"""Console batch jobs: a batch the gateway does not hold, the poll's order and
budget, and the client's headers.

1. get-batch for a batch the gateway does not hold (404). A batch past its
   key's 24 h lifetime is finished as failed (outcome unknown) right there,
   as the poll does; a finished batch whose results the gateway dropped
   (it keeps a finished batch for an hour) reads as the status Django
   recorded. Both answer 200 in the get-batch contract, so the console hook
   (BATCH_TERMINAL_STATUSES) stops polling.
   A 404 within the key's lifetime still answers the gateway error.
2. poll_open_batches asks about the least recently polled batch first, so a
   backlog of lost batches never starves newer ones; it stops at its time
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

from agentcc.models import AgentccAPIKey
from agentcc.models.webhook import AgentccWebhook, AgentccWebhookEvent
from agentcc.serializers.contracts import GatewayBatchDetailResultSerializer
from agentcc.services.gateway_client import GatewayClient, GatewayClientError
from agentcc.tests.batch_support import *  # noqa: F403  (fixtures)
from agentcc.tests.batch_support import BATCH_BODY, GATEWAY


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
    return AgentccWebhook.no_workspace_objects.create(
        organization=organization,
        name="batch-review-hook",
        url="https://hooks.invalid/batch-review",
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


# --- 1. get-batch for a batch the gateway does not hold -----------------


@pytest.mark.integration
def test_get_batch_finishes_a_lost_batch_past_its_key_lifetime_as_failed(
    auth_client, organization, fake_gateway, delivered
):
    hook = _hook(organization)
    batch_id = _submit(auth_client)
    key_id = next(iter(fake_gateway.minted))
    fake_gateway.get_error = _lost()
    _age(batch_id, 25)

    response = _get(auth_client, batch_id)
    assert response.status_code == 200, response.content
    result = response.json()["result"]
    assert result["status"] == "failed"
    assert result["outcome"] == "unknown"
    assert "does not hold" in result["error"]
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
    assert fake_gateway.revoked == [key_id]
    job = _job_model().objects.get(gateway_batch_id=batch_id)
    assert job.notified is True and job.status == "failed"

    # The next read answers the same, and nothing is sent or revoked again.
    again = _get(auth_client, batch_id)
    assert again.status_code == 200, again.content
    assert again.json()["result"]["status"] == "failed"
    from agentcc.services.batch_jobs import poll_open_batches

    poll_open_batches(client=fake_gateway)
    assert len(_events(hook)) == 1
    assert fake_gateway.revoked == [key_id]


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
    assert "does not hold" in result["error"]
    assert "outcome" not in result
    assert "results" not in result and "summary" not in result
    _in_contract(result)


@pytest.mark.integration
def test_get_batch_other_gateway_answers_are_unchanged(
    auth_client, organization, fake_gateway, delivered
):
    """Controls: a 404 within the key's lifetime may be passing (another replica, a
    restart in progress), so it answers the gateway error and the job stays open;
    any other gateway error answers the gateway error, finished job or not."""
    hook = _hook(organization)
    batch_id = _submit(auth_client)
    fake_gateway.get_error = _lost()
    response = _get(auth_client, batch_id)
    assert response.status_code == 400
    assert "Gateway error" in response.json()["result"]
    assert _job_model().objects.get(gateway_batch_id=batch_id).notified is False
    assert _events(hook) == []

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
    """A batch the gateway lost stays open for 24 h; the poll asks about the least
    recently polled batch first, so every open batch is reached in turn."""
    from agentcc.services.batch_jobs import poll_open_batches

    _open_job(organization, "batch-lost", created_hours_ago=2, polled_hours_ago=2)
    _open_job(organization, "batch-newer", created_hours_ago=1, polled_hours_ago=1)
    _held(fake_gateway, organization, "batch-newer")

    poll_open_batches(client=fake_gateway, limit=1)
    poll_open_batches(client=fake_gateway, limit=1)
    assert _asked(fake_gateway) == ["batch-lost", "batch-newer"]


@pytest.mark.integration
def test_poll_stops_at_the_first_unreachable_gateway_answer(
    db, organization, fake_gateway
):
    from agentcc.services.batch_jobs import poll_open_batches

    stale = _open_job(
        organization, "batch-stale", created_hours_ago=25, polled_hours_ago=30
    )
    row = AgentccAPIKey.no_workspace_objects.create(
        organization=organization,
        workspace=None,
        gateway_key_id=stale.gateway_key_id,
        key_hash=hashlib.sha256(b"sk-agentcc-batch-review-test").hexdigest(),
        name="console-batch-review",
        status=AgentccAPIKey.ACTIVE,
    )
    for n in range(3):
        _open_job(
            organization,
            f"batch-u{n}",
            created_hours_ago=1,
            polled_hours_ago=3 - n,
        )
    fake_gateway.get_error = _unreachable()

    counts = poll_open_batches(client=fake_gateway)
    assert _asked(fake_gateway) == ["batch-stale"]
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
    for name in ("batch-stale", "batch-u0", "batch-u1", "batch-u2"):
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
        name = f"batch-t{n}"
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
    assert _asked(fake_gateway) == ["batch-t0", "batch-t1", "batch-t2"]
    assert counts["checked"] == 3
    assert counts["stopped"] == "time_budget"

    # The next run starts with the batch this one did not reach.
    poll_open_batches(client=fake_gateway, limit=1)
    assert _asked(fake_gateway)[-1] == "batch-t3"


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
