"""Bounded recovery for evaluation metadata reads, never evaluation side effects."""

import re
import time
from collections.abc import Callable, Iterable
from typing import TypeVar
from uuid import UUID

import psycopg
from django.db import InterfaceError, OperationalError, connections
from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.models import Model
from psycopg.errors import ConnectionTimeout

from accounts.models.workspace import Workspace
from model_hub.models.develop_dataset import Column, Row

_T = TypeVar("_T")
_RETRY_DELAYS = (0.25, 0.5)

# Server SQLSTATEs (besides class 08) meaning "unavailable now": admin/crash
# shutdown, cannot_connect_now and too_many_connections.
_RETRYABLE_SQLSTATES = frozenset({"57P01", "57P02", "57P03", "53300"})

# Configuration or statement faults. Retrying cannot help, so they escape even
# when another marker below also appears in the message. The one exception is a
# pg_hba.conf refusal of one login attempt next to another attempt's capacity
# refusal; see _capacity_outranks_hba_refusal.
_DETERMINISTIC_MARKERS = (
    "authentication failed",
    "no password supplied",
    "pg_hba.conf",
    "does not exist",
    "certificate verify failed",
    "invalid connection option",
    "statement timeout",
)

# libpq reports a login rejection without the server's SQLSTATE, so match the
# texts of PostgreSQL's 57P03/53300 rejections and PgBouncer's pool limits and
# server-down responses. Per-role and per-database connection limits count as
# capacity too; the retry budget stays bounded either way.
_LOGIN_UNAVAILABLE_MARKERS = (
    "the database system is starting up",
    "the database system is shutting down",
    "the database system is in recovery mode",
    "the database system is not yet accepting connections",
    "the database system is not accepting connections",
    "sorry, too many clients already",
    "remaining connection slots are reserved",
    "too many connections for role",
    "too many connections for database",
    "no more connections allowed",
    "server login has been failing",
    "pgbouncer cannot connect to server",
    "client_login_timeout (server down)",
    "query_wait_timeout",
)

_TRANSPORT_MARKERS = (
    "connection refused",
    "server closed the connection unexpectedly",
    "ssl connection has been closed unexpectedly",
    "ssl syscall error: eof detected",
    "connection reset by peer",
    "could not send data to server: broken pipe",
    "the connection is closed",
    "the connection is lost",
)

# libpq starts each attempt's report with "connection to server ...", and
# sslmode=prefer joins a refused TLS attempt and its plaintext retry this way.
_LOGIN_ATTEMPT_BOUNDARY = re.compile(r"\n(?=connection to server )")


class EvaluationMetadataUnavailable(RuntimeError):
    """No evaluator or billing call started; metadata could not be loaded."""

    code = "EVALUATION_METADATA_UNAVAILABLE"

    def __init__(self):
        super().__init__(
            "Evaluation not started: database temporarily unavailable while loading "
            "evaluation metadata. Please retry the evaluation."
        )


def _capacity_outranks_hba_refusal(message: str) -> bool:
    # PostgreSQL says "sorry, too many clients already" before it reads
    # pg_hba.conf, so libpq retries that refusal in plaintext. If a slot frees
    # in between, an SSL-only server refuses the plaintext attempt in
    # pg_hba.conf. Its rules are per encryption method, so that refusal does not
    # show that the login refused at capacity is misconfigured. Retry only when
    # every attempt is a capacity/unavailable refusal or a pg_hba.conf one, and
    # at least one is the former; any other deterministic text still escapes.
    attempts = _LOGIN_ATTEMPT_BOUNDARY.split(message)
    if len(attempts) < 2:
        return False
    unavailable = False
    for attempt in attempts:
        if "pg_hba.conf" in attempt:
            continue
        if any(marker in attempt for marker in _DETERMINISTIC_MARKERS):
            return False
        if not any(marker in attempt for marker in _LOGIN_UNAVAILABLE_MARKERS):
            return False
        unavailable = True
    return unavailable


def _is_connection_failure(error: Exception, connection: BaseDatabaseWrapper) -> bool:
    # Django wraps the driver's exception. A server SQLSTATE takes precedence
    # over connection state: authentication and statement failures aren't outages.
    cause = error.__cause__
    if isinstance(cause, ConnectionTimeout):
        return True
    sqlstate = getattr(cause, "sqlstate", None)
    if sqlstate:
        return sqlstate.startswith("08") or sqlstate in _RETRYABLE_SQLSTATES
    if not isinstance(cause, (psycopg.OperationalError, psycopg.InterfaceError)):
        return False

    # libpq can report connect/transport failures without a server SQLSTATE.
    # In particular a refused connection has no installed driver connection to
    # inspect. Keep these signatures narrow; unknown OperationalErrors escape.
    message = str(cause).lower()
    if _capacity_outranks_hba_refusal(message):
        return True
    if any(marker in message for marker in _DETERMINISTIC_MARKERS):
        return False
    if any(marker in message for marker in _LOGIN_UNAVAILABLE_MARKERS):
        return True
    # Any other login rejection (role may not log in, database closed to new
    # connections, ...) is configuration, not an outage.
    if "fatal:" in message:
        return False
    driver_connection = connection.connection
    if driver_connection is not None and driver_connection.broken:
        return True
    return any(marker in message for marker in _TRANSPORT_MARKERS)


def _read_metadata(read: Callable[[], _T], *, using: str) -> _T:
    for attempt in range(len(_RETRY_DELAYS) + 1):
        try:
            return read()
        except (OperationalError, InterfaceError) as error:
            connection = connections[using]
            if not _is_connection_failure(error, connection):
                raise
            # A caller-owned transaction cannot be repaired here. Closing it or
            # retrying queries inside it would leave the caller's work unusable.
            if connection.in_atomic_block:
                raise EvaluationMetadataUnavailable() from error
            connection.close()
            if attempt == len(_RETRY_DELAYS):
                raise EvaluationMetadataUnavailable() from error
            time.sleep(_RETRY_DELAYS[attempt])
    raise AssertionError("Unreachable metadata retry state")


def read_evaluation_column_types(
    column_ids: Iterable[str | UUID], *, dataset_id: str | UUID | None = None
) -> dict[str, str]:
    lookup = {"id__in": list(column_ids)}
    if dataset_id is not None:
        # Only the given dataset's columns; ids from other datasets are ignored.
        lookup["dataset_id"] = dataset_id
    columns = Column.objects.filter(**lookup)
    # Keep routing on the queryset, not its iterable values. Each attempt builds
    # and materializes a fresh values query inside the retry boundary.
    return _read_metadata(
        lambda: {
            str(col["id"]): col["data_type"]
            for col in columns.values("id", "data_type")
        },
        using=columns.db,
    )


def _read_metadata_relation(instance: Model, name: str) -> Model | None:
    field = instance._meta.get_field(name)
    if field.is_cached(instance) or field.value_from_object(instance) is None:
        # Preserve descriptor semantics for cached and nullable relations.
        return getattr(instance, name)

    # Match Django's forward-FK lookup (base manager + instance routing hint),
    # then pin the routed alias so the query and cleanup use the same wrapper.
    related = field.remote_field.model._base_manager.db_manager(
        hints={"instance": instance}
    ).filter(field.get_reverse_related_filter(instance))
    using = related.db
    related = related.using(using)
    value = _read_metadata(related.get, using=using)
    field.set_cached_value(instance, value)
    return value


def read_evaluation_workspace(row: Row) -> Workspace | None:
    # These are independently routed reads. The Row's alias is not necessarily
    # the Dataset's or Workspace's, and cached relations need no retry boundary.
    dataset = _read_metadata_relation(row, "dataset")
    return _read_metadata_relation(dataset, "workspace")
