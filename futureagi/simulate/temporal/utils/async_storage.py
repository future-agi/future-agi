"""
Async storage utilities for Temporal activities.

Provides async versions of storage operations to avoid thread pool exhaustion
in high-concurrency scenarios.

Uses httpx for async HTTP operations (already available in the project).
"""
from typing import Optional

import httpx
import structlog

logger = structlog.get_logger(__name__)

# Max file size: 100MB
MAX_AUDIO_FILE_SIZE = 100 * 1024 * 1024

# Timeout settings
DOWNLOAD_TIMEOUT = 200.0  # seconds


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
                logger.debug(f"Downloading audio (attempt {attempt + 1}): {audio_url}")

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
                    logger.info(
                        f"Downloaded audio: {len(audio_data)} bytes from {audio_url}"
                    )
                    return audio_data

            except (httpx.HTTPError, httpx.StreamError) as e:
                last_error = e
                logger.warning(
                    f"Download attempt {attempt + 1} failed for {audio_url}: {e}"
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
) -> tuple[str, int]:
    """Internal worker that does the download + upload and reports size.

    Returns (s3_url_or_original_on_failure, bytes_uploaded_to_s3). The
    bytes count is 0 when the source URL was already on S3 or the upload
    did not succeed; callers can use that to decide whether to bill.
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

    # Check if already an S3 URL
    if audio_url and ("amazonaws.com" in str(audio_url) or "minio" in str(audio_url)):
        logger.info(f"{url_type} URL is already S3: {audio_url}")
        return audio_url, 0

    try:
        logger.info(f"Converting {url_type} URL to S3: {audio_url}")

        # Async download
        audio_bytes = await download_audio_from_url_async(
            audio_url,
            provider=provider,
            api_key=api_key,
            call_id=auth_call_id,
            artifact_type=artifact_type,
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
            object_key = f"call-recordings/{call_id}/{url_type}.mp3"
            audio_data = {"bytes": audio_bytes}
            return upload_audio_to_s3(audio_data, object_key=object_key)

        # Run upload in thread pool (small operation compared to download)
        s3_url = await loop.run_in_executor(None, do_upload)

        logger.info(f"Successfully converted {url_type} URL to S3: {s3_url}")
        return s3_url, len(audio_bytes)

    except Exception as e:
        logger.error(f"Error converting {url_type} URL to S3: {e}")
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
) -> tuple[str, int]:
    """Like `convert_audio_url_to_s3_async` but also reports uploaded bytes.

    Returns (s3_url_or_original_on_failure, bytes_uploaded). The bytes
    value is 0 when nothing was uploaded (already-on-S3 / failure), so
    billing call sites can sum it directly without re-checking the URL.
    """
    return await _convert_audio_url_to_s3_async_with_size(
        call_id,
        audio_url,
        url_type,
        provider=provider,
        api_key=api_key,
        vapi_call_id=vapi_call_id,
        artifact_type=artifact_type,
    )
