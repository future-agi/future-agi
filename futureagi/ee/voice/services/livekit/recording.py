"""LiveKit recording post-processing — egress polling + streaming upload.

Streaming pipeline: S3 egress file → (optional ffmpeg) → S3 final destination.
All I/O is streaming — peak memory is O(chunk_size), never O(file_size).
No temp files or large in-memory buffers, safe for distributed workers.
"""

from __future__ import annotations

import asyncio
import subprocess
import threading
from typing import TYPE_CHECKING

import boto3
import structlog
from livekit.api import ListEgressRequest, LiveKitAPI
from livekit.protocol.egress import EgressStatus

from tfc.utils.storage import upload_stream_to_s3

if TYPE_CHECKING:
    from livekit.protocol.egress import EgressInfo

    from ee.voice.services.livekit.config import LiveKitConfig

logger = structlog.get_logger(__name__)

# Egress polling parameters
_POLL_INITIAL_DELAY = 2.0  # seconds
_POLL_MAX_DELAY = 10.0  # seconds
_POLL_MAX_ATTEMPTS = 30  # ~2.5 min max wait
_POLL_BACKOFF_FACTOR = 1.5

_TERMINAL_STATUSES = {
    EgressStatus.EGRESS_COMPLETE,
    EgressStatus.EGRESS_FAILED,
    EgressStatus.EGRESS_ABORTED,
    EgressStatus.EGRESS_LIMIT_REACHED,
}

# Streaming I/O
_STREAM_CHUNK_SIZE = 1024 * 1024  # 1 MB chunks


# ---------------------------------------------------------------------------
# Egress polling
# ---------------------------------------------------------------------------


async def poll_egress_completion(
    lkapi: LiveKitAPI,
    egress_id: str,
    max_attempts: int = _POLL_MAX_ATTEMPTS,
) -> EgressInfo | None:
    """Poll egress until terminal status. Returns EgressInfo or None on timeout."""
    delay = _POLL_INITIAL_DELAY

    for attempt in range(1, max_attempts + 1):
        try:
            resp = await lkapi.egress.list_egress(
                ListEgressRequest(egress_id=egress_id)
            )
            if not resp.items:
                logger.warning(
                    "livekit_egress_not_found",
                    egress_id=egress_id,
                    attempt=attempt,
                )
                return None

            info = resp.items[0]
            logger.debug(
                "livekit_egress_poll",
                egress_id=egress_id,
                status=info.status,
                attempt=attempt,
            )

            if info.status in _TERMINAL_STATUSES:
                return info

        except Exception:
            logger.warning(
                "livekit_egress_poll_error",
                egress_id=egress_id,
                attempt=attempt,
            )

        await asyncio.sleep(delay)
        delay = min(delay * _POLL_BACKOFF_FACTOR, _POLL_MAX_DELAY)

    logger.warning("livekit_egress_poll_timeout", egress_id=egress_id)
    return None


# ---------------------------------------------------------------------------
# S3 egress bucket helpers
# ---------------------------------------------------------------------------


def _create_s3_client(config: LiveKitConfig):
    """Create a boto3 S3 client for the egress bucket."""
    return boto3.client(
        "s3",
        aws_access_key_id=config.s3_access_key,
        aws_secret_access_key=config.s3_secret_key,
        region_name=config.s3_region,
    )


def delete_egress_recording(config: LiveKitConfig, object_key: str) -> None:
    """Delete a recording file from the S3 egress bucket."""
    client = _create_s3_client(config)
    client.delete_object(Bucket=config.egress_bucket, Key=object_key)
    logger.info(
        "livekit_egress_deleted",
        bucket=config.egress_bucket,
        object_key=object_key,
    )


# ---------------------------------------------------------------------------
# Streaming pipeline: S3 egress → (ffmpeg) → S3 final
# ---------------------------------------------------------------------------


def _feed_pipe(source_iter, pipe) -> None:
    """Stream chunks from an iterator into a writable pipe, then close it.

    Catches BrokenPipeError so the thread exits cleanly if ffmpeg
    terminates early (error / killed).
    """
    try:
        for chunk in source_iter:
            pipe.write(chunk)
    except (BrokenPipeError, OSError):
        pass  # ffmpeg exited early
    finally:
        pipe.close()


def _drain_pipe(pipe, max_bytes: int = 8192) -> bytes:
    """Read up to *max_bytes* from a pipe for error diagnostics.

    Drains any data beyond *max_bytes* so the pipe never blocks.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = pipe.read(4096)
        if not chunk:
            break
        if total < max_bytes:
            keep = chunk[: max_bytes - total]
            chunks.append(keep)
        total += len(chunk)
    return b"".join(chunks)


def _s3_stream_iter(config: LiveKitConfig, object_key: str):
    """Yield chunks from an S3 object as a streaming iterator."""
    client = _create_s3_client(config)
    response = client.get_object(Bucket=config.egress_bucket, Key=object_key)
    return response["Body"].iter_chunks(chunk_size=_STREAM_CHUNK_SIZE)


def stream_egress_to_s3(
    config: LiveKitConfig,
    object_key: str,
    s3_key: str,
    ffmpeg_args: list[str] | None = None,
) -> str:
    """Stream a recording from the egress S3 location to its final S3 key.

    If *ffmpeg_args* is ``None``, performs a direct stream copy.
    Otherwise pipes through ffmpeg for channel extraction / mixing.

    Threading model (deadlock-free):
      - Thread A (feeder): S3 chunks → ffmpeg stdin, closes stdin on EOF
      - Thread B (drainer): ffmpeg stderr → buffer (prevents pipe fill)
      - Main thread: ffmpeg stdout → S3 multipart upload

    Back-pressure flows naturally: slow S3 upload → stdout buffer fills →
    ffmpeg blocks on write → stops reading stdin → feeder blocks.
    All three pipes are serviced concurrently, so no circular wait.
    """
    if ffmpeg_args is None:
        # Direct stream copy: S3 Body → S3 destination (no processing).
        # Body has .read() as required by minio; the chunk iterator does not.
        client = _create_s3_client(config)
        response = client.get_object(Bucket=config.egress_bucket, Key=object_key)
        return upload_stream_to_s3(response["Body"], s3_key)

    source_iter = _s3_stream_iter(config, object_key)

    # Pipe through ffmpeg: S3 → stdin → stdout → S3
    cmd = [
        "ffmpeg",
        "-i",
        "-",  # read from stdin
        *ffmpeg_args,
        "-f",
        "mp3",
        "-codec:a",
        "libmp3lame",
        "-q:a",
        "2",  # VBR quality ~190kbps
        "-",  # write to stdout
    ]
    process = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Thread A: feed S3 stream → ffmpeg stdin
    feeder = threading.Thread(
        target=_feed_pipe,
        args=(source_iter, process.stdin),
        daemon=True,
    )

    # Thread B: drain stderr to prevent pipe buffer deadlock
    stderr_buf: list[bytes] = [b""]

    def _drain_stderr():
        stderr_buf[0] = _drain_pipe(process.stderr)

    stderr_drainer = threading.Thread(target=_drain_stderr, daemon=True)

    feeder.start()
    stderr_drainer.start()

    try:
        # Main thread: ffmpeg stdout → S3 streaming multipart upload
        url = upload_stream_to_s3(process.stdout, s3_key)
    except Exception:
        process.kill()
        raise
    finally:
        feeder.join(timeout=30)
        if feeder.is_alive():
            logger.warning("livekit_ffmpeg_feeder_join_timeout")
        stderr_drainer.join(timeout=10)
        if stderr_drainer.is_alive():
            logger.warning("livekit_ffmpeg_stderr_drainer_join_timeout")
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            logger.warning("livekit_ffmpeg_process_wait_timeout")
            process.kill()
            process.wait(timeout=5)

    if process.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed (rc={process.returncode}): "
            f"{stderr_buf[0].decode('utf-8', errors='replace')}"
        )

    return url
