"""Bounded recording segments and F01 ``RecordingManifest`` construction (B10).

Pure planning/validation lives here so the importer, the SDK segment path and
the derivative worker share one definition of a playable, gap-honest manifest.
A manifest is published only after every segment was validated in the sandbox
and the recording's policy/deletion fences still hold.
"""

import json
import math
import uuid
from dataclasses import dataclass

from tracer.services.attachments.contract import AttachmentError

HEADER_BYTES = 44
MANIFEST_MAX_BYTES = 1024 * 1024
MAX_SEGMENTS = 65536
LAYOUTS = {"mixed", "stereo", "separate_tracks"}
GAP_REASONS = {
    "queue_full",
    "bootstrap_pending",
    "source_unavailable",
    "policy_denied",
    "truncated",
    "incomplete",
}
ROLES = {"user", "assistant", "mixed", "unknown"}
DIRECTIONS = {"input", "output", "mixed"}
PLAYOUT_EVIDENCE = {"generated", "played", "unknown", "truncated"}
MAX_MS = 7_200_000


@dataclass(frozen=True)
class Limits:
    """Negotiated F01 ``Limits`` subset used for recordings and imports."""

    max_upload_bytes: int
    segment_bytes: int
    segment_duration_ms: int
    recording_bytes: int
    recording_duration_ms: int
    reserved_bytes: int

    @classmethod
    def from_wire(cls, value):
        return cls(**{field: value[field] for field in cls.__dataclass_fields__})

    @property
    def window(self):
        """One output segment window: min(max_upload_bytes, segment_bytes)."""
        return min(self.max_upload_bytes, self.segment_bytes)


@dataclass(frozen=True)
class SegmentPlan:
    index: int
    start_frame: int
    frame_count: int
    start_ms: int
    duration_ms: int
    byte_length: int


def ceil_ms(frames, rate):
    return -(-frames * 1000 // rate)


def segment_frames(*, rate, channels, limits):
    """Largest whole-frame segment within BOTH the byte and duration bounds.

    Rounded down to a whole-millisecond frame count when possible, so every
    internal boundary is exact and only the final one is rounded up.
    """
    align = channels * 2
    by_bytes = (limits.window - HEADER_BYTES) // align
    by_time = rate * limits.segment_duration_ms // 1000
    frames = min(by_bytes, by_time)
    unit = rate // math.gcd(rate, 1000)
    if frames >= unit:
        frames -= frames % unit
    if frames < 1:
        # A single frame plus its container cannot fit: reject, never overrun.
        raise AttachmentError("too_large", 413)
    return frames


def plan_segments(*, frame_count, rate, channels, limits):
    if frame_count < 1:
        raise AttachmentError("validation_failed", 422)
    per = segment_frames(rate=rate, channels=channels, limits=limits)
    count = math.ceil(frame_count / per)
    if count > MAX_SEGMENTS:
        raise AttachmentError("too_large", 413)
    plans = []
    for index in range(count):
        start = index * per
        frames = min(per, frame_count - start)
        start_ms = ceil_ms(start, rate)
        plans.append(
            SegmentPlan(
                index=index,
                start_frame=start,
                frame_count=frames,
                start_ms=start_ms,
                duration_ms=ceil_ms(start + frames, rate) - start_ms,
                byte_length=HEADER_BYTES + frames * channels * 2,
            )
        )
    return plans


def tracks_for(*, channels, direction, track_ids):
    """Imported sources: no speaker/playout evidence, so nothing is asserted.

    Mono is one ``mixed`` track. Stereo keeps both channels as separately
    playable tracks, but each keeps the source's direction and an ``unknown``
    role: left is never implicitly the customer.
    """
    if channels == 1:
        # A single-direction mono source is one isolated track, not a "mix".
        layout = "mixed" if direction == "mixed" else "separate_tracks"
        return layout, [
            {
                "track_id": str(track_ids[0]),
                "channels": [0],
                "direction": direction,
                "role": "mixed" if direction == "mixed" else "unknown",
            }
        ]
    if channels == 2:
        return "stereo", [
            {
                "track_id": str(track_ids[index]),
                "channels": [index],
                "direction": direction,
                "role": "unknown",
            }
            for index in range(2)
        ]
    raise AttachmentError("validation_failed", 422)


def build_manifest(
    *,
    recording_id,
    rate,
    channels,
    direction,
    track_ids,
    segments,
    duration_ms,
    gaps=(),
    capture_complete,
):
    """``segments`` is a sequence of (occurrence_id, SegmentPlan)."""
    layout, tracks = tracks_for(
        channels=channels, direction=direction, track_ids=track_ids
    )
    # separate_tracks needs per-interval track identity; one track here.
    track = {"track_id": tracks[0]["track_id"]} if layout == "separate_tracks" else {}
    manifest = {
        "schema_version": 1,
        "recording_id": str(recording_id),
        "timebase": "session_relative_ms",
        "sample_format": "s16le",
        "sample_rate_hz": rate,
        "channels": channels,
        "codec": "pcm",
        "container": "wav",
        "layout": layout,
        "tracks": tracks,
        "segments": [
            {
                "occurrence_id": str(occurrence_id),
                "index": plan.index,
                "start_ms": plan.start_ms,
                "duration_ms": plan.duration_ms,
                **track,
            }
            for occurrence_id, plan in segments
        ],
        "gaps": [{**gap, **track} for gap in gaps],
        "playout": [],
        "capture_complete": capture_complete,
        "duration_ms": duration_ms,
    }
    validate_manifest(manifest)
    return manifest


def _uuid(value):
    if not isinstance(value, str):
        raise ValueError
    parsed = uuid.UUID(value)
    if str(parsed) != value:
        raise ValueError
    return value


def _int(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError
    return value


def _interval(value, keys, *, max_duration):
    if not isinstance(value, dict) or not set(keys[0]) <= set(value) <= set(keys[1]):
        raise ValueError
    _int(value["start_ms"], 0, MAX_MS)
    _int(value["duration_ms"], 1, max_duration)
    if "track_id" in value:
        _uuid(value["track_id"])


def _check_manifest(value):
    required = {
        "schema_version",
        "recording_id",
        "timebase",
        "sample_format",
        "sample_rate_hz",
        "channels",
        "codec",
        "container",
        "layout",
        "tracks",
        "segments",
        "gaps",
        "playout",
        "capture_complete",
        "duration_ms",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError
    if len(json.dumps(value, separators=(",", ":")).encode()) > MANIFEST_MAX_BYTES:
        raise ValueError
    if (
        value["schema_version"] != 1
        or value["timebase"] != "session_relative_ms"
        or value["sample_format"] not in {"s16le", "s24le", "s32le", "f32le", "encoded"}
        or value["codec"] not in {"pcm", "opus", "mp3", "aac", "flac"}
        or value["container"] not in {"wav", "ogg", "mp3", "mp4", "webm", "flac", "raw"}
        or value["layout"] not in LAYOUTS
        or type(value["capture_complete"]) is not bool
    ):
        raise ValueError
    _uuid(value["recording_id"])
    _int(value["sample_rate_hz"], 8000, 192000)
    _int(value["channels"], 1, 32)
    _int(value["duration_ms"], 0, MAX_MS)
    tracks = value["tracks"]
    if not isinstance(tracks, list) or not 1 <= len(tracks) <= 32:
        raise ValueError
    for track in tracks:
        if (
            not isinstance(track, dict)
            or set(track) != {"track_id", "channels", "direction", "role"}
            or track["direction"] not in DIRECTIONS
            or track["role"] not in ROLES
            or not isinstance(track["channels"], list)
            or not 1 <= len(track["channels"]) <= 32
            or len(set(track["channels"])) != len(track["channels"])
        ):
            raise ValueError
        _uuid(track["track_id"])
        for channel in track["channels"]:
            _int(channel, 0, 31)
    for name in ("segments", "gaps", "playout"):
        if not isinstance(value[name], list) or len(value[name]) > MAX_SEGMENTS:
            raise ValueError
    for segment in value["segments"]:
        _interval(
            segment,
            (
                {"occurrence_id", "index", "start_ms", "duration_ms"},
                {"occurrence_id", "index", "start_ms", "duration_ms", "track_id"},
            ),
            max_duration=30000,
        )
        _uuid(segment["occurrence_id"])
        _int(segment["index"], 0, 65535)
    for gap in value["gaps"]:
        _interval(
            gap,
            (
                {"start_ms", "duration_ms", "reason"},
                {"start_ms", "duration_ms", "reason", "track_id"},
            ),
            max_duration=MAX_MS,
        )
        if gap["reason"] not in GAP_REASONS:
            raise ValueError
    for interval in value["playout"]:
        _interval(
            interval,
            (
                {"start_ms", "duration_ms", "evidence"},
                {"start_ms", "duration_ms", "evidence"},
            ),
            max_duration=MAX_MS,
        )
        if interval["evidence"] not in PLAYOUT_EVIDENCE:
            raise ValueError


def validate_manifest(value):
    """F01 RecordingManifest schema plus cross-field coverage invariants."""
    try:
        _check_manifest(value)
        if value["capture_complete"] and value["gaps"]:
            raise ValueError
        track_ids = [track["track_id"] for track in value["tracks"]]
        if len(track_ids) != len(set(track_ids)):
            raise ValueError
        channels = sorted(c for track in value["tracks"] for c in track["channels"])
        if channels != list(range(value["channels"])):
            raise ValueError
        if value["layout"] == "mixed" and any(
            track["direction"] != "mixed" for track in value["tracks"]
        ):
            raise ValueError
        if value["layout"] == "stereo" and value["channels"] != 2:
            raise ValueError
        seen, previous = set(), -1
        for index, segment in enumerate(value["segments"]):
            if (
                segment["index"] != index
                or segment["start_ms"] < previous
                or segment["occurrence_id"] in seen
            ):
                raise ValueError
            seen.add(segment["occurrence_id"])
            previous = segment["start_ms"]
        separate = value["layout"] == "separate_tracks"
        timelines: dict[str | None, list[dict]] = (
            {track: [] for track in track_ids} if separate else {None: []}
        )
        for interval in value["segments"] + value["gaps"]:
            track = interval.get("track_id")
            if track not in timelines:
                raise ValueError
            timelines[track].append(interval)
        for intervals in timelines.values():
            end = 0
            for interval in sorted(intervals, key=lambda item: item["start_ms"]):
                if interval["start_ms"] != end:
                    raise ValueError
                end += interval["duration_ms"]
            if end != value["duration_ms"]:
                raise ValueError
        for interval in value["segments"] + value["gaps"] + value["playout"]:
            if interval["start_ms"] + interval["duration_ms"] > value["duration_ms"]:
                raise ValueError
    except AttachmentError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError):
        raise AttachmentError("validation_failed", 422) from None
    return value


def with_truncation(manifest, *, captured_ms, limit_ms):
    """A recording stopped at a bound is incomplete with an explicit gap."""
    if captured_ms >= limit_ms:
        return manifest
    gap = {
        "start_ms": captured_ms,
        "duration_ms": limit_ms - captured_ms,
        "reason": "truncated",
    }
    if manifest["layout"] == "separate_tracks":
        gaps = [{**gap, "track_id": track["track_id"]} for track in manifest["tracks"]]
    else:
        gaps = [gap]
    result = dict(manifest, gaps=[*manifest["gaps"], *gaps], capture_complete=False)
    result["duration_ms"] = limit_ms
    return validate_manifest(result)
