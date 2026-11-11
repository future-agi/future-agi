"""Optional trace/Voice view adapter; the control-store reader is injected.

Only existing JSON observation-span envelopes gain an `attachments` member. No
URL, inferred provider, new route or playback grant is manufactured here.
"""

from datetime import UTC, datetime
from itertools import islice

from .binding import Parent, Scope
from .reads import MAX_PARENTS, ReadTarget, attachment_statuses
from .references import InvalidReference


def _enrich_parents(*, request, project_id, observations, reader):
    """One bounded authorized batch of (response row, explicit parent) pairs.

    Missing explicit workspace fails closed, rather than adopting an older
    null-workspace project. B10 must additionally reauthorize each parent;
    a trace read is not media permission.
    """
    if reader is None:
        return
    organization = getattr(request, "organization", None) or getattr(
        getattr(request, "user", None), "organization", None
    )
    workspace = getattr(request, "workspace", None)
    try:
        scope = Scope(
            str(getattr(organization, "id", "")),
            str(getattr(workspace, "id", "")),
            str(project_id),
        )
    except InvalidReference:
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
