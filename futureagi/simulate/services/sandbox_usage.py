"""Time chat, rebuild and fix-engine sandboxes, and bill each once its end is known."""

from __future__ import annotations

import logging
from datetime import timedelta
from uuid import UUID, uuid5

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from simulate.models import (
    AgentFixRun,
    HostedHarnessConversationLease,
    HostedSandboxLifetime,
)
from simulate.services.harness_usage import (
    OUR_FIX_FAILURES,
    billable_sandbox_minutes,
    reached_its_lifetime,
)
from tfc.ee_gating import is_oss

logger = logging.getLogger(__name__)

# A sandbox nobody confirmed deleted is closed this long after its lifetime limit.
SWEEP_GRACE = timedelta(minutes=10)
# How long a chat sandbox's charge waits, after it ended, for a rebuild turn that ran in it.
TURN_WAIT = timedelta(hours=24)
LIFETIME_ELAPSED = "lifetime_elapsed"


def open_sandbox_lifetime(
    *,
    kind: str,
    provider_name: str,
    provider_ref: str,
    organization_id,
    workspace_id,
    owner_id,
    lifetime_seconds: int,
    armed_at,
) -> None:
    """Start timing a sandbox just created; a repeat for the same sandbox changes nothing."""
    if is_oss():
        return
    try:
        with transaction.atomic():
            HostedSandboxLifetime.no_workspace_objects.get_or_create(
                provider_ref=provider_ref,
                provider=provider_name,
                defaults={
                    "organization_id": organization_id,
                    "workspace_id": workspace_id,
                    "kind": kind,
                    "owner_id": owner_id,
                    "started_at": timezone.now(),
                    "lifetime_seconds": int(lifetime_seconds),
                    "armed_at": armed_at,
                    "lifetime_ends_at": armed_at
                    + timedelta(seconds=int(lifetime_seconds)),
                },
            )
    except Exception:  # noqa: BLE001 - metering never fails a create
        logger.exception("sandbox lifetime not opened kind=%s owner=%s", kind, owner_id)


def _open_row(provider_ref: str):
    return (
        HostedSandboxLifetime.no_workspace_objects.select_for_update()
        .filter(provider_ref=provider_ref, ended_at__isnull=True)
        .first()
    )


def rearm_sandbox_lifetime(
    *, provider, provider_ref: str, lifetime_seconds: int, renewed_at
) -> None:
    """Record a renewed lifetime limit, when the provider's renewal restarts it."""
    if not getattr(provider, "renews_ttl", False):
        return
    try:
        with transaction.atomic():
            row = _open_row(provider_ref)
            if row is None:
                return
            row.armed_at = renewed_at
            row.lifetime_seconds = int(lifetime_seconds)
            row.lifetime_ends_at = renewed_at + timedelta(seconds=int(lifetime_seconds))
            row.save(
                update_fields=[
                    "armed_at",
                    "lifetime_seconds",
                    "lifetime_ends_at",
                    "updated_at",
                ]
            )
    except Exception:  # noqa: BLE001 - metering never fails a renewal
        logger.exception("sandbox lifetime not updated")


def fix_engine_died_unseen(*, state, heartbeat_at, ended_at) -> bool:
    """Whether a fix's engine was silent longer than its watch allows at ``ended_at``:
    it died before a watch saw it. Never for a stopping or stopped fix: an engine cannot
    report once its fix is stopping, so ``request_stop`` calls this with the stop moment
    before it changes the fix's state, and nothing after the stop measures it again."""
    if state in (AgentFixRun.State.STOPPING, AgentFixRun.State.STOPPED):
        return False
    return (
        heartbeat_at is not None
        and ended_at is not None
        and ended_at - heartbeat_at
        > timedelta(seconds=settings.AGENT_FIX_HEARTBEAT_TIMEOUT_SECONDS)
    )


def _note_reason(row, *, reason: str, failure_domain: str, last_seen, now) -> None:
    """First reason wins; the first failure of ours is never lost. A sandbox found gone
    after living to its lifetime limit expired, which is charged, not ours."""
    if failure_domain and reached_its_lifetime(
        cap=row.lifetime_ends_at, last_seen=last_seen, found_at=now
    ):
        reason, failure_domain = LIFETIME_ELAPSED, ""
    if not row.end_reason:
        row.end_reason = reason
    if failure_domain and not row.failure_domain:
        row.failure_domain = failure_domain


def record_sandbox_end_reason(
    *, provider_ref: str, reason: str, failure_domain: str = "", last_seen=None
) -> bool:
    """Why a sandbox is ending, recorded before its deletion. ``last_seen``, the last
    moment the platform saw it alive, is read only for an ending of ours. Returns False
    only when the write failed; True when it was written, or no open row was there to
    write to."""
    try:
        with transaction.atomic():
            row = _open_row(provider_ref)
            if row is None:
                return True
            _note_reason(
                row,
                reason=reason,
                failure_domain=failure_domain,
                last_seen=last_seen,
                now=timezone.now(),
            )
            row.save(update_fields=["end_reason", "failure_domain", "updated_at"])
    except Exception:  # noqa: BLE001 - metering never fails a delete
        logger.exception("sandbox lifetime not updated")
        return False
    return True


def close_sandbox_lifetime(
    *,
    provider_ref: str,
    reason: str = "deleted",
    failure_domain: str = "",
    last_seen=None,
) -> None:
    """Stop timing a sandbox whose deletion is confirmed, and bill it after commit. The
    ending is applied as ``record_sandbox_end_reason`` does, so it holds even when the
    write before the deletion was lost."""
    try:
        from simulate.tasks.hosted_harness_usage import schedule_sandbox_lifetime_seal

        with transaction.atomic():
            row = _open_row(provider_ref)
            if row is None:
                return
            now = timezone.now()
            _note_reason(
                row,
                reason=reason,
                failure_domain=failure_domain,
                last_seen=last_seen,
                now=now,
            )
            row.ended_at = now
            row.save(
                update_fields=["ended_at", "end_reason", "failure_domain", "updated_at"]
            )
            row_id = str(row.id)
            transaction.on_commit(lambda: schedule_sandbox_lifetime_seal(row_id))
    except Exception:  # noqa: BLE001 - metering never fails a delete
        logger.exception("sandbox lifetime not closed")


def _lease_of(provider_ref: str):
    """(state, last heartbeat) of the chat lease that still carries this sandbox, or None."""
    return (
        HostedHarnessConversationLease._base_manager.filter(provider_ref=provider_ref)
        .values_list("state", "heartbeat_at")
        .first()
    )


def seal_sandbox_lifetime(lifetime_id: str) -> str:
    """Bill one ended sandbox, once. Returns "settled", "waiting" or "skipped"."""
    from simulate.services.harness_versioning import rebuild

    with transaction.atomic():
        row = (
            HostedSandboxLifetime.no_workspace_objects.select_for_update()
            .filter(id=lifetime_id)
            .first()
        )
        if row is None or row.ended_at is None or row.settled_at is not None:
            return "skipped"
        now = timezone.now()
        domains, excluded = [row.failure_domain or None], ()
        if row.kind == HostedSandboxLifetime.Kind.PLATFORM_CHECK:
            # Started by the platform to check its own build, not asked for by the customer.
            domains.append("infrastructure")
        if row.kind == HostedSandboxLifetime.Kind.CHAT:
            turns = rebuild.failed_turn_intervals(
                conversation_id=row.owner_id,
                provider_ref=row.provider_ref,
                # It ran to its lifetime limit: a rebuild turn running then was cut off.
                cut_off_at=(
                    row.lifetime_ends_at
                    if row.ended_at >= row.lifetime_ends_at
                    else None
                ),
            )
            if turns.pending and row.ended_at > now - TURN_WAIT:
                # Moves the row behind the others in the sweep's queue.
                row.save(update_fields=["updated_at"])
                return "waiting"
            excluded = turns.intervals
            # Whoever closed the row recorded an expiry only after the last-seen test
            # (the retire, the abandon, or the sweep's expire pass below).
            if (
                turns.sandbox_failed_at is not None
                and row.end_reason != LIFETIME_ELAPSED
            ):
                # The rebuild running in it was lost: the whole sandbox is free, and the
                # row says why.
                domains.append("infrastructure")
                row.end_reason, row.failure_domain = "rebuild_stuck", "infrastructure"
        minutes = billable_sandbox_minutes(
            provider=row.provider,
            failure_domains=domains,
            started_at=row.started_at,
            ended_at=row.ended_at,
            armed_at=row.armed_at,
            lifetime_seconds=row.lifetime_seconds,
            excluded=excluded,
        )
        row.billed_minutes = minutes
        row.settled_at = now
        row.save(
            update_fields=[
                "billed_minutes",
                "settled_at",
                "end_reason",
                "failure_domain",
                "updated_at",
            ]
        )
        if minutes and not is_oss():
            _emit_lifetime(row, minutes)
    return "settled"


def _emit_lifetime(row: HostedSandboxLifetime, minutes) -> None:
    from ee.usage.schemas.event_types import BillingEventType
    from ee.usage.schemas.events import UsageEvent
    from ee.usage.services.emitter import emit

    # Raises when the event did not reach the stream: settled_at rolls back with it.
    emit(
        UsageEvent(
            event_id=str(uuid5(UUID(str(row.id)), "sandbox_runtime")),
            org_id=str(row.organization_id),
            event_type=BillingEventType.SANDBOX_RUNTIME,
            amount=float(minutes),
            timestamp=min(row.ended_at, row.lifetime_ends_at),
            properties={
                "source": "hosted_sandbox",
                "source_id": str(row.id),
                "sandbox_kind": row.kind,
                "owner_id": str(row.owner_id),
                "workspace_id": str(row.workspace_id or ""),
                "provider": row.provider,
                "failure_domain": row.failure_domain or None,
            },
        ),
        raise_on_failure=True,
    )


def sweep_sandbox_lifetimes(*, limit: int = 200) -> dict[str, int]:
    """Close sandboxes whose deletion was never confirmed once their lifetime limit has
    passed, then bill every ended, unsettled sandbox, least recently tried first."""
    now = timezone.now()
    counts = {"expired": 0, "settled": 0, "waiting": 0, "errors": 0}
    overdue = list(
        HostedSandboxLifetime.no_workspace_objects.filter(
            ended_at__isnull=True, lifetime_ends_at__lte=now - SWEEP_GRACE
        )
        .order_by("lifetime_ends_at")
        .values_list("id", "kind", "provider_ref", "owner_id")[:limit]
    )
    for row_id, kind, provider_ref, owner_id in overdue:
        try:
            fix_code, fix_seen, fix_died_unseen = None, None, False
            if kind == HostedSandboxLifetime.Kind.FIX_ENGINE:
                fix = (
                    AgentFixRun.no_workspace_objects.filter(id=owner_id)
                    .values_list("state", "failure", "heartbeat_at", "finished_at")
                    .first()
                )
                fix_state, fix_failure, fix_seen, fix_ended = fix or (None,) * 4
                if fix_state in AgentFixRun.ACTIVE_STATES:
                    # Its fix is still going: the fix's own release, on a relaunch or at
                    # its end, records how this sandbox ended.
                    continue
                fix_code = (fix_failure or {}).get("code")
                fix_died_unseen = fix_engine_died_unseen(
                    state=fix_state, heartbeat_at=fix_seen, ended_at=fix_ended
                )
            lease = (
                _lease_of(provider_ref)
                if kind == HostedSandboxLifetime.Kind.CHAT
                else None
            )
            with transaction.atomic():
                row = (
                    HostedSandboxLifetime.no_workspace_objects.select_for_update()
                    .filter(id=row_id, ended_at__isnull=True)
                    .first()
                )
                if row is None:
                    # A close that won the race keeps its own end and reason.
                    continue
                cap = row.lifetime_ends_at
                if lease is not None and lease[0] in (
                    HostedHarnessConversationLease.State.ACTIVE,
                    HostedHarnessConversationLease.State.STARTING,
                ):
                    # A process that stopped reporting, or never finished starting, well
                    # before the limit died of our failure, whatever ending was recorded
                    # first. A fenced-out lease stops reporting while its sandbox lives, so
                    # it is not read.
                    _note_reason(
                        row,
                        reason="chat_runtime_stopped",
                        failure_domain="infrastructure",
                        last_seen=lease[1],
                        now=cap,
                    )
                if fix_code in OUR_FIX_FAILURES:
                    # The fix ended on our failure: free, as its own release records it,
                    # also after an expiry.
                    _note_reason(
                        row,
                        reason=fix_code,
                        failure_domain="infrastructure",
                        last_seen=None,
                        now=cap,
                    )
                elif fix_died_unseen:
                    # Its engine died unseen before the fix ended: free, as its own
                    # release records it, unless it still reported at the limit.
                    _note_reason(
                        row,
                        reason="engine_gone",
                        failure_domain="infrastructure",
                        last_seen=fix_seen,
                        now=cap,
                    )
                _note_reason(
                    row,
                    reason=LIFETIME_ELAPSED,
                    failure_domain="",
                    last_seen=None,
                    now=cap,
                )
                # One write: the end and its reason land together or not at all.
                row.ended_at = cap
                row.save(
                    update_fields=[
                        "ended_at",
                        "end_reason",
                        "failure_domain",
                        "updated_at",
                    ]
                )
            counts["expired"] += 1
        except Exception:  # noqa: BLE001 - one bad row never stops the others
            logger.exception("sandbox lifetime not expired lifetime=%s", row_id)
            counts["errors"] += 1
    unsettled = list(
        HostedSandboxLifetime.no_workspace_objects.filter(
            ended_at__isnull=False,
            settled_at__isnull=True,
            ended_at__lte=now - SWEEP_GRACE,
        )
        .order_by("updated_at")
        .values_list("id", flat=True)[:limit]
    )
    for row_id in unsettled:
        try:
            outcome = seal_sandbox_lifetime(str(row_id))
        except Exception:  # noqa: BLE001 - one bad row never stops the others
            logger.exception("sandbox lifetime seal failed lifetime=%s", row_id)
            counts["errors"] += 1
            # Behind the rows not yet tried, so it cannot hold the head of the queue.
            HostedSandboxLifetime.no_workspace_objects.filter(id=row_id).update(
                updated_at=timezone.now()
            )
            continue
        if outcome in ("settled", "waiting"):
            counts[outcome] += 1
    return counts
