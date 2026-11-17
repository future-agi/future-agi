"""An open grant-backed batch missing on the gateway is immediately lost."""

import pytest

from accounts.models.service_grant import ServiceGrant
from agentcc.models import AgentccBatchJob
from agentcc.services.batch_jobs import LOST_BATCH_ERROR, poll_open_batches
from agentcc.services.gateway_client import GatewayClientError
from agentcc.tests.r7_batch_support import *  # noqa: F403
from agentcc.tests.r7_batch_support import GATEWAY
from agentcc.tests.test_r7_batch_completed import _events, _hook, _submit

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("path", ["poll", "get"])
def test_open_grant_batch_404_is_immediately_lost(
    auth_client, organization, fake_gateway, delivered, path
):
    hook = _hook(organization, "immediate-loss", ["batch.completed"])
    batch_id = _submit(auth_client)
    assert fake_gateway.minted == {}
    assert ServiceGrant.objects.filter(kind="batch").count() == 1
    grant = ServiceGrant.objects.get(kind="batch")
    job = AgentccBatchJob.objects.get(pk=grant.batch_ref)
    assert job.status == AgentccBatchJob.PROCESSING and not job.notified
    assert grant.revoked_at is None
    fake_gateway.get_error = GatewayClientError("batch not found", status_code=404)

    def check():
        if path == "poll":
            return poll_open_batches(client=fake_gateway)
        response = auth_client.get(f"{GATEWAY}/get-batch/", {"batch_id": batch_id})
        assert response.status_code == 200
        assert response.json()["result"]["outcome"] == "unknown"
        return response

    check()
    assert len(fake_gateway.named("get_batch")) == 1
    job.refresh_from_db()
    grant.refresh_from_db()
    assert job.status == AgentccBatchJob.FAILED and job.notified
    assert job.finished_at is not None and grant.revoked_at is not None
    revoked_at = grant.revoked_at
    events = _events(hook)
    assert len(events) == 1
    assert events[0].payload["event"] == "batch.completed"
    assert events[0].payload["status"] == "failed"
    assert events[0].payload["outcome"] == "unknown"
    assert events[0].payload["error"] == LOST_BATCH_ERROR
    check()
    grant.refresh_from_db()
    assert grant.revoked_at == revoked_at
    assert len(_events(hook)) == len(delivered) == 1
    assert fake_gateway.minted == {} and fake_gateway.revoked == []
