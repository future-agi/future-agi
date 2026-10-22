"""batch.completed fires once per batch.

The first time get-batch or the poll activity
(services.batch_jobs.poll_open_batches, every minute) sees a batch completed,
failed or cancelled, it creates one batch.completed event per subscribed
webhook of the batch's org and delivers it through webhook_delivery.
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from agentcc.models import AgentccAPIKey
from agentcc.models.webhook import AgentccWebhook, AgentccWebhookEvent
from agentcc.services.gateway_client import GatewayClientError
from agentcc.tests.batch_support import *  # noqa: F403  (fixtures)
from agentcc.tests.batch_support import (
    BATCH_BODY,
    GATEWAY,
    SUMMARY,
)


def _job_model():
    from agentcc.models import AgentccBatchJob

    return AgentccBatchJob


def _poll(fake):
    from agentcc.services.batch_jobs import poll_open_batches

    return poll_open_batches(client=fake)


def _hook(organization, name, events):
    return AgentccWebhook.no_workspace_objects.create(
        organization=organization,
        name=name,
        url=f"https://hooks.invalid/{name}",
        events=events,
    )


def _submit(client):
    response = client.post(f"{GATEWAY}/submit-batch/", BATCH_BODY, format="json")
    assert response.status_code == 200, response.content
    return response.json()["result"]["batch_id"]


def _events(hook):
    return list(
        AgentccWebhookEvent.no_workspace_objects.filter(webhook=hook).order_by(
            "created_at"
        )
    )


@pytest.mark.integration
def test_completed_batch_emits_batch_completed_once(
    auth_client, organization, fake_gateway, delivered, other_org_client
):
    """The console's get-batch sees the batch complete, and the subscribed webhook
    gets batch.completed once, with the status; a second get and the poll activity
    add nothing."""
    hook = _hook(organization, "batch-hook", ["batch.completed"])
    other_event_hook = _hook(organization, "requests-hook", ["request.completed"])
    other_org, _ = other_org_client
    other_org_hook = _hook(other_org, "other-org-hook", ["batch.completed"])

    batch_id = _submit(auth_client)
    fake_gateway.next_status = "processing"
    auth_client.get(f"{GATEWAY}/get-batch/", {"batch_id": batch_id})
    assert _events(hook) == []

    fake_gateway.next_status = "completed"
    polled = auth_client.get(f"{GATEWAY}/get-batch/", {"batch_id": batch_id})
    assert polled.status_code == 200, polled.content
    assert polled.json()["result"]["status"] == "completed"

    events = _events(hook)
    assert len(events) == 1
    payload = events[0].payload
    assert payload["event"] == "batch.completed"
    assert payload["batch_id"] == batch_id
    assert payload["status"] == "completed"
    assert payload["org_id"] == str(organization.id)
    assert payload["total"] == 2
    assert payload["summary"] == SUMMARY
    assert [p["body"]["event"] for p in delivered] == ["batch.completed"]
    assert delivered[0]["url"] == hook.url
    assert events[0].status == AgentccWebhookEvent.DELIVERED

    # Exactly once: another get, then the poll activity.
    auth_client.get(f"{GATEWAY}/get-batch/", {"batch_id": batch_id})
    _poll(fake_gateway)
    assert len(_events(hook)) == 1
    assert len(delivered) == 1
    assert _events(other_event_hook) == []
    assert _events(other_org_hook) == []
    assert _job_model().objects.get(gateway_batch_id=batch_id).notified is True


@pytest.mark.integration
def test_poll_emits_for_a_cancelled_batch_and_revokes_its_key(
    auth_client, organization, fake_gateway, delivered
):
    hook = _hook(organization, "batch-hook", ["batch.completed"])
    batch_id = _submit(auth_client)
    key_id = next(iter(fake_gateway.minted))
    cancel = auth_client.post(
        f"{GATEWAY}/cancel-batch/", {"batch_id": batch_id}, format="json"
    )
    assert cancel.status_code == 200
    # Cancel alone emits nothing: the gateway's summary comes with the next
    # read, which get-batch or the poll makes.
    assert _events(hook) == []

    fake_gateway.next_status = "cancelled"
    counts = _poll(fake_gateway)
    events = _events(hook)
    assert len(events) == 1, counts
    assert events[0].payload["status"] == "cancelled"
    assert events[0].payload["summary"] == SUMMARY
    assert fake_gateway.revoked == [key_id]
    assert counts["notified"] == 1

    _poll(fake_gateway)
    assert len(_events(hook)) == 1


@pytest.mark.integration
def test_a_batch_the_gateway_lost_fails_only_after_its_key_lifetime(
    auth_client, organization, fake_gateway, delivered
):
    """Batches live in the gateway's memory: a restart loses them, and the
    gateway answers 404. Within the key's 24 h lifetime the job stays open
    (a 404 may be a passing condition); after it, the job is reported failed
    with an unknown outcome, once, and its key is revoked."""
    hook = _hook(organization, "batch-hook", ["batch.completed"])
    batch_id = _submit(auth_client)
    key_id = next(iter(fake_gateway.minted))
    fake_gateway.get_error = GatewayClientError(
        'Gateway returned 404: {"error":"batch not found"}', status_code=404
    )

    _poll(fake_gateway)
    assert _events(hook) == []
    assert fake_gateway.revoked == []

    _job_model().objects.filter(gateway_batch_id=batch_id).update(
        created_at=timezone.now() - timedelta(hours=25)
    )
    _poll(fake_gateway)
    events = _events(hook)
    assert len(events) == 1
    payload = events[0].payload
    assert payload["status"] == "failed"
    assert payload["outcome"] == "unknown"
    assert "does not hold" in payload["error"]
    assert fake_gateway.revoked == [key_id]

    _poll(fake_gateway)
    assert len(_events(hook)) == 1


@pytest.mark.integration
def test_poll_revokes_a_minted_key_older_than_24h(
    auth_client, organization, fake_gateway, delivered
):
    """The stored key carries a 24 h expires_at (the gateway's key sync
    loads it), and the poll revokes a minted key 24 h after the batch was
    submitted even if the batch is still running. The batch still gets its
    batch.completed when it ends."""
    hook = _hook(organization, "batch-hook", ["batch.completed"])
    batch_id = _submit(auth_client)
    key_id = next(iter(fake_gateway.minted))
    fake_gateway.next_status = "processing"

    _poll(fake_gateway)
    assert fake_gateway.revoked == []

    _job_model().objects.filter(gateway_batch_id=batch_id).update(
        created_at=timezone.now() - timedelta(hours=24, minutes=1)
    )
    counts = _poll(fake_gateway)
    assert fake_gateway.revoked == [key_id]
    assert counts["keys_revoked"] == 1
    assert (
        AgentccAPIKey.no_workspace_objects.get(gateway_key_id=key_id).status
        == AgentccAPIKey.REVOKED
    )
    job = _job_model().objects.get(gateway_batch_id=batch_id)
    assert job.notified is False and job.status == "processing"
    assert _events(hook) == []

    fake_gateway.next_status = "completed"
    _poll(fake_gateway)
    assert len(_events(hook)) == 1
    assert fake_gateway.revoked == [key_id], "the key was revoked twice"


@pytest.mark.integration
def test_the_batch_schedule_runs_the_poll_activity(
    auth_client, organization, fake_gateway, delivered
):
    """tfc/temporal/schedules/agentcc_batches.py: one schedule, every minute,
    naming the activity in the same file, which polls the open batches."""
    from tfc.temporal.schedules.agentcc_batches import (
        AGENTCC_BATCH_SCHEDULES,
        poll_agentcc_batches_activity,
    )

    assert len(AGENTCC_BATCH_SCHEDULES) == 1
    schedule = AGENTCC_BATCH_SCHEDULES[0]
    assert schedule.schedule_id == "agentcc-batches"
    assert schedule.interval_seconds == 60
    assert schedule.activity_name == "poll_agentcc_batches_activity"

    hook = _hook(organization, "batch-hook", ["batch.completed"])
    batch_id = _submit(auth_client)
    fake_gateway.next_status = "completed"
    with patch(
        "agentcc.services.batch_jobs.get_gateway_client", return_value=fake_gateway
    ):
        result = poll_agentcc_batches_activity._original_func()
    assert result["notified"] == 1
    assert [e.payload["batch_id"] for e in _events(hook)] == [batch_id]
