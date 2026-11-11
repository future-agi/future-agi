"""Optional trace/Voice view adapter; the control-store reader is injected.

Only existing JSON observation-span envelopes gain an `attachments` member. No
URL, inferred provider, new route or playback grant is manufactured here.
"""

from datetime import UTC, datetime

from .binding import Parent, Scope
from .reads import MAX_PARENTS, ReadTarget, attachment_statuses
from .references import InvalidReference


def enrich_observations(*, request, project_id, observations, reader=None):
    """One bounded authorized batch for both trace and Voice response builders.

    The default has zero queries/response changes. Missing explicit workspace
    fails closed, rather than adopting an older null-workspace project. B10 must
    additionally reauthorize each parent; a trace read is not media permission.
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
    selected = []
    # Caller supplies only its already-selected response page. Never expand the
    # CH query or hydrate a whole conversation just to discover attachment IDs.
    for index, observation in enumerate(observations):
        if index == MAX_PARENTS:
            break
        try:
            target = ReadTarget(
                scope,
                Parent(
                    str(observation.get("trace_id", "")), str(observation.get("id", ""))
                ),
            )
        except InvalidReference:
            continue
        selected.append((observation, target))
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


def enrich_voice_page(*, request, project_id, entries, reader=None):
    """Enrich the existing root stubs with one query, not one per voice call."""
    if reader is None:
        return
    prepared = []
    for entry in entries[:MAX_PARENTS]:
        if str(entry.get("project_id")) != str(project_id):
            continue
        roots = entry.get("observation_span") or []
        if not roots:
            continue
        original = roots[0]
        prepared.append((original, {**original, "trace_id": entry.get("trace_id")}))
    enrich_observations(
        request=request,
        project_id=project_id,
        observations=[row for _, row in prepared],
        reader=reader,
    )
    for original, row in prepared:
        if "attachments" in row:
            original["attachments"] = row["attachments"]


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
