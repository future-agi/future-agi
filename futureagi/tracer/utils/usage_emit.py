from __future__ import annotations

import structlog

from tfc.billing.boundary import BillingEventType, get_billing

logger = structlog.get_logger(__name__)


_MODE_CACHE_TTL = 300


def _tracing_billing_mode(org_id_str: str) -> str:
    """Resolve the org's tracing billing mode (``events`` or ``storage``).

    Mirrors ee.usage.services.billing_engine: the dimension we fill must be the
    one we bill, so the ``or "storage"`` fallback has to stay in sync with it.
    Cached in Redis (5 min TTL) — span ingest runs hot and the mode rarely
    changes; a stale read at month boundary at worst delays a single emit's
    dimension switch.
    """
    cache_key = f"tracing_billing_mode:{org_id_str}"
    try:
        from ee.usage.services.emitter import get_redis

        cached = get_redis().get(cache_key)
        if cached is not None:
            return cached if isinstance(cached, str) else cached.decode()
    except Exception:
        pass

    from ee.usage.models.usage import OrganizationSubscription

    mode = (
        OrganizationSubscription.objects.filter(
            organization_id=org_id_str, deleted=False
        )
        .values_list("tracing_billing_mode", flat=True)
        .first()
    ) or "storage"

    try:
        from ee.usage.services.emitter import get_redis

        get_redis().setex(cache_key, _MODE_CACHE_TTL, mode)
    except Exception:
        pass

    return mode


def emit_span_ingestion_usage(
    organization_id,
    num_traces: int,
    num_spans: int,
    payload_bytes: int,
    *,
    source: str,
) -> None:
    try:
        # In OSS, get_billing() returns _NoopBilling which silently no-ops.
        # In EE/Cloud, it delegates to ee.usage.
        billing = get_billing()
        org_id_str = str(organization_id)

        # Voice recording rehost lands real bytes in our S3 — bill storage
        # regardless of the org's tracing billing mode.
        if source == "voice_recording_rehost":
            if payload_bytes:
                billing.record_usage(
                    org_id_str,
                    BillingEventType.OBSERVE_ADD,
                    amount=payload_bytes,
                    source=source,
                )
            return

        mode = _tracing_billing_mode(org_id_str)
        tracing_units = (num_traces or 0) + (num_spans or 0)

        if mode == "storage":
            if payload_bytes:
                props: dict = {"source": source}
                if tracing_units:
                    props["units"] = tracing_units
                billing.record_usage(
                    org_id_str,
                    BillingEventType.OBSERVE_ADD,
                    amount=payload_bytes,
                    **props,
                )
            return

        # events mode: payload_bytes is intentionally ignored; span storage
        # is not billed in events mode, and the only OBSERVE_ADD line in
        # events mode comes from the voice_recording_rehost branch above.
        if tracing_units:
            billing.record_usage(
                org_id_str,
                BillingEventType.TRACING_EVENT,
                amount=tracing_units,
                traces=tracing_units,
                source=source,
            )
    except Exception:
        logger.exception("usage_metering_skipped")
