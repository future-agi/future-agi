"""Endpoints: create, read, update and delete (DESIGN.md sections 4.1, 4.3, 4.4, 5.3, 9 and 10).

Create returns the signing secret once; nothing returns it again. URLs, header values and secrets are stored only as
ciphertext under ``WEBHOOKS_ENCRYPTION_KEYS``, checked before any of them is handled (K2). A delete shreds the
secrets, the URL and the header values at once; the delivery log stays for its retention, and waiting deliveries are
cancelled. Every write is audited and counted toward the 120/min management rate.

An update and a delete work on the row as it is under the lock (``api_locks.locked_endpoint``), and an update writes
only the columns it changes, so a turn-off or a delete that commits during the request is never undone.
Where a webhook points (its URL and headers) is admin-only when it carries what only admins may receive: prompt text
(``payload_mode=full``), an admin's run-targeting opt-in, or such deliveries still waiting to be sent
(sections 8.1 and 9.1).
"""

from __future__ import annotations

import hashlib

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from webhooks import audit, crypto
from webhooks.api_errors import WebhookApiError
from webhooks.api_locks import locked_endpoint
from webhooks.api_validation import (
    check_endpoint_limits,
    check_headers,
    check_secret,
    check_subscriptions,
    check_url,
    note_write,
    require_keys,
)
from webhooks.core.addresses import display_url
from webhooks.core.signing import new_secret, secret_tail
from webhooks.models import (
    Delivery,
    Endpoint,
    EndpointState,
    Secret,
    Subscription,
)
from webhooks.permissions import ADMIN, MANAGE, full_payload_allowed

CANCEL_BATCH = 500
SETTINGS_FIELDS = ("timeout_ms", "rate_limit_per_s", "max_in_flight", "max_age_s")
PLAIN_FIELDS = (
    "name",
    "description",
    "payload_mode",
    "include_people_names",
    "accepts_run_targeting",
    *SETTINGS_FIELDS,
)
URL_FIELDS = ("url_encrypted", "url_host", "url_display", "url_sha256")
WAITING = ("pending", "retrying", "paused", "sending")


def _set_url(ep: Endpoint, url: str, decision) -> None:
    ep.url_encrypted = crypto.encrypt(url)
    ep.url_host = decision.host or ""
    ep.url_display = display_url(url)
    ep.url_sha256 = hashlib.sha256(url.encode("utf-8")).hexdigest()


def _check_payload_mode(caller, workspace_id, mode: str | None) -> None:
    if mode == "full" and not (
        caller.can(ADMIN, workspace_id) and full_payload_allowed(caller.organization_id)
    ):
        raise WebhookApiError("WEBHOOK_PAYLOAD_FULL_NOT_ALLOWED", attr="payload_mode")


def _check_admin_fields(caller, workspace_id, data: dict) -> None:
    if data.get("accepts_run_targeting") and not caller.can(ADMIN, workspace_id):
        raise WebhookApiError(
            "WEBHOOK_FORBIDDEN",
            "Only admins can let runs send their results to a webhook.",
            attr="accepts_run_targeting",
        )
    if data.get("secret") is not None and not caller.can(ADMIN, workspace_id):
        raise WebhookApiError(
            "WEBHOOK_FORBIDDEN",
            "Only admins can bring their own signing secret.",
            attr="secret",
        )
    _check_payload_mode(caller, workspace_id, data.get("payload_mode"))


def _carries_admin_bodies(ep: Endpoint) -> bool:
    """Whether the endpoint receives what only admins may receive: prompt text, an admin's run-targeting opt-in, or
    deliveries of either still waiting to be sent (they would go to a new URL)."""
    if ep.payload_mode == "full" or ep.accepts_run_targeting:
        return True
    return (
        Delivery.objects.filter(endpoint_id=ep.id, status__in=WAITING)
        .filter(Q(payload__payload_mode="full") | Q(targeted=True))
        .exists()
    )


def _check_redirect(caller, ep: Endpoint, data: dict) -> None:
    """A Member with ``members_can_manage`` may edit an endpoint, but not move where it points when it
    carries admin-only bodies; they could otherwise read every full body and an admin's targeted run results."""
    moved = [k for k in ("url", "headers") if k in data]
    if not moved or caller.can(ADMIN, ep.workspace_id):
        return
    if _carries_admin_bodies(ep):
        raise WebhookApiError(
            "WEBHOOK_FORBIDDEN",
            "Only admins can change the URL or headers of a webhook that receives prompt text or run results.",
            attr=moved[0],
        )


def _subscribe(ep: Endpoint, subs: list[dict], actor_id) -> None:
    Subscription.objects.filter(endpoint=ep).delete()
    Subscription.objects.bulk_create(
        [
            Subscription(
                organization_id=ep.organization_id,
                endpoint=ep,
                event_type=s["event_type"],
                filters=s["filters"],
                created_by_id=actor_id,
            )
            for s in subs
        ]
    )


def _name_taken(exc: IntegrityError) -> bool:
    return "whep_live_name" in str(exc)


def create_endpoint(caller, data: dict) -> tuple[Endpoint, str, list[str]]:
    """Returns the endpoint, its ``whsec_`` secret (never shown again) and the query parameter names that look like
    credentials (the dialog's D13 warning)."""
    workspace_id = (
        data["workspace_id"] if "workspace_id" in data else caller.request_workspace_id
    )
    workspace_id = None if workspace_id is None else str(workspace_id)
    caller.require(MANAGE, workspace_id)
    if data.get("kind", "http") != "http":
        raise WebhookApiError("WEBHOOK_KIND_UNAVAILABLE", attr="kind")
    _check_admin_fields(caller, workspace_id, data)
    require_keys()
    subs = check_subscriptions(data["subscriptions"])
    headers = check_headers(data.get("headers") or {})
    decision = check_url(caller.organization_id, workspace_id, data["url"])
    secret = check_secret(data["secret"]) if data.get("secret") else new_secret()
    with transaction.atomic():
        note_write(
            caller, workspace_id
        )  # the org's API lock first: the limit count below is atomic (S9)
        check_endpoint_limits(caller.organization_id, workspace_id)
        ep = Endpoint(
            organization_id=caller.organization_id,
            workspace_id=workspace_id,
            kind="http",
            name=data["name"].strip(),
            description=data.get("description", ""),
            header_names=sorted(headers, key=str.lower),
            headers_encrypted=crypto.encrypt_json(headers) if headers else None,
            payload_mode=data.get("payload_mode", "thin"),
            include_people_names=data.get("include_people_names", True),
            accepts_run_targeting=data.get("accepts_run_targeting", False),
            owner_id=caller.user_id,
            created_by_id=caller.user_id,
            **{k: data[k] for k in SETTINGS_FIELDS if k in data},
        )
        _set_url(ep, data["url"], decision)
        try:
            with transaction.atomic():
                ep.save()
        except IntegrityError as exc:
            if _name_taken(exc):
                raise WebhookApiError("WEBHOOK_NAME_TAKEN", attr="name") from None
            raise
        EndpointState.objects.create(endpoint=ep, organization_id=ep.organization_id)
        Secret.objects.create(
            organization_id=ep.organization_id,
            endpoint=ep,
            secret_encrypted=crypto.encrypt(secret),
            tail=secret_tail(secret),
            state="active",
            created_by_id=caller.user_id,
        )
        _subscribe(ep, subs, caller.user_id)
        org = caller.organization
        audit.record(
            organization=org,
            action=audit.ENDPOINT_CREATED,
            target=ep.id,
            actor=caller.user,
            changes={"after": audit.endpoint_snapshot(ep)},
        )
        audit.record(
            organization=org,
            action=audit.SECRET_IMPORTED
            if data.get("secret")
            else audit.SECRET_CREATED,
            target=ep.id,
            actor=caller.user,
            metadata={"tail": secret_tail(secret)},
        )
        if ep.payload_mode == "full":
            audit.record(
                organization=org,
                action=audit.PAYLOAD_FULL_ENABLED,
                target=ep.id,
                actor=caller.user,
            )
        if ep.accepts_run_targeting:
            audit.record(
                organization=org,
                action=audit.RUN_TARGETING_ENABLED,
                target=ep.id,
                actor=caller.user,
            )
    return ep, secret, list(decision.warn_query_params)


def update_endpoint(caller, ep: Endpoint, data: dict) -> Endpoint:
    caller.require(MANAGE, ep.workspace_id)
    _check_admin_fields(caller, ep.workspace_id, data)
    if "url" in data or "headers" in data:
        require_keys()
    subs = (
        check_subscriptions(data["subscriptions"]) if "subscriptions" in data else None
    )
    headers = None
    if "headers" in data:
        headers = check_headers(data["headers"] or {})
    with transaction.atomic():
        note_write(caller, ep.workspace_id, ep)
        ep = locked_endpoint(ep.id, caller.organization_id)
        _check_redirect(caller, ep, data)
        decision = (
            check_url(ep.organization_id, ep.workspace_id, data["url"])
            if "url" in data
            else None
        )
        before = audit.endpoint_snapshot(ep)
        changed: set[str] = set()
        for k in PLAIN_FIELDS:
            if k in data:
                setattr(ep, k, data[k].strip() if k == "name" else data[k])
                changed.add(k)
        if headers is not None:
            ep.header_names = sorted(headers, key=str.lower)
            ep.headers_encrypted = crypto.encrypt_json(headers) if headers else None
            changed.update(("header_names", "headers_encrypted"))
        if decision is not None:
            _set_url(ep, data["url"], decision)
            changed.update(URL_FIELDS)
            if ep.status == "blocked_by_policy":
                # A URL the policy allows again leaves the blocked state (section 7.7); the paused backlog waits
                # for resume. Decided on the row under the lock.
                ep.status, ep.disabled_reason, ep.disabled_at = "enabled", "", None
                changed.update(("status", "disabled_reason", "disabled_at"))
        try:
            with transaction.atomic():
                # Only the columns this request changed: never a stale deleted, status, owner or ciphertext.
                ep.save(update_fields=sorted(changed | {"updated_at"}))
        except IntegrityError as exc:
            if _name_taken(exc):
                raise WebhookApiError("WEBHOOK_NAME_TAKEN", attr="name") from None
            raise
        if subs is not None:
            _subscribe(ep, subs, caller.user_id)
        after = audit.endpoint_snapshot(ep)
        org = caller.organization
        audit.record(
            organization=org,
            action=audit.ENDPOINT_UPDATED,
            target=ep.id,
            actor=caller.user,
            changes=audit.diff(before, after),
        )
        if after["payload_mode"] == "full" and before["payload_mode"] != "full":
            audit.record(
                organization=org,
                action=audit.PAYLOAD_FULL_ENABLED,
                target=ep.id,
                actor=caller.user,
            )
        if after["accepts_run_targeting"] and not before["accepts_run_targeting"]:
            audit.record(
                organization=org,
                action=audit.RUN_TARGETING_ENABLED,
                target=ep.id,
                actor=caller.user,
            )
    return ep


def delete_endpoint(caller, ep: Endpoint) -> int:
    """Soft delete; the secrets, the URL and the header values are shredded now. Returns how many waiting
    deliveries were cancelled (the claim cancels any it already holds: it reads ``deleted`` under the state lock)."""
    caller.require(MANAGE, ep.workspace_id)
    now = timezone.now()
    with transaction.atomic():
        note_write(caller, ep.workspace_id, ep)
        # One lock order everywhere (api_locks); a second delete of the same endpoint answers 404.
        ep = locked_endpoint(ep.id, caller.organization_id)
        before = audit.endpoint_snapshot(ep)
        Endpoint.objects.filter(id=ep.id).update(
            deleted=True,
            deleted_at=now,
            url_encrypted=None,
            headers_encrypted=None,
            github_token_encrypted=None,
            legacy_signing_key_encrypted=None,
        )
        tails = list(
            Secret.objects.filter(endpoint_id=ep.id).values_list("tail", flat=True)
        )
        Secret.objects.filter(endpoint_id=ep.id).delete()
        audit.record(
            organization=caller.organization,
            action=audit.ENDPOINT_DELETED,
            target=ep.id,
            actor=caller.user,
            changes={"before": before},
            metadata={"secret_tails_deleted": tails},
        )
    return cancel_waiting(ep.id)


def cancel_waiting(endpoint_id: str) -> int:
    """Waiting deliveries of a deleted endpoint become ``cancelled`` / ``endpoint_deleted``, in short SKIP LOCKED
    batches."""
    total = 0
    while True:
        with transaction.atomic():
            ids = list(
                Delivery.objects.select_for_update(skip_locked=True)
                .filter(
                    endpoint_id=endpoint_id,
                    status__in=("pending", "retrying", "paused"),
                )
                .order_by("id")
                .values_list("id", flat=True)[:CANCEL_BATCH]
            )
            if not ids:
                return total
            total += Delivery.objects.filter(id__in=ids).update(
                status="cancelled",
                final_outcome="endpoint_deleted",
                next_attempt_at=None,
            )
            EndpointState.objects.filter(endpoint_id=endpoint_id).update(paused_count=0)
        if len(ids) < CANCEL_BATCH:
            return total
