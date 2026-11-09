"""Batch grants and their job links are durable before gateway submission."""

import time
import uuid
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from accounts.models.service_grant import (
    BatchCancellationIntent,
    BatchSubmissionIntent,
    ServiceGrant,
)
from accounts.services.service_grants import issue_person_grant, revoke_grants
from agentcc.models.batch_job import AgentccBatchJob
from agentcc.services.delegated_calls import CHAT_TIMEOUT, Delegation
from agentcc.services.gateway_client import GatewayClientError


def submit(client, *, organization, user, workspace, requests, max_concurrency):
    from agentcc.services.batch_jobs import record_submitted

    deadline = time.monotonic() + CHAT_TIMEOUT
    batch_ref = uuid.uuid4()
    with transaction.atomic(durable=True):
        token, claims, grant = issue_person_grant(
            kind="batch",
            org=organization.pk,
            user=user.pk,
            workspace=getattr(workspace, "pk", None),
            sub="batch:" + str(batch_ref),
            ttl=86400,
        )
        AgentccBatchJob.objects.create(
            id=batch_ref,
            organization=organization,
            created_by=user,
            gateway_batch_id="pending-" + str(batch_ref),
            gateway_key_id="",
            total=len(requests),
        )
        # Allow the bounded submit and its last request envelope to expire before
        # an empty authoritative listing can establish that this job was lost.
        BatchSubmissionIntent.objects.create(
            grant=grant, next_attempt_at=timezone.now() + timedelta(seconds=120)
        )
    try:
        result = client.submit_delegated_batch(
            Delegation(token, claims, deadline=deadline), requests, max_concurrency
        )
        record_submitted(organization, result, user=user, grant=grant)
    except Exception as exc:
        # A lost response can still mean the batch was accepted. Preserve a
        # durable pending intent; the admin list can recover its dlg_<jti> id.
        revoke_grants(
            ServiceGrant.objects.filter(pk=grant.pk), reason="submission_unconfirmed"
        )
        if (
            isinstance(exc, GatewayClientError)
            and exc.status_code is not None
            and 400 <= exc.status_code < 500
        ):
            AgentccBatchJob.objects.filter(pk=batch_ref).update(
                status=AgentccBatchJob.FAILED,
                finished_at=timezone.now(),
                notified=True,
            )
        raise
    return result


def link_submitted(organization, result, grant):
    batch_id = result.get("batch_id") if isinstance(result, dict) else None
    if not isinstance(batch_id, str) or not batch_id.strip() or len(batch_id) > 64:
        raise GatewayClientError(
            "The gateway accepted the batch but returned no batch id"
        )
    with transaction.atomic():
        # Serialize against revocation: an intent created during submission
        # receives the real gateway id here, never a transient memory update.
        grant = ServiceGrant.objects.select_for_update().get(pk=grant.pk)
        job = AgentccBatchJob.objects.select_for_update().get(
            pk=grant.batch_ref, organization=organization
        )
        job.gateway_batch_id = batch_id
        job.save(update_fields=["gateway_batch_id", "updated_at"])
        BatchSubmissionIntent.objects.filter(grant=grant).update(
            resolved_at=timezone.now()
        )
        BatchCancellationIntent.objects.filter(grant=grant).update(
            gateway_batch_id=batch_id, next_attempt_at=timezone.now()
        )
    return job


def finish_grant(job):
    revoke_grants(
        ServiceGrant.objects.filter(kind="batch", batch_ref=job.pk),
        reason="batch_ended",
    )
    now = timezone.now()
    AgentccBatchJob.objects.filter(pk=job.pk).update(key_revoked_at=now)
    job.key_revoked_at = now
    return True
