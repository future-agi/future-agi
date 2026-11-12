"""Shared static credential invalidation through the gateway purge seam.

A shared bearer/API-key credential reaches every gateway replica in the
config push, with its org-and-server slot's grant revision: every new or
rotated secret takes a larger one under the org lock. Revoking, lapsing or
replacing it then calls the same gateway eviction a personal credential uses,
naming the revision the slot is revoked through, so a replica that still holds
an older config can neither finish another call with that secret nor admit it
afresh. The obligation is recorded (``gateway_purge_pending``) before the call
and cleared only when the gateway confirms it; the org's next shared
credential change retries it, and so does the
``agentcc-mcp-shared-evictions-retry`` schedule (``retry_pending``) every
minute. Every eviction runs under the org's config-write lock, which is what
lets one confirmation clear its slot's obligations.
"""

import random
import time

import structlog
from django.db import transaction
from django.db.models import Max

from accounts.models import Organization
from agentcc.models.mcp_credential import AgentccMCPCredential as Credential
from agentcc.services.config_sync_receipts import current_sync_warnings
from agentcc.services.gateway_admin import mcp_credentials as gateway
from agentcc.services.org_config_writer import GATEWAY_SYNC_FAILED, lock_org

logger = structlog.get_logger(__name__)

STATIC_KINDS = (Credential.Kind.BEARER, Credential.Kind.API_KEY)
SHARED_CREDENTIAL_REVOCATION = "shared_credential_revocation"
SHARED_CREDENTIAL_REVOCATION_MESSAGE = (
    "The gateway has not confirmed it revoked the stored key yet."
)

# One tick of ``retry_pending``: at most this many slots, none started past
# this many seconds. The admin client gives up on a call after 10 s, so a tick
# ends well inside its activity's 120 s limit even with the gateway down.
SWEEP_SLOT_LIMIT = 20
SWEEP_ORG_SLOT_LIMIT = 3
REQUEST_RETRY_SLOT_LIMIT = 3
SWEEP_BUDGET_SECONDS = 60
_now = time.monotonic


def _slot(org_id, server_id):
    return Credential.objects.filter(
        organization_id=org_id, server_id=server_id, scope=Credential.Scope.SHARED
    )


def next_revision(org_id, server_id) -> int:
    """The revision a new or rotated shared secret of the slot takes. The
    caller holds the org lock, so no two grants share one."""
    top = _slot(org_id, server_id).aggregate(top=Max("grant_revision"))["top"]
    return (top or 0) + 1


def revoked_through(org_id, server_id) -> int:
    """Every revision of the slot but its active static grant's: below the
    active one when there is one (revisions only grow), else all of them."""
    slot = _slot(org_id, server_id)
    active = slot.filter(
        status=Credential.Status.ACTIVE, kind__in=STATIC_KINDS
    ).aggregate(top=Max("grant_revision"))["top"]
    if active:
        return active - 1
    return slot.aggregate(top=Max("grant_revision"))["top"] or 0


def _evict_slot(org_id, server_id) -> bool | None:
    """Revoke the slot through ``revoked_through``; clear its obligations when
    the gateway confirms. None when there is nothing to revoke."""
    # Only static obligations: a shared OAuth row keeps its own cleanup state.
    pending = _slot(org_id, server_id).filter(
        gateway_purge_pending=True, kind__in=STATIC_KINDS
    )
    through = revoked_through(org_id, server_id)
    if through <= 0:
        pending.update(gateway_purge_pending=False)
        return None
    synced = gateway.purge(str(org_id), server_id, None, revision=through)
    if synced:
        pending.update(gateway_purge_pending=False)
    return synced


def _pending():
    return Credential.objects.filter(
        scope=Credential.Scope.SHARED,
        kind__in=STATIC_KINDS,
        gateway_purge_pending=True,
    ).order_by()


def _retry_org(org_id, done, *, limit=None) -> bool:
    """Retry pending slots other than ``done`` under the caller's org lock.
    A bounded request leaves the remainder to the schedule and returns False
    while any obligation remains unconfirmed."""
    synced = True
    servers = (
        _pending()
        .filter(organization_id=org_id)
        .exclude(server_id__in=done)
        .order_by("server_id")
        .values_list("server_id", flat=True)
        .distinct()
    )
    if limit is not None:
        servers = servers[:limit]
    for server_id in servers:
        synced = _evict_slot(org_id, server_id) is not False and synced
    return synced and not _pending().filter(organization_id=org_id).exists()


def _retry_org_locked(org_id, own_slots) -> bool:
    """Evict every slot this write changed, then only a bounded backlog."""
    with transaction.atomic():
        lock_org(org_id)
        synced = True
        for server_id in own_slots:
            synced = _evict_slot(org_id, server_id) is not False and synced
        others = _retry_org(org_id, own_slots, limit=REQUEST_RETRY_SLOT_LIMIT)
        return synced and others


def _lock_org_unless_held(org_id) -> bool:
    """``lock_org``, unless a config writer holds it now: then False, at once.
    That writer retries the org's evictions itself (``evict``)."""
    return bool(
        list(
            Organization.objects.select_for_update(no_key=True, skip_locked=True)
            .filter(pk=org_id)
            .only("pk")
        )
    )


def retry_pending() -> dict[str, int]:
    """One tick of the retry schedule: the shared static evictions the gateway
    has not confirmed, so none waits for its org's next credential change.

    Bounded: at most ``SWEEP_SLOT_LIMIT`` slots and ``SWEEP_ORG_SLOT_LIMIT``
    per org, none started once
    ``SWEEP_BUDGET_SECONDS`` have passed, in random order so a slot that keeps
    failing cannot starve the rest; whatever is left waits for the next tick
    (``deferred``). Each org's slots run under its config-write lock, and an
    org a writer holds is skipped (``skipped_locked``), never waited on.
    Personal obligations keep their own cleanup (Disconnect)."""
    deadline = _now() + SWEEP_BUDGET_SECONDS
    slots = sorted(set(_pending().values_list("organization_id", "server_id")))
    random.shuffle(slots)
    by_org: dict = {}
    selected = 0
    for org_id, server_id in slots:
        servers = by_org.get(org_id, [])
        if len(servers) >= SWEEP_ORG_SLOT_LIMIT:
            continue
        by_org.setdefault(org_id, servers).append(server_id)
        selected += 1
        if selected >= SWEEP_SLOT_LIMIT:
            break
    result = {
        "orgs": 0,
        "slots": 0,
        "confirmed": 0,
        "pending": 0,
        "skipped_locked": 0,
        "deferred": len(slots) - selected,
        "failed": 0,
    }
    for org_id, servers in by_org.items():
        if _now() >= deadline:
            result["deferred"] += len(servers)
            continue
        try:
            with transaction.atomic():
                if not _lock_org_unless_held(org_id):
                    result["skipped_locked"] += 1
                    continue
                result["orgs"] += 1
                for index, server_id in enumerate(servers):
                    if _now() >= deadline:
                        result["deferred"] += len(servers) - index
                        break
                    result["slots"] += 1
                    synced = _evict_slot(org_id, server_id)
                    result["pending" if synced is False else "confirmed"] += 1
        except Exception:
            result["failed"] += 1
            logger.exception("mcp_shared_eviction_retry_failed", org_id=str(org_id))
    return result


def evict(row) -> bool | None:
    """Revoke the shared static ``row``'s slot on every replica after its
    status or secret changed, and retry the org's other unconfirmed
    evictions. The caller holds the org lock and has written (and pushed)
    the change; whether the gateway confirmed, None when there was nothing to
    revoke."""
    if row.scope != Credential.Scope.SHARED or row.kind not in STATIC_KINDS:
        return None
    if revoked_through(row.organization_id, row.server_id) <= 0:
        return None
    row.gateway_purge_pending = True
    row.save(update_fields=["gateway_purge_pending"])
    synced = _evict_slot(row.organization_id, row.server_id)
    row.refresh_from_db(fields=["gateway_purge_pending"])
    others = _retry_org(
        row.organization_id, [row.server_id], limit=REQUEST_RETRY_SLOT_LIMIT
    )
    return synced is True and others


def revoke_removed_references(org, stored, value) -> None:
    """Retire shared static grants the active config stops sending.

    All writers and version activations call this under the org lock. Use the
    vault's push predicate (including aliases, managed slots and origin), not
    raw JSON equality: metadata edits must not revoke a retained credential.
    Explicit vault operations may already have retired the row; those own
    their obligation. Never touch personal/OAuth rows or a successor grant.
    """
    if stored == value:
        return
    from agentcc.services.mcp_credentials import pushes_credential

    removed = [
        row
        for row in Credential.objects.filter(
            organization=org,
            scope=Credential.Scope.SHARED,
            kind__in=STATIC_KINDS,
            status=Credential.Status.ACTIVE,
        ).only("pk", "organization_id", "server_id", "scope", "kind", "origin")
        if pushes_credential(stored, row) and not pushes_credential(value, row)
    ]
    if removed:
        Credential.objects.filter(pk__in=[row.pk for row in removed]).update(
            status=Credential.Status.REVOKED
        )
        evict_after_commit(removed)


def evict_after_commit(rows):
    """Record an obligation now, then attempt it only after the save commits.

    The writer's receipt stays unsynced until eviction is confirmed. A refused
    write rolls back the obligation and discards the callback. A callback
    failure cannot undo a committed save; it leaves the obligation for retry.
    """
    warnings = current_sync_warnings()
    rows = [
        row
        for row in rows
        if row.scope == Credential.Scope.SHARED and row.kind in STATIC_KINDS
    ]
    if not rows:
        return warnings
    Credential.objects.filter(pk__in=[row.pk for row in rows]).update(
        gateway_purge_pending=True
    )
    # Keep the writer's unsynced code until confirmation, but distinguish this
    # post-commit revocation from a failed config PUT in the customer message.
    pending = {
        "code": GATEWAY_SYNC_FAILED,
        "operation": SHARED_CREDENTIAL_REVOCATION,
        "message": SHARED_CREDENTIAL_REVOCATION_MESSAGE,
    }
    warnings.append(pending)
    slots_by_org = {}
    for row in rows:
        slots_by_org.setdefault(row.organization_id, set()).add(row.server_id)

    # Under the org lock again: one confirmation clears every obligation of
    # its slot, so it must not overlap a writer recording a newer one. Preserve
    # all of this write's slots even when they exceed the backlog retry cap.
    def finish():
        confirmed = True
        for org_id in sorted(slots_by_org, key=str):
            try:
                synced = _retry_org_locked(org_id, sorted(slots_by_org[org_id]))
            except Exception:
                logger.exception(
                    "mcp_shared_eviction_after_commit_failed", org_id=str(org_id)
                )
                confirmed = False
            else:
                if not synced:
                    logger.warning(
                        "mcp_shared_eviction_after_commit_pending", org_id=str(org_id)
                    )
                    confirmed = False
        if confirmed:
            # Remove only this callback's receipt, not an equal push-failure
            # warning or another callback's unconfirmed obligation.
            warnings[:] = [warning for warning in warnings if warning is not pending]

    transaction.on_commit(finish)
    return warnings
