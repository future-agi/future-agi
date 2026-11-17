"""Postgres capture triggers for the revocation outbox (READINESS lane 1.2a).

Why triggers, not only Django signals: most member removals in this code base are
``QuerySet.update()`` calls (``rbac_views`` member removal and invite cancel,
``workspace_management`` user deactivation and removal, ``member_role_service``
workspace revoke, the ``remove_user`` AI tool) or go through ``_base_manager``.
``post_save`` never fires for those. A row-level AFTER trigger runs in the writer's
own transaction for every path, so a committed removal always has its outbox row and
a rolled-back one never does. ``tracer/services/clickhouse/oss_outbox_cdc.py`` reached
the same conclusion for its own capture.

The triggers record raw facts only (who, where, old and new access columns). What a
change means for Omega (removed, or a new role) is decided in Python by
``accounts.host_events.interpret``, next to ``delegation_role``, so the role rules
live in one place.
"""

OUTBOX = "accounts_host_event_outbox"


def _insert(*, source, user, org, ws, old, new):
    return f"""
    INSERT INTO {OUTBOX} (
        event_id, source, operation, user_id, organization_id, workspace_id,
        old_state, new_state, captured_at, status, event, attempts,
        next_attempt_at, lease_until, delivered_at, sent_at, ee_received_at_ms, last_error
    ) VALUES (
        gen_random_uuid(), '{source}', TG_OP, {user}, {org}, {ws},
        {old}, {new}, clock_timestamp(), 'pending', NULL, 0,
        clock_timestamp(), NULL, NULL, NULL, NULL, ''
    );"""


def _member_state(row, ids):
    pairs = ", ".join(f"'{column}', {row}.{column}" for column in ids)
    return (
        f"jsonb_build_object({pairs}, 'is_active', {row}.is_active, "
        f"'deleted', {row}.deleted, 'level', {row}.level, 'role', {row}.role)"
    )


_ORG_FUNCTION = f"""
CREATE OR REPLACE FUNCTION accounts_host_event_capture_org_membership()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        {_insert(source="organization_membership", user="OLD.user_id", org="OLD.organization_id", ws="NULL",
                 old=_member_state("OLD", ("user_id", "organization_id")), new="NULL")}
        RETURN OLD;
    END IF;
    {_insert(source="organization_membership", user="OLD.user_id", org="OLD.organization_id", ws="NULL",
             old=_member_state("OLD", ("user_id", "organization_id")),
             new=_member_state("NEW", ("user_id", "organization_id")))}
    RETURN NEW;
END $$;
"""

# The workspace's organization is read when the change is written: a workspace never
# moves between organizations, and the row still exists while its memberships go.
_WS_FUNCTION = f"""
CREATE OR REPLACE FUNCTION accounts_host_event_capture_ws_membership()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    ws_org uuid;
BEGIN
    SELECT organization_id INTO ws_org FROM accounts_workspace WHERE id = OLD.workspace_id;
    IF TG_OP = 'DELETE' THEN
        {_insert(source="workspace_membership", user="OLD.user_id", org="ws_org", ws="OLD.workspace_id",
                 old=_member_state("OLD", ("user_id", "workspace_id")), new="NULL")}
        RETURN OLD;
    END IF;
    {_insert(source="workspace_membership", user="OLD.user_id", org="ws_org", ws="OLD.workspace_id",
             old=_member_state("OLD", ("user_id", "workspace_id")),
             new=_member_state("NEW", ("user_id", "workspace_id")))}
    RETURN NEW;
END $$;
"""

_USER_FUNCTION = f"""
CREATE OR REPLACE FUNCTION accounts_host_event_capture_user()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    {_insert(source="user", user="OLD.id", org="NULL", ws="NULL",
             old="jsonb_build_object('is_active', OLD.is_active)",
             new="CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE jsonb_build_object('is_active', NEW.is_active) END")}
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END $$;
"""

_ACCESS_CHANGED = (
    "OLD.is_active IS DISTINCT FROM NEW.is_active OR OLD.deleted IS DISTINCT FROM NEW.deleted"
    " OR OLD.level IS DISTINCT FROM NEW.level OR OLD.role IS DISTINCT FROM NEW.role"
    " OR OLD.user_id IS DISTINCT FROM NEW.user_id"
)

_ORG = "accounts_host_event_capture_org_membership"
_WS = "accounts_host_event_capture_ws_membership"
_USER = "accounts_host_event_capture_user"

# (trigger, table, event, WHEN condition or "", function)
TRIGGERS = (
    (
        "accounts_host_event_org_membership_upd",
        "accounts_organization_membership",
        "UPDATE",
        f"{_ACCESS_CHANGED} OR OLD.organization_id IS DISTINCT FROM NEW.organization_id",
        _ORG,
    ),
    (
        "accounts_host_event_org_membership_del",
        "accounts_organization_membership",
        "DELETE",
        "",
        _ORG,
    ),
    (
        "accounts_host_event_ws_membership_upd",
        "accounts_workspacemembership",
        "UPDATE",
        f"{_ACCESS_CHANGED} OR OLD.workspace_id IS DISTINCT FROM NEW.workspace_id",
        _WS,
    ),
    (
        "accounts_host_event_ws_membership_del",
        "accounts_workspacemembership",
        "DELETE",
        "",
        _WS,
    ),
    (
        "accounts_host_event_user_deactivated",
        "accounts_user",
        "UPDATE",
        "OLD.is_active AND NOT NEW.is_active",
        _USER,
    ),
    ("accounts_host_event_user_del", "accounts_user", "DELETE", "", _USER),
)

FUNCTIONS = ((_ORG, _ORG_FUNCTION), (_WS, _WS_FUNCTION), (_USER, _USER_FUNCTION))


def install_statements():
    statements = [body for _, body in FUNCTIONS]
    for name, table, event, when, function in TRIGGERS:
        condition = f" WHEN ({when})" if when else ""
        statements.append(f"DROP TRIGGER IF EXISTS {name} ON {table}")
        statements.append(
            f"CREATE TRIGGER {name} AFTER {event} ON {table} FOR EACH ROW{condition}"
            f" EXECUTE FUNCTION {function}()"
        )
    return statements


def uninstall_statements():
    statements = [
        f"DROP TRIGGER IF EXISTS {name} ON {table}" for name, table, *_ in TRIGGERS
    ]
    statements.extend(f"DROP FUNCTION IF EXISTS {name}()" for name, _ in FUNCTIONS)
    return statements


def install(schema_editor):
    """Migration hook. Postgres only: other engines (the SQLite unit settings) skip it."""
    if schema_editor.connection.vendor != "postgresql":
        return
    for statement in install_statements():
        schema_editor.execute(statement)


def uninstall(schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    for statement in uninstall_statements():
        schema_editor.execute(statement)
