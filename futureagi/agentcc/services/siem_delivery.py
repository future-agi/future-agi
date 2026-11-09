"""Queue and deliver SIEM exports (G16-SIEM).

Tool-call ingestion queues one delivery per enabled destination and stream in
the transaction that stores the calls (``enqueue``), so a call is queued
exactly when it is recorded and never for a destination that was off then.
``deliver_due`` (the ``agentcc_siem_deliver`` command; no schedule runs it
until the deployment adopts one) sends what is due. Nothing is queued or sent
while the deployment's export switch is off.

A worker leases a delivery before it posts, as the outbound webhooks do, and
records the attempt only while it still holds that lease: a worker whose lease
lapsed (another has taken the delivery since) writes nothing. An attempt has
at most DELIVERY_DEADLINE_SECONDS in all, however slowly a receiver answers;
the response body is never read. A 2xx delivers it. A 408, 429, 5xx, network
failure, refused address or the deadline retries after 30 s, doubling to at
most an hour (a 429's Retry-After is honoured up to an hour); any other
status, a redirect, a webhook without a usable signing secret or the last
attempt dead-letters it. The error kept is a status line or an error class,
never a body or a URL. Payloads apply the org's privacy redactor.
"""

import threading
from datetime import timedelta

import structlog
from django.db.models import Q
from django.utils import timezone

from agentcc.models import AgentccToolCall
from agentcc.models.siem import (
    STREAM_AUDIT_EVENTS,
    STREAM_SPANS,
    AgentccSIEMDelivery,
    AgentccSIEMDestination,
)
from agentcc.services import (
    siem_hec,
    siem_payloads,
    siem_redactor,
    siem_signing,
    siem_transport,
)
from agentcc.services.siem_settings import export_enabled

logger = structlog.get_logger(__name__)

BATCH_SIZE = 200
LEASE = timedelta(minutes=2)
FIRST_RETRY_SECONDS = 30
MAX_RETRY_SECONDS = 3600
REQUEST_TIMEOUT = (5, 10)
# A whole attempt, connect to status line; well inside LEASE.
DELIVERY_DEADLINE_SECONDS = 30
_RETRYABLE = {408, 425, 429}

Status = AgentccSIEMDelivery.Status


def retry_delay(attempts):
    return timedelta(
        seconds=min(
            FIRST_RETRY_SECONDS * (2 ** max(attempts - 1, 0)), MAX_RETRY_SECONDS
        )
    )


def enqueue(organization, event_ids):
    """Queue ``event_ids`` (just stored for ``organization``) for its enabled
    destinations; returns the number of deliveries created."""
    if not event_ids or not export_enabled():
        return 0
    destinations = AgentccSIEMDestination.objects.filter(
        organization=organization, enabled=True
    ).only("id", "streams")
    ids = list(dict.fromkeys(event_ids))
    rows = []
    for destination in destinations:
        for stream in (STREAM_AUDIT_EVENTS, STREAM_SPANS):
            if stream not in (destination.streams or []):
                continue
            for start in range(0, len(ids), BATCH_SIZE):
                chunk = ids[start : start + BATCH_SIZE]
                rows.append(
                    AgentccSIEMDelivery(
                        organization=organization,
                        destination=destination,
                        stream=stream,
                        event_ids=chunk,
                        event_count=len(chunk),
                    )
                )
    AgentccSIEMDelivery.objects.bulk_create(rows)
    return len(rows)


def _due(now):
    return Q(next_retry_at__isnull=True) | Q(next_retry_at__lte=now)


def _lease(delivery, now):
    """Take the delivery; returns the lease (its expiry) or None."""
    lease = now + LEASE
    taken = (
        AgentccSIEMDelivery.objects.filter(id=delivery.id, status=Status.PENDING)
        .filter(_due(now))
        .update(next_retry_at=lease)
    )
    if taken != 1:
        return None
    # Another worker may have recorded an attempt since the batch was read.
    delivery.refresh_from_db(fields=["attempts", "max_attempts", "next_retry_at"])
    return lease


def _request(destination, delivery, rows, now, redactor):
    """The URL, body and headers of one attempt."""
    if destination.kind == AgentccSIEMDestination.Kind.HEC:
        raw = siem_hec.encode_events(rows, redactor)
        return (
            destination.url,
            raw,
            siem_signing.headers(destination, delivery, raw, now),
        )
    if destination.kind == AgentccSIEMDestination.Kind.OTLP_HTTP:
        if delivery.stream == STREAM_SPANS:
            body, path = siem_payloads.spans(rows, redactor), "/v1/traces"
        else:
            body, path = siem_payloads.logs(rows, now, redactor), "/v1/logs"
        url = destination.url.rstrip("/") + path
    else:
        body = siem_payloads.envelope(
            delivery.stream, delivery.id, delivery.organization_id, now, redactor
        )
        if delivery.stream == STREAM_SPANS:
            body["otlp"] = siem_payloads.spans(rows, redactor)
        else:
            body["events"] = siem_payloads.audit_events(rows, redactor)
        url = destination.url
    raw = siem_signing.encode(body)
    headers = siem_signing.headers(destination, delivery, raw, now)
    return url, raw, headers


def _retry_after(response):
    value = (response.headers or {}).get("Retry-After", "")
    if value.isdigit():
        return min(int(value), MAX_RETRY_SECONDS)
    return 0


class LeaseLost(Exception):
    """The worker's lease lapsed; another worker owns the delivery now."""


def _finish(
    delivery,
    destination,
    now,
    *,
    lease,
    ok,
    code=None,
    error="",
    retry=True,
    wait=0,
    sent=True,
    event_count=None,
):
    """Record one attempt, only while ``lease`` (from _lease) still holds."""
    fields = {
        "attempts": delivery.attempts + 1,
        "last_attempt_at": now,
        "last_response_code": code,
        "last_error": error[:64],
    }
    if event_count is not None:
        fields["event_count"] = event_count
    if ok:
        fields.update(status=Status.DELIVERED, delivered_at=now, next_retry_at=None)
    elif retry and fields["attempts"] < delivery.max_attempts:
        delay = max(retry_delay(fields["attempts"]), timedelta(seconds=wait))
        fields.update(status=Status.PENDING, next_retry_at=now + delay)
    else:
        fields.update(status=Status.DEAD_LETTER, next_retry_at=None)
    held = AgentccSIEMDelivery.objects.filter(
        id=delivery.id, status=Status.PENDING, next_retry_at=lease
    ).update(**fields)
    if held != 1:
        raise LeaseLost()
    for name, value in fields.items():
        setattr(delivery, name, value)
    if not ok:
        stamp = {"last_failed_at": now}
    elif sent:
        stamp = {"last_delivered_at": now}
    else:
        stamp = {}
    if stamp:
        AgentccSIEMDestination.objects.filter(id=destination.id).update(**stamp)
    return ok


def _post(url, raw, headers, *, hec=False):
    """POST within DELIVERY_DEADLINE_SECONDS: (status, Retry-After seconds, error).

    requests bounds each read, not the whole response, so the exchange runs on
    a daemon thread the worker stops waiting for at the deadline. The body is
    never read; a late answer is dropped, and the attempt counts as failed."""
    outcome = {}
    session = siem_transport.open_session()

    def exchange():
        try:
            response = session.post(
                url,
                data=raw,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=False,
                stream=True,
            )
            try:
                error = ""
                if hec and 200 <= response.status_code < 300:
                    error = siem_hec.acknowledgement(response)
                outcome["result"] = (
                    response.status_code,
                    _retry_after(response),
                    error,
                )
            finally:
                response.close()
        except Exception as exc:  # network, TLS, SSRF refusal at connect time
            outcome["result"] = (None, 0, type(exc).__name__)
        finally:
            session.close()

    worker = threading.Thread(target=exchange, name="siem-delivery", daemon=True)
    worker.start()
    worker.join(DELIVERY_DEADLINE_SECONDS)
    return outcome.get("result") or (None, 0, "deadline_exceeded")


def _deliver_one(delivery, lease):
    destination = delivery.destination
    rows = list(
        AgentccToolCall.objects.filter(
            organization_id=delivery.organization_id,
            event_id__in=delivery.event_ids or [],
        ).order_by("started_at", "id")
    )
    now = timezone.now()
    finish = {"lease": lease}
    if not rows:
        # Every call of the batch is gone (retention): nothing to export.
        return _finish(
            delivery, destination, now, ok=True, sent=False, event_count=0, **finish
        )
    redactor = siem_redactor.org_redactor(delivery.organization_id)
    try:
        url, raw, headers = _request(destination, delivery, rows, now, redactor)
    except siem_signing.SigningSecretMissing:
        return _finish(
            delivery,
            destination,
            now,
            ok=False,
            error="signing_secret_missing",
            retry=False,
            **finish,
        )
    try:
        siem_transport.check_destination_url(url)
    except ValueError:
        return _finish(
            delivery, destination, now, ok=False, error="destination_refused", **finish
        )
    if destination.kind == AgentccSIEMDestination.Kind.HEC:
        code, wait, error = _post(url, raw, headers, hec=True)
    else:
        code, wait, error = _post(url, raw, headers)
    now = timezone.now()
    if code is None:
        return _finish(delivery, destination, now, ok=False, error=error, **finish)
    if 200 <= code < 300 and not error:
        return _finish(delivery, destination, now, ok=True, code=code, **finish)
    retry = code in _RETRYABLE or code >= 500
    return _finish(
        delivery,
        destination,
        now,
        ok=False,
        code=code,
        error=error or f"HTTP {code}",
        retry=retry,
        wait=wait if code == 429 else 0,
        **finish,
    )


def deliver_due(limit=100, delivery_ids=None):
    """Send the due deliveries of enabled destinations; returns the counts."""
    if not export_enabled():
        return {"delivered": 0, "failed": 0, "lease_lost": 0, "skipped": True}
    now = timezone.now()
    queryset = AgentccSIEMDelivery.objects.filter(
        status=Status.PENDING, destination__enabled=True
    ).filter(_due(now))
    if delivery_ids:
        queryset = queryset.filter(id__in=delivery_ids)
    candidates = list(
        queryset.select_related("destination").order_by("created_at")[:limit]
    )
    delivered = failed = lost = 0
    for delivery in candidates:
        lease = _lease(delivery, timezone.now())
        if lease is None:
            continue
        try:
            ok = _deliver_one(delivery, lease)
        except LeaseLost:
            # Another worker took the delivery after this lease lapsed; its
            # attempt is the one recorded.
            logger.warning("siem_delivery_lease_lost", delivery_id=str(delivery.id))
            lost += 1
            continue
        except Exception as exc:
            # An unexpected failure (an OTLP header value that no longer
            # decrypts, a row that cannot be encoded) is an attempt like any
            # other, so it backs off and dead-letters instead of retrying
            # every lease.
            logger.warning(
                "siem_delivery_error",
                delivery_id=str(delivery.id),
                error_type=type(exc).__name__,
            )
            ok = False
            try:
                delivery.refresh_from_db()
                _finish(
                    delivery,
                    delivery.destination,
                    timezone.now(),
                    lease=lease,
                    ok=False,
                    error=type(exc).__name__,
                )
            except LeaseLost:
                lost += 1
                continue
            except Exception:
                # The store itself failed; the lease ends on its own.
                logger.warning("siem_delivery_unrecorded", delivery_id=str(delivery.id))
        delivered += int(ok)
        failed += int(not ok)
    logger.info(
        "siem_deliveries_processed", delivered=delivered, failed=failed, lost=lost
    )
    return {
        "delivered": delivered,
        "failed": failed,
        "lease_lost": lost,
        "skipped": False,
    }
