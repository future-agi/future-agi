"""The sole runtime persistence boundary for Falcon connector rows.

Provider work uses a short, durable WRITE snapshot transaction, then a locked
compare-and-swap on its generation. Never yield a row lock to provider code.
The historical encryption migration deliberately does not use runtime helpers.
"""

from contextlib import contextmanager
from copy import deepcopy

from django.db import router, transaction
from django.utils import timezone

from ee.falcon_ai.credentials import AUTH_REQUIRED, FalconCredentialError

# Generation covers ALL participating runtime writes. Identity comparison also
# protects old/in-memory snapshots and legacy fixtures whose generation is zero.
_REGISTRATION_FIELDS = (
    "credential_generation",
    "oauth_client_id",
    "oauth_client_secret",
    "oauth_state",
    "oauth_code_verifier",
    "oauth_access_token",
    "oauth_refresh_token",
    "oauth_server_metadata",
    "auth_header_value",
    "auth_type",
    "auth_header_name",
    "server_url",
)

# Row ownership, generation and tombstone columns belong to this boundary, never to
# a caller-supplied changes mapping. Erasure adds tombstone columns only below.
_WRITABLE_FIELDS = frozenset(
    {
        "name",
        "server_url",
        "transport",
        "auth_type",
        "auth_header_name",
        "auth_header_value",
        "oauth_client_id",
        "oauth_client_secret",
        "oauth_server_metadata",
        "oauth_access_token",
        "oauth_refresh_token",
        "oauth_token_expires_at",
        "oauth_code_verifier",
        "oauth_state",
        "is_active",
        "is_verified",
        "discovered_tools",
        "enabled_tool_names",
        "last_discovery_at",
        "last_error",
    }
)


def _identity(connector):
    return tuple(deepcopy(getattr(connector, name)) for name in _REGISTRATION_FIELDS)


def _sync(target, current, alias):
    for field in current._meta.concrete_fields:
        setattr(target, field.attname, getattr(current, field.attname))
    target._state.db = alias
    target._credential_alias = alias
    target._credential_snapshot = _identity(current)


def _refuse():
    raise FalconCredentialError(AUTH_REQUIRED) from None


def _locked_row(connector, alias, *, include_deleted=False):
    # Callers already applied authorization/scope; preserve ownership binding on
    # the fresh authoritative row rather than allowing a stale cross-scope write.
    active = {} if include_deleted else {"deleted": False}
    return (
        type(connector)
        ._base_manager.using(alias)
        .select_for_update()
        .filter(
            pk=connector.pk,
            organization_id=connector.organization_id,
            workspace_id=connector.workspace_id,
            **active,
        )
        .first()
    )


@contextmanager
def registration_operation(connector, *, match_registration=False):
    """Validate before exchange; release the lock before ANY provider I/O.

    durable=True rejects real outer transactions, which would otherwise retain
    the snapshot lock across HTTP. Django's own TestCase isolation is supported.
    At persistence, save_connector compares the frozen generation again.
    """
    alias = router.db_for_write(type(connector))
    if getattr(connector, "_credential_alias", alias) != alias:
        _refuse()
    with transaction.atomic(using=alias, durable=True):
        current = _locked_row(connector, alias)
        if current is None or current.deleted:
            _refuse()
        if match_registration and _identity(current) != _identity(connector):
            _refuse()
        current._credential_snapshot = _identity(current)
        current._credential_alias = alias
    yield current
    _sync(connector, current, alias)


def save_connector(
    connector, changes, *, fresh_metadata=False, discard_stale=False, erase=None
):
    """Own-column locked generation CAS, on the WRITE route without instance hints.

    Normal writes require a live matching snapshot. Admin metadata and erasure
    instead derive their snapshot from the locked row itself: never trust form
    credentials or a stale replica when deleting. ``erase`` is a local capture
    function returning a post-commit revocation closure, never network I/O.
    A legacy already-deleted but unscrubbed row may ONLY undergo erasure; this
    preserves the inherited F5 remediation contract, never restoring a value.
    """
    model = type(connector)
    alias = router.db_for_write(model)
    if not set(changes).issubset(_WRITABLE_FIELDS):
        _refuse()
    # _state.db can name a replica; only an explicit pre-I/O WRITE snapshot is
    # route-bound. Never compare generations belonging to different databases.
    if getattr(connector, "_credential_alias", alias) != alias:
        if discard_stale:
            return False
        _refuse()
    with transaction.atomic(using=alias):
        current = _locked_row(connector, alias, include_deleted=bool(erase))
        expected = getattr(connector, "_credential_snapshot", None)
        if expected is None:
            expected = _identity(connector)
        # Metadata-only admin and erasure take their operation snapshot HERE,
        # under this lock, rather than carrying credentials out of a stale form.
        if current is not None and (fresh_metadata or erase):
            expected = _identity(current)
        invalid = (
            current is None
            or (current.deleted and not erase)
            or _identity(current) != expected
        )
        if invalid:
            if discard_stale:
                return False
            _refuse()
        if fresh_metadata and not set(changes).issubset({"name", "is_active"}):
            _refuse()
        changes = dict(changes)
        if erase:
            if current.deleted and all(
                getattr(current, key) == value for key, value in changes.items()
            ):
                _sync(connector, current, alias)
                return False
            provider, revoke = erase(current)
            changes.update(
                deleted=True, deleted_at=current.deleted_at or timezone.now()
            )
        # The row lock makes this generation comparison and increment atomic;
        # equivalent to UPDATE ... WHERE generation = expected_generation.
        # Only the declared operation's columns (plus generation/time) are saved.
        for name, value in changes.items():
            setattr(current, name, value)
        current.credential_generation += 1
        current.save(
            using=alias,
            update_fields=[*changes, "credential_generation", "updated_at"],
        )
        if erase:
            from integrations.services.disconnect import _after_commit

            transaction.on_commit(lambda: _after_commit(provider, revoke), using=alias)
    _sync(connector, current, alias)
    return True


def save_registration(connector, *, update_fields):
    """Persist OAuth's prepared fields against its pre-I/O frozen snapshot."""
    return save_connector(
        connector,
        {
            name: getattr(connector, name)
            for name in update_fields
            if name != "updated_at"
        },
    )


def create_connector(**fields):
    """New-row INSERT has no prior generation; keep it in the same boundary."""
    from ee.falcon_ai.models import MCPConnector

    return MCPConnector.objects.using(router.db_for_write(MCPConnector)).create(
        **fields
    )
