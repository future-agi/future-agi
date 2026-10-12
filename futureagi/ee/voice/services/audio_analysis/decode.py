"""Bounded object reads and native-rate, manifest-selected mono decoding."""

import hashlib
import io
import time
from dataclasses import dataclass

import numpy as np

from ee.voice.services.audio_metrics import _SILENCE_POWER, _frame_power
from ee.voice.services.audio_provenance import validate_object_key

from .constants import (
    ALLOWED_CHANNELS,
    MAX_DURATION_SECONDS,
    MAX_ENCODED_BYTES,
    MAX_SAMPLE_RATE_HZ,
    MAX_TARGET_SAMPLES,
    MIN_DURATION_SECONDS,
    MIN_SAMPLE_RATE_HZ,
    SUPPORTED_SUBTYPES,
)
from .errors import AudioDeterministicError, AudioLimitError, AudioProvenanceError


@dataclass(frozen=True)
class DecodedAudio:
    waveform: np.ndarray
    sample_rate_hz: int
    original_channel_count: int
    sha256: str
    codec: str

    @property
    def target_sample_count(self):
        return len(self.waveform)

    @property
    def duration_seconds(self):
        return len(self.waveform) / self.sample_rate_hz


def validate_encoded_size(size):
    if not isinstance(size, int) or size < 0:
        raise AudioDeterministicError("storage_unavailable")
    if size > MAX_ENCODED_BYTES:
        raise AudioLimitError()


def validate_header(header):
    if not MIN_SAMPLE_RATE_HZ <= header.samplerate <= MAX_SAMPLE_RATE_HZ:
        raise AudioDeterministicError("unsupported_sample_rate")
    if (
        header.channels not in ALLOWED_CHANNELS
        or header.subtype not in SUPPORTED_SUBTYPES.get(header.format, set())
    ):
        raise AudioDeterministicError("unsupported_format")
    if header.frames == 0:
        raise AudioDeterministicError("empty_audio")
    if header.frames < 0:
        raise AudioDeterministicError("invalid_audio")
    if (
        header.frames > MAX_TARGET_SAMPLES
        or header.frames / header.samplerate > MAX_DURATION_SECONDS
    ):
        raise AudioLimitError()


def _storage_call(method, **kwargs):
    from botocore.exceptions import ClientError

    try:
        return method(**kwargs)
    except ClientError as exc:
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        code = exc.response.get("Error", {}).get("Code")
        if status in {401, 403} or code in {
            "AccessDenied",
            "ExpiredToken",
            "InvalidAccessKeyId",
            "SignatureDoesNotMatch",
        }:
            raise AudioDeterministicError("provider_access_denied") from None
        if status in {404, 412} or code in {
            "NoSuchKey",
            "NoSuchVersion",
            "PreconditionFailed",
        }:
            raise AudioDeterministicError("storage_unavailable") from None
        # Transport failures and 5xx retain their retryable class for the worker.
        raise


def fetch_audio(s3, *, bucket, key, call_id, expected_version=None, sleep=time.sleep):
    """Use an injected deployment S3 client, never a caller-supplied URL."""
    validate_object_key(key, call_id)
    if not bucket or "/" in bucket or ":" in bucket:
        raise AudioProvenanceError("analysis_error")
    head = _storage_call(s3.head_object, Bucket=bucket, Key=key)
    validate_encoded_size(head["ContentLength"])
    if expected_version and expected_version not in {
        head.get("ETag"),
        head.get("VersionId"),
    }:
        sleep(2)
        head = _storage_call(s3.head_object, Bucket=bucket, Key=key)
        validate_encoded_size(head["ContentLength"])
        if expected_version not in {head.get("ETag"), head.get("VersionId")}:
            raise AudioDeterministicError("storage_unavailable")
    conditions = {}
    if head.get("VersionId"):
        conditions["VersionId"] = head["VersionId"]
    elif head.get("ETag"):
        conditions["IfMatch"] = head["ETag"]
    response = _storage_call(s3.get_object, Bucket=bucket, Key=key, **conditions)
    body = response["Body"]
    try:
        if expected_version and expected_version not in {
            response.get("ETag"),
            response.get("VersionId"),
        }:
            raise AudioDeterministicError("storage_unavailable")
        buf = io.BytesIO()
        limit = min(head["ContentLength"], MAX_ENCODED_BYTES)
        while True:
            chunk = body.read(min(1024 * 1024, limit + 1 - buf.tell()))
            if not chunk:
                break
            buf.write(chunk)
            if buf.tell() > MAX_ENCODED_BYTES:
                raise AudioLimitError()
            if buf.tell() > limit:
                raise AudioDeterministicError("storage_unavailable")
        if buf.tell() != head["ContentLength"]:
            raise AudioDeterministicError("storage_unavailable")
        return buf.getvalue()
    finally:
        body.close()


def decode_audio(
    data: bytes, *, channel_index=None, expected_channels=None
) -> DecodedAudio:
    import soundfile as sf

    validate_encoded_size(len(data))
    if not data:
        raise AudioDeterministicError("invalid_audio")
    try:
        with sf.SoundFile(io.BytesIO(data)) as audio:
            validate_header(audio)
            sr, channels = audio.samplerate, audio.channels
            if expected_channels is not None and expected_channels != channels:
                raise AudioProvenanceError("unknown_agent_track")
            if channels == 2 and (
                type(channel_index) is not int or channel_index not in {0, 1}
            ):
                raise AudioProvenanceError("unknown_agent_track")
            if channels == 1 and channel_index not in (None, 0):
                raise AudioProvenanceError("unknown_agent_track")
            selected = channel_index if channel_index is not None else 0
            # Allocate only the bounded target channel; decode transient 1s chunks.
            target = np.empty(audio.frames, dtype=np.float32)
            count = 0
            while True:
                chunk = audio.read(sr, dtype="float32", always_2d=True)
                if not len(chunk):
                    break
                end = count + len(chunk)
                if end > MAX_TARGET_SAMPLES or end / sr > MAX_DURATION_SECONDS:
                    raise AudioLimitError()
                if end > len(target):
                    raise AudioDeterministicError("invalid_audio")
                if not np.isfinite(chunk).all():
                    raise AudioDeterministicError("invalid_audio")
                target[count:end] = chunk[:, selected]
                count = end
            if count != audio.frames:
                raise AudioDeterministicError("invalid_audio")
            if count / sr < MIN_DURATION_SECONDS:
                raise AudioDeterministicError("insufficient_audio")
            if _frame_power(target, sr).max() < _SILENCE_POWER:
                raise AudioDeterministicError("no_voice")
            return DecodedAudio(
                target,
                sr,
                channels,
                hashlib.sha256(data).hexdigest(),
                audio.format.lower(),
            )
    except (sf.LibsndfileError, RuntimeError, ValueError):
        raise AudioDeterministicError("invalid_audio") from None
