from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)


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
            return

        if DeploymentMode.is_oss():
            return

        from ee.usage.schemas.event_types import BillingEventType
        from ee.usage.schemas.events import UsageEvent
        from ee.usage.services.emitter import emit

        org_id_str = str(organization_id)

        if num_traces:
            emit(
                UsageEvent(
                    org_id=org_id_str,
                    event_type=BillingEventType.TRACING_EVENT,
                    amount=num_traces,
                    properties={"traces": num_traces, "source": source},
                )
            )

        if num_spans:
            emit(
                UsageEvent(
                    org_id=org_id_str,
                    event_type=BillingEventType.OBSERVE_ADD,
                    amount=payload_bytes or 0,
                    properties={"source": source, "spans": num_spans},
                )
            )
    except Exception:
        logger.debug("usage_metering_skipped", exc_info=True)
