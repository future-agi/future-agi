"""Optional trace/Voice view adapter; the control-store reader is injected.

Only existing JSON observation-span envelopes gain an `attachments` member. No
URL, inferred provider, new route or playback grant is manufactured here.
"""

from datetime import UTC, datetime
from itertools import islice

from .binding import Parent, Scope
from .reads import MAX_PARENTS, ReadTarget, attachment_statuses
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
        return
    scope = _request_scope(request, project_id)
    if scope is None:
        return
    selected = [
        (observation, ReadTarget(scope, parent))
        for observation, parent in islice(observations, MAX_PARENTS)
    ]
    statuses = attachment_statuses(
        viewer=request,
        targets=[target for _, target in selected],
        reader=reader,
        now=datetime.now(UTC),
    )
    for observation, target in selected:
        # No empty counts/placeholders for denied/unknown objects.
        if target in statuses:
            observation["attachments"] = statuses[target]


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

    _enrich_parents(
        request=request,
        project_id=project_id,
        observations=parents(),
        reader=reader,
    )


RECORDING_PURPOSE = "conversation_recording"
# I04 coarse states. A recording is projected playable only with an authorized
# manifest whose bytes the gateway can serve as-is (see ``_playable``); every
# other ready recording reads as not-ready until channel/stitched derivatives
# (B11) exist.
_COARSE_RECORDING_STATES = {
    "pending": "pending",
    "pending_upload": "pending_upload",
    "importing": "importing",
    "validating": "validating",
    "partial": "waiting",
    "ready": "pending",
    "failed": "failed",
}


def _playable(manifest, occurrence_id):
    """One complete mixed segment covering the whole timeline, and nothing else.

    The gateway serves an occurrence's validated bytes unchanged, so only a
    recording that IS one mixed occurrence plays without derivatives.
    """
    try:
        (segment,) = manifest["segments"]
        return (
            manifest["layout"] == "mixed"
            and manifest["capture_complete"] is True
            and not manifest["gaps"]
            and segment["occurrence_id"] == occurrence_id
            and segment["start_ms"] == 0
            and segment["duration_ms"] == manifest["duration_ms"]
        )
    except (KeyError, TypeError, ValueError):
        return False


def enrich_voice_detail(*, request, result, reader=None):
    """Enrich Voice detail rows, then project I04 `recording_attachment`.

    The projection is derived ONLY from authorized statuses on the explicit
    conversation root (the first row). Absent, denied or non-recording roots
    leave the key absent so the legacy URL tier keeps working; when present the
    UI never falls back to legacy URLs. Segments of one server recording are
    one recording; two recordings on one root are never resolved by guessing.
    The shape is `{occurrence_id, parent, state}` plus `manifest` only when
    ``state`` is `ready`. No origin, URL or grant is projected.
    """
    observations = result.get("observation_span") or []
    enrich_observations(
        request=request,
        project_id=result.get("project_id"),
        observations=observations,
        reader=reader,
    )
    if not observations:
        return
    root = observations[0]
    recordings = {}
    for item in root.get("attachments") or ():
        reference = item["reference"]
        if (
            reference.get("purpose") == RECORDING_PURPOSE
            and reference["modality"] == "audio"
        ):
            key = item.get("recording_id") or reference["occurrence_id"]
            recordings.setdefault(key, []).append(item)
    if not recordings:
        return
    if len(recordings) != 1:
        result["recording_attachment"] = {"state": "rejected"}
        return
    try:
        parent = Parent(str(root.get("trace_id", "")), str(root.get("id", "")))
    except InvalidReference:
        return
    ((recording_id, items),) = recordings.items()
    item = items[0]
    occurrence_id = item["reference"]["occurrence_id"]
    statuses = [entry["status"] for entry in items]
    status = next((value for value in statuses if value != "ready"), "ready")
    projection = {
        "occurrence_id": occurrence_id,
        "parent": {"trace_id": parent.trace_id, "span_id": parent.span_id},
        "state": _COARSE_RECORDING_STATES[status],
    }
    scope = _request_scope(request, result.get("project_id"))
    if (
        status == "ready"
        and len(items) == 1
        and "recording_id" in item
        and scope is not None
    ):
        manifest = reader.recording_manifest(
            request, ReadTarget(scope, parent), recording_id
        )
        if manifest is not None and _playable(manifest, occurrence_id):
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

    _enrich_parents(
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
