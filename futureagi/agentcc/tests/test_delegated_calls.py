"""Console requests run as people without adding API-key rows."""

from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from accounts.models import OrgApiKey
from accounts.models.service_grant import BatchCancellationIntent, ServiceGrant
from accounts.services.envelope_keys import pair_keys
from accounts.services.service_grants import revoke_person_grants
from django.db import transaction
from django.utils import timezone
from tfc.service_envelope import verify_grant

from agentcc.models import AgentccAPIKey, AgentccBatchJob
from agentcc.services.batch_cancellations import retry_cancellations
from agentcc.services.delegated_calls import STREAM_TIMEOUT
from agentcc.services.gateway_client import GatewayClientError

pytestmark = pytest.mark.django_db


@pytest.fixture
def enabled(settings):
    settings.SECRET_KEY = "test_only_fa_signing_key_for_delegation_00000"
    settings.AGENTCC_DELEGATED_PLAYGROUND_ENABLED = True
    settings.AGENTCC_DELEGATED_BATCH_ENABLED = True


def key_counts():
    return OrgApiKey._base_manager.count(), AgentccAPIKey._base_manager.count()


def test_playground_no_key_rows_or_create_call(auth_client, user, workspace, enabled):
    client = MagicMock()
    client.send_delegated_chat_completion.return_value = (200, {"choices": []}, {})
    before = key_counts()
    with (
        patch("agentcc.views.gateway.get_gateway_client", return_value=client),
        patch(
            "agentcc.views.gateway.AgentccGatewayViewSet._ensure_org_config_pushed",
            return_value=True,
        ),
    ):
        response = auth_client.post(
            "/agentcc/gateways/default/test-playground/",
            {"prompt": "hello", "model": "test/model"},
            format="json",
        )
    assert response.status_code == 200, response.data
    delegation = client.send_delegated_chat_completion.call_args.args[0]
    claims = verify_grant(
        delegation.token,
        keys=pair_keys("fa", "fa-eval-grant"),
        audience="fa-eval-grant",
    )
    assert claims["org"] == str(user.organization_id)
    assert claims["user"] == str(user.pk)
    assert claims["workspace"] == str(workspace.pk)
    assert key_counts() == before == (0, 0)
    client.create_key.assert_not_called()


def test_stream_grant_covers_long_stream_and_post_eval(auth_client, user, enabled):
    client = MagicMock()
    captured = []

    @contextmanager
    def stream(delegation, **kwargs):
        captured.append(delegation)
        response = MagicMock(
            status_code=200, headers={"content-type": "text/event-stream"}
        )
        response.iter_lines.return_value = [
            'data: {"choices": [{"delta": {"content": "hello"}}]}',
            "data: [DONE]",
        ]
        yield response

    client.stream_delegated_chat_completion.side_effect = stream
    with (
        patch("agentcc.views.gateway.get_gateway_client", return_value=client),
        patch(
            "agentcc.views.gateway.AgentccGatewayViewSet._ensure_org_config_pushed",
            return_value=True,
        ),
    ):
        response = auth_client.post(
            "/agentcc/gateways/default/test-playground-stream/",
            {"prompt": "hello", "model": "test/model"},
            format="json",
        )
        assert response.status_code == 200
        assert b"hello" in b"".join(response.streaming_content)
    grant = captured[0]
    assert int(grant.claims["exp"]) - int(grant.claims["iat"]) == STREAM_TIMEOUT + 60
    verify_grant(
        grant.token,
        keys=pair_keys("fa", "fa-eval-grant"),
        audience="fa-eval-grant",
        now=int(grant.claims["iat"]) + 130,
    )
    assert key_counts() == (0, 0)
    client.create_key.assert_not_called()


def test_batch_registered_before_submit_and_linked(auth_client, user, enabled):
    client = MagicMock()

    def submitted(delegation, requests, concurrency):
        row = ServiceGrant.objects.get(pk=delegation.claims["id"])
        assert AgentccBatchJob.objects.filter(pk=row.batch_ref).exists()
        return {"batch_id": "test-batch", "status": "processing", "total": 1}

    client.submit_delegated_batch.side_effect = submitted
    with patch("agentcc.views.gateway.get_gateway_client", return_value=client):
        response = auth_client.post(
            "/agentcc/gateways/default/submit-batch/",
            {"requests": [{"model": "test/model"}]},
            format="json",
        )
    assert response.status_code == 200, response.data
    row = ServiceGrant.objects.get(kind="batch")
    job = AgentccBatchJob.objects.get(pk=row.batch_ref)
    assert job.gateway_batch_id == "test-batch"
    assert job.gateway_key_id == ""
    assert key_counts() == (0, 0)
    client.create_key.assert_not_called()


def test_refused_batch_revokes_grant(auth_client, enabled):
    client = MagicMock()
    client.submit_delegated_batch.side_effect = GatewayClientError(
        "unavailable", status_code=503
    )
    with patch("agentcc.views.gateway.get_gateway_client", return_value=client):
        response = auth_client.post(
            "/agentcc/gateways/default/submit-batch/",
            {"requests": [{"model": "test/model"}]},
            format="json",
        )
    assert response.status_code == 400
    assert ServiceGrant.objects.filter(revoked_at__isnull=True).count() == 0
    assert (
        BatchCancellationIntent.objects.filter(confirmed_at__isnull=True).count() == 1
    )
    assert key_counts() == (0, 0)


def test_dropped_cancel_is_durable_until_terminal_readback(user, workspace, enabled):
    import uuid

    from accounts.services.service_grants import issue_person_grant

    batch_ref = uuid.uuid4()
    with transaction.atomic():
        _, _, grant = issue_person_grant(
            kind="batch",
            org=user.organization_id,
            user=user.pk,
            workspace=workspace.pk,
            sub="batch:" + str(batch_ref),
            ttl=86400,
        )
        job = AgentccBatchJob.objects.create(
            id=batch_ref,
            organization=user.organization,
            created_by=user,
            gateway_batch_id="test-queued",
            gateway_key_id="",
            total=3,
        )
    now = timezone.now()
    revoke_person_grants(user.pk, user.organization_id)
    client = MagicMock()
    client.cancel_batch.side_effect = GatewayClientError("call dropped")
    client.get_batch.return_value = {"status": "processing", "queued": 2}
    assert retry_cancellations(client, now=now + timedelta(seconds=1)) == 0
    intent = BatchCancellationIntent.objects.get(grant=grant)
    assert intent.confirmed_at is None and intent.attempts == 1
    # Gateway-stub contract: its next eligibility sync stops the queued items
    # even though cancel was dropped. The real sync-bound proof belongs to Go.
    client.get_batch.return_value = {"status": "cancelled", "queued": 0}
    assert retry_cancellations(client, now=now + timedelta(seconds=60)) == 1
    intent.refresh_from_db()
    job.refresh_from_db()
    assert intent.confirmed_at is not None
    assert intent.attempts == 2
    assert job.status == AgentccBatchJob.CANCELLED
    assert client.cancel_batch.call_count == client.get_batch.call_count == 2
