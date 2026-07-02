from __future__ import annotations

import importlib.util

import structlog

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
        try:
            from ee.usage.deployment import DeploymentMode
        except ImportError:
            # ee absent means OSS; ee present but failing to import means
            # billing would silently turn off — surface that instead.
            if importlib.util.find_spec("ee") is None:
                return
            raise

        if DeploymentMode.is_oss():
            return

        from ee.usage.schemas.event_types import BillingEventType
        from ee.usage.schemas.events import UsageEvent
        from ee.usage.services.emitter import emit

        org_id_str = str(organization_id)

        # Voice recording rehost lands real bytes in our S3 — bill storage
        # regardless of the org's tracing billing mode.
        if source == "voice_recording_rehost":
            if payload_bytes:
                emit(
                    UsageEvent(
                        org_id=org_id_str,
                        event_type=BillingEventType.OBSERVE_ADD,
                        amount=payload_bytes,
                        properties={"source": source},
                    )
                )
            return

        mode = _tracing_billing_mode(org_id_str)
        tracing_units = (num_traces or 0) + (num_spans or 0)

        if mode == "storage":
            if payload_bytes:
                props = {"source": source}
                if tracing_units:
                    props["units"] = tracing_units
                emit(
                    UsageEvent(
                        org_id=org_id_str,
                        event_type=BillingEventType.OBSERVE_ADD,
                        amount=payload_bytes,
                        properties=props,
                    )
                )
            return

        # events mode: payload_bytes is intentionally ignored; span storage
        # is not billed in events mode, and the only OBSERVE_ADD line in
        # events mode comes from the voice_recording_rehost branch above.
        if tracing_units:
            emit(
                UsageEvent(
                    org_id=org_id_str,
                    event_type=BillingEventType.TRACING_EVENT,
                    amount=tracing_units,
                    properties={"traces": tracing_units, "source": source},
                )
            )
    except Exception:
        logger.exception("usage_metering_skipped")
