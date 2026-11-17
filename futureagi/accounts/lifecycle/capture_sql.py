"""Postgres capture triggers for workspace and organization deletion (READINESS lane 3.22, E02).

They write into the same outbox as the revocation channel (``accounts_host_event_outbox``,
lane 1.2a) in the deleting transaction, for every path: the workspace DELETE endpoint's soft
delete (``is_active`` to false), a ``deleted`` flag, a ``QuerySet.update()``, the admin, and the
hard delete Django's cascade issues when an organization is deleted. A rolled-back deletion
never leaves a row. The relay turns a row into ``workspace.deleted`` or ``org.deleted`` and EE
purges what it holds for that host scope.

Also here: when a user is deactivated by any path, every one of their Future AGI browser
tokens is deactivated in the same transaction, so their sessions end at the next request
(``accounts.authentication.decode_token`` checks the token row on its cache path too).
"""

from accounts.host_events.capture_sql import OUTBOX


def _insert(*, source, user, org, ws, old, new):
    # A lifecycle event names no person, but the outbox's user_id is NOT NULL: it holds the
    # workspace's creator, or the organization's own id. Neither is sent to EE.
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


def _ws_state(row):
    return (
        f"jsonb_build_object('is_active', {row}.is_active, 'deleted', {row}.deleted, "
        f"'organization_id', {row}.organization_id)"
    )


_WORKSPACE = "accounts_lifecycle_capture_workspace"
_ORGANIZATION = "accounts_lifecycle_capture_organization"
_SESSIONS = "accounts_lifecycle_end_user_sessions"

_WORKSPACE_FUNCTION = f"""
CREATE OR REPLACE FUNCTION {_WORKSPACE}()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        {_insert(source="workspace", user="OLD.created_by_id", org="OLD.organization_id", ws="OLD.id", old=_ws_state("OLD"), new="NULL")}
        RETURN OLD;
    END IF;
    {_insert(source="workspace", user="OLD.created_by_id", org="OLD.organization_id", ws="OLD.id", old=_ws_state("OLD"), new=_ws_state("NEW"))}
    RETURN NEW;
END $$;
"""

_ORGANIZATION_FUNCTION = f"""
CREATE OR REPLACE FUNCTION {_ORGANIZATION}()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    {_insert(source="organization", user="OLD.id", org="OLD.id", ws="NULL", old="jsonb_build_object('id', OLD.id)", new="NULL")}
    RETURN OLD;
END $$;
"""

# Sessions end with the account: every browser token of a deactivated user is turned off.
_SESSIONS_FUNCTION = f"""
CREATE OR REPLACE FUNCTION {_SESSIONS}()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    UPDATE accounts_auth_token SET is_active = FALSE, updated_at = clock_timestamp()
        WHERE user_id = NEW.id AND is_active;
    RETURN NEW;
END $$;
"""

# A workspace leaves access when it stops being active or is flagged deleted; re-activation and
# renames write nothing.
_WORKSPACE_GONE = (
    "(OLD.is_active AND NOT OLD.deleted) AND (NOT NEW.is_active OR NEW.deleted)"
)

# (trigger, table, event, WHEN condition or "", function)
TRIGGERS = (
    (
        "accounts_lifecycle_workspace_upd",
        "accounts_workspace",
        "UPDATE",
        _WORKSPACE_GONE,
        _WORKSPACE,
    ),
    (
        "accounts_lifecycle_workspace_del",
        "accounts_workspace",
        "DELETE",
        "OLD.is_active AND NOT OLD.deleted",
        _WORKSPACE,
    ),
    (
        "accounts_lifecycle_organization_del",
        "accounts_organization",
        "DELETE",
        "",
        _ORGANIZATION,
    ),
    (
        "accounts_lifecycle_user_sessions",
        "accounts_user",
        "UPDATE",
        "OLD.is_active AND NOT NEW.is_active",
        _SESSIONS,
    ),
)

FUNCTIONS = (
    (_WORKSPACE, _WORKSPACE_FUNCTION),
    (_ORGANIZATION, _ORGANIZATION_FUNCTION),
    (_SESSIONS, _SESSIONS_FUNCTION),
)


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
