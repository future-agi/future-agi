"""
Recording rehost tasks.

Per-call activity that downloads external Vapi/Retell recording URLs and
re-hosts them on FAGI S3, overwriting the same `conversation.recording.*`
span_attribute keys in place. The provider's original URL is preserved
verbatim under `span_attributes["raw_log"]`.

Dispatched from `tracer.utils.observability_provider.process_and_store_logs`
via `transaction.on_commit` after each upsert.
"""

import uuid

import structlog

from tfc.temporal import temporal_activity
from tfc.utils.storage import download_audio_from_url, upload_audio_to_s3
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


def _convert_audio_url_to_s3(call_id: str, audio_url: str, url_type: str) -> str:
    """Sync download-then-upload of an external audio URL onto FAGI S3.

    Returns the FAGI S3 URL on success, or the input URL unchanged on
    failure so the caller can detect "no rehost happened" by comparing
    input vs output.
    """
    try:
        audio_bytes = download_audio_from_url(audio_url)
        object_key = f"call-recordings/{call_id}/{uuid.uuid4()}.mp3"
        s3_url = upload_audio_to_s3({"bytes": audio_bytes}, object_key=object_key)
        logger.info(
            "rehost_external_recordings: uploaded to S3",
            call_id=call_id,
            url_type=url_type,
            s3_url=s3_url,
        )
        return s3_url
    except Exception as exc:
        logger.warning(
            "rehost_external_recordings: convert failed",
            call_id=call_id,
            url_type=url_type,
            audio_url=audio_url,
            error=str(exc),
        )
        return audio_url


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

    Reads `conversation.recording.*` keys from the span, downloads each URL
    that isn't already on S3, uploads to FAGI S3, and overwrites the same
    keys with the durable S3 URL. `span_attributes["raw_log"]` is left
    untouched. Idempotent: URLs already containing "amazonaws.com" or
    "minio" are skipped, so retries and re-runs are safe.

    On per-URL download failure, `_convert_audio_url_to_s3` returns the
    input URL unchanged — we leave the key as-is so a future tick can
    pick it up.
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
    call_id = _resolve_call_id(span)
    changed = False

    for key, url_type in keys:
        url = attrs.get(key)
        if not url or is_already_s3_url(url):
            continue

        s3_url = _convert_audio_url_to_s3(call_id, url, url_type)
        if s3_url and s3_url != url:
            attrs[key] = s3_url
            changed = True

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
