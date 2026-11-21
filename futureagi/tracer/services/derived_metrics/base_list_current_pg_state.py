"""A03: cooperative, same-transaction PostgreSQL response revisions.

This is not a qualification provider. Tokens prove neither complete producer
coverage nor CH visibility. Raw SQL/COPY, historical models, save_base and
unintegrated Collector cascades remain unenrolled. No deployment can qualify
from these tokens alone, even when hooks are enabled.

Supported lock order (one primary connection / transaction): already-owned
sorted lifecycle locks, if any -> PG writer advisory gate -> native rows/label
ownership -> identity fence -> sorted project fences. This adapter NEVER takes
lifecycle/lease locks. Do not acquire them from inside a writer context. The
single cooperative gate also orders nested saves and multi-statement bulk work;
it is a conservative write-serialization cost, not a reader/lifecycle lock.
Callers taking source row locks themselves must enter pg_response_writer FIRST.
A caller already holding arbitrary row/lease locks is not an enrolled lock path.
A06 must enter the gate after lifecycle locks and before freezing its graph;
this module neither replaces nor monkeypatches the existing Collector protocol.

Increments occur after the native mutation but BEFORE transaction commit, not in
on_commit or a signal. Source and revisions are never independently visible.
Failure of scope resolution or revision persistence rolls the mutation back.
An enclosing transaction retains all locks; do not catch and ignore DB errors.
"""

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID

from django.db import DEFAULT_DB_ALIAS, connections, transaction

from tfc.routers import force_primary

MAX_REVISION = (1 << 63) - 1
# PostgreSQL's two-int namespace is disjoint from lifecycle's bigint locks.
# Stable across processes; no Python hash() / session-scoped lock involved.
PG_RESPONSE_WRITER_LOCK = (0x50475245, 0x53504F4E)


class PgRevisionUnavailable(RuntimeError):
    """No usable primary revision evidence (never permission to fast-serve)."""


@dataclass(frozen=True, order=True)
class PgProjectScope:
    """Project coordinate, NOT a tenant authorization assertion."""

    project_id: UUID

    def __post_init__(self):
        if not isinstance(self.project_id, UUID):
            raise TypeError("project_id must be UUID")


@dataclass(frozen=True)
class PgRevisionToken:
    incarnation: UUID
    revision: int


@dataclass(frozen=True)
class PgProjectRevision:
    scope: PgProjectScope
    token: PgRevisionToken


@dataclass(frozen=True)
class PgResponseVector:
    using: str
    projects: tuple[PgProjectRevision, ...]
    identity: PgRevisionToken

    @property
    def production_qualified(self) -> bool:
        # Deliberate fail-closed boundary. A04/A07 and the independent CH/helper
        # authorities must implement qualification; this adapter cannot do so.
        return False


def _primary(using: str):
    # Only the application's authoritative alias. default_direct is a separate
    # connection, even if it points at the same PG database: not interchangeable
    # inside the source transaction. Do not silently reroute explicit replicas.
    if using != DEFAULT_DB_ALIAS:
        raise PgRevisionUnavailable("PG revisions require the default primary alias")
    db = connections[using]
    if db.vendor != "postgresql":
        raise PgRevisionUnavailable("PG revisions require PostgreSQL")
    return db


def _scopes(scopes: Iterable[PgProjectScope]) -> tuple[PgProjectScope, ...]:
    result = tuple(scopes)
    if any(not isinstance(scope, PgProjectScope) for scope in result):
        raise TypeError("expected PgProjectScope values")
    return tuple(sorted(set(result)))


@contextmanager
def pg_response_writer(*, using: str = DEFAULT_DB_ALIAS) -> Iterator[None]:
    """Enter BEFORE caller-owned source locks; retain the gate until commit.

    This explicit API is unconditional. Only automatic cooperative hooks are
    default-OFF. No timeout/bounded-reader claim is made by this writer API.
    """
    db = _primary(using)
    with force_primary(), transaction.atomic(using=using):
        with db.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(%s, %s)", PG_RESPONSE_WRITER_LOCK
            )
        yield


def _increment(row, *, using: str) -> None:
    if row.revision >= MAX_REVISION:
        raise PgRevisionUnavailable("PG revision exhausted")
    row.revision += 1
    row.save(using=using, update_fields=["revision"])


@contextmanager
def pg_response_mutation(
    old_new_project_scopes: Iterable[PgProjectScope],
    identity_changed: bool,
    using: str = DEFAULT_DB_ALIAS,
) -> Iterator[None]:
    """Commit native changes and serialized fences atomically, or roll back both.

    Scopes must include the locked current OLD and actual NEW owners, not stale
    request objects. Score tenancy is label.project, never Score.project or its
    denormalized tracer_project_id. A06 may call this after taking its lifecycle
    locks, inside pg_response_writer and the existing delete transaction.
    """
    from tracer.models.metric_pg_revision import (
        MetricPgIdentityRevision,
        MetricPgResponseRevision,
    )

    scopes = _scopes(old_new_project_scopes)
    if type(identity_changed) is not bool:
        raise TypeError("identity_changed must be bool")
    with pg_response_writer(using=using):
        yield
        # Identity is initialized even for an empty project. This establishes a
        # vector, not coverage. No sequence reservation or timestamp is used.
        MetricPgIdentityRevision.objects.using(using).get_or_create(singleton=1)
        identity = (
            MetricPgIdentityRevision.objects.using(using)
            .select_for_update()
            .get(singleton=1)
        )
        if identity_changed:
            _increment(identity, using=using)
        for scope in scopes:
            MetricPgResponseRevision.objects.using(using).get_or_create(
                project_id=scope.project_id
            )
            row = (
                MetricPgResponseRevision.objects.using(using)
                .select_for_update()
                .get(project_id=scope.project_id)
            )
            _increment(row, using=using)


def read_pg_response_vector(
    project_scopes: Iterable[PgProjectScope], *, using: str = DEFAULT_DB_ALIAS
) -> PgResponseVector:
    """One primary statement snapshot, without initializing absent fence rows.

    A04 must invoke before all response reads and freshly after complete lazy
    materialization, on READ COMMITTED. This helper alone does not bracket reads
    or reauthorize Project. It refuses repeatable snapshots and missing rows.
    """
    from tracer.models.metric_pg_revision import (
        MetricPgIdentityRevision,
        MetricPgResponseRevision,
    )

    scopes = _scopes(project_scopes)
    db = _primary(using)
    # One SQL statement for the entire vector including global identity, so a
    # multi-project or identity commit cannot tear the initial/final vector.
    project_table = db.ops.quote_name(MetricPgResponseRevision._meta.db_table)
    identity_table = db.ops.quote_name(MetricPgIdentityRevision._meta.db_table)
    with force_primary(), db.cursor() as cursor:
        cursor.execute("SHOW transaction_isolation")
        if cursor.fetchone()[0] != "read committed":
            raise PgRevisionUnavailable("PG vector requires READ COMMITTED")
        cursor.execute(
            f"SELECT project_id, incarnation, revision FROM {project_table} "
            "WHERE project_id = ANY(%s::uuid[]) "
            f"UNION ALL SELECT NULL::uuid, incarnation, revision FROM {identity_table} "
            "WHERE singleton = 1",
            [[str(scope.project_id) for scope in scopes]],
        )
        rows = {
            key: PgRevisionToken(epoch, revision) for key, epoch, revision in cursor
        }
    if None not in rows or any(scope.project_id not in rows for scope in scopes):
        raise PgRevisionUnavailable("PG revision row missing; native only")
    return PgResponseVector(
        using=using,
        projects=tuple(
            PgProjectRevision(scope, rows[scope.project_id]) for scope in scopes
        ),
        identity=rows[None],
    )
