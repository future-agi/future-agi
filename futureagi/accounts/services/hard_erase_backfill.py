"""Typed boundary for the three immutable membership-FK backfill callers.

0015.backfill_ws_org_membership_fk, 0022.backfill_ws_org_membership_fk and
0023.create_missing_org_memberships fill a NULL FK with a membership for the SAME
person and workspace organization. They neither erase nor move ownership. No
migration is edited and no caller-name bypass exists: only these two exact SQL
shapes qualify. The boundary locks their person/org keys and selected rows, then
pins the executed UPDATE to that selection. Other SQL still refuses normally.
"""

from accounts.services.hard_erase_sql_policy import _lex

_INITIAL = """
UPDATE accounts_workspacemembership wm
SET organization_membership_id = (
    SELECT om.id FROM accounts_organization_membership om
    JOIN accounts_workspace w ON w.id = wm.workspace_id
    WHERE om.user_id = wm.user_id AND om.organization_id = w.organization_id
    LIMIT 1
) WHERE wm.organization_membership_id IS NULL
"""
_BATCHED = """
UPDATE accounts_workspacemembership wm
SET organization_membership_id = (
    SELECT om.id FROM accounts_organization_membership om
    JOIN accounts_workspace w ON w.id = wm.workspace_id
    WHERE om.user_id = wm.user_id AND om.organization_id = w.organization_id
      AND om.is_active = true
    ORDER BY om.created_at DESC, om.id DESC LIMIT 1
) WHERE wm.id = ANY(%s::uuid[]) AND wm.organization_membership_id IS NULL
"""


def _canonical(sql):
    return tuple(tuple(statement) for statement in _lex(sql))


SHAPES = {_canonical(_INITIAL): False, _canonical(_BATCHED): True}


def membership_backfill_kind(sql):
    """None means not qualified; False/True mean initial/batched exact shape."""
    if not isinstance(sql, str):
        return None
    return SHAPES.get(_canonical(sql))


def execute_membership_backfill(execute, sql, params, many, context):
    from django.db import connections, transaction

    from accounts.services.hard_erase import _refuse
    from accounts.services.hard_erase_graph import MAX_NODES
    from accounts.services.hard_erase_lifecycle import (
        identity_keys,
        lifecycle_lock,
        require_held,
    )
    from tfc.routers import force_primary

    kind = membership_backfill_kind(sql)
    connection = context["connection"]
    qualified_cursor = context.get("cursor")
    params = list(params or ())
    if (
        kind is None
        or many
        or connection is not connections["default"]
        or getattr(qualified_cursor, "db", None) is not connection
        or len(params) != int(kind)
    ):
        _refuse("unqualified membership backfill invocation")
    select = (
        "SELECT wm.id, wm.user_id, w.organization_id "
        "FROM accounts_workspacemembership wm "
        "JOIN accounts_workspace w ON w.id = wm.workspace_id "
        "WHERE wm.organization_membership_id IS NULL"
    )
    if kind:
        select += " AND wm.id = ANY(%s::uuid[])"

    def keys(rows):
        return identity_keys(
            organizations=[row[2] for row in rows], people=[row[1] for row in rows]
        )

    with force_primary(), transaction.atomic(using="default"):
        with connection.cursor() as cursor:
            cursor.execute(
                select + " ORDER BY wm.id LIMIT %s", params + [MAX_NODES + 1]
            )
            rows = cursor.fetchall()
        if len(rows) > MAX_NODES:
            _refuse("membership backfill exceeds bounded selection")
        ids = [row[0] for row in rows]
        with lifecycle_lock("default", keys=keys(rows)):
            # Only this fixed, bounded recheck uses the already-qualified cursor's
            # downstream continuation. A fresh cursor re-enters the raw SQL guard,
            # which must continue refusing arbitrary SELECT FOR UPDATE and DML.
            # No ambient SQL authority is granted to callbacks or other cursors.
            execute(
                select
                + " AND wm.id = ANY(%s::uuid[]) ORDER BY wm.id FOR UPDATE OF wm",
                params + [ids],
                False,
                context,
            )
            require_held("default", keys(qualified_cursor.fetchall()))
            # Retain the original predicate and bind the frozen IDs as parameters.
            return execute(
                sql + " AND wm.id = ANY(%s::uuid[])", params + [ids], many, context
            )
