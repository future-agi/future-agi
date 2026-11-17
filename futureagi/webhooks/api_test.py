"""Test delivery and preview (DESIGN.md section 8.2; S1, K5).

**A test is sent by the worker, never by the web tier (S1).** The API writes a test event (``evt_test_…``, ``is_test``,
for this endpoint only) in its own transaction and fires ``pg_notify``; the dispatcher fans it out to this endpoint,
signs it and sends it under every address rule, exactly as a real event. The API then long-polls the delivery for up
to 20 s: 200 with the result when the attempt settled, else 202 with the ids, and the UI or CLI keeps polling
``GET /webhooks/deliveries/{id}/``. Tests take no sequence number and are never retried (the core's rule).

**Preview** makes no network call, so it stays in the web tier: the exact request for a sample of the chosen type,
with the signature replaced by ``<signature>`` and custom header values masked.
"""

from __future__ import annotations

import hashlib
import time

from django.db import transaction
from django.utils import timezone

from webhooks import audit, config
from webhooks.api_errors import WebhookApiError
from webhooks.api_locks import lock_org
from webhooks.api_samples import names_from_filters, sample_event
from webhooks.api_validation import check_headers, count_recent
from webhooks.core import ids
from webhooks.core.fanout import CATALOG
from webhooks.core.payloads import variant_body
from webhooks.core.signing import attempt_headers, mask_header_value
from webhooks.models import ApiAction, Attempt, Delivery, Event
from webhooks.permissions import MANAGE, VIEW
from webhooks.publish import notify

TEST_WAIT_S = 20.0
TEST_POLL_S = 0.25
TESTS_PER_MINUTE_PERSON = 5
TESTS_PER_MINUTE_ORG = 30


def envelope_workspace(caller, workspace_id) -> str | None:
    """Every envelope names a workspace (section 14.1). An org-wide endpoint's test, and a preview, use the
    workspace the person is working in, else the org's default workspace."""
    if workspace_id is not None:
        return str(workspace_id)
    if caller.request_workspace_id:
        return caller.request_workspace_id
    from accounts.models.workspace import Workspace

    ws = (
        Workspace.no_workspace_objects.filter(
            organization_id=caller.organization_id, is_active=True
        )
        .order_by("-is_default", "created_at")
        .values_list("id", flat=True)
        .first()
    )
    return None if ws is None else str(ws)


def _subject(event_type: str, data: dict) -> tuple[str, str]:
    if event_type.startswith("prompt."):
        return "prompt", data["prompt"]["id"]
    return "deployment", data["deployment"]["id"]


def send_test(
    caller, ep, event_type: str, wait_s: float | None = None
) -> tuple[int, dict]:
    caller.require(MANAGE, ep.workspace_id)
    if event_type not in CATALOG:
        raise WebhookApiError("WEBHOOK_EVENT_TYPE_UNKNOWN", attr="event_type")
    if config.send_blockers():
        raise WebhookApiError("WEBHOOK_SENDING_OFF")
    ws = envelope_workspace(caller, ep.workspace_id)
    subs = list(ep.subscriptions.values("event_type", "filters"))
    names = names_from_filters(caller.organization_id, subs, event_type)
    s = sample_event(
        event_type,
        organization_id=caller.organization_id,
        workspace_id=ws,
        test=True,
        names=names,
    )
    kind, sid = _subject(event_type, s["data"])
    with transaction.atomic():
        # The rates are counted under the org's API lock, with this test's ledger row in the same transaction, so
        # replicas cannot both pass the count.
        lock_org(caller.organization_id)
        if count_recent(
            caller.organization_id, "test", 60, actor_id=caller.user_id
        ) >= TESTS_PER_MINUTE_PERSON or (
            count_recent(caller.organization_id, "test", 60) >= TESTS_PER_MINUTE_ORG
        ):
            raise WebhookApiError("WEBHOOK_TEST_RATE_LIMITED", retry_after_s=60)
        Event.objects.create(
            id=s["id"],
            organization_id=caller.organization_id,
            workspace_id=ws,
            type=event_type,
            occurred_at=timezone.now(),
            producer="webhooks.test",
            subject_kind=kind,
            subject_id=sid,
            source_key=f"webhooks.test:{s['id']}",
            source_hash=hashlib.sha256(s["id"].encode("ascii")).hexdigest(),
            data=s["data"],
            builder_input=s["builder_input"],
            is_test=True,
            test_endpoint_id=ep.id,
        )
        ApiAction.objects.create(
            organization_id=caller.organization_id,
            workspace_id=ep.workspace_id,
            actor_id=caller.user_id,
            kind="test",
            endpoint_id=ep.id,
        )
        notify()
    code, result = _wait(s["id"], TEST_WAIT_S if wait_s is None else wait_s)
    with transaction.atomic():
        audit.record(
            organization=caller.organization,
            action=audit.TEST_SENT,
            target=ep.id,
            actor=caller.user,
            metadata={
                "event_type": event_type,
                "event_id": s["id"],
                "delivery_id": result.get("delivery_id"),
                "status_code": result.get("status_code"),
                "outcome": result.get("outcome"),
            },
        )
    return code, result


def _wait(event_id: str, wait_s: float) -> tuple[int, dict]:
    deadline = time.monotonic() + max(0.0, wait_s)
    delivery_id = None
    while True:
        d = Delivery.objects.filter(event_id=event_id).values("id", "status").first()
        if d is not None:
            delivery_id = d["id"]
            if d["status"] in ("succeeded", "failed"):
                a = (
                    Attempt.objects.filter(delivery_id=delivery_id)
                    .order_by("-n", "-started_at")
                    .first()
                )
                if a is not None:
                    return 200, {
                        "event_id": event_id,
                        "delivery_id": delivery_id,
                        "status_code": a.response_status,
                        "outcome": a.outcome,
                        "latency_ms": a.duration_ms,
                        "response_excerpt": a.response_body_excerpt,
                    }
        if time.monotonic() >= deadline:
            return 202, {"event_id": event_id, "delivery_id": delivery_id}
        time.sleep(TEST_POLL_S)


def preview(caller, data: dict) -> dict:
    """The request a receiver would get for a sample of ``event_type`` under this payload variant."""
    caller.require(VIEW, caller.request_workspace_id)
    workspace_id = envelope_workspace(caller, None)
    event_type = data["event_type"]
    if event_type not in CATALOG:
        raise WebhookApiError("WEBHOOK_EVENT_TYPE_UNKNOWN", attr="event_type")
    headers = check_headers(data.get("headers") or {})
    s = sample_event(
        event_type,
        organization_id=caller.organization_id,
        workspace_id=workspace_id,
        test=False,
    )
    body = variant_body(
        event_id=s["id"],
        type_=event_type,
        timestamp=s["timestamp"],
        organization_id=str(caller.organization_id),
        workspace_id=workspace_id,
        data=s["data"],
        builder_input=s["builder_input"],
        variant={
            "payload_mode": data.get("payload_mode", "thin"),
            "include_people_names": data.get("include_people_names", True),
        },
    )
    sent = attempt_headers(
        msg_id=s["id"],
        timestamp=int(time.time()),
        signature="v1,<signature>",
        event_type=event_type,
        delivery_id=ids.new_id("dlv", int(time.time() * 1000)),
        attempt=1,
        sequence=1,
        test=False,
    )
    rows = [[k, v] for k, v in sent.items()] + [
        [k, mask_header_value(v)] for k, v in headers.items()
    ]
    return {
        "method": "POST",
        "headers": rows,
        "body": body.decode("utf-8"),
        "body_bytes": len(body),
    }
