"""Checked, connection-owned anonymous relations used during PREPARED refreshes.

These are capture handles, not publication receipts or recoverable range plans.
Only the final legacy FrozenRows ledger is replayed. Scratch tables never enter
TABLE_KINDS, a manifest, or the retired-parts store. Disconnect destroys every
backing table, including an ambiguous in-flight INSERT; a new connection cannot
adopt a handle. A failed preparation must be replanned, never resumed from one.
"""

from dataclasses import dataclass
from hashlib import sha256
from uuid import uuid4

from clickhouse_driver.errors import ServerException

from tracer.services.derived_metrics.compaction_ranges import canonical
from tracer.services.derived_metrics.publication import MetricVisibilityError
from tracer.services.derived_metrics.storage import (
    BUCKET_STAT_SCHEMA,
    FIELD_CHECKPOINT_BUCKETS,
)
from tracer.services.private_boundary_log import PrivateBoundaryLogger

private_logger = PrivateBoundaryLogger(__name__)

PART_SCHEMA = (
    ("bucket_start", "DateTime('UTC')"),
    ("group_key", "String"),
    ("scope_key", "String"),
    ("stratum_key", "String"),
    *BUCKET_STAT_SCHEMA,
)
PART_COLUMNS = ", ".join(name for name, _ in PART_SCHEMA)
PART_KEYS = "bucket_start, group_key, scope_key, stratum_key"
RELATION_ROWS = 250_000
RELATION_BYTES = 128 * 1024 * 1024
REGISTRY_LIMIT = 512
SCHEMA_SHA256 = sha256(canonical(PART_SCHEMA)).hexdigest()


class FrozenRelationOverflow(MetricVisibilityError):
    code = "metric_frozen_relation_overflow"


# Pass-scoped input captures: a proposal/overlay relation is executed ONCE
# into session scratch, then every page refers to it by name. The same fail-
# closed row/byte bounds as other scratch relations; never a lossy truncation.
CAPTURE_ROWS = 1_000_000
CAPTURE_BYTES = RELATION_BYTES


class SessionCapture:
    """A checked, connection-owned copy of immutable compiler inputs.

    Sources are compiler-owned (sql, params) pairs over immutable inputs only:
    frozen ledger batches, allowlisted committed attempts, or earlier captures.
    Each source is a separate INSERT, so no statement carries more than one
    frozen batch of inline bytes. Every exception discards the native session
    (and this table); the store replays registered captures on the next
    statement, in registration (dependency) order, and the replay must reproduce
    the identical count/bytes/content digest or the pass fails closed.
    """

    def __init__(self, store, *, schema, sources, role):
        self.store = store
        self.schema = tuple(schema)
        self.columns = tuple(name for name, _ in self.schema)
        self.sources = sources  # callable -> iterable of (sql, params)
        self.role = role
        self.name = "metric_capture_" + uuid4().hex
        self.socket = None
        self.digest = None
        self.overflow_rows = None  # cumulative rows measured before a rejected insert

    def __enter__(self):
        self.store._restore_transients()
        try:
            self._build()
        except Exception:
            # A rejected (e.g. over-budget) build may already own an empty or
            # partial scratch table. Never leave private rows on the session.
            socket = self.store.client.connection.socket
            try:
                if socket is not None:
                    self.store.execute(f"DROP TEMPORARY TABLE IF EXISTS {self.name}")
            except Exception as cleanup_error:
                self.store.client.disconnect()
                private_logger.exception("metric_capture_drop_failed", cleanup_error)
            raise
        relations = getattr(self.store, "_transient_relations", None)
        if relations is None:
            relations = self.store._transient_relations = []
        relations.append(self)
        return self

    def __exit__(self, exc_type, exc, tb):
        relations = getattr(self.store, "_transient_relations", None) or []
        if self in relations:
            relations.remove(self)
        socket = self.store.client.connection.socket
        try:
            if socket is not None and socket is self.socket:
                self.store.execute(f"DROP TEMPORARY TABLE IF EXISTS {self.name}")
        except Exception:
            # Never return a pooled connection holding private scratch rows.
            self.store.client.disconnect()
            if exc_type is None:
                raise
        finally:
            self.socket = None
        return False

    @property
    def select(self):
        return f"SELECT {', '.join(self.columns)} FROM {self.name}"

    def ensure(self):
        socket = self.store.client.connection.socket
        if socket is None or socket is not self.socket:
            self._build()

    def _measure(self):
        encoded = f"formatRow('RowBinary', {', '.join(self.columns)})"
        return tuple(
            self.store.execute(
                f"SELECT count(), sum(length({encoded})), "
                f"toString(sum(cityHash64({encoded}))) FROM {self.name}"
            )[0]
        )

    def _build(self):
        store = self.store
        previous = getattr(store, "_restoring", False)
        # Our own statements must not recursively replay this capture.
        store._restoring = True
        try:
            store.execute("SELECT 1")
            socket = store.client.connection.socket
            if socket is None:
                raise MetricVisibilityError("session capture requires a native session")
            schema = ", ".join(f"{column} {kind}" for column, kind in self.schema)
            store.execute(
                f"CREATE TEMPORARY TABLE {self.name} ({schema}) ENGINE=Memory"
            )
            casts = ", ".join(
                "CAST(" + column + ", '" + kind.replace("'", "\\'") + "') AS " + column
                for column, kind in self.schema
            )
            encoded = f"formatRow('RowBinary', {', '.join(self.columns)})"
            rows = size = 0
            for sql, params in self.sources():
                typed = f"SELECT {casts} FROM ({sql})"
                count, length = store.execute(
                    f"SELECT count(), sum(length({encoded})) FROM ({typed})", params
                )[0]
                rows, size = rows + count, size + (length or 0)
                if rows > CAPTURE_ROWS or size > CAPTURE_BYTES:
                    self.overflow_rows = rows
                    raise FrozenRelationOverflow("session capture exceeds its budget")
                if count:
                    store.execute(
                        f"INSERT INTO {self.name} ({', '.join(self.columns)}) {typed}",
                        params,
                    )
            if store.client.connection.socket is not socket:
                raise MetricVisibilityError("session capture lost its native session")
            digest = self._measure()
            if digest[0] != rows or (digest[1] or 0) != size:
                raise MetricVisibilityError("session capture is incomplete")
            if self.digest is not None and digest != self.digest:
                raise MetricVisibilityError("session capture changed on replay")
            self.digest, self.socket = digest, socket
        finally:
            store._restoring = previous


@dataclass(frozen=True, repr=False)
class FrozenRelationHandle:
    """No payload list and no unqualified/latest-table read API.

    The issuing registry also requires object identity, its original live native
    socket, exact scope/source/attempt/schema and independently re-read bytes.
    Copying a dataclass or retaining it after its context closes grants no access.
    """

    namespace: str
    role: str
    scope: tuple[str, str, str]
    source_sha256: str
    schema_sha256: str
    row_count: int
    byte_count: int
    content_sha256: str
    registry_sha256: str
    backend: str = "session-memory-v1"

    def __repr__(self):
        return f"<FrozenRelationHandle role={self.role} count={self.row_count}>"


class FrozenRelations:
    """Bounded physical backing with an exhaustive contribution registry.

    Each child compiler executes once. Consumers read only checked materialized
    rows. The global merge and global top-50 fold each execute once as well.
    Bounds are fail-closed, never Memory's lossy max_rows_to_keep settings.
    """

    def __init__(self, store, *, org_id, project_id, attempt_id, source):
        self.store = store
        self.scope = tuple(map(str, (org_id, project_id, attempt_id)))
        self.source_sha256 = sha256(canonical(source)).hexdigest()
        self.handles = {}
        self.names = []
        self.socket = None
        self.closed = False
        self.contributions = []
        self.complete = False
        self.coverage_sha256 = None

    def __enter__(self):
        # Establish the native session BEFORE assigning any namespace. A lost
        # socket invalidates every handle even if the driver could reconnect.
        self.store.execute("SELECT 1")
        self.socket = self.store.client.connection.socket
        if self.socket is None:
            raise MetricVisibilityError("frozen relation requires a native session")
        return self

    def _check_session(self):
        if self.closed or self.store.client.connection.socket is not self.socket:
            raise MetricVisibilityError("frozen relation session is no longer valid")

    def _measure(self, name):
        self._check_session()
        # Hash bounded leaves of exact physical RowBinary; no payload or giant
        # concatenation crosses the wire. Empty relations have a defined root.
        rows = self.store.execute(
            "SELECT intDiv(position - 1, 4096) AS leaf, count(), "
            "sum(length(encoded)), min(position), max(position), "
            "hex(SHA256(arrayStringConcat(arrayMap(x -> x.2, "
            "arraySort(x -> x.1, groupArray(tuple(position, SHA256(encoded)))))))) "
            "FROM (SELECT row_number() OVER (ORDER BY " + PART_KEYS + ") AS position, "
            f"formatRow('RowBinary', {PART_COLUMNS}) AS encoded FROM {name}) "
            "GROUP BY leaf ORDER BY leaf"
        )
        digest = sha256(b"metric-frozen-capture-v1\0")
        count = size = 0
        for ordinal, row in enumerate(rows):
            leaf, n, length, first, last, hashed = row
            if (
                leaf != ordinal
                or not 1 <= n <= 4096
                or first != count + 1
                or last != count + n
                or (ordinal < len(rows) - 1 and n != 4096)
            ):
                raise MetricVisibilityError("frozen relation digest is incomplete")
            count += n
            size += length
            if count > RELATION_ROWS or size > RELATION_BYTES:
                raise FrozenRelationOverflow("frozen relation exceeds capture budget")
            digest.update(canonical(row) + b"\n")
        return count, size, digest.hexdigest()

    def materialize(self, sql, params, *, role, parents=()):
        self._check_session()
        if role not in {"child", "retired", "complete-merged", "complete-folded"}:
            raise ValueError("invalid frozen relation completeness role")
        if len(self.names) >= REGISTRY_LIMIT:
            raise FrozenRelationOverflow("frozen relation registry budget exceeded")
        if role in {"child", "retired"} and self.complete:
            raise MetricVisibilityError("frozen contribution registry is sealed")
        if role == "retired" and any(h.role == "retired" for h in self.contributions):
            raise MetricVisibilityError(
                "retired contributions may be captured only once"
            )
        if role.startswith("complete-") and not self.complete:
            raise MetricVisibilityError("frozen contribution coverage is incomplete")
        for parent in parents:
            self._check_handle(parent)
        name = "metric_frozen_" + uuid4().hex
        self.names.append(name)  # registered BEFORE either DDL or INSERT
        schema = ", ".join(f"{column} {kind}" for column, kind in PART_SCHEMA)
        self.store.execute(f"CREATE TEMPORARY TABLE {name} ({schema}) ENGINE=Memory")
        casts = ", ".join(
            "CAST(" + column + ", '" + kind.replace("'", "\\'") + "') AS " + column
            for column, kind in PART_SCHEMA
        )
        # Validate complete typed output BEFORE writing any physical rows. A
        # LIMIT sentinel followed by a measurement would already have stored an
        # arbitrarily wide row in Memory. Neither row nor byte overflow may
        # silently truncate. The source compiler occurs exactly once here.
        remaining_bytes = RELATION_BYTES - sum(
            h.byte_count for h in self.handles.values()
        )
        remaining_rows = RELATION_ROWS - sum(h.row_count for h in self.handles.values())
        try:
            self.store.execute(
                f"INSERT INTO {name} ({PART_COLUMNS}) SELECT {PART_COLUMNS} FROM ("
                "SELECT *, count() OVER () AS capture_rows, "
                f"sum(length(formatRow('RowBinary', {PART_COLUMNS}))) OVER () AS capture_bytes "
                f"FROM (SELECT {casts} FROM ({sql}))) "
                f"WHERE throwIf(capture_rows > {remaining_rows} OR capture_bytes > {remaining_bytes}, "
                "'metric_frozen_relation_budget') = 0",
                params,
            )
        except ServerException as exc:
            # Normalize only our pre-insert throwIf envelope, not memory,
            # timeout, storage or unrelated server failures. The caller may
            # subdivide this INPUT capture before any publication consumer runs.
            message = exc.message.removeprefix("DB::Exception: ")
            if (
                exc.code != 395
                or message.split(":", 1)[0] != "metric_frozen_relation_budget"
            ):
                raise
            raise FrozenRelationOverflow(
                "frozen relation exceeds capture budget"
            ) from exc
        self._check_session()
        count, size, digest = self._measure(name)
        if self.store.execute(
            f"SELECT count(), uniqExact(tuple({PART_KEYS})) FROM {name}"
        ) != [(count, count)]:
            raise MetricVisibilityError("frozen relation contains duplicate keys")
        if sum(h.byte_count for h in self.handles.values()) + size > RELATION_BYTES:
            raise FrozenRelationOverflow("frozen relation backing budget exceeded")
        root = sha256(
            canonical(
                [
                    "metric-frozen-registry-v1",
                    name,
                    role,
                    self.scope,
                    self.source_sha256,
                    SCHEMA_SHA256,
                    self.coverage_sha256,
                    count,
                    size,
                    digest,
                    sha256(canonical([sql, params])).hexdigest(),
                    [p.registry_sha256 for p in parents],
                ]
            )
        ).hexdigest()
        handle = FrozenRelationHandle(
            name,
            role,
            self.scope,
            self.source_sha256,
            SCHEMA_SHA256,
            count,
            size,
            digest,
            root,
        )
        self.handles[name] = handle
        if role in {"child", "retired"}:
            self.contributions.append(handle)
        return handle

    def _check_handle(self, handle, *, role=None):
        self._check_session()
        if (
            not isinstance(handle, FrozenRelationHandle)
            or self.handles.get(handle.namespace) is not handle
            or handle.scope != self.scope
            or handle.source_sha256 != self.source_sha256
            or handle.schema_sha256 != SCHEMA_SHA256
            or handle.backend != "session-memory-v1"
            or (role is not None and handle.role != role)
        ):
            raise MetricVisibilityError(
                "frozen relation identity or completeness mismatch"
            )

    def select(self, handle, *, role):
        self._check_handle(handle, role=role)
        if self._measure(handle.namespace) != (
            handle.row_count,
            handle.byte_count,
            handle.content_sha256,
        ):
            raise MetricVisibilityError("frozen relation changed after capture")
        return f"SELECT {PART_COLUMNS} FROM {handle.namespace}"

    def finish(self, *, expected_children, coverage=None):
        """Called only after the exhaustive child iterator has been exhausted."""
        from tracer.services.derived_metrics.buckets import (
            fold_parts_select,
            merge_parts_select,
        )

        self._check_session()
        children = [h for h in self.contributions if h.role == "child"]
        retired = [h for h in self.contributions if h.role == "retired"]
        if self.complete or len(children) != expected_children or len(retired) != 1:
            raise MetricVisibilityError("frozen contribution registry is incomplete")
        if type(expected_children) is not int or expected_children < 0:
            raise MetricVisibilityError("invalid frozen child count")
        if coverage is not None:
            descriptors, routes = coverage
            if len(descriptors) != expected_children or routes not in (
                [None],
                list(range(FIELD_CHECKPOINT_BUCKETS)),
            ):
                raise MetricVisibilityError("frozen field route coverage is incomplete")
            for route in routes:
                previous = None
                for routed, lower, upper, count, size in descriptors:
                    if routed != route:
                        continue
                    if lower != previous or upper == lower or count < 1 or size < 1:
                        raise MetricVisibilityError(
                            "frozen field ranges are discontinuous"
                        )
                    previous = upper
            if any(d[0] not in routes for d in descriptors):
                raise MetricVisibilityError("unexpected frozen field route")
        self.coverage_sha256 = sha256(canonical(coverage)).hexdigest()
        # Explicit empty retired/child captures are valid; no truthiness fallback.
        union = " UNION ALL ".join(
            self.select(h, role=h.role) for h in self.contributions
        )
        self.complete = True
        merged = self.materialize(
            merge_parts_select(union),
            {},
            role="complete-merged",
            parents=tuple(self.contributions),
        )
        return self.materialize(
            fold_parts_select(self.select(merged, role="complete-merged")),
            {},
            role="complete-folded",
            parents=(merged,),
        )

    def __exit__(self, exc_type, exc, tb):
        try:
            if self.store.client.connection.socket is self.socket:
                for name in reversed(self.names):
                    self.store.execute(f"DROP TEMPORARY TABLE IF EXISTS {name}")
        except Exception:
            # Never return a pooled connection containing private scratch rows.
            self.store.client.disconnect()
            if exc_type is None:
                raise
        finally:
            self.closed = True
            self.handles.clear()
            self.contributions.clear()
        return False
