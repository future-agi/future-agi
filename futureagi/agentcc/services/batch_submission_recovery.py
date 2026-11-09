"""Reconcile the durable submit outbox without ever submitting a batch twice."""

import time
from datetime import timedelta

from django.db.models import F
from django.utils import timezone

from accounts.models.service_grant import BatchSubmissionIntent
from agentcc.models import AgentccBatchJob
from agentcc.services.gateway_client import GatewayClientError
from tfc.routers import primary_required


@primary_required
def recover_submissions(client, *, limit=10, deadline=None):
    from agentcc.services.batch_jobs import finish_lost
    from agentcc.services.delegated_batches import link_submitted

    now = timezone.now()
    intents = list(
        BatchSubmissionIntent.objects.using("default")
        .filter(resolved_at__isnull=True, next_attempt_at__lte=now)
        .select_related("grant")
        .order_by("next_attempt_at")[:limit]
    )
    if not intents or (deadline is not None and time.monotonic() >= deadline):
        return 0
    try:
        listing = client.list_delegated_batches()
    except GatewayClientError:
        return 0  # an unavailable gateway is not an empty authoritative list
    if not isinstance(listing, dict) or not isinstance(listing.get("batches"), list):
        return 0
    candidates = listing["batches"]
    # An entry with no usable identity could be this very submission. Reject
    # the whole listing before resolving any intent, even if another entry is a
    # match; malformed data cannot establish authoritative absence.
    if any(
        not isinstance(item, dict)
        or any(
            not isinstance(item.get(field), str) or not item[field].strip()
            for field in ("org_id", "key_id")
        )
        for item in candidates
    ):
        return 0
    recovered = 0
    for intent in intents:
        if deadline is not None and time.monotonic() >= deadline:
            break
        BatchSubmissionIntent.objects.filter(
            pk=intent.pk, resolved_at__isnull=True
        ).update(
            attempts=F("attempts") + 1, next_attempt_at=now + timedelta(seconds=30)
        )
        job = AgentccBatchJob.objects.filter(pk=intent.grant.batch_ref).first()
        if job is None:
            continue
        # A concurrent successful submit or recovery may already have linked it.
        if not job.gateway_batch_id.startswith("pending-") or job.notified:
            BatchSubmissionIntent.objects.filter(pk=intent.pk).update(resolved_at=now)
            continue
        matches = [
            item
            for item in candidates
            if (
                item.get("key_id") == "dlg_" + intent.grant_id
                and item.get("org_id") == str(intent.grant.org)
            )
        ]
        if len(matches) == 1:
            try:
                link_submitted(job.organization, matches[0], intent.grant)
            except GatewayClientError:
                continue
        elif not matches:
            # The sender's deadline + request validity window have elapsed.
            # Preserve the explicit unknown-outcome notification and revoke grant.
            finish_lost(job, client)
            BatchSubmissionIntent.objects.filter(pk=intent.pk).update(resolved_at=now)
        else:
            continue  # ambiguous identity never selects an arbitrary batch
        recovered += 1
    return recovered
