"""Console batch jobs: run as the caller's org, batch.completed once.

The gateway's /-/batches runs each item as a chat request authenticated by
the API key the submit names in ``x-agentcc-batch-key``. A console batch gets
a key minted here for the caller's org (``console-batch-<uuid>``), stored by
hash with a 24 h ``expires_at`` so the gateway's key sync keeps it while the
batch runs and then carries its expiry. ``poll_open_batches`` also revokes a
minted key 24 h after its batch was submitted; the key is revoked earlier, at
the batch's first terminal status, whichever of get-batch or the poll sees it.

The first time either sees a batch completed, failed or cancelled, the org's
webhooks subscribed to ``batch.completed`` get one event each, delivered
through ``webhook_delivery`` (retries are its own).
"""

import hashlib
import time
import uuid
from datetime import timedelta

import structlog
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from agentcc.models import AgentccAPIKey, AgentccBatchJob
from agentcc.services.auth_bridge import bound_key_metadata
from agentcc.services.gateway_client import GatewayClientError, get_gateway_client

logger = structlog.get_logger(__name__)

BATCH_KEY_TTL = timedelta(hours=24)
BATCH_KEY_NAME_PREFIX = "console-batch-"
BATCH_KEY_PURPOSE = "console-batch"
BATCH_COMPLETED_EVENT = "batch.completed"
POLL_LIMIT = 500
# Seconds one poll run asks the gateway for: each call can take the client's
# 10 s timeout, and the activity is killed at 300 s.
POLL_TIME_BUDGET_SECONDS = 120.0
LOST_BATCH_ERROR = (
    "The gateway does not hold this batch (it restarted, or the batch's "
    "results expired), so its outcome is unknown."
)
RESULTS_GONE_ERROR = (
    "The gateway does not hold this batch (it keeps a finished batch for an "
    "hour, and a restart loses it), so its results and summary are gone; the "
    "status is the one recorded when it finished."
)


class BatchKeyUnavailable(Exception):
    """The gateway did not mint a usable key for the batch."""


def mint_batch_key(organization, user=None, client=None):
    """Mint the key a console batch runs as, for ``organization``.

    Returns ``(AgentccAPIKey, raw_key)``. The key is created on the gateway
    (``gateway_client.create_key``) and stored here by hash with a 24 h
    expiry, so key syncs keep it; if storing fails it is revoked on the
    gateway again. The raw key is returned once and never stored.
    """
    client = client or get_gateway_client()
    name = f"{BATCH_KEY_NAME_PREFIX}{uuid.uuid4()}"
    owner = (getattr(user, "email", "") or "") if user is not None else ""
    metadata = bound_key_metadata({"purpose": BATCH_KEY_PURPOSE}, organization.id)
    result = client.create_key(name=name, owner=owner, metadata=metadata)
    key_id = result.get("id") if isinstance(result, dict) else None
    raw_key = result.get("key") if isinstance(result, dict) else None
    if not isinstance(key_id, str) or not key_id:
        raise BatchKeyUnavailable("The gateway did not return an id for the batch key")
    if not isinstance(raw_key, str) or not raw_key:
        _revoke_on_gateway(client, key_id)
        raise BatchKeyUnavailable("The gateway did not return the batch key")
    try:
        api_key = AgentccAPIKey.no_workspace_objects.create(
            organization=organization,
            workspace=None,
            user=user if getattr(user, "pk", None) else None,
            gateway_key_id=key_id,
            key_prefix=str(result.get("key_prefix") or "")[:20],
            key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
            name=name,
            owner=owner,
            status=AgentccAPIKey.ACTIVE,
            metadata=metadata,
            expires_at=timezone.now() + BATCH_KEY_TTL,
        )
    except Exception:
        _revoke_on_gateway(client, key_id)
        raise
    return api_key, raw_key


def record_submitted(organization, result, api_key, user=None):
    """Record the batch the gateway accepted, as ``organization``'s."""
    batch_id = result.get("batch_id") if isinstance(result, dict) else None
    if not isinstance(batch_id, str) or not batch_id:
        raise GatewayClientError(
            "The gateway accepted the batch but returned no batch id"
        )
    total = result.get("total")
    return AgentccBatchJob.objects.create(
        organization=organization,
        created_by=user if getattr(user, "pk", None) else None,
        gateway_batch_id=batch_id,
        gateway_key_id=api_key.gateway_key_id,
        status=str(result.get("status") or AgentccBatchJob.PROCESSING)[:16],
        total=total if isinstance(total, int) and total >= 0 else 0,
    )


def job_for(organization, batch_id):
    """The organization's batch job with gateway id ``batch_id``, or None."""
    if organization is None or not batch_id:
        return None
    return AgentccBatchJob.objects.filter(
        organization=organization, gateway_batch_id=batch_id
    ).first()


def revoke_minted_key(api_key, client=None):
    """Revoke a minted key whose batch never started (a refused submit)."""
    client = client or get_gateway_client()
    _revoke_on_gateway(client, api_key.gateway_key_id)
    if api_key.status != AgentccAPIKey.REVOKED:
        api_key.status = AgentccAPIKey.REVOKED
        api_key.save(update_fields=["status", "updated_at"])


def record_status(job, result, client=None):
    """Record what the gateway answered for ``job``'s batch.

    On the first terminal status (completed, failed, cancelled) the job is
    finished: batch.completed is sent once and the minted key revoked.
    Returns True when this call sent batch.completed.
    """
    status = result.get("status") if isinstance(result, dict) else None
    if status in AgentccBatchJob.TERMINAL_STATUSES:
        return _finish(job, status, result, client)
    if isinstance(status, str) and status and status != job.status and not job.notified:
        AgentccBatchJob.objects.filter(pk=job.pk, notified=False).update(
            status=status[:16], updated_at=timezone.now()
        )
        job.status = status[:16]
    return False


def is_past_key_lifetime(job, now=None):
    """True once ``job``'s minted key has outlived its 24 h lifetime."""
    return job.created_at <= (now or timezone.now()) - BATCH_KEY_TTL


def finish_lost(job, client):
    """Finish ``job``, whose batch the gateway does not hold, as failed
    with an unknown outcome. Returns True when this call claimed it."""
    lost = {
        "status": AgentccBatchJob.FAILED,
        "outcome": "unknown",
        "error": LOST_BATCH_ERROR,
    }
    return _finish(job, AgentccBatchJob.FAILED, lost, client)


def recorded_result(job):
    """get-batch's answer for a finished ``job`` the gateway does not hold:
    what was recorded when it finished. Its results and summary went with
    the gateway's copy; ``error`` says so (and ``outcome`` is ``unknown``
    for a batch the gateway lost before it was seen to end)."""
    result = {
        "batch_id": job.gateway_batch_id,
        "org_id": str(job.organization_id),
        "key_id": job.gateway_key_id,
        "status": job.status,
        "total": job.total,
        "created_at": job.created_at.isoformat(),
    }
    if job.finished_at is not None:
        result["completed_at"] = job.finished_at.isoformat()
    if job.status == AgentccBatchJob.FAILED:
        result["outcome"] = "unknown"
        result["error"] = LOST_BATCH_ERROR
    else:
        result["error"] = RESULTS_GONE_ERROR
    return result


def answer_for_missing_batch(job, exc, client=None):
    """What get-batch answers when the gateway refused ``job``'s batch with
    ``exc``; None to answer the gateway's error unchanged.

    Only a 404 (the gateway does not hold the batch) gets an answer: a
    finished job answers its recorded status (``recorded_result``); an open
    job past its key's 24 h lifetime is first finished as failed with an
    unknown outcome, as the poll does (batch.completed once, key revoked).
    An open job within that lifetime gets None: the 404 may be passing (a
    restart in progress, another replica), and the poll keeps asking.
    """
    if getattr(exc, "status_code", None) != 404:
        return None
    if not job.notified:
        if not is_past_key_lifetime(job):
            return None
        if not finish_lost(job, client or get_gateway_client()):
            job.refresh_from_db()  # another caller finished it first
    return recorded_result(job)


def poll_open_batches(
    client=None,
    limit=POLL_LIMIT,
    time_budget=POLL_TIME_BUDGET_SECONDS,
    clock=time.monotonic,
):
    """Ask the gateway about every batch not yet reported, and revoke minted
    keys whose batch ended or which are past their 24 h lifetime.

    A batch the gateway does not hold (404) stays open while its key could
    still run it, then is finished as failed with an unknown outcome.

    Least recently polled first: each job is touched (``updated_at``) before
    the gateway is asked, so batches the gateway lost cannot starve newer
    ones, and a run killed mid-call still moves on. A run stops asking the
    gateway after ``time_budget`` seconds (``clock``), or at the first answer
    that shows it unreachable (no HTTP status); minted keys due for revoking
    are then still revoked here, and on the gateway by a later run.
    Returns counts for the activity's log; ``stopped`` names why a run
    stopped early ("time_budget", "unreachable"), else None.
    """
    client = client or get_gateway_client()
    deadline = clock() + time_budget
    now = timezone.now()
    expired_before = now - BATCH_KEY_TTL
    counts = {
        "checked": 0,
        "notified": 0,
        "lost": 0,
        "keys_revoked": 0,
        "errors": 0,
        "stopped": None,
    }

    open_jobs = list(
        AgentccBatchJob.objects.filter(notified=False).order_by(
            "updated_at", "created_at"
        )[:limit]
    )
    for job in open_jobs:
        if clock() >= deadline:
            counts["stopped"] = "time_budget"
            break
        counts["checked"] += 1
        _touch(job)
        try:
            result = client.get_batch(
                job.gateway_batch_id, org_id=str(job.organization_id)
            )
        except GatewayClientError as exc:
            if exc.status_code is None:
                counts["errors"] += 1
                counts["stopped"] = "unreachable"
                logger.warning(
                    "agentcc_batch_poll_gateway_unreachable",
                    batch_id=job.gateway_batch_id,
                    error=str(exc),
                )
                break
            if exc.status_code == 404 and is_past_key_lifetime(job, now):
                if finish_lost(job, client):
                    counts["notified"] += 1
                    counts["lost"] += 1
                continue
            counts["errors"] += 1
            logger.warning(
                "agentcc_batch_poll_failed",
                batch_id=job.gateway_batch_id,
                org_id=str(job.organization_id),
                status_code=exc.status_code,
            )
            continue
        except Exception:
            counts["errors"] += 1
            logger.exception("agentcc_batch_poll_failed", batch_id=job.gateway_batch_id)
            continue
        try:
            if record_status(job, result, client):
                counts["notified"] += 1
        except Exception:
            counts["errors"] += 1
            logger.exception(
                "agentcc_batch_finish_failed", batch_id=job.gateway_batch_id
            )

    # Keys still live: a finished batch whose revoke did not reach the gateway,
    # or any batch past the key's lifetime. Once the run stopped, keys are
    # only revoked here (no key sync hands them back); the gateway is asked
    # by a later run.
    pending = (
        AgentccBatchJob.objects.filter(key_revoked_at__isnull=True)
        .filter(Q(notified=True) | Q(created_at__lte=expired_before))
        .order_by("updated_at", "created_at")[:limit]
    )
    for job in pending:
        if counts["stopped"] is None and clock() >= deadline:
            counts["stopped"] = "time_budget"
        revoked = _revoke_job_key(job, client, ask_gateway=counts["stopped"] is None)
        if revoked:
            counts["keys_revoked"] += 1
            continue
        if revoked is None:
            counts["stopped"] = "unreachable"
        _touch(job)
    return counts


def _touch(job):
    """Mark ``job`` as just polled (the poll asks the least recent first)."""
    now = timezone.now()
    AgentccBatchJob.objects.filter(pk=job.pk).update(updated_at=now)
    job.updated_at = now


def _finish(job, status, result, client):
    """Finish ``job`` at terminal ``status``: claim the notification (one
    caller wins), create the batch.completed events with the claim, deliver
    them, and revoke the minted key. Returns True when this call claimed it."""
    now = timezone.now()
    event_ids = []
    with transaction.atomic():
        claimed = AgentccBatchJob.objects.filter(pk=job.pk, notified=False).update(
            notified=True, status=status, finished_at=now, updated_at=now
        )
        if claimed:
            event_ids = _create_batch_completed_events(job, status, result, now)
    if claimed:
        job.notified, job.status, job.finished_at = True, status, now
        logger.info(
            "agentcc_batch_finished",
            batch_id=job.gateway_batch_id,
            org_id=str(job.organization_id),
            status=status,
            webhook_events=len(event_ids),
        )
        if event_ids:
            from agentcc.services.webhook_delivery import deliver_webhook_events

            try:
                deliver_webhook_events(
                    event_ids=event_ids,
                    org_id=job.organization_id,
                    limit=len(event_ids),
                )
            except Exception:
                logger.exception(
                    "agentcc_batch_completed_delivery_failed",
                    batch_id=job.gateway_batch_id,
                )
    if job.key_revoked_at is None:
        _revoke_job_key(job, client)
    return bool(claimed)


def _create_batch_completed_events(job, status, result, now):
    from agentcc.models.webhook import AgentccWebhook, AgentccWebhookEvent

    hooks = [
        hook
        for hook in AgentccWebhook.no_workspace_objects.filter(
            organization_id=job.organization_id, is_active=True, deleted=False
        )
        if BATCH_COMPLETED_EVENT in (hook.events or [])
    ]
    if not hooks:
        return []
    payload = _batch_completed_payload(job, status, result, now)
    events = AgentccWebhookEvent.no_workspace_objects.bulk_create(
        [
            AgentccWebhookEvent(
                organization_id=job.organization_id,
                webhook=hook,
                event_type=BATCH_COMPLETED_EVENT,
                payload=payload,
                status=AgentccWebhookEvent.PENDING,
            )
            for hook in hooks
        ]
    )
    return [event.id for event in events]


def _batch_completed_payload(job, status, result, now):
    result = result if isinstance(result, dict) else {}
    total = result.get("total")
    payload = {
        "event": BATCH_COMPLETED_EVENT,
        "batch_id": job.gateway_batch_id,
        "org_id": str(job.organization_id),
        "status": status,
        "total": total if isinstance(total, int) else job.total,
        "created_at": result.get("created_at") or job.created_at.isoformat(),
        "completed_at": result.get("completed_at") or now.isoformat(),
    }
    if isinstance(result.get("summary"), dict):
        payload["summary"] = result["summary"]
    for field in ("outcome", "error"):
        if result.get(field):
            payload[field] = result[field]
    return payload


def _revoke_job_key(job, client, ask_gateway=True):
    """Revoke ``job``'s minted key here and, with ``ask_gateway``, on the
    gateway. The job records the revoke only once the gateway confirmed it
    (or does not hold the key), so a gateway that was unreachable is asked
    again by the next poll; meanwhile the stored row is revoked, so no key
    sync hands the key back. Returns True when the gateway confirmed, None
    when it was unreachable, else False."""
    row = AgentccAPIKey.no_workspace_objects.filter(
        organization_id=job.organization_id, gateway_key_id=job.gateway_key_id
    ).first()
    if row is not None and row.status != AgentccAPIKey.REVOKED:
        row.status = AgentccAPIKey.REVOKED
        row.save(update_fields=["status", "updated_at"])
    if not ask_gateway:
        return False
    outcome = _revoke_on_gateway(client, job.gateway_key_id)
    if outcome is not True:
        return outcome
    now = timezone.now()
    AgentccBatchJob.objects.filter(pk=job.pk, key_revoked_at__isnull=True).update(
        key_revoked_at=now, updated_at=now
    )
    job.key_revoked_at = now
    return True


def _revoke_on_gateway(client, key_id):
    """Revoke ``key_id`` on the gateway; True when it is revoked or the
    gateway does not hold it (404), None when the gateway was unreachable
    (no HTTP status), False when it refused."""
    try:
        client.revoke_key(key_id)
        return True
    except GatewayClientError as exc:
        if exc.status_code == 404:
            return True
        logger.warning(
            "agentcc_batch_key_revoke_failed",
            key_id=key_id,
            status_code=exc.status_code,
        )
        return None if exc.status_code is None else False
