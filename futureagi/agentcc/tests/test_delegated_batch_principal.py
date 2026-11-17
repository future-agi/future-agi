"""A grant-backed batch job names the principal its items ran under.

The gateway runs a delegated batch's items as "dlg_<grant jti>" and its
request logs carry that id as api_key_id; the job records it at link time.
That id is never an AgentccAPIKey: ending such a job revokes its grant, never
a key. Jobs linked before this (gateway_key_id "") stay readable.
"""

import uuid
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.db import transaction
from django.utils import timezone

from accounts.models.service_grant import BatchCancellationIntent, ServiceGrant
from accounts.services.service_grants import issue_person_grant, revoke_person_grants
from agentcc.models import AgentccAPIKey, AgentccBatchJob
from agentcc.services import batch_jobs
from agentcc.services.batch_cancellations import retry_cancellations
from agentcc.services.batch_submission_recovery import recover_submissions
from agentcc.services.delegated_batches import link_submitted
from agentcc.services.gateway_client import GatewayClientError
from agentcc.tests.r7_batch_support import *  # noqa: F403  (fixtures)
from agentcc.tests.r7_batch_support import GATEWAY, user_with_level
from agentcc.tests.test_submission_listing_identity import (
    pending_submission as _pending_submission,
)
from tfc.constants.levels import Level

pending_submission = _pending_submission

pytestmark = pytest.mark.django_db


def _grant_job(org, owner, workspace, *, key_id="dlg", **job_fields):
    """A grant-backed job; key_id "dlg" stores the grant's own dlg_<jti>."""
    ref = uuid.uuid4()
    with transaction.atomic():
        _, _, grant = issue_person_grant(
            kind="batch",
            org=org.pk,
            user=owner.pk,
            workspace=workspace.pk,
            sub="batch:" + str(ref),
            ttl=86400,
        )
        job = AgentccBatchJob.objects.create(
            id=ref,
            organization=org,
            created_by=owner,
            gateway_batch_id=job_fields.pop(
                "gateway_batch_id", "test-only-batch-" + uuid.uuid4().hex[:8]
            ),
            gateway_key_id="dlg_" + grant.jti if key_id == "dlg" else key_id,
            total=1,
            **job_fields,
        )
    return job, grant


# --- link_submitted -------------------------------------------------------


def test_link_stores_the_grants_delegated_principal(pending_submission):
    job, grant, intent, _ = pending_submission
    linked = link_submitted(
        job.organization,
        {"batch_id": "test-only-linked", "key_id": "dlg_" + grant.jti},
        grant,
    )
    job.refresh_from_db()
    intent.refresh_from_db()
    assert linked.gateway_key_id == job.gateway_key_id == "dlg_" + grant.jti
    assert job.gateway_batch_id == "test-only-linked"
    assert intent.resolved_at is not None


def test_link_without_key_id_keeps_empty_and_still_links(pending_submission):
    job, grant, intent, _ = pending_submission
    with patch("agentcc.services.delegated_batches.logger") as log:
        link_submitted(job.organization, {"batch_id": "test-only-no-key"}, grant)
    job.refresh_from_db()
    intent.refresh_from_db()
    assert job.gateway_key_id == ""
    assert job.gateway_batch_id == "test-only-no-key"
    assert intent.resolved_at is not None
    log.warning.assert_not_called()


@pytest.mark.parametrize(
    "value, reason",
    [
        ("other-grant", "mismatch"),
        ("prefix-only", "mismatch"),
        ("padded", "malformed"),
        ("upper", "malformed"),
        ("key_test_only_minted", "malformed"),
        ("", "malformed"),
        (123, "malformed"),
        (["list"], "malformed"),
    ],
)
def test_link_refuses_a_principal_other_than_the_grants(
    pending_submission, value, reason
):
    job, grant, intent, _ = pending_submission
    if isinstance(value, str):
        value = {
            "other-grant": "dlg_" + "0" * 32,
            "prefix-only": "dlg_",
            "padded": " dlg_" + grant.jti,
            "upper": "DLG_" + grant.jti,
        }.get(value, value)
    assert value != "dlg_" + grant.jti
    with patch("agentcc.services.delegated_batches.logger") as log:
        link_submitted(
            job.organization,
            {"batch_id": "test-only-mismatch", "key_id": value},
            grant,
        )
    job.refresh_from_db()
    intent.refresh_from_db()
    # The accepted batch stays tracked (and cancellable); the principal is not
    # stored, so readers resolve the job through its grant as before.
    assert job.gateway_batch_id == "test-only-mismatch"
    assert job.gateway_key_id == ""
    assert intent.resolved_at is not None
    log.warning.assert_called_once_with(
        "delegated_batch_principal_not_stored", reason=reason
    )
    # Only the error type is logged: never the received value or the grant id.
    assert grant.jti not in str(log.mock_calls)
    if isinstance(value, str) and value:
        assert value not in str(log.mock_calls)


def test_listing_recovery_records_the_principal(pending_submission):
    job, grant, intent, entry = pending_submission
    client = MagicMock()
    client.list_delegated_batches.return_value = {"batches": [entry]}
    assert recover_submissions(client) == 1
    job.refresh_from_db()
    assert job.gateway_batch_id == entry["batch_id"]
    assert job.gateway_key_id == "dlg_" + grant.jti


def test_cancellation_listing_link_records_the_principal(user, workspace, settings):
    settings.SECRET_KEY = "test_only_principal_signing_material_000000"
    job, grant = _grant_job(
        user.organization,
        user,
        workspace,
        key_id="",
        gateway_batch_id="pending-" + uuid.uuid4().hex,
    )
    revoke_person_grants(user.pk, user.organization_id)
    client = MagicMock()
    client.list_delegated_batches.return_value = {
        "batches": [
            {
                "batch_id": "test-only-cancel-found",
                "key_id": "dlg_" + grant.jti,
                "org_id": str(user.organization_id),
            }
        ]
    }
    client.get_batch.return_value = {"status": "cancelled"}
    assert retry_cancellations(client, now=timezone.now() + timedelta(seconds=1)) == 1
    job.refresh_from_db()
    assert job.gateway_batch_id == "test-only-cancel-found"
    assert job.gateway_key_id == "dlg_" + grant.jti
    assert job.status == AgentccBatchJob.CANCELLED
    client.revoke_key.assert_not_called()
    assert (
        BatchCancellationIntent.objects.get(grant=grant).confirmed_at is not None
    )


# --- ending a delegated job revokes its grant, never a key ----------------


@pytest.mark.parametrize("ask_gateway", [True, False])
@pytest.mark.parametrize("key_id", ["dlg", ""], ids=["dlg-id", "legacy-empty"])
def test_revoke_of_a_delegated_job_goes_through_the_grant(
    user, workspace, settings, key_id, ask_gateway
):
    settings.SECRET_KEY = "test_only_principal_signing_material_000000"
    job, grant = _grant_job(
        user.organization,
        user,
        workspace,
        key_id=key_id,
        status=AgentccBatchJob.COMPLETED,
        notified=True,
        finished_at=timezone.now(),
    )
    # A decoy key row with the same id must not be looked up or revoked.
    decoy = AgentccAPIKey._base_manager.create(
        organization=user.organization,
        user=user,
        workspace=workspace,
        gateway_key_id="dlg_" + grant.jti,
        name="test-only decoy",
    )
    client = MagicMock()
    assert batch_jobs._revoke_job_key(job, client, ask_gateway=ask_gateway) is True
    client.revoke_key.assert_not_called()
    grant.refresh_from_db()
    job.refresh_from_db()
    decoy.refresh_from_db()
    assert grant.revoked_at is not None and grant.revoked_reason == "batch_ended"
    assert job.key_revoked_at is not None
    assert decoy.status != AgentccAPIKey.REVOKED
    assert job.gateway_key_id == ("dlg_" + grant.jti if key_id == "dlg" else "")


def test_poll_finishes_a_dlg_job_through_its_grant(user, workspace, settings):
    settings.SECRET_KEY = "test_only_principal_signing_material_000000"
    job, grant = _grant_job(
        user.organization,
        user,
        workspace,
        status=AgentccBatchJob.COMPLETED,
        notified=True,
        finished_at=timezone.now(),
    )
    client = MagicMock()
    counts = batch_jobs.poll_open_batches(client=client)
    assert counts["keys_revoked"] == 1
    client.revoke_key.assert_not_called()
    grant.refresh_from_db()
    job.refresh_from_db()
    assert grant.revoked_at is not None and job.key_revoked_at is not None


def test_terminal_status_of_a_dlg_job_revokes_the_grant_not_a_key(
    user, workspace, settings
):
    settings.SECRET_KEY = "test_only_principal_signing_material_000000"
    job, grant = _grant_job(user.organization, user, workspace)
    client = MagicMock()
    assert batch_jobs.record_status(job, {"status": "completed", "total": 1}, client)
    client.revoke_key.assert_not_called()
    grant.refresh_from_db()
    assert grant.revoked_at is not None and job.key_revoked_at is not None


def test_minted_key_job_still_revokes_its_key(user, workspace):
    key_id = "test_only_minted_" + uuid.uuid4().hex
    row = AgentccAPIKey._base_manager.create(
        organization=user.organization,
        user=user,
        workspace=workspace,
        gateway_key_id=key_id,
        name="test-only minted",
    )
    job = AgentccBatchJob.objects.create(
        organization=user.organization,
        created_by=user,
        gateway_batch_id="test-only-legacy-" + uuid.uuid4().hex[:8],
        gateway_key_id=key_id,
        status=AgentccBatchJob.COMPLETED,
        notified=True,
    )
    client = MagicMock()
    assert batch_jobs._revoke_job_key(job, client) is True
    client.revoke_key.assert_called_once_with(key_id)
    row.refresh_from_db()
    assert row.status == AgentccAPIKey.REVOKED
    assert not batch_jobs.is_delegated_job(job)


# --- reads: "" (legacy) and dlg_ jobs answer the same principal ----------


@pytest.mark.parametrize("key_id", ["dlg", ""], ids=["dlg-id", "legacy-empty"])
def test_recorded_result_names_the_principal_for_both_shapes(
    user, workspace, settings, key_id
):
    settings.SECRET_KEY = "test_only_principal_signing_material_000000"
    job, grant = _grant_job(
        user.organization,
        user,
        workspace,
        key_id=key_id,
        status=AgentccBatchJob.COMPLETED,
        notified=True,
        finished_at=timezone.now(),
    )
    assert batch_jobs.is_delegated_job(job)
    assert batch_jobs.recorded_result(job)["key_id"] == "dlg_" + grant.jti


@pytest.mark.parametrize("held", [True, False], ids=["gateway-holds", "gateway-404"])
@pytest.mark.parametrize("key_id", ["dlg", ""], ids=["dlg-id", "legacy-empty"])
def test_get_and_cancel_read_both_shapes(
    organization, workspace, make_client, fake_gateway, key_id, held
):
    owner, _ = user_with_level(organization, Level.MEMBER, workspace)
    job, grant = _grant_job(
        organization,
        owner,
        workspace,
        key_id=key_id,
        status=AgentccBatchJob.COMPLETED,
        notified=True,
        finished_at=timezone.now(),
        key_revoked_at=timezone.now(),
    )
    if held:
        fake_gateway.batches[job.gateway_batch_id] = {
            "org_id": str(organization.pk),
            "status": "completed",
        }
        fake_gateway.next_status = "completed"
    client = make_client(owner, workspace)
    got = client.get(f"{GATEWAY}/get-batch/", {"batch_id": job.gateway_batch_id})
    assert got.status_code == 200, got.content
    result = got.json()["result"]
    assert result["status"] == "completed"
    if not held:
        assert result["key_id"] == "dlg_" + grant.jti
    if held:
        # Authorization resolves both shapes through the grant, never a key.
        cancelled = client.post(
            f"{GATEWAY}/cancel-batch/",
            {"batch_id": job.gateway_batch_id},
            format="json",
        )
        assert cancelled.status_code == 200, cancelled.content
    assert fake_gateway.revoked == []
    assert ServiceGrant.objects.get(pk=grant.pk).batch_ref == job.pk


def test_dlg_job_of_another_grant_is_not_readable(
    organization, workspace, make_client, fake_gateway
):
    owner, _ = user_with_level(organization, Level.MEMBER, workspace)
    job, _ = _grant_job(
        organization, owner, workspace, key_id="dlg_" + "f" * 32
    )
    fake_gateway.batches[job.gateway_batch_id] = {
        "org_id": str(organization.pk),
        "status": "processing",
    }
    before = list(fake_gateway.calls)
    got = make_client(owner, workspace).get(
        f"{GATEWAY}/get-batch/", {"batch_id": job.gateway_batch_id}
    )
    assert got.status_code == 404
    assert fake_gateway.calls == before


def test_a_refused_submit_link_error_still_raises_before_mutation(pending_submission):
    job, grant, intent, _ = pending_submission
    with pytest.raises(GatewayClientError, match="returned no batch id"):
        link_submitted(
            job.organization, {"key_id": "dlg_" + grant.jti}, grant
        )
    job.refresh_from_db()
    assert job.gateway_key_id == "" and job.gateway_batch_id.startswith("pending-")
