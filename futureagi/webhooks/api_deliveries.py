"""The delivery log, redelivery and the events list (DESIGN.md sections 7.6, 8.1, 8.3 and 10).

What a tenant sees: every attempt's outcome, status, timings, sanitized response headers, the redacted excerpt (stored
inert by the worker), which secrets signed it (tails), and the redacted request headers once per delivery. Never
``staff_detail`` and never a non-public address (the worker stores public addresses only). Full-mode bodies (prompt
text) are for admins only; deliveries of org-wide endpoints for org admins only; custom header names (and their masked
tails) only for those who may see the endpoint's header names, Workspace Admin and above (section 9.1).

Redelivery keeps the event id, the delivery id and the sequence; the worker sends it with a fresh ``t`` under the
current secrets. The worker writes the attempt row and does not know who asked, so the API's ledger
(``webhooks_api_action``) records the person and the attempt number, and the log shows that attempt as
``redeliver`` (:func:`trigger_of`).
"""

from __future__ import annotations

from django.db import transaction
from django.db.models import F, Q
from django.db.models.functions import Greatest
from django.utils import timezone

from webhooks import audit
from webhooks.api_errors import WebhookApiError
from webhooks.api_validation import count_recent, note_write
from webhooks.core.signing import RESERVED_HEADER_NAMES, RESERVED_HEADER_PREFIXES
from webhooks.models import ApiAction, Attempt, Delivery, EndpointState
from webhooks.permissions import ADMIN, MANAGE, VIEW_FULL_BODY, VIEW_LOG, VIEW_URL
from webhooks.publish import notify

BULK_CAP = 1000
BULK_PER_HOUR_PER_WORKSPACE = 10
TERMINAL = ("succeeded", "failed", "skipped", "paused")


def scope_q(caller, action: str, prefix: str = "endpoint__") -> Q:
    """Rows whose endpoint (or, for events, whose workspace) the caller may see for ``action``."""
    ws = caller.visible_workspaces(action)
    key = f"{prefix}workspace_id" if prefix else "workspace_id"
    scope = Q(**{f"{key}__isnull": False}) if ws is None else Q(**{f"{key}__in": ws})
    if caller.sees_org_wide(action):
        scope |= Q(**{f"{key}__isnull": True})
    return scope


def _subject_q(subject: str, prefix: str) -> Q:
    kind, _, sid = subject.partition(":")
    if kind not in ("prompt", "deployment") or not sid:
        raise WebhookApiError(
            "WEBHOOK_FILTER_INVALID",
            "subject is prompt:<id> or deployment:<id>.",
            attr="subject",
        )
    return Q(**{f"{prefix}subject_kind": kind, f"{prefix}subject_id": sid})


def view_filtered(qs, params: dict):
    """The delivery log's filters (event type, subject, time window, sequence range), applied the same way to the
    log and to ``Redeliver failed (N)`` on that view (WH-3 F5)."""
    if params.get("event_type"):
        qs = qs.filter(event__type=params["event_type"])
    if params.get("subject"):
        qs = qs.filter(_subject_q(params["subject"], "event__"))
    if params.get("since"):
        qs = qs.filter(created_at__gte=params["since"])
    if params.get("until"):
        qs = qs.filter(created_at__lt=params["until"])
    if params.get("seq_from") is not None:
        qs = qs.filter(seq__gte=params["seq_from"])
    if params.get("seq_to") is not None:
        qs = qs.filter(seq__lte=params["seq_to"])
    return qs


def list_deliveries(caller, params: dict, endpoint=None) -> dict:
    qs = Delivery.objects.filter(organization_id=caller.organization_id).select_related(
        "event", "endpoint"
    )
    if endpoint is not None:
        qs = qs.filter(endpoint_id=endpoint.id)
    qs = qs.filter(scope_q(caller, VIEW_LOG))
    if params.get("status"):
        qs = qs.filter(status=params["status"])
    if params.get("event"):
        qs = qs.filter(event_id=params["event"])
    qs = view_filtered(qs, params)
    if params.get("cursor"):
        qs = qs.filter(id__lt=params["cursor"])
    limit = params.get("limit") or 50
    rows = list(qs.order_by("-id")[: limit + 1])
    more = len(rows) > limit
    rows = rows[:limit]
    with_body = bool(params.get("include_body")) or (
        params.get("seq_from") is not None and params.get("seq_to") is not None
    )
    last = _last_attempts([d.id for d in rows])
    return {
        "results": [
            represent(caller, d, last.get(d.id), with_body=with_body) for d in rows
        ],
        "next_cursor": rows[-1].id if more and rows else None,
    }


def _last_attempts(delivery_ids: list[str]) -> dict:
    out: dict = {}
    for a in Attempt.objects.filter(delivery_id__in=delivery_ids).order_by(
        "delivery_id", "-n", "-started_at"
    ):
        out.setdefault(a.delivery_id, a)
    return out


def _body(caller, d: Delivery) -> dict:
    p = d.payload
    out = {
        "body_sha256": p.body_sha256,
        "body_bytes": p.body_bytes,
        "body_expired": p.body is None,
    }
    if p.payload_mode == "full" and not caller.can(
        VIEW_FULL_BODY, d.endpoint.workspace_id
    ):
        out.update(body=None, body_hidden="full_payload_admins_only")
    else:
        out["body"] = None if p.body is None else bytes(p.body).decode("utf-8")
    return out


def represent(
    caller, d: Delivery, last: Attempt | None = None, *, with_body: bool = False
) -> dict:
    ev = d.event
    out = {
        "id": d.id,
        "event_id": d.event_id,
        "event_type": ev.type,
        "subject": f"{ev.subject_kind}:{ev.subject_id}" if ev.subject_kind else None,
        "endpoint_id": d.endpoint_id,
        "seq": d.seq,
        "status": d.status,
        "retry_class": d.retry_class or None,
        "skip_reason": d.skip_reason or None,
        "final_outcome": d.final_outcome or None,
        "attempts_count": d.attempts_count,
        "next_attempt_at": d.next_attempt_at,
        "first_attempt_at": d.first_attempt_at,
        "succeeded_at": d.succeeded_at,
        "created_at": d.created_at,
        "is_test": d.is_test,
        "targeted": d.targeted,
        "last_attempt": None
        if last is None
        else {
            "n": last.n,
            "outcome": last.outcome,
            "response_status": last.response_status,
            "duration_ms": last.duration_ms,
            "started_at": last.started_at,
        },
    }
    if with_body:
        out.update(_body(caller, d))
    return out


def trigger_of(delivery_id: str) -> dict:
    """``{attempt n: (trigger, actor id)}`` for the attempts a person asked for (redeliveries). This is the narrow
    hook for the claim: ``store_claim._claim_of`` can read it to write ``trigger`` and ``actor`` on the attempt row
    itself (owner of that module)."""
    return {
        n: ("redeliver", None if actor is None else str(actor))
        for n, actor in ApiAction.objects.filter(
            delivery_id=delivery_id, kind="redeliver"
        ).values_list("attempt_n", "actor_id")
    }


def get_delivery(caller, delivery_id: str) -> Delivery:
    d = (
        Delivery.objects.select_related("event", "endpoint", "payload")
        .filter(id=delivery_id, organization_id=caller.organization_id)
        .first()
    )
    if d is None:
        raise WebhookApiError("WEBHOOK_NOT_FOUND")
    caller.require(VIEW_LOG, d.endpoint.workspace_id)
    return d


def delivery_detail(caller, d: Delivery) -> dict:
    triggers = trigger_of(d.id)
    attempts = []
    for a in Attempt.objects.filter(delivery_id=d.id).order_by("n", "started_at"):
        trig, actor = triggers.get(
            a.n, (a.trigger, None if a.actor_id is None else str(a.actor_id))
        )
        attempts.append(
            {
                "id": a.id,
                "n": a.n,
                "trigger": trig if a.trigger == "auto" else a.trigger,
                "actor_id": actor,
                "t": a.t,
                "signature_key_tails": list(a.signature_key_tails or []),
                "started_at": a.started_at,
                "settled_at": a.settled_at,
                "dns_ms": a.dns_ms,
                "ttfb_ms": a.ttfb_ms,
                "duration_ms": a.duration_ms,
                "resolved_ip": a.resolved_ip,
                "ip_family": a.ip_family,
                "response_status": a.response_status,
                "response_headers": dict(a.response_headers or {}),
                "response_body_excerpt": a.response_body_excerpt,
                "outcome": a.outcome,
                "error_detail": a.error_detail or None,
            }
        )
    out = represent(caller, d, with_body=True)
    out["request_headers"] = request_headers(caller, d)
    out["attempts"] = attempts
    return out


def _own_header(name: str) -> bool:
    """A header Future AGI sets on every request (Standard Webhooks, ``X-FutureAGI-*``, content type): never an
    endpoint's custom header, whose names are refused when reserved (``core.signing.custom_header_problems``)."""
    low = name.lower()
    return low in RESERVED_HEADER_NAMES or low.startswith(RESERVED_HEADER_PREFIXES)


def request_headers(caller, d: Delivery) -> dict:
    """The redacted request headers; custom ones only for those who may see the endpoint's header names."""
    headers = dict(d.request_headers or {})
    if caller.can(VIEW_URL, d.endpoint.workspace_id):
        return headers
    return {k: v for k, v in headers.items() if _own_header(k)}


def _queue(d: Delivery, now) -> None:
    """Due now: ``pending`` before any attempt, else ``retrying``; a paused one leaves the endpoint's paused count."""
    Delivery.objects.filter(id=d.id).update(
        status="retrying" if d.attempts_count else "pending",
        next_attempt_at=now,
        final_outcome="",
        skip_reason="",
    )
    if d.status == "paused":
        EndpointState.objects.filter(endpoint_id=d.endpoint_id).update(
            paused_count=Greatest(F("paused_count") - 1, 0)
        )


def redeliver_one(caller, delivery_id: str) -> dict:
    d = get_delivery(caller, delivery_id)
    caller.require(MANAGE, d.endpoint.workspace_id)
    if d.payload.payload_mode == "full" and not caller.can(
        VIEW_FULL_BODY, d.endpoint.workspace_id
    ):
        # A body the caller may not read is not theirs to send again.
        raise WebhookApiError(
            "WEBHOOK_FORBIDDEN",
            "Only admins can redeliver a delivery that carries prompt text.",
        )
    now = timezone.now()
    with transaction.atomic():
        note_write(
            caller, d.endpoint.workspace_id, d.endpoint
        )  # the org's API lock first (api_locks)
        d = (
            Delivery.objects.select_for_update(of=("self",))
            .select_related("endpoint", "payload", "event")
            .get(id=d.id)
        )
        if d.status == "cancelled" or d.endpoint.deleted:
            raise WebhookApiError("WEBHOOK_DELIVERY_NOT_REDELIVERABLE")
        if d.endpoint.status != "enabled":
            raise WebhookApiError("WEBHOOK_ENDPOINT_DISABLED")
        if d.payload.body is None:
            raise WebhookApiError("WEBHOOK_BODY_EXPIRED")
        if d.status == "sending":
            raise WebhookApiError("WEBHOOK_DELIVERY_IN_FLIGHT")
        n = d.attempts_count + 1
        _queue(d, now)
        ApiAction.objects.create(
            organization_id=caller.organization_id,
            workspace_id=d.endpoint.workspace_id,
            actor_id=caller.user_id,
            kind="redeliver",
            endpoint_id=d.endpoint_id,
            delivery_id=d.id,
            attempt_n=n,
        )
        audit.record(
            organization=caller.organization,
            action=audit.DELIVERY_REDELIVERED,
            target=d.id,
            actor=caller.user,
            metadata={
                "endpoint_id": d.endpoint_id,
                "event_id": d.event_id,
                "attempt": n,
            },
        )
        notify()
    return {"delivery_id": d.id, "queued": True, "attempt": n}


def redeliver_bulk(caller, params: dict) -> dict:
    """``Redeliver failed (N)`` on a filtered view of one endpoint: a dry run counts; a real run queues at most 1,000
    (at the endpoint's own rate, which the claim applies), 10 per hour per workspace, audited with the filter."""
    ep = caller.require_endpoint(params["endpoint"], ADMIN)
    qs = view_filtered(
        Delivery.objects.filter(endpoint_id=ep.id, status=params["status"]), params
    )
    expired = qs.filter(payload__body__isnull=True).count()
    qs = qs.filter(payload__body__isnull=False)
    count = qs.count()
    filt = {
        k: (v.isoformat() if hasattr(v, "isoformat") else v)
        for k, v in params.items()
        if k != "dry_run"
    }
    if params.get("dry_run"):
        return {"count": count, "queued": 0, "expired": expired, "dry_run": True}
    if count > BULK_CAP:
        raise WebhookApiError("WEBHOOK_BULK_TOO_LARGE", count=count, cap=BULK_CAP)
    if ep.status != "enabled":
        raise WebhookApiError("WEBHOOK_ENDPOINT_DISABLED")
    match = (
        {"workspace_id": ep.workspace_id}
        if ep.workspace_id
        else {"workspace__isnull": True}
    )
    now = timezone.now()
    queued = 0
    with transaction.atomic():
        note_write(caller, ep.workspace_id, ep)
        # Counted under the org's API lock, with the ledger row below in the same transaction.
        if (
            count_recent(caller.organization_id, "bulk_redeliver", 3600, **match)
            >= BULK_PER_HOUR_PER_WORKSPACE
        ):
            raise WebhookApiError("WEBHOOK_RATE_LIMITED", retry_after_s=3600)
        rows = list(
            qs.select_for_update(skip_locked=True, of=("self",)).order_by("id")[
                :BULK_CAP
            ]
        )
        ledger = []
        for d in rows:
            _queue(d, now)
            ledger.append(
                ApiAction(
                    organization_id=caller.organization_id,
                    workspace_id=ep.workspace_id,
                    actor_id=caller.user_id,
                    kind="redeliver",
                    endpoint_id=ep.id,
                    delivery_id=d.id,
                    attempt_n=d.attempts_count + 1,
                )
            )
        queued = len(rows)
        ApiAction.objects.bulk_create(ledger)
        ApiAction.objects.create(
            organization_id=caller.organization_id,
            workspace_id=ep.workspace_id,
            actor_id=caller.user_id,
            kind="bulk_redeliver",
            endpoint_id=ep.id,
        )
        audit.record(
            organization=caller.organization,
            action=audit.DELIVERY_REDELIVERED,
            target=ep.id,
            actor=caller.user,
            metadata={
                "bulk": True,
                "filter": filt,
                "count": queued,
                "expired": expired,
            },
        )
        if queued:
            notify()
    return {"count": count, "queued": queued, "expired": expired, "dry_run": False}
