"""Audit entries for webhooks (DESIGN.md section 9.2): ``AuditLog`` rows with scope ``webhooks``.

``AuditLog.target_id`` is a UUID; webhook ids are prefixed ULIDs, and a ULID is 128 bits, so the target is the ULID's
bits as a UUID (``target_uuid``) and the prefixed id is also in ``metadata.target``. Never a full URL, a secret or a
header value: URLs appear as ``url_display`` and ``url_sha256`` (G3), secrets as their 4-character tails, headers by
name only.
"""

from __future__ import annotations

import uuid

from webhooks.core.ids import CROCKFORD

SCOPE = "webhooks"

ENDPOINT_CREATED = "webhook.endpoint.created"
ENDPOINT_UPDATED = "webhook.endpoint.updated"
ENDPOINT_DELETED = "webhook.endpoint.deleted"
ENDPOINT_ENABLED = "webhook.endpoint.enabled"
ENDPOINT_DISABLED = "webhook.endpoint.disabled"
ENDPOINT_AUTO_DISABLED = "webhook.endpoint.auto_disabled"
ENDPOINT_BLOCKED = "webhook.endpoint.blocked_by_policy"
ENDPOINT_RESUMED = "webhook.endpoint.resumed"
ENDPOINT_OWNER_CHANGED = "webhook.endpoint.owner_changed"
PAYLOAD_FULL_ENABLED = "webhook.endpoint.payload_full_enabled"
RUN_TARGETING_ENABLED = "webhook.endpoint.run_targeting_enabled"
SECRET_CREATED = "webhook.secret.created"
SECRET_ROTATED = "webhook.secret.rotated"
SECRET_PREVIOUS_REVOKED = "webhook.secret.previous_revoked"
SECRET_IMPORTED = "webhook.secret.imported"
TEST_SENT = "webhook.test.sent"
DELIVERY_REDELIVERED = "webhook.delivery.redelivered"
POLICY_UPDATED = "webhook.policy.updated"

ACTIONS = (
    ENDPOINT_CREATED,
    ENDPOINT_UPDATED,
    ENDPOINT_DELETED,
    ENDPOINT_ENABLED,
    ENDPOINT_DISABLED,
    ENDPOINT_AUTO_DISABLED,
    ENDPOINT_BLOCKED,
    ENDPOINT_RESUMED,
    ENDPOINT_OWNER_CHANGED,
    PAYLOAD_FULL_ENABLED,
    RUN_TARGETING_ENABLED,
    SECRET_CREATED,
    SECRET_ROTATED,
    SECRET_PREVIOUS_REVOKED,
    SECRET_IMPORTED,
    TEST_SENT,
    DELIVERY_REDELIVERED,
    POLICY_UPDATED,
)


def target_uuid(prefixed_id: str) -> uuid.UUID:
    """The 128 bits of the id's ULID as a UUID (lossless: ``ulid_of(target_uuid(x))`` gives x's ULID back)."""
    raw = prefixed_id.rsplit("_", 1)[-1]
    if len(raw) != 26:
        raise ValueError("not a prefixed ULID")
    n = 0
    for ch in raw:
        n = n * 32 + CROCKFORD.index(ch)
    return uuid.UUID(int=n)


def endpoint_snapshot(ep) -> dict:
    """What an endpoint audit entry records (section 9.2): never the full URL, a secret or a header value."""
    subs = sorted(
        (
            {"event_type": s.event_type, "filters": s.filters}
            for s in ep.subscriptions.all()
        ),
        key=lambda s: s["event_type"],
    )
    return {
        "name": ep.name,
        "kind": ep.kind,
        "workspace_id": None if ep.workspace_id is None else str(ep.workspace_id),
        "url_display": ep.url_display,
        "url_sha256": ep.url_sha256,
        "subscriptions": subs,
        "payload_mode": ep.payload_mode,
        "include_people_names": ep.include_people_names,
        "accepts_run_targeting": ep.accepts_run_targeting,
        "header_names": list(ep.header_names or []),
        "timeout_ms": ep.timeout_ms,
        "rate_limit_per_s": ep.rate_limit_per_s,
        "max_in_flight": ep.max_in_flight,
        "max_age_s": ep.max_age_s,
        "status": ep.status,
    }


def diff(before: dict, after: dict) -> dict:
    return {
        k: {"before": before.get(k), "after": after.get(k)}
        for k in sorted(set(before) | set(after))
        if before.get(k) != after.get(k)
    }


def record(
    *,
    organization,
    action: str,
    target: str,
    actor=None,
    changes: dict | None = None,
    metadata: dict | None = None,
    system: bool = False,
):
    """One ``AuditLog`` row. ``actor`` None falls back to the request's user (``tfc.utils.audit``); the offboarding
    check passes the system's absence of an actor explicitly as ``metadata.actor = "system"``.

    ``system=True`` writes no actor at all, never the request's user: for an entry written while user rows are being
    deleted (the offboarding ``pre_delete``), where the request's user may be one of them and a row pointing at it
    would fail the deletion at commit."""
    if action not in ACTIONS:
        raise ValueError(f"unknown webhooks audit action {action!r}")
    from tfc.utils.audit import is_audit_muted, log_audit

    meta = {"target": target, **(metadata or {})}
    if system:
        if is_audit_muted():
            return None
        from accounts.models.audit_log import AuditLog

        return AuditLog.objects.create(
            organization=organization,
            actor=None,
            action=action,
            scope=SCOPE,
            target_id=target_uuid(target),
            changes=changes or {},
            metadata=meta,
        )
    return log_audit(
        organization=organization,
        action=action,
        scope=SCOPE,
        target_id=target_uuid(target),
        changes=changes or {},
        metadata=meta,
        actor=actor,
    )


def record_auto_disabled(endpoint_id: str, *, reason: str, details: dict | None = None):
    """``webhook.endpoint.auto_disabled`` (section 9.2: the rule that fired, last status, failing since), with no
    actor. For the code that turns an endpoint off on its own (a settle's 410 or third ``ssrf_blocked``, the 24 h
    failing rule): ``store_settle.turn_off`` calls it in the turn-off's own transaction, in a savepoint.

    ``system=True``: never the request's or the thread's user, which a worker thread may still carry."""
    from accounts.models.organization import Organization
    from webhooks.models import Endpoint

    ep = Endpoint.objects.only("id", "organization_id").get(id=endpoint_id)
    return record(
        organization=Organization.objects.get(id=ep.organization_id),
        action=ENDPOINT_AUTO_DISABLED,
        target=ep.id,
        actor=None,
        system=True,
        metadata={"reason": reason, "actor": "system", **(details or {})},
    )
