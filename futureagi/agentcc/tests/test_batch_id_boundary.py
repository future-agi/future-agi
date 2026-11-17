"""Unusable gateway IDs must leave durable submissions pending and retryable."""

from unittest.mock import MagicMock, patch

import pytest

from agentcc.models import AgentccBatchJob
from agentcc.services.batch_submission_recovery import recover_submissions
from agentcc.services.delegated_batches import link_submitted
from agentcc.services.gateway_client import GatewayClientError
from agentcc.tests.test_submission_listing_identity import (
    pending_submission as _pending_submission,
)

pending_submission = _pending_submission

pytestmark = pytest.mark.django_db

# Unicode White_Space plus the four additional separators recognized by str.strip.
WHITESPACE = [
    chr(n)
    for n in (
        *range(9, 14),
        *range(28, 33),
        0x85,
        0xA0,
        0x1680,
        *range(0x2000, 0x200B),
        0x2028,
        0x2029,
        0x202F,
        0x205F,
        0x3000,
    )
]
INVALID_IDS = ["", *WHITESPACE, "".join(WHITESPACE)]


def unchanged(job, grant, intent):
    job.refresh_from_db()
    grant.refresh_from_db()
    intent.refresh_from_db()
    assert job.gateway_batch_id == "pending-" + str(job.pk)
    assert job.status == AgentccBatchJob.PROCESSING
    assert not job.notified and job.finished_at is None
    assert grant.revoked_at is None and intent.resolved_at is None


@pytest.mark.parametrize("value", INVALID_IDS)
def test_matching_batch_id_boundary(pending_submission, value):
    job, grant, intent, entry = pending_submission
    client = MagicMock()
    client.list_delegated_batches.return_value = {
        "batches": [{**entry, "batch_id": value}]
    }
    with patch("agentcc.services.batch_jobs.finish_lost") as lost:
        recovered = recover_submissions(client)
    lost.assert_not_called()
    client.list_delegated_batches.assert_called_once_with()
    assert recovered == 0, "Malformed blank batch ID resolved an unknown submission"
    unchanged(job, grant, intent)
    client.list_delegated_batches.return_value = {"batches": [entry]}
    # Retain the outbox's retry backoff; retry when the durable intent is due.
    with patch(
        "agentcc.services.batch_submission_recovery.timezone.now",
        return_value=intent.next_attempt_at,
    ):
        assert recover_submissions(client) == 1
    job.refresh_from_db()
    intent.refresh_from_db()
    assert job.gateway_batch_id == entry["batch_id"] and intent.resolved_at is not None
    client.submit_delegated_batch.assert_not_called()


@pytest.mark.parametrize("value", INVALID_IDS)
def test_shared_link_validator_refuses_before_mutation(pending_submission, value):
    job, grant, intent, entry = pending_submission
    with pytest.raises(GatewayClientError, match="returned no batch id"):
        link_submitted(job.organization, {**entry, "batch_id": value}, grant)
    unchanged(job, grant, intent)


def test_valid_batch_id_control(pending_submission):
    job, grant, intent, entry = pending_submission
    client = MagicMock()
    client.list_delegated_batches.return_value = {"batches": [entry]}
    assert recover_submissions(client) == 1
    client.list_delegated_batches.assert_called_once_with()
    job.refresh_from_db()
    intent.refresh_from_db()
    grant.refresh_from_db()
    assert job.gateway_batch_id == entry["batch_id"]
    assert intent.resolved_at is not None and grant.revoked_at is None
    assert not job.notified and job.status == AgentccBatchJob.PROCESSING
