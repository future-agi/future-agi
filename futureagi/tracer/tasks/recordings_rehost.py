"""
Recording rehost tasks.

Per-call activity that downloads external Vapi/Retell recording URLs and
re-hosts them on FAGI S3, overwriting the same `conversation.recording.*`
span_attribute keys in place. The provider's original URL is preserved
verbatim under `span_attributes["raw_log"]`.

Dispatched from `tracer.utils.observability_provider.process_and_store_logs`
via `transaction.on_commit` after each upsert.
"""

import asyncio

import structlog

from simulate.temporal.utils.async_storage import convert_audio_url_to_s3_async
from tfc.temporal import temporal_activity
from tracer.models.observability_provider import ProviderChoices
from tracer.models.observation_span import ObservationSpan
from tracer.utils.otel import ConversationAttributes

logger = structlog.get_logger(__name__)


# Recording attribute keys per provider — overwritten in place with S3 URLs
# after rehost. Raw provider URLs remain in span_attributes["raw_log"].
RECORDING_KEYS_BY_PROVIDER: dict[str, list[tuple[str, str]]] = {
    ProviderChoices.VAPI: [
        (
            f"{ConversationAttributes.CONVERSATION_RECORDING}.{ConversationAttributes.MONO_COMBINED}",
            "mono_combined",
        ),
        (
            f"{ConversationAttributes.CONVERSATION_RECORDING}.{ConversationAttributes.MONO_CUSTOMER}",
            "mono_customer",
        ),
        (
            f"{ConversationAttributes.CONVERSATION_RECORDING}.{ConversationAttributes.MONO_ASSISTANT}",
            "mono_assistant",
        ),
        (
            f"{ConversationAttributes.CONVERSATION_RECORDING}.{ConversationAttributes.STEREO}",
            "stereo",
        ),
    ],
    ProviderChoices.RETELL: [
        (
            f"{ConversationAttributes.CONVERSATION_RECORDING}.{ConversationAttributes.MONO_COMBINED}",
            "mono_combined",
        ),
        (
            f"{ConversationAttributes.CONVERSATION_RECORDING}.{ConversationAttributes.STEREO}",
            "stereo",
        ),
    ],
}


def is_already_s3_url(url: str) -> bool:
    return "amazonaws.com" in str(url) or "minio" in str(url)


def _resolve_call_id(span: ObservationSpan) -> str:
    """Best-effort call id used for the S3 object path."""
    attrs = span.span_attributes or {}
    raw_log = attrs.get("raw_log") or {}
    return (
        attrs.get("vapi.call_id")
        or raw_log.get("id")
        or raw_log.get("call_id")
        or (span.metadata or {}).get("provider_log_id")
        or str(span.id)
    )


@temporal_activity(
    max_retries=3,
    time_limit=600,
    queue="tasks_s",
)
def rehost_external_recordings(span_id: str) -> None:
    """Re-host external provider recording URLs (Vapi/Retell) on FAGI S3.

    Hands each non-S3 `conversation.recording.*` URL to
    `convert_audio_url_to_s3_async` (downloads run concurrently) and
    overwrites the key with the durable S3 URL. The helper returns the
    input URL unchanged on download failure, so the key falls back to the
    provider URL and a future tick can pick it up.

    `span_attributes["raw_log"]` is left untouched. Idempotent: URLs
    already on S3 are skipped before dispatch.
    """
    try:
        span = ObservationSpan.objects.get(id=span_id)
    except ObservationSpan.DoesNotExist:
        logger.warning("rehost_external_recordings: span not found", span_id=span_id)
        return

    keys = RECORDING_KEYS_BY_PROVIDER.get(span.provider) or []
    if not keys:
        return

    attrs = dict(span.span_attributes or {})
    jobs = [
        (key, attrs[key], url_type)
        for key, url_type in keys
        if attrs.get(key) and not is_already_s3_url(attrs[key])
    ]
    if not jobs:
        return

    call_id = _resolve_call_id(span)

    async def _rehost_all() -> list[str]:
        return await asyncio.gather(
            *(
                convert_audio_url_to_s3_async(call_id, url, url_type)
                for _, url, url_type in jobs
            )
        )

    results = asyncio.run(_rehost_all())

    changed = False
    for (key, original_url, url_type), s3_url in zip(jobs, results):
        if s3_url and s3_url != original_url:
            attrs[key] = s3_url
            changed = True
            logger.info(
                "rehost_external_recordings: uploaded to S3",
                call_id=call_id,
                url_type=url_type,
                s3_url=s3_url,
            )

    if not changed:
        return

    span.span_attributes = attrs
    span.save(update_fields=["span_attributes"])
    logger.info(
        "rehost_external_recordings: persisted recordings",
        span_id=span_id,
        provider=span.provider,
        call_id=call_id,
    )
