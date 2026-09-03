"""
Async storage utilities for Temporal activities.

Provides async versions of storage operations to avoid thread pool exhaustion
in high-concurrency scenarios.

Uses httpx for async HTTP operations (already available in the project).
"""
from typing import Optional

import httpx
import requests
import structlog

logger = structlog.get_logger(__name__)

# Max file size: 100MB
MAX_AUDIO_FILE_SIZE = 100 * 1024 * 1024

# Timeout settings
DOWNLOAD_TIMEOUT = 200.0  # seconds


_AUDIO_FORMAT_TO_EXTENSION = {
    "mp3": "mp3",
    "mpeg": "mp3",
    "wav": "wav",
    "ogg": "ogg",
    "opus": "ogg",
    "flac": "flac",
    "aac": "aac",
    "m4a": "m4a",
    "mp4": "m4a",
    "webm": "webm",
    "wma": "wma",
    "aiff": "aiff",
    "au": "au",
}

# The extension is deliberately part of the object key: browser audio players
# use it as a fallback when the storage backend does not preserve content-type.
_REHOST_AUDIO_EXTENSIONS = tuple(dict.fromkeys(_AUDIO_FORMAT_TO_EXTENSION.values()))


def _rehost_object_key_base(
    call_id: str,
    url_type: str,
    project_id: Optional[str],
    provider: Optional[str],
) -> str:
    """Return the deterministic extension-less key for a recording artifact."""
    # Callers in the observability path always provide these values. Keeping a
    # stable fallback makes this helper backwards-compatible for direct users,
    # while still preventing an omitted value from masquerading as Vapi data.
    project_segment = str(project_id) if project_id else "unknown-project"
    provider_segment = str(provider).lower() if provider else "unknown-provider"
    return (
        f"call-recordings/{project_segment}/{provider_segment}/"
        f"{call_id}/{url_type}"
    )


def _rehost_object_key(object_key_base: str, extension: str) -> str:
    return f"{object_key_base}.{extension}"


def _existing_rehosted_audio(
    object_key_base: str,
) -> Optional[tuple[str, int]]:
    """Return a pre-existing durable recording URL and its stored byte size.

    The source URL on a later poll does not reliably retain an extension, so
    look up the small supported extension set instead of assuming MP3.
    """
    try:
        from tfc.settings.settings import UPLOAD_BUCKET_NAME
        from tfc.utils.storage_client import get_object_url, get_storage_client

        storage_client = get_storage_client()
    except Exception:
        return None
    for extension in _REHOST_AUDIO_EXTENSIONS:
        object_key = _rehost_object_key(object_key_base, extension)
        try:
            stat = storage_client.stat_object(UPLOAD_BUCKET_NAME, object_key)
            return get_object_url(UPLOAD_BUCKET_NAME, object_key), int(stat.size)
        except Exception:
            # A missing candidate is expected. Storage failures remain
            # best-effort as well: download/upload below gets a chance to run.
            continue
    return None


def _is_fagi_storage_url(url: Optional[str]) -> bool:
    """Use the canonical own-storage validation rather than hostname guesses."""
    from tracer.utils.vapi_recording import VapiRecordingService

    return bool(url) and VapiRecordingService.is_fagi_s3_url(url)


def _detected_audio_extension(audio_bytes: bytes) -> str:
    """Detect a supported audio extension without relabelling media as MP3."""
    from tfc.utils.storage import detect_audio_format

    detected_format = detect_audio_format(audio_bytes)
    # ffmpeg can return comma-separated format aliases (for example, mp4/m4a).
    for candidate in (detected_format or "").lower().split(","):
        extension = _AUDIO_FORMAT_TO_EXTENSION.get(candidate.strip())
        if extension:
            return extension
    raise ValueError(f"Unsupported or undetected audio format: {detected_format!r}")


async def download_audio_from_url_async(
    audio_url: Optional[str],
    max_retries: int = 5,
    timeout: float = DOWNLOAD_TIMEOUT,
    *,
    provider: Optional[str] = None,
    api_key: Optional[str] = None,
    call_id: Optional[str] = None,
    artifact_type: Optional[str] = None,
) -> bytes:
    """
    Async version of download_audio_from_url using httpx.

    Downloads audio file from URL with retries and size limits.
    Does NOT do audio format conversion (that would need sync code).

    Args:
        audio_url: URL to download audio from
        max_retries: Number of retry attempts
        timeout: Request timeout in seconds

    Returns:
        bytes: Raw audio data

    Raises:
        httpx.HTTPError: On download failure after retries
        ValueError: If file exceeds size limit
    """
    from tracer.utils.vapi_recording import VapiRecordingService

    if VapiRecordingService.is_authenticated_download(provider, api_key, call_id, artifact_type):
        return await VapiRecordingService.download_artifact_async(
            call_id=call_id,
            artifact_type=artifact_type,
            api_key=api_key,
            timeout_seconds=timeout,
        )

    if not audio_url:
        raise ValueError("audio_url is required for unauthenticated download")

    last_error = None

    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(max_retries):
            try:
                # Stream the response to handle large files
                async with client.stream("GET", audio_url) as response:
                    response.raise_for_status()

                    chunks = []
                    total_size = 0

                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        chunks.append(chunk)
                        total_size += len(chunk)

                        if total_size > MAX_AUDIO_FILE_SIZE:
                            raise ValueError(
                                f"Audio file exceeds maximum size of "
                                f"{MAX_AUDIO_FILE_SIZE / (1024 * 1024):.1f}MB"
                            )

                    audio_data = b"".join(chunks)
                    return audio_data

            except (httpx.HTTPError, httpx.StreamError) as e:
                last_error = e
                logger.warning(
                    "recording_download_failed",
                    attempt=attempt + 1,
                    error_type=type(e).__name__,
                )
                if attempt < max_retries - 1:
                    # Exponential backoff
                    import asyncio

                    await asyncio.sleep(2**attempt)
                continue

    raise last_error or httpx.HTTPError(f"Failed to download {audio_url}")


async def _convert_audio_url_to_s3_async_with_size(
    call_id: str,
    audio_url: Optional[str],
    url_type: str = "audio",
    *,
    provider: Optional[str] = None,
    api_key: Optional[str] = None,
    vapi_call_id: Optional[str] = None,
    artifact_type: Optional[str] = None,
    project_id: Optional[str] = None,
) -> tuple[str, int]:
    """Internal worker that does the download + upload and reports size.

    Returns (s3_url_or_original_on_failure, artifact_bytes). Existing
    deterministic objects return their stored size so a failed billing emit
    can be retried safely with the same idempotency key.
    """
    from tracer.utils.vapi_recording import VapiRecordingService

    # ``call_id`` positional is the S3-object-key ID. ``vapi_call_id`` kwarg
    # is the provider-side call ID used for the authenticated endpoint. In
    # the observability rehost path the two happen to be equal, so callers
    # that don't split them fall back to positional.
    auth_call_id = vapi_call_id or call_id
    vapi_authenticated = VapiRecordingService.is_authenticated_download(
        provider, api_key, auth_call_id, artifact_type
    )
    if not audio_url and not vapi_authenticated:
        return audio_url, 0

    # Only skip URLs verified as belonging to our configured storage. Provider
    # signed URLs can legitimately be hosted on S3 too.
    if _is_fagi_storage_url(audio_url):
        return audio_url, 0

    object_key_base = _rehost_object_key_base(
        call_id, url_type, project_id, provider
    )
    existing = _existing_rehosted_audio(object_key_base)
    if existing:
        return existing

    try:
        # SSRF guard: a recording URL arrives inside a provider's API response,
        # so validate its host resolves to a public address before we fetch it —
        # a tampered/unexpected value must not reach an internal or cloud-metadata
        # endpoint. Reuses the shared ssrf_guard classifier. Skips the
        # authenticated-provider download (fixed host, no free-form URL) and our
        # own storage (already returned above). Only the converter is guarded:
        # the raw download_audio_from_url_async intentionally stays open because
        # the LiveKit egress path fetches internal storage through it.
        if audio_url and not vapi_authenticated:
            import asyncio

            from tfc.utils.ssrf_guard import assert_url_host_public

            await asyncio.get_running_loop().run_in_executor(
                None, assert_url_host_public, audio_url
            )

        # Async download
        audio_bytes = await download_audio_from_url_async(
            audio_url,
            provider=provider,
            api_key=api_key,
            call_id=auth_call_id,
            artifact_type=artifact_type,
        )
        object_key = _rehost_object_key(
            object_key_base, _detected_audio_extension(audio_bytes)
        )

        # S3 upload (still sync - minio client doesn't have async support)
        # We use run_in_executor for just the upload, which is faster than download
        import asyncio

        # Use get_running_loop() to get the loop with the worker's large thread pool
        # (set in worker.py via loop.set_default_executor)
        loop = asyncio.get_running_loop()

        def do_upload():
            from tfc.utils.storage import upload_audio_to_s3

            # Deterministic key per (call_id, url_type) so retries overwrite
            # the same object instead of creating orphans. Required for
            # idempotent rehost.
            audio_data = {"bytes": audio_bytes}
            return upload_audio_to_s3(audio_data, object_key=object_key)

        # Run upload in thread pool (small operation compared to download)
        s3_url = await loop.run_in_executor(None, do_upload)

        return s3_url, len(audio_bytes)

    except Exception as e:
        logger.error(
            "recording_rehost_failed",
            artifact_type=url_type,
            error_type=type(e).__name__,
            exc_info=True,
        )
        # Return original URL on failure
        return audio_url, 0


async def convert_audio_url_to_s3_async(
    call_id: str,
    audio_url: Optional[str],
    url_type: str = "audio",
    *,
    provider: Optional[str] = None,
    api_key: Optional[str] = None,
    vapi_call_id: Optional[str] = None,
    artifact_type: Optional[str] = None,
    project_id: Optional[str] = None,
) -> str:
    """
    Async version of convert_audio_url_to_s3.

    Downloads audio from URL and uploads to S3/MinIO.

    Note: The S3 upload is still sync (minio client), but the download
    is async which is typically the bigger bottleneck.

    Args:
        call_id: Call ID for organizing S3 path
        audio_url: Source URL to download from
        url_type: Type for logging ("recording", "stereo_recording", etc.)

    Returns:
        str: S3 URL or original URL if conversion fails
    """
    s3_url, _ = await _convert_audio_url_to_s3_async_with_size(
        call_id,
        audio_url,
        url_type,
        provider=provider,
        api_key=api_key,
        vapi_call_id=vapi_call_id,
        artifact_type=artifact_type,
        project_id=project_id,
    )
    return s3_url


async def convert_audio_url_to_s3_async_with_size(
    call_id: str,
    audio_url: Optional[str],
    url_type: str = "audio",
    *,
    provider: Optional[str] = None,
    api_key: Optional[str] = None,
    vapi_call_id: Optional[str] = None,
    artifact_type: Optional[str] = None,
    project_id: Optional[str] = None,
) -> tuple[str, int]:
    """Like `convert_audio_url_to_s3_async` but also reports uploaded bytes.

    Returns (s3_url_or_original_on_failure, artifact_bytes). Existing
    deterministic objects return their stored size for safe billing retries.
    """
    return await _convert_audio_url_to_s3_async_with_size(
        call_id,
        audio_url,
        url_type,
        provider=provider,
        api_key=api_key,
        vapi_call_id=vapi_call_id,
        artifact_type=artifact_type,
        project_id=project_id,
    )


def convert_audio_url_to_s3_sync(
    call_id: str,
    audio_url: Optional[str],
    url_type: str = "audio",
    *,
    provider: Optional[str] = None,
    api_key: Optional[str] = None,
    vapi_call_id: Optional[str] = None,
    artifact_type: Optional[str] = None,
    project_id: Optional[str] = None,
) -> tuple[str, int]:
    """Sync mirror of ``_convert_audio_url_to_s3_async_with_size``.

    Downloads audio via ``requests`` and uploads to S3/MinIO.  Best-effort:
    on any exception the original URL is returned with 0 bytes.

    Returns (s3_url_or_original_on_failure, artifact_bytes). Existing
    deterministic objects return their stored size for safe billing retries.
    """
    from tracer.utils.vapi_recording import VapiRecordingService

    auth_call_id = vapi_call_id or call_id
    vapi_authenticated = VapiRecordingService.is_authenticated_download(
        provider, api_key, auth_call_id, artifact_type
    )

    if not audio_url and not vapi_authenticated:
        return audio_url, 0

    if _is_fagi_storage_url(audio_url):
        return audio_url, 0

    object_key_base = _rehost_object_key_base(
        call_id, url_type, project_id, provider
    )
    existing = _existing_rehosted_audio(object_key_base)
    if existing:
        return existing

    try:
        if vapi_authenticated:
            audio_bytes = VapiRecordingService.download_artifact_sync(
                call_id=auth_call_id,
                artifact_type=artifact_type,
                api_key=api_key,
            )
        else:
            # Unauthenticated sync download via requests with size guard
            response = requests.get(
                audio_url, stream=True, timeout=DOWNLOAD_TIMEOUT
            )
            response.raise_for_status()

            chunks = []
            total_size = 0
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    chunks.append(chunk)
                    total_size += len(chunk)
                    if total_size > MAX_AUDIO_FILE_SIZE:
                        raise ValueError(
                            f"Audio file exceeds maximum size of "
                            f"{MAX_AUDIO_FILE_SIZE / (1024 * 1024):.1f}MB"
                        )
            audio_bytes = b"".join(chunks)

        object_key = _rehost_object_key(
            object_key_base, _detected_audio_extension(audio_bytes)
        )

        # Upload to S3
        from tfc.utils.storage import upload_audio_to_s3

        s3_url = upload_audio_to_s3({"bytes": audio_bytes}, object_key=object_key)

        return s3_url, len(audio_bytes)

    except Exception as e:
        logger.error(
            "recording_rehost_failed",
            artifact_type=url_type,
            error_type=type(e).__name__,
            exc_info=True,
        )
        return audio_url, 0
