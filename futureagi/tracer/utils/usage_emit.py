from __future__ import annotations

import structlog

from tfc.billing.boundary import BillingEventType, get_billing

logger = structlog.get_logger(__name__)


def _tracing_billing_mode(org_id_str: str) -> str:
    """Resolve the org's tracing billing mode (``events`` or ``storage``).

    Delegates to the billing boundary so OSS returns the ``storage`` default
    without touching ee.usage and EE goes through the boundary's cached
    OrganizationSubscription lookup.
    """
    return get_billing().get_tracing_billing_mode(org_id_str)


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
