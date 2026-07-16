"""Per-call rehost of provider recordings to FA S3."""

from __future__ import annotations

import asyncio

import structlog
from django.db import transaction

from simulate.temporal.utils.async_storage import (
    convert_audio_url_to_s3_async_with_size,
)
from tfc.temporal import temporal_activity
from tracer.models.observability_provider import ProviderChoices
from tracer.models.observation_span import ObservationSpan
from tracer.selectors import get_agent_api_key
from tracer.utils.otel import ConversationAttributes
from tracer.utils.usage_emit import emit_span_ingestion_usage
from tracer.utils.vapi_recording import VapiRecordingService

logger = structlog.get_logger(__name__)


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
    return VapiRecordingService.is_fagi_s3_url(url)


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
    """Rehost a span's provider recording URLs to FA S3 and mirror to consumer fields."""
    try:
        span = ObservationSpan.objects.get(id=span_id)
    except ObservationSpan.DoesNotExist:
        logger.warning("rehost_external_recordings: span not found", span_id=span_id)
        return

    logger.info("rehost_external_recordings_start", span_id=span_id, provider=span.provider)

    api_key = None
    if span.provider == ProviderChoices.VAPI:
        api_key = get_agent_api_key(span.project_id, ProviderChoices.VAPI)
        if not api_key:
            logger.warning(
                "rehost_external_recordings: vapi_api_key_missing",
                span_id=span_id,
                project_id=str(span.project_id),
            )

    keys = RECORDING_KEYS_BY_PROVIDER.get(span.provider) or []
    if not keys:
        return

    already_billed = set((span.metadata or {}).get("rehost_billed_url_types", []))

    attrs = dict(span.span_attributes or {})
    jobs = [
        (key, attrs[key], url_type)
        for key, url_type in keys
        if attrs.get(key)
        and not is_already_s3_url(attrs[key])
        and url_type not in already_billed
    ]
    if not jobs:
        logger.info("rehost_external_recordings_no_jobs", span_id=span_id, provider=span.provider, reason="all_urls_already_s3_or_billed")
        return

    call_id = _resolve_call_id(span)
    is_vapi = span.provider == ProviderChoices.VAPI

    async def _rehost_all() -> list[tuple[str, int]]:
        return await asyncio.gather(
            *(
                convert_audio_url_to_s3_async_with_size(
                    call_id,
                    url,
                    url_type,
                    provider=(ProviderChoices.VAPI if is_vapi else None),
                    api_key=(api_key if is_vapi else None),
                    artifact_type=(
                        VapiRecordingService.artifact_for_url_type(url_type)
                        if is_vapi
                        else None
                    ),
                )
                for _, url, url_type in jobs
            )
        )

    results = asyncio.run(_rehost_all())

    successful: list[tuple[str, str, str, int]] = []  # (key, url_type, s3_url, size)
    for (key, original_url, url_type), (s3_url, size) in zip(jobs, results):
        if s3_url and s3_url != original_url:
            successful.append((key, url_type, s3_url, size))
            logger.info(
                "rehost_external_recordings: uploaded to S3",
                call_id=call_id,
                url_type=url_type,
                s3_url=s3_url,
                bytes=size,
            )

    if not successful:
        return

    s3_url_by_url_type = {url_type: s3_url for (_, url_type, s3_url, _) in successful}

    with transaction.atomic():
        locked = (
            ObservationSpan.objects.select_for_update().filter(id=span_id).first()
        )
        if not locked:
            return

        current_billed = set(
            (locked.metadata or {}).get("rehost_billed_url_types", [])
        )
        to_bill_types = {ut for (_, ut, _, _) in successful if ut not in current_billed}
        bytes_to_bill = sum(
            size for (_, ut, _, size) in successful if ut in to_bill_types
        )

        new_attrs = dict(locked.span_attributes or {})
        for (key, _, s3_url, _) in successful:
            new_attrs[key] = s3_url

        # Mirror the S3 URL onto every consumer-facing storage location
        # (flat span-attribute aliases, CallExecution + Snapshot columns)
        # so downstream readers never see a raw provider URL.
        new_attrs = VapiRecordingService.mirror_s3_url_to_consumer_fields(
            attrs=new_attrs,
            call_id=call_id,
            s3_url_by_url_type=s3_url_by_url_type,
        )

        md = dict(locked.metadata or {})
        md["rehost_billed_url_types"] = sorted(current_billed | to_bill_types)

        locked.span_attributes = new_attrs
        locked.metadata = md
        locked.save(update_fields=["span_attributes", "metadata"])

        org_id = locked.project.organization_id

    logger.info(
        "rehost_external_recordings: persisted recordings",
        span_id=span_id,
        provider=span.provider,
        call_id=call_id,
        uploaded_bytes=bytes_to_bill,
    )

    if bytes_to_bill:
        emit_span_ingestion_usage(
            organization_id=org_id,
            num_traces=0,
            num_spans=0,
            payload_bytes=bytes_to_bill,
            source="voice_recording_rehost",
        )
