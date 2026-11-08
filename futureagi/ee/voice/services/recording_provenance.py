"""Produce internal artifacts only from verified deployment storage URLs."""

import asyncio

from django.conf import settings

from ee.voice.services.audio_provenance import ProvenanceArtifact
from ee.voice.services.audio_storage import get_audio_storage

PIN_TOTAL_TIMEOUT_SECONDS = 10
PIN_ATTEMPT_TIMEOUT_SECONDS = 4


async def _pin_artifact(artifact, bucket):
    def head_object():
        return get_audio_storage().head_object(Bucket=bucket, Key=artifact.object_key)

    for attempt in range(2):
        try:
            head = await asyncio.wait_for(
                asyncio.to_thread(head_object), timeout=PIN_ATTEMPT_TIMEOUT_SECONDS
            )
            version = next(
                (
                    value
                    for value in (head.get("VersionId"), head.get("ETag"))
                    if isinstance(value, str) and value.strip() and value != "null"
                ),
                None,
            )
            if version:
                artifact.object_version_or_etag = version
                return
        except Exception:
            # Pinning is optional; an unpinned target cannot be scheduled.
            pass
        if attempt == 0:
            await asyncio.sleep(0.1)


async def recording_artifacts(recordings, *, pin_versions=False):
    from tfc.utils.storage import is_own_storage_url
    from tfc.utils.storage_client import extract_object_key

    artifacts = []
    bucket = settings.UPLOAD_BUCKET_NAME
    for role, url in {
        "combined": recordings.recording_url,
        "stereo": recordings.stereo_recording_url,
        "assistant": recordings.assistant_recording_url,
        "customer": recordings.customer_recording_url,
    }.items():
        if not url or not is_own_storage_url(url, bucket):
            continue
        try:
            key = extract_object_key(url, bucket)
        except (IndexError, ValueError):
            continue
        artifacts.append(
            ProvenanceArtifact(role, key, None, codec=key.rsplit(".", 1)[-1])
        )
    if pin_versions and settings.VOICE_AUDIO_METRICS_ENABLED and artifacts:
        try:
            await asyncio.wait_for(
                asyncio.gather(
                    *(_pin_artifact(artifact, bucket) for artifact in artifacts)
                ),
                timeout=PIN_TOTAL_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            # Cancel pending awaits, not playback persistence. Late HEAD results
            # cannot mutate the returned artifacts; unpinned versions remain None.
            pass
    return artifacts
