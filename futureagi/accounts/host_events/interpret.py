"""What one captured change means for Omega (READINESS 1.2a).

A captured row says how an organization membership, a workspace membership or a user
changed. This module turns it into at most one event, using the same rules the
session exchange uses to mint a key (``accounts.omega_session.delegation_role``):

- ``membership.removed``: the person can no longer get a key for that organization
  (no ``workspaceId``) or for that one workspace (``workspaceId`` set);
- ``role.changed``: the person keeps organization access with a different role, so a
  stream opened with the old role ends and reconnects with the new one;
- ``user.deactivated``: the account is off, in every organization it belongs to.

A change that grants access, or changes nothing Omega can see, yields no event: a key
minted after it carries the new state on its own.
"""

from dataclasses import dataclass

from accounts.lifecycle.interpret import (
    LIFECYCLE_SOURCES,
    interpret_lifecycle,
    redundant_under_org_deletion,
)
from accounts.omega_session import delegation_role
from tfc.constants.levels import Level

EVENT_MEMBERSHIP_REMOVED = "membership.removed"
EVENT_ROLE_CHANGED = "role.changed"
EVENT_USER_DEACTIVATED = "user.deactivated"


@dataclass(frozen=True)
class Lookups:
    """Reads of the current state, injected so the rules can be tested without a database."""

    # (user_id, organization_id) -> the active organization level, or None.
    org_level: object
    # user_id -> every organization id the user has a membership row in.
    organizations_of: object


def _level(state, default):
    level = state.get("level")
    if level is not None:
        return level
    return Level.STRING_TO_LEVEL.get(state.get("role"), default)


def _active(state):
    return bool(state) and bool(state.get("is_active")) and not state.get("deleted")


def _role(org_level, workspace_level):
    try:
        return delegation_role(org_level, workspace_level)
    except ValueError:
        return None


def org_role(state):
    """The Omega role a membership state grants at organization level, or None.

    A member's key also needs workspace access; that is the workspace row's concern,
    so the organization row is judged as if it had it.
    """
    if not _active(state):
        return None
    return _role(_level(state, 0), Level.WORKSPACE_MEMBER)


def _workspace_access(state):
    """Whether a workspace row gives an organization member a key there."""
    return (
        _active(state)
        and _role(Level.MEMBER, _level(state, Level.WORKSPACE_VIEWER)) is not None
    )


def _event(kind, row, *, workspace=False, role=None, organizations=None):
    event = {
        "type": kind,
        "subject": str(row.user_id),
        "organizationId": str(row.organization_id) if row.organization_id else None,
        "workspaceId": (
            str(row.workspace_id) if workspace and row.workspace_id else None
        ),
        "role": role,
    }
    if organizations is not None:
        event["organizationId"] = None
        event["organizations"] = sorted(str(org) for org in organizations)
    return event


def _moved(old, new, column):
    return new is not None and str(new.get(column)) != str(old.get(column))


def interpret_org_membership(row):
    old, new = row.old_state or {}, row.new_state
    before = org_role(old)
    if before is None:
        # Access was already off; nothing Omega holds can depend on this row.
        return None
    after = (
        None
        if new is None
        or _moved(old, new, "user_id")
        or _moved(old, new, "organization_id")
        else org_role(new)
    )
    if after is None:
        return _event(EVENT_MEMBERSHIP_REMOVED, row)
    if after != before:
        return _event(EVENT_ROLE_CHANGED, row, role=after)
    return None


def interpret_ws_membership(row, lookups):
    old, new = row.old_state or {}, row.new_state
    if row.organization_id is None or not _workspace_access(old):
        return None
    still = (
        new is not None
        and not _moved(old, new, "user_id")
        and not _moved(old, new, "workspace_id")
    )
    if still and _workspace_access(new):
        # Workspace admin <-> member are both "member" in Omega.
        return None
    # Organization owners and admins reach every workspace without a workspace row.
    if lookups.org_level(row.user_id, row.organization_id) in (
        Level.OWNER,
        Level.ADMIN,
    ):
        return None
    return _event(EVENT_MEMBERSHIP_REMOVED, row, workspace=True)


def interpret_user(row, lookups):
    organizations = lookups.organizations_of(row.user_id)
    if not organizations:
        # A deleted user's memberships were deleted first and each produced its own removal.
        return None
    return _event(EVENT_USER_DEACTIVATED, row, organizations=organizations)


def interpret(row, lookups):
    """The event body for one captured row (without id, issuer or time), or None."""
    if row.source == "organization_membership":
        return interpret_org_membership(row)
    if row.source == "workspace_membership":
        return interpret_ws_membership(row, lookups)
    if row.source == "user":
        return interpret_user(row, lookups)
    if row.source in LIFECYCLE_SOURCES:
        # Workspace and organization deletion (READINESS 3.22).
        return interpret_lifecycle(row)
    return None


def collapse(events):
    """Drop workspace removals made redundant by an organization removal in the same batch.

    ``events`` is a list of (row_id, event or None). A member removal deactivates the
    organization membership and every workspace membership in one transaction; EE only
    needs the organization-wide event. Likewise an organization's deletion covers the
    deletion of its workspaces that Django's cascade captured just before it.
    """
    org_wide = {
        (event["subject"], event["organizationId"])
        for _, event in events
        if event is not None
        and event["type"] == EVENT_MEMBERSHIP_REMOVED
        and event["workspaceId"] is None
    }
    deleted_workspaces = redundant_under_org_deletion(events)
    collapsed = []
    for row_id, event in events:
        redundant = row_id in deleted_workspaces or (
            event is not None
            and event["type"] == EVENT_MEMBERSHIP_REMOVED
            and event["workspaceId"] is not None
            and (event["subject"], event["organizationId"]) in org_wide
        )
        collapsed.append((row_id, None if redundant else event))
    return collapsed
