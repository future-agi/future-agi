"""Optional trace/Voice view adapter; the control-store reader is injected.

Only existing JSON observation-span envelopes gain an `attachments` member. No
URL, inferred provider, new route or playback grant is manufactured here.
"""

from datetime import UTC, datetime
from itertools import islice
from uuid import UUID, uuid5

from . import wire
from .binding import Parent, Scope
from .reads import MAX_PARENTS, ReadTarget, attachment_status_page
from .references import InvalidReference


def _request_scope(request, project_id):
    """The viewer's explicit scope, or None. Never adopts a null workspace."""
    organization = getattr(request, "organization", None) or getattr(
        getattr(request, "user", None), "organization", None
    )
    workspace = getattr(request, "workspace", None)
    try:
        return Scope(
            str(getattr(organization, "id", "")),
            str(getattr(workspace, "id", "")),
            str(project_id),
        )
    except InvalidReference:
        return None


def _enrich_parents(*, request, project_id, observations, reader):
    """One bounded authorized batch of (response row, explicit parent) pairs.

    Missing explicit workspace fails closed, rather than adopting an older
    null-workspace project. The reader additionally reauthorizes each parent;
    a trace read is not media permission.
    """
    if reader is None:
        return [], False
    scope = _request_scope(request, project_id)
    if scope is None:
        return [], False
    selected = [
        (observation, ReadTarget(scope, parent))
        for observation, parent in islice(observations, MAX_PARENTS)
    ]
    statuses, complete = attachment_status_page(
        viewer=request,
        targets=[target for _, target in selected],
        reader=reader,
        now=datetime.now(UTC),
    )
    for observation, target in selected:
        # No empty counts/placeholders for denied/unknown objects.
        if target in statuses:
            observation["attachments"] = statuses[target]
    return [(target, statuses.get(target, ())) for _, target in selected], complete


def enrich_trace_detail(*, request, result, reader=None):
    """Adapt the standard trace-detail `trace` field, never a Voice-shaped alias."""

    def parents():
        for observation in trace_observations(result):
            try:
                parent = Parent(
                    str(observation.get("trace", "")), str(observation.get("id", ""))
                )
            except InvalidReference:
                continue
            yield observation, parent

    _enrich_parents(
        request=request,
        project_id=(result.get("trace") or {}).get("project"),
        observations=parents(),
        reader=reader,
    )


def enrich_observations(*, request, project_id, observations, reader=None):
    """Adapt Voice detail rows, whose identity is explicitly named `trace_id`."""

    def parents():
        for observation in islice(observations, MAX_PARENTS):
            try:
                parent = Parent(
                    str(observation.get("trace_id", "")), str(observation.get("id", ""))
                )
            except InvalidReference:
                continue
            yield observation, parent

    return _enrich_parents(
        request=request,
        project_id=project_id,
        observations=parents(),
        reader=reader,
    )


RECORDING_PURPOSE = "conversation_recording"
# I04 coarse states. A recording is projected ready only with an authorized,
# complete authorized manifest (mixed, stereo, separate tracks, including
# explicit gaps/partial captures), or validated standalone audio facts. A multi-segment
# manifest is a playlist of unchanged occurrences, not a stitched media object;
# consumers play its segments in timeline order. Channel layouts use exact
# per-segment derivatives; readiness never asserts capture_complete.
_COARSE_RECORDING_STATES = {
    "pending": "pending",
    "pending_upload": "pending_upload",
    "importing": "importing",
    "validating": "validating",
    "partial": "waiting",
    "ready": "pending",
    "failed": "failed",
}


# Validated standalone audio type -> RecordingManifest (codec, container). The
# probe admits only Opus in Ogg; WebM and MP4 may also carry Vorbis/Opus/FLAC,
# which the codec enum cannot name, so the codec there is a hint: the player
# plays the served bytes by their validated MIME, never by this field.
STANDALONE_CODECS = {
    "audio/wav": ("pcm", "wav"),
    "audio/ogg": ("opus", "ogg"),
    "audio/mpeg": ("mp3", "mp3"),
    "audio/mp4": ("aac", "mp4"),
    "audio/webm": ("opus", "webm"),
    "audio/flac": ("flac", "flac"),
}
# F01 RecordingManifest bounds: Segment.duration_ms <= 30 s, recording <= 2 h.
MAX_SEGMENT_MS = 30000
MAX_RECORDING_MS = 7200000
_STANDALONE_TRACK = "fi.attachments.v1/standalone-mixed"


def standalone_manifest(occurrence_id, facts):
    """The playable manifest of one standalone audio occurrence, or None.

    ``facts`` are the blob's validated ``{mime_type, sample_rate, channels,
    frame_count}``. The whole file is one mixed track (``sample_format``
    ``encoded``; no channel or role is implied, F01). The timeline is tiled
    with segments of at most ``MAX_SEGMENT_MS`` that all name this occurrence:
    F01 caps a segment at 30 s, so a longer file cannot be one segment. Such
    a manifest is a read projection only; it is never stored, and the F01
    oracle's distinct-occurrence rule for stored manifests does not apply.
    ``recording_id`` is the occurrence id, since no server recording exists.
    """
    try:
        codec, container = STANDALONE_CODECS[facts["mime_type"]]
        rate, channels, frames = (
            facts["sample_rate"],
            facts["channels"],
            facts["frame_count"],
        )
        occurrence = UUID(occurrence_id)
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    if (
        any(type(value) is not int for value in (rate, channels, frames))
        or not 8000 <= rate <= 192000
        or not 1 <= channels <= 32
    ):
        return None
    duration = (frames * 1000 + rate // 2) // rate
    if not 1 <= duration <= MAX_RECORDING_MS:
        return None
    return {
        "schema_version": 1,
        "recording_id": str(occurrence),
        "timebase": "session_relative_ms",
        "sample_format": "encoded",
        "sample_rate_hz": rate,
        "channels": channels,
        "codec": codec,
        "container": container,
        "layout": "mixed",
        "tracks": [
            {
                "track_id": str(uuid5(occurrence, _STANDALONE_TRACK)),
                "channels": list(range(channels)),
                "direction": "mixed",
                "role": "mixed",
            }
        ],
        "segments": [
            {
                "occurrence_id": str(occurrence),
                "index": index,
                "start_ms": start,
                "duration_ms": min(MAX_SEGMENT_MS, duration - start),
            }
            for index, start in enumerate(range(0, duration, MAX_SEGMENT_MS))
        ],
        "gaps": [],
        "playout": [],
        "capture_complete": True,
        "duration_ms": duration,
    }


def _playable_standalone(manifest, occurrence_id):
    """Every segment is this occurrence and they tile the whole timeline."""
    try:
        end = 0
        for index, segment in enumerate(manifest["segments"]):
            if (
                segment["occurrence_id"] != occurrence_id
                or segment["index"] != index
                or segment["start_ms"] != end
                or not 1 <= segment["duration_ms"] <= MAX_SEGMENT_MS
            ):
                return False
            end += segment["duration_ms"]
        return (
            manifest["layout"] == "mixed"
            and manifest["sample_format"] == "encoded"
            and manifest["capture_complete"] is True
            and not manifest["gaps"]
            and end == manifest["duration_ms"] > 0
        )
    except (KeyError, TypeError, ValueError):
        return False


def _playable(manifest, occurrence_ids, recording_id):
    """One finalized logical recording, with every segment authorized and ready.

    The F01 oracle checks codec/track shape, unique ordered segment identities,
    per-segment limits and exact timeline coverage. Do not infer completion from
    ready uploads, sort a corrupt timeline, or omit a denied segment. The read
    batch may be ordered by database identity rather than recording time.
    """
    if not wire.is_valid("RecordingManifest", manifest):
        return False
    return (
        manifest["recording_id"] == recording_id
        and bool(manifest["segments"])
        and len(occurrence_ids) == len(set(occurrence_ids))
        and {segment["occurrence_id"] for segment in manifest["segments"]}
        == set(occurrence_ids)
    )


def enrich_voice_detail(*, request, result, reader=None):
    """Enrich Voice detail rows, then project I04 `recording_attachment`.

    The projection is derived ONLY from authorized, bound statuses on this
    call's own rows: the conversation root first, then its children. A
    recording occurrence names its own parent, which need not be the root
    (LiveKit binds the session recording on ``agent_session``, a child of
    ``job_entrypoint``). Absent, denied or non-recording rows leave the key
    absent so the legacy URL tier keeps working; when present the UI never
    falls back to legacy URLs. Segments of one server recording are one
    recording; two recordings anywhere in the call are never resolved by
    guessing. The shape is `{occurrence_id, parent, state}` plus `manifest`
    only when ``state`` is `ready`; ``parent`` is the occurrence's own span.
    A ready standalone recording (no ``recording_id``) is re-authorized and
    plays through ``standalone_manifest``. No origin, URL or grant is
    projected.
    """
    observations = result.get("observation_span") or []
    authorized, complete = enrich_observations(
        request=request,
        project_id=result.get("project_id"),
        observations=observations,
        reader=reader,
    )
    recordings = {}
    for target, items in authorized:
        for item in items:
            reference = item["reference"]
            if (
                reference.get("purpose") == RECORDING_PURPOSE
                and reference["modality"] == "audio"
            ):
                key = (
                    item.get("recording_id"),
                    None if "recording_id" in item else reference["occurrence_id"],
                )
                recordings.setdefault(key, []).append((target, item))
    if not recordings:
        return
    if len(recordings) != 1:
        result["recording_attachment"] = {"state": "rejected"}
        return
    ((identity, owned),) = recordings.items()
    target, item = owned[0]
    parent = target.parent
    recording_id, standalone_id = identity
    if len(observations) > MAX_PARENTS or (
        not complete
        and reader.recording_is_unique(
            request,
            tuple(dict.fromkeys(t for t, _ in authorized)),
            recording_id=recording_id,
            occurrence_id=standalone_id,
        )
        is not True
    ):
        result["recording_attachment"] = {"state": "rejected"}
        return
    items = [entry for _, entry in owned]
    occurrence_id = item["reference"]["occurrence_id"]
    statuses = [entry["status"] for entry in items]
    status = next(
        (value for value in statuses if value not in {"ready", "partial"}),
        "partial" if "partial" in statuses else "ready",
    )
    projection = {
        "occurrence_id": occurrence_id,
        "parent": {"trace_id": parent.trace_id, "span_id": parent.span_id},
        "state": _COARSE_RECORDING_STATES.get(status, "pending"),
    }
    if status in {"ready", "partial"}:
        playable = False
        if "recording_id" in item:
            # A manifest has one exact parent. Never borrow authorization from
            # another span, even if a reader reports the same recording id.
            same_parent = all(
                other == target and entry.get("recording_id") == recording_id
                for other, entry in owned
            )
            manifest = (
                reader.recording_manifest(request, target, recording_id)
                if same_parent
                else None
            )
            # The authorized manifest reader must check EVERY occurrence, not
            # only the bounded (64/parent) metadata page. A complete 120-part
            # recording therefore remains playable, while a denied later part
            # makes the reader return None. Never trim a manifest to this page.
            playable = manifest is not None and _playable(
                manifest,
                [segment["occurrence_id"] for segment in manifest.get("segments", ())],
                recording_id,
            )
            if playable:
                visible = {entry["reference"]["occurrence_id"] for entry in items}
                timeline = {
                    segment["occurrence_id"] for segment in manifest["segments"]
                }
                playable = (
                    visible == timeline if complete else visible <= timeline
                ) and (manifest["capture_complete"] or status == "partial")
            if playable and manifest is not None:
                # Stable anchor: database row order is not timeline order.
                projection["occurrence_id"] = manifest["segments"][0]["occurrence_id"]
        elif status == "ready" and len(items) == 1:
            # Re-authorized for this exact occurrence (ready only); any denial
            # leaves the projection not-ready.
            facts = reader.standalone_recording(request, target, occurrence_id)
            manifest = standalone_manifest(occurrence_id, facts) if facts else None
            playable = manifest is not None and _playable_standalone(
                manifest, occurrence_id
            )
        if playable:
            projection["state"] = "ready"
            projection["manifest"] = manifest
    result["recording_attachment"] = projection


def enrich_voice_page(*, request, project_id, entries, reader=None):
    """Adapt Voice list identity from its entry, not from its root span stub."""

    def parents():
        for entry in islice(entries, MAX_PARENTS):
            if str(entry.get("project_id")) != str(project_id):
                continue
            roots = entry.get("observation_span") or []
            if not roots:
                continue
            root = roots[0]
            try:
                parent = Parent(str(entry.get("trace_id", "")), str(root.get("id", "")))
            except InvalidReference:
                continue
            yield root, parent

    return _enrich_parents(
        request=request,
        project_id=project_id,
        observations=parents(),
        reader=reader,
    )


def trace_observations(result):
    """Bounded, iterative walk over the existing trace response, not a DB scan."""
    stack = iter(result.get("observation_spans", ()))
    iterators = [stack]
    visited = 0
    while iterators and visited < MAX_PARENTS:
        node = next(iterators[-1], None)
        if node is None:
            iterators.pop()
            continue
        visited += 1
        if not isinstance(node, dict):
            continue
        observation = node.get("observation_span")
        if isinstance(observation, dict):
            yield observation
        children = node.get("children")
        if isinstance(children, list):
            iterators.append(iter(children))
