"""Produce internal artifacts only from verified deployment storage URLs."""

import asyncio

from django.conf import settings

from ee.voice.services.audio_provenance import ProvenanceArtifact
from ee.voice.services.audio_storage import get_audio_storage


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
        version = None
        if pin_versions:
            try:
                head = await asyncio.to_thread(
                    get_audio_storage().head_object, Bucket=bucket, Key=key
                )
                version = head.get("VersionId") or head.get("ETag")
            except Exception:
                # A manifest may record an unknown version. Fetch still uses a
                # conditional read, and never falls back to the provider URL.
                pass
        artifacts.append(
            ProvenanceArtifact(role, key, version, codec=key.rsplit(".", 1)[-1])
        )
    return artifacts
