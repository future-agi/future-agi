"""
Async storage utilities for Temporal activities.

Provides async versions of storage operations to avoid thread pool exhaustion
in high-concurrency scenarios.

Uses httpx for async HTTP operations (already available in the project).
"""

import uuid

import httpx
import structlog

logger = structlog.get_logger(__name__)

# Max file size: 100MB
MAX_AUDIO_FILE_SIZE = 100 * 1024 * 1024

# Timeout settings
DOWNLOAD_TIMEOUT = 200.0  # seconds


async def download_audio_from_url_async(
    audio_url: str,
    max_retries: int = 5,
    timeout: float = DOWNLOAD_TIMEOUT,
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


async def convert_audio_url_to_s3_async(
    call_id: str,
    audio_url: str,
    url_type: str = "audio",
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
    if not audio_url:
        return audio_url

    # Check if already an S3 URL
    if "amazonaws.com" in str(audio_url) or "minio" in str(audio_url):
        logger.info(f"{url_type} URL is already S3: {audio_url}")
        return audio_url

    try:
        logger.info(f"Converting {url_type} URL to S3: {audio_url}")

        # Async download
        audio_bytes = await download_audio_from_url_async(audio_url)

        # S3 upload (still sync - minio client doesn't have async support)
        # We use run_in_executor for just the upload, which is faster than download
        import asyncio

        # Use get_running_loop() to get the loop with the worker's large thread pool
        # (set in worker.py via loop.set_default_executor)
        loop = asyncio.get_running_loop()

        def do_upload():
            from tfc.utils.storage import upload_audio_to_s3

            object_key = f"call-recordings/{call_id}/{uuid.uuid4()}.mp3"
            audio_data = {"bytes": audio_bytes}
            return upload_audio_to_s3(audio_data, object_key=object_key)

        # Run upload in thread pool (small operation compared to download)
        s3_url = await loop.run_in_executor(None, do_upload)

        logger.info(f"Successfully converted {url_type} URL to S3: {s3_url}")
        return s3_url

    except Exception as e:
        logger.error(f"Error converting {url_type} URL to S3: {e}")
        # Return original URL on failure
        return audio_url
