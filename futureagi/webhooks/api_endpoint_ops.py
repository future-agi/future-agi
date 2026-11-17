"""Endpoint operations: turn on or off, resume a paused backlog, rotate the secret, end an overlap (DESIGN.md sections
5.3, 7.7, 9 and 10).

**Lock order** (``api_locks``): the organization's API lock, then the endpoint's state row, then
the endpoint row, re-read under the lock, as ``store_settle.disable_endpoint`` does, so a claim waiting on that row sees
the turn-off and pauses instead of leasing. Every check that depends on the row or the policy (turning on re-checks
the URL against the allowlists) runs on the row as it is under the lock. The backlog is paused after the
commit, in SKIP LOCKED batches (``store_settle.pause_backlog``). Resume takes delivery rows (SKIP LOCKED) and then the
state row, the dispatcher's order.
"""

from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.db.models import F
from django.db.models.functions import Greatest
from django.utils import timezone

from webhooks import audit, crypto
from webhooks.api_errors import WebhookApiError
from webhooks.api_locks import lock_state, locked_endpoint
from webhooks.api_validation import check_secret, check_url, note_write, require_keys
from webhooks.core.signing import DEFAULT_OVERLAP_S, new_secret, secret_tail
from webhooks.models import Delivery, Endpoint, EndpointState, Secret
from webhooks.permissions import ADMIN, MANAGE, may_still_manage
from webhooks.publish import notify
from webhooks.store_settle import pause_backlog

RESUME_BATCH = 500


def turn_off_manual(endpoint_id: str, *, reason: str, actor_id, now) -> bool:
    """``disabled_manual`` (by an admin, or ``creator_removed`` by the offboarding check); True when it changed. The
    caller pauses the backlog after its transaction commits."""
    lock_state(endpoint_id)
    return bool(
        Endpoint.objects.filter(id=endpoint_id, deleted=False)
        .exclude(status="disabled_manual")
        .update(
            status="disabled_manual",
            disabled_reason=reason,
            disabled_at=now,
            disabled_by_id=actor_id,
        )
    )


def _takes_ownership(caller, fresh: Endpoint) -> bool:
    """The person who turns an endpoint on becomes its owner when the owner may no longer manage it: after
    ``creator_removed``, and whenever the owner's account is gone or has lost the level, so the sweep
    does not turn it off again for an owner who left while it was off for another reason."""
    if fresh.owner_id == caller.user_id:
        return False
    if fresh.disabled_reason == "creator_removed" or fresh.owner_id is None:
        return True
    return not may_still_manage(fresh.owner_id, fresh)[0]


def set_enabled(caller, ep: Endpoint, on: bool, note: str = "") -> Endpoint:
    caller.require(MANAGE, ep.workspace_id)
    now = timezone.now()
    if on:
        require_keys()
    changed = False
    with transaction.atomic():
        note_write(caller, ep.workspace_id, ep)
        fresh = locked_endpoint(ep.id, caller.organization_id)
        if not on:
            changed = turn_off_manual(
                ep.id, reason="manual", actor_id=caller.user_id, now=now
            )
            if changed:
                audit.record(
                    organization=caller.organization,
                    action=audit.ENDPOINT_DISABLED,
                    target=ep.id,
                    actor=caller.user,
                    changes={
                        "status": {"before": fresh.status, "after": "disabled_manual"}
                    },
                    metadata={"reason": "manual", "note": note[:500]},
                )
        elif fresh.status != "enabled":
            # The URL must pass the rules and the allowlists as they are now, under the lock, so a policy change
            # that commits meanwhile is either seen here or re-applied after this commits (a blocked one stays
            # blocked).
            check_url(
                fresh.organization_id,
                fresh.workspace_id,
                crypto.decrypt(fresh.url_encrypted) or "",
            )
            was = (fresh.status, fresh.disabled_reason)
            fields = {
                "status": "enabled",
                "disabled_reason": "",
                "disabled_at": None,
                "disabled_by": None,
            }
            new_owner = _takes_ownership(caller, fresh)
            if new_owner:
                fields["owner_id"] = caller.user_id
            Endpoint.objects.filter(id=ep.id).update(**fields)
            # Health starts again: an old run of failures or blocks must not turn it off on the next answer.
            EndpointState.objects.filter(endpoint_id=ep.id).update(
                consecutive_failed_attempts=0,
                consecutive_ssrf_blocked=0,
                failing_since=None,
            )
            changed = True
            audit.record(
                organization=caller.organization,
                action=audit.ENDPOINT_ENABLED,
                target=ep.id,
                actor=caller.user,
                changes={"status": {"before": was[0], "after": "enabled"}},
                metadata={"previous_reason": was[1], "note": note[:500]},
            )
            if new_owner:
                audit.record(
                    organization=caller.organization,
                    action=audit.ENDPOINT_OWNER_CHANGED,
                    target=ep.id,
                    actor=caller.user,
                    changes={
                        "owner_id": {
                            "before": None
                            if fresh.owner_id is None
                            else str(fresh.owner_id),
                            "after": str(caller.user_id),
                        }
                    },
                )
    if changed and not on:
        pause_backlog(ep.id)
    return Endpoint.objects.get(id=ep.id)


def resume(caller, ep: Endpoint, mode: str) -> dict:
    """``send`` moves paused deliveries to due now (inside the rate limit), except those past the max age, which end
    ``failed`` / ``max_age_exceeded``; ``skip`` marks them ``skipped`` (``manual``). Tests are never paused."""
    caller.require(ADMIN, ep.workspace_id)
    with transaction.atomic():
        note_write(caller, ep.workspace_id, ep)
        ep = locked_endpoint(ep.id, caller.organization_id)
        if ep.status != "enabled":
            raise WebhookApiError("WEBHOOK_ENDPOINT_DISABLED")
    now = timezone.now()
    cutoff = now - timedelta(seconds=ep.max_age_s)
    counts = {"sent": 0, "skipped": 0, "past_max_age": 0}
    while True:
        with transaction.atomic():
            rows = list(
                Delivery.objects.select_for_update(skip_locked=True)
                .filter(endpoint_id=ep.id, status="paused", is_test=False)
                .order_by("id")
                .values_list("id", "created_at", "attempts_count")[:RESUME_BATCH]
            )
            if not rows:
                break
            ids = [r[0] for r in rows]
            if mode == "skip":
                counts["skipped"] += Delivery.objects.filter(id__in=ids).update(
                    status="skipped", skip_reason="manual", next_attempt_at=None
                )
            else:
                old = [r[0] for r in rows if r[1] < cutoff]
                counts["past_max_age"] += Delivery.objects.filter(id__in=old).update(
                    status="failed",
                    final_outcome="max_age_exceeded",
                    next_attempt_at=None,
                )
                live = [r for r in rows if r[1] >= cutoff]
                for status_, ids_ in (
                    ("pending", [r[0] for r in live if r[2] == 0]),
                    ("retrying", [r[0] for r in live if r[2] > 0]),
                ):
                    counts["sent"] += Delivery.objects.filter(id__in=ids_).update(
                        status=status_, next_attempt_at=now
                    )
            EndpointState.objects.filter(endpoint_id=ep.id).update(
                paused_count=Greatest(F("paused_count") - len(ids), 0)
            )
        if len(rows) < RESUME_BATCH:
            break
    capped = Delivery.objects.filter(
        endpoint_id=ep.id, status="skipped", skip_reason="backlog_cap"
    ).count()
    with transaction.atomic():
        if counts["sent"]:
            notify()
        audit.record(
            organization=caller.organization,
            action=audit.ENDPOINT_RESUMED,
            target=ep.id,
            actor=caller.user,
            metadata={"mode": mode, **counts, "backlog_capped": capped},
        )
    return {"mode": mode, **counts, "backlog_capped": capped}


def _revoke(row: Secret, actor_id, now) -> str:
    row.state, row.revoked_at, row.revoked_by_id, row.secret_encrypted = (
        "revoked",
        now,
        actor_id,
        None,
    )
    row.save(update_fields=["state", "revoked_at", "revoked_by", "secret_encrypted"])
    return row.tail


def rotate_secret(
    caller, ep: Endpoint, overlap_s: int | None, secret: str | None
) -> dict:
    """The new secret becomes active and is returned once; the active one becomes ``previous`` until
    ``now + overlap`` (0 revokes it at once); an existing previous is revoked now, so at most two live secrets."""
    caller.require(MANAGE, ep.workspace_id)
    if secret is not None:
        caller.require(ADMIN, ep.workspace_id)
        check_secret(secret)
    require_keys()
    overlap = DEFAULT_OVERLAP_S if overlap_s is None else overlap_s
    new = secret or new_secret()
    now = timezone.now()
    with transaction.atomic():
        note_write(caller, ep.workspace_id, ep)
        # The state row serializes rotations of one endpoint: a second one waits here, then reads the
        # first one's new active secret, instead of missing it and hitting whk_one_active.
        locked_endpoint(ep.id, caller.organization_id)
        rows = list(
            Secret.objects.select_for_update()
            .filter(endpoint_id=ep.id, state__in=("active", "previous"))
            .order_by("created_at")
        )
        revoked = [
            _revoke(r, caller.user_id, now) for r in rows if r.state == "previous"
        ]
        previous = None
        for r in rows:
            if r.state != "active":
                continue
            if overlap == 0:
                revoked.append(_revoke(r, caller.user_id, now))
            else:
                r.state, r.expires_at = "previous", now + timedelta(seconds=overlap)
                r.save(update_fields=["state", "expires_at"])
                previous = {"id": r.id, "tail": r.tail, "expires_at": r.expires_at}
        row = Secret.objects.create(
            organization_id=ep.organization_id,
            endpoint_id=ep.id,
            secret_encrypted=crypto.encrypt(new),
            tail=secret_tail(new),
            state="active",
            created_by_id=caller.user_id,
        )
        audit.record(
            organization=caller.organization,
            action=audit.SECRET_ROTATED,
            target=ep.id,
            actor=caller.user,
            metadata={
                "tail": row.tail,
                "previous_tail": None if previous is None else previous["tail"],
                "overlap_seconds": overlap,
                "expires_at": None
                if previous is None
                else previous["expires_at"].isoformat(),
                "revoked_tails": revoked,
                "imported": secret is not None,
            },
        )
    return {
        "secret": new,
        "id": row.id,
        "tail": row.tail,
        "previous": previous,
        "revoked_tails": revoked,
    }


def revoke_previous(caller, ep: Endpoint) -> dict:
    caller.require(MANAGE, ep.workspace_id)
    now = timezone.now()
    with transaction.atomic():
        note_write(caller, ep.workspace_id, ep)
        locked_endpoint(ep.id, caller.organization_id)
        rows = list(
            Secret.objects.select_for_update().filter(
                endpoint_id=ep.id, state="previous"
            )
        )
        tails = [_revoke(r, caller.user_id, now) for r in rows]
        if tails:
            audit.record(
                organization=caller.organization,
                action=audit.SECRET_PREVIOUS_REVOKED,
                target=ep.id,
                actor=caller.user,
                metadata={"revoked_tails": tails},
            )
    return {"revoked_tails": tails}
