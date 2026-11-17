"""What a captured workspace or organization deletion means for Omega (READINESS 3.22).

- ``workspace.deleted``: the workspace stopped being active (the DELETE endpoint's soft delete),
  was flagged deleted, or its row was deleted while active. EE purges what it holds for it.
- ``org.deleted``: the organization's row was deleted. EE purges every workspace of it.

Both name no person (``subject`` and ``role`` are null). They travel in request bodies of their
own (:func:`split_lifecycle`), so an EE that predates them refuses only them, never a revocation.
"""

EVENT_WORKSPACE_DELETED = "workspace.deleted"
EVENT_ORG_DELETED = "org.deleted"
LIFECYCLE_EVENTS = frozenset({EVENT_WORKSPACE_DELETED, EVENT_ORG_DELETED})
LIFECYCLE_SOURCES = frozenset({"workspace", "organization"})


def _was_live(state):
    return bool(state) and bool(state.get("is_active")) and not state.get("deleted")


def _event(kind, row, *, workspace):
    return {
        "type": kind,
        "subject": None,
        "organizationId": str(row.organization_id),
        "workspaceId": str(row.workspace_id) if workspace else None,
        "role": None,
    }


def interpret_lifecycle(row):
    """The event body for one captured workspace or organization row, or None."""
    if row.organization_id is None:
        return None
    if row.source == "organization":
        return _event(EVENT_ORG_DELETED, row, workspace=False)
    if row.source != "workspace" or row.workspace_id is None:
        return None
    old, new = row.old_state or {}, row.new_state
    if not _was_live(old) or (new is not None and _was_live(new)):
        return None
    return _event(EVENT_WORKSPACE_DELETED, row, workspace=True)


def redundant_under_org_deletion(events):
    """Event pairs (row id, event) whose workspace deletion an org deletion in the same batch covers."""
    deleted_orgs = {
        event["organizationId"]
        for _, event in events
        if event is not None and event["type"] == EVENT_ORG_DELETED
    }
    return {
        row_id
        for row_id, event in events
        if event is not None
        and event["type"] == EVENT_WORKSPACE_DELETED
        and event["organizationId"] in deleted_orgs
    }


def is_lifecycle(event):
    return event.get("type") in LIFECYCLE_EVENTS


def split_lifecycle(rows):
    """(revocation rows, lifecycle rows): the relay sends them in separate bodies."""
    revocations, lifecycle = [], []
    for row in rows:
        (lifecycle if row.source in LIFECYCLE_SOURCES else revocations).append(row)
    return revocations, lifecycle
