"""Only a complete batch listing can resolve an unknown submission outcome."""

import uuid
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from accounts.models.service_grant import BatchSubmissionIntent
from accounts.services.service_grants import issue_person_grant
from agentcc.models import AgentccBatchJob
from agentcc.services.batch_submission_recovery import recover_submissions

pytestmark = pytest.mark.django_db


@pytest.fixture
def pending_submission(user, workspace, settings):
    settings.SECRET_KEY = "test_only_listing_signing_material_0000000000"
    ref = uuid.uuid4()
    _, _, grant = issue_person_grant(
        kind="batch",
        org=user.organization_id,
        user=user.pk,
        workspace=workspace.pk,
        sub="batch:" + str(ref),
        ttl=86400,
    )
    job = AgentccBatchJob.objects.create(
        id=ref,
        organization=user.organization,
        created_by=user,
        gateway_batch_id="pending-" + str(ref),
        gateway_key_id="",
        total=1,
    )
    intent = BatchSubmissionIntent.objects.create(
        grant=grant, next_attempt_at=timezone.now() - timedelta(minutes=1)
    )
    entry = {
        "batch_id": "test-only-accepted-batch",
        "key_id": "dlg_" + grant.jti,
        "org_id": str(user.organization_id),
    }
    return job, grant, intent, entry


@pytest.mark.parametrize(
    "damage",
    [
        "missing-org",
        "missing-key",
        "empty-object",
        "numeric-org",
        "numeric-key",
        "empty-org",
        "empty-key",
        "blank-org",
        "blank-key",
        "null-org",
        "null-key",
        "boolean-org",
        "boolean-key",
        "list-org",
        "list-key",
        "dict-org",
        "dict-key",
    ],
)
def test_malformed_identity_keeps_submission_unknown(pending_submission, damage):
    job, grant, intent, entry = pending_submission
    kind, field = damage.split("-", 1)
    field += "_id"
    if damage == "empty-object":
        entry = {}
    elif kind == "missing":
        entry.pop(field)
    else:
        entry[field] = {
            "numeric": 123,
            "empty": "",
            "blank": " \t",
            "null": None,
            "boolean": True,
            "list": [],
            "dict": {},
        }[kind]
    client = MagicMock()
    client.list_delegated_batches.return_value = {"batches": [entry]}
    with patch("agentcc.services.batch_jobs.finish_lost") as finish_lost:
        assert recover_submissions(client) == 0
    finish_lost.assert_not_called()
    client.list_delegated_batches.assert_called_once_with()
    job.refresh_from_db()
    grant.refresh_from_db()
    intent.refresh_from_db()
    assert job.gateway_batch_id == "pending-" + str(job.pk)
    assert job.status == AgentccBatchJob.PROCESSING
    assert not job.notified and job.finished_at is None
    assert grant.revoked_at is None and intent.resolved_at is None
    # A later authoritative response must still be able to recover this intent.
    client.list_delegated_batches.return_value = {
        "batches": [
            {
                "batch_id": "test-only-recovered-batch",
                "key_id": "dlg_" + grant.jti,
                "org_id": str(job.organization_id),
            }
        ]
    }
    assert recover_submissions(client) == 1
    job.refresh_from_db()
    intent.refresh_from_db()
    assert job.gateway_batch_id == "test-only-recovered-batch"
    assert intent.resolved_at is not None


@pytest.mark.parametrize("present", [True, False], ids=["valid-present", "valid-empty"])
def test_valid_listing_resolves_submission(pending_submission, present):
    job, grant, intent, entry = pending_submission
    client = MagicMock()
    client.list_delegated_batches.return_value = {"batches": [entry] if present else []}
    assert recover_submissions(client) == 1
    client.list_delegated_batches.assert_called_once_with()
    job.refresh_from_db()
    grant.refresh_from_db()
    intent.refresh_from_db()
    assert intent.resolved_at is not None
    if present:
        assert job.gateway_batch_id == entry["batch_id"]
        assert job.status == AgentccBatchJob.PROCESSING and not job.notified
        assert grant.revoked_at is None
    else:
        assert job.status == AgentccBatchJob.FAILED and job.notified
        assert job.finished_at is not None and grant.revoked_at is not None
    client.submit_delegated_batch.assert_not_called()


def test_malformed_sibling_prevents_partial_listing_resolution(pending_submission):
    job, grant, intent, entry = pending_submission
    client = MagicMock()
    client.list_delegated_batches.return_value = {"batches": [entry, {}]}
    with patch("agentcc.services.batch_jobs.finish_lost") as finish_lost:
        assert recover_submissions(client) == 0
    finish_lost.assert_not_called()
    job.refresh_from_db()
    intent.refresh_from_db()
    grant.refresh_from_db()
    assert job.gateway_batch_id.startswith("pending-") and not job.notified
    assert intent.resolved_at is None and grant.revoked_at is None
