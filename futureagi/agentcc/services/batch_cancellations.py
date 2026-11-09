"""Retry durable batch cancellations; a successful POST alone is not evidence."""

import time
from datetime import timedelta

from accounts.models.service_grant import BatchCancellationIntent
from django.db.models import F
from django.utils import timezone

from agentcc.models.batch_job import AgentccBatchJob
from agentcc.services.gateway_client import GatewayClientError


def retry_cancellations(client, *, limit=100, deadline=None, now=None):
    from agentcc.services import batch_jobs

    now = now or timezone.now()
    confirmed = 0
    intents = (
        BatchCancellationIntent.objects.using("default")
        .filter(confirmed_at__isnull=True, next_attempt_at__lte=now)
        .order_by("next_attempt_at")[:limit]
    )
    for intent in intents:
        if deadline is not None and time.monotonic() >= deadline:
            break
        # Write retry state before I/O so a worker exit cannot lose the intent.
        BatchCancellationIntent.objects.filter(
            pk=intent.pk, confirmed_at__isnull=True
        ).update(
            attempts=F("attempts") + 1, next_attempt_at=now + timedelta(seconds=30)
        )
        job = AgentccBatchJob.objects.filter(pk=intent.grant.batch_ref).first()
        batch_id = intent.gateway_batch_id
        if not batch_id and job and not job.gateway_batch_id.startswith("pending-"):
            batch_id = job.gateway_batch_id
            BatchCancellationIntent.objects.filter(pk=intent.pk).update(
                gateway_batch_id=batch_id
            )
        if not batch_id and not (
            job and job.status in AgentccBatchJob.TERMINAL_STATUSES
        ):
            try:
                listing = client.list_delegated_batches()
                candidates = (
                    listing.get("batches", []) if isinstance(listing, dict) else []
                )
                for candidate in candidates:
                    if (
                        candidate.get("key_id") == "dlg_" + intent.grant_id
                        and candidate.get("org_id") == str(intent.organization_id)
                        and isinstance(candidate.get("batch_id"), str)
                    ):
                        batch_id = candidate["batch_id"]
                        BatchCancellationIntent.objects.filter(pk=intent.pk).update(
                            gateway_batch_id=batch_id
                        )
                        if job:
                            AgentccBatchJob.objects.filter(pk=job.pk).update(
                                gateway_batch_id=batch_id
                            )
                        break
            except GatewayClientError:
                pass
        terminal = bool(job and job.status in AgentccBatchJob.TERMINAL_STATUSES)
        if not terminal and batch_id:
            try:
                client.cancel_batch(batch_id, org_id=str(intent.organization_id))
            except GatewayClientError:
                pass  # even an ambiguous response must be read back
            try:
                result = client.get_batch(batch_id, org_id=str(intent.organization_id))
            except GatewayClientError as exc:
                terminal = exc.status_code == 404
                if terminal and job:
                    batch_jobs.finish_lost(job, client)
            else:
                terminal = result.get("status") in AgentccBatchJob.TERMINAL_STATUSES
                if terminal and job:
                    batch_jobs.record_status(job, result, client)
        if terminal:
            confirmed += BatchCancellationIntent.objects.filter(
                pk=intent.pk, confirmed_at__isnull=True
            ).update(confirmed_at=now)
    return confirmed
