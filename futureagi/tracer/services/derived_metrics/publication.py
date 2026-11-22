"""Manifest-only reads and synchronous, lossless ClickHouse batch receipts.

The client must address the selected LOCAL writer/read shard, not Distributed or
load-balanced replicas. A receipt from another server/database fails closed. A
replica needs its own verified receipts before it can serve the same manifest.
Opaque RowBinary preserves AggregateFunction states without decoding/rebuilding
sketches in Python. Payloads are private, bounded and never included in errors.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
from time import monotonic

from tracer.models.derived_metric import MetricGenerationPin, MetricStateConflict
from tracer.services.derived_metrics.deletion_protocol import (
    capture_pin,
    serialized_project,
)
from tracer.services.derived_metrics.sketch_serialization import canonical_sketch_row
from tracer.services.derived_metrics.storage import (
    BUCKET_CHECKPOINT,
    BUCKET_TOTALS,
    FIELD_CHECKPOINT,
    FIELD_CHECKPOINT_BUCKETS,
    FIELD_CHECKPOINT_PARTITION,
    FIELD_VALUES,
    TABLES,
)

MAX_BATCH_ROWS = 500
# Checkpoint transport is independent of discovery/admission's 500-row pages.
# Both paths retain the same byte and statement-memory ceilings.
MAX_COMPACTION_ROWS = 50_000
MAX_BATCH_BYTES = 16 * 1024 * 1024
# format(RowBinary, unhex(bound_literal)) constant-folds and copies the hex
# multiple times before INSERT. A 16 MiB frozen page measured 624.69 MiB
# statement memory under the 512 MiB cap; checkpoint transport stays smaller.
MAX_COMPACTION_BYTES = 4 * 1024 * 1024
WRITE_SETTINGS = {
    "async_insert": 0,
    "max_threads": 2,
    # Parallel primary-key/skip-index analysis keeps untracked per-thread
    # memory proportional to parts x IN-set size and charges it at once: one
    # 1,000-unit page on 773 spans parts peaked at 1.19 GiB (max_threads 2)
    # versus 102 MB with a single index-analysis thread.
    "max_threads_for_indexes": 1,
    "max_memory_usage": 512 * 1024 * 1024,
    "max_execution_time": 60,
    "use_query_cache": 0,
    # Execution blocks are not publication pages. Using the 500-row transport
    # limit here also fragments every upstream scan, sort and digest. Keep the
    # wire/page limits in freeze_select/FrozenRows, and amortize scan work within
    # a preferred byte target. The hard memory/thread/time caps above still apply.
    "max_block_size": 8192,
    "preferred_block_size_bytes": 4 * 1024 * 1024,
    # RowBinary is bound as one opaque hex string, not parsed as row literals.
    "max_query_size": 64 * 1024 * 1024,
}


class MetricVisibilityError(MetricStateConflict):
    code = "metric_visibility_unavailable"


class MetricCheckpointLayoutPending(MetricVisibilityError):
    code = "committed_layout_pending"


class MetricStatementTimeout(MetricVisibilityError):
    """The client-side statement wall elapsed while packets were still pending."""


# ClickHouse TIMEOUT_EXCEEDED: the server enforced the max_execution_time we set.
SERVER_TIMEOUT_CODE = 159


def statement_timed_out(exc):
    """Only transport/statement walls, never a memory, storage or query error."""
    from clickhouse_driver import errors

    timeouts = (
        MetricStatementTimeout,
        TimeoutError,
        getattr(errors, "SocketTimeoutError", TimeoutError),
    )
    if isinstance(exc, timeouts):
        return True
    return (
        isinstance(exc, errors.ServerException)
        and getattr(exc, "code", None) == SERVER_TIMEOUT_CODE
    )


def pass_bounded(lease, statement_deadline):
    """Whether the reservation's monotonic pass deadline bounded a statement.

    int() flooring of the server cap leaves at most one second of slack. The
    60-second statement cap, a compaction wall or lease expiry are not ours.
    """
    deadline = getattr(lease, "deadline", None)
    return (
        deadline is not None
        and statement_deadline is not None
        and deadline <= statement_deadline + 1
    )


def cooperative_timeout(lease, statement_deadline, exc):
    """Map a timeout caused by OUR pass-budget clamp to cooperative cancellation.

    Only a statement/transport timeout of a statement whose wall was reduced to
    the reservation's remaining pass budget qualifies (also ClickHouse's early
    estimate against that reduced cap). Unclamped timeouts and every other
    backend failure still fail. Callers have already discarded the connection.
    """
    if not statement_timed_out(exc) or not pass_bounded(lease, statement_deadline):
        return None
    from tracer.services.derived_metrics.lease import MetricPassDeadline

    return MetricPassDeadline()


@dataclass(frozen=True)
class CheckpointPartition:
    partition: str
    partition_id: str
    active_parts: int
    rows: int
    bytes_on_disk: int


@dataclass(frozen=True, repr=False)
class FrozenRows:
    """Exact serialized rows in version order; safe for equal-version retries."""

    rows: tuple[str, ...]
    last_key: tuple | None = None
    row_limit: int = MAX_BATCH_ROWS

    def __post_init__(self):
        if self.row_limit not in (MAX_BATCH_ROWS, MAX_COMPACTION_ROWS):
            raise ValueError("invalid metric batch row cap")
        if len(self.rows) > self.row_limit:
            raise ValueError("metric batch row cap exceeded")
        try:
            size = sum(len(bytes.fromhex(row)) for row in self.rows)
        except (ValueError, TypeError):
            raise ValueError("invalid frozen metric payload") from None
        byte_cap = (
            MAX_COMPACTION_BYTES
            if self.row_limit == MAX_COMPACTION_ROWS
            else MAX_BATCH_BYTES
        )
        if size > byte_cap:
            raise ValueError("metric batch byte cap exceeded")

    @classmethod
    def from_plan(cls, plan):
        return cls(tuple(plan["rows"]), row_limit=plan.get("row_limit", MAX_BATCH_ROWS))

    @property
    def digest(self):
        return sha256(bytes.fromhex("".join(self.rows))).hexdigest()

    def __repr__(self):
        return f"<FrozenRows count={len(self.rows)}>"


def physical_table(table, *, checkpoint=False):
    table = TABLES[table.name if hasattr(table, "name") else table]
    if checkpoint:
        return {
            FIELD_VALUES.name: FIELD_CHECKPOINT,
            BUCKET_TOTALS.name: BUCKET_CHECKPOINT,
        }.get(table.name, table)
    return table


def committed_rows_select(
    table,
    manifest,
    *,
    org_id,
    project_id,
    overlay=None,
    key_after=None,
    key_in=None,
    key_relation=None,
    param_prefix="pub",
):
    """Collapse an exact allowlist BEFORE any mutable/time/deletion predicate.

    An optional writer-owned overlay is an already frozen attempt, never a raw
    latest-row read. All columns include tombstones, including for compaction.
    Identifiers come exclusively from storage.py. Callers may wrap this SQL with
    post-collapse filters. key_in accepts bounded scalar or correlated-tuple
    memberships in immutable logical keys ONLY; an empty membership is false.
    key_relation is a compiler-owned positive key subquery for large nominations,
    not a raw predicate on the revisions being collapsed.
    Give composed readers distinct param_prefix values so their bindings cannot
    overwrite each other. No mutable/time/deletion pushdown is exposed.
    """
    from tracer.services.derived_metrics.bounded_reads import committed_branches

    table = TABLES[table.name if hasattr(table, "name") else table]
    columns = ", ".join(table.columns)
    branches, params = committed_branches(
        table,
        manifest,
        org_id=org_id,
        project_id=project_id,
        overlay=overlay,
        key_after=key_after,
        key_in=key_in,
        key_relation=key_relation,
        param_prefix=param_prefix,
    )
    if not branches:
        branches.append(f"SELECT {columns} FROM {table.name} WHERE 0")
    return (
        f"SELECT {columns} FROM ({' UNION ALL '.join(branches)}) "
        f"ORDER BY generation DESC, {table.version_column} DESC "
        f"LIMIT 1 BY {', '.join(table.logical_key)}",
        params,
    )


class ClickHousePublicationStore:
    """One local shard, with server-enforced bounds on every statement."""

    def __init__(self, client, *, lease=None):
        self.client = client
        self.lease = lease

    def _restore_transients(self):
        """Rebuild checked session captures after the driver replaced the session.

        Every exception discards the native connection (and therefore its
        temporary tables). Retry paths such as bucket subdivision continue on a
        new session; registered captures replay their immutable sources there.
        """
        relations = getattr(self, "_transient_relations", None)
        if not relations or getattr(self, "_restoring", False):
            return
        self._restoring = True
        try:
            for relation in tuple(relations):
                relation.ensure()
        finally:
            self._restoring = False

    @contextmanager
    def compaction_budget(self, deadline):
        """One invocation budget, including preparation, digesting and staging.

        A client deadline excludes an ambiguous write; it does not claim that
        the server stopped. Durable submission obligations remain with callers.
        """
        previous = getattr(self, "_compaction_deadline", None)
        self._compaction_deadline = min(previous, deadline) if previous else deadline
        try:
            yield
        finally:
            self._compaction_deadline = previous

    def execute(self, sql, params=None, **kwargs):
        self._restore_transients()
        deadline = getattr(self, "_compaction_deadline", None)
        if self.lease is not None:
            self.lease.keepalive()
            lease_deadline = monotonic() + self.lease.remaining_seconds()
            deadline = min(deadline, lease_deadline) if deadline else lease_deadline
        if deadline is None:
            return self.client.execute(
                sql, params or {}, settings=WRITE_SETTINGS, **kwargs
            )
        seconds = min(WRITE_SETTINGS["max_execution_time"], int(deadline - monotonic()))
        if seconds < 1:
            if self.lease is not None:
                from tracer.services.derived_metrics.lease import MetricPassDeadline

                self.lease.check()
                raise MetricPassDeadline()
            raise MetricVisibilityError("checkpoint compaction deadline exceeded")
        statement_deadline = min(deadline, monotonic() + seconds)
        try:
            return self._bounded_execute(
                sql, params, seconds, statement_deadline, **kwargs
            )
        except Exception as exc:
            # The connection is already discarded. Our own remaining-budget
            # clamp is a cooperative yield: resumable, not a refresh failure.
            cancelled = cooperative_timeout(self.lease, statement_deadline, exc)
            if cancelled is not None:
                raise cancelled from exc
            raise

    def _bounded_execute(self, sql, params, seconds, statement_deadline, **kwargs):
        connection = self.client.connection
        old_connect = connection.connect_timeout
        old_receive = connection.send_receive_timeout
        socket = connection.socket
        old_timeout = socket.gettimeout() if socket is not None else None
        try:
            connection.connect_timeout = min(old_connect, seconds)
            connection.send_receive_timeout = min(old_receive, seconds)
            if socket is not None:
                socket.settimeout(seconds)
            result = self.client.execute_with_progress(
                sql,
                params or {},
                settings={**WRITE_SETTINGS, "max_execution_time": seconds},
                **kwargs,
            )
            socket = connection.socket
            packets = iter(result.packet_generator)
            while True:
                remaining = statement_deadline - monotonic()
                if remaining <= 0:
                    raise MetricStatementTimeout(
                        "checkpoint compaction statement timed out"
                    )
                socket.settimeout(min(old_receive, remaining))
                try:
                    packet = next(packets)
                except StopIteration:
                    break
                result.store(packet)
            return result.get_result()
        except BaseException:
            self.client.disconnect()
            raise
        finally:
            connection.connect_timeout = old_connect
            connection.send_receive_timeout = old_receive
            if socket is not None and connection.socket is socket:
                socket.settimeout(old_receive if old_timeout is None else old_timeout)

    def identity(self):
        engines = self.execute(
            "SELECT name, engine FROM system.tables WHERE database = currentDatabase() AND name IN %(local_tables)s",
            {"local_tables": tuple(TABLES)},
        )
        if len(engines) != len(TABLES) or any(
            not engine.endswith("ReplacingMergeTree") for _, engine in engines
        ):
            raise MetricVisibilityError("publication requires all local shard tables")
        row = self.execute("SELECT toString(serverUUID()), currentDatabase()")[0]
        return list(row)

    def freeze_select(
        self, table, sql, params, *, key_columns=(), row_limit=MAX_BATCH_ROWS
    ):
        """Serialize a bounded, deterministically ordered SELECT on this shard.

        formatRowNoNewline MUST NOT be used: it removes the final byte of a
        RowBinary record, not just text newlines. formatRow is lossless.
        """
        # Computed SELECTs can infer e.g. Int64 rather than UInt8, empty maps,
        # or DateTime64(9) rather than (6). Serialize the PHYSICAL types exactly.
        columns = ", ".join(
            "CAST(" + name + ", '" + kind.replace("'", "\\'") + "')"
            for name, kind in table.schema
        )
        if set(key_columns) - set(table.logical_key):
            raise ValueError("invalid metric batch paging keys")
        if row_limit not in (MAX_BATCH_ROWS, MAX_COMPACTION_ROWS):
            raise ValueError("invalid metric batch row cap")
        keys = (", " + ", ".join(key_columns)) if key_columns else ""
        encoded = (
            f"SELECT hex(formatRow('RowBinary', {columns})) AS encoded{keys}, "
            f"{table.version_column} AS encoded_version FROM ({sql}) "
            f"ORDER BY {table.version_column} LIMIT %(pub_limit)s"
        )
        if row_limit == MAX_COMPACTION_ROWS:
            # Truncate by bytes on the server, BEFORE transporting the payload.
            # Keep an oversized first record so FrozenRows fails closed instead
            # of mistaking an indivisible oversize key for an empty range.
            encoded = (
                "SELECT * FROM (SELECT *, "
                "sum(length(encoded) / 2) OVER (ORDER BY encoded_version "
                "ROWS UNBOUNDED PRECEDING) AS encoded_bytes, "
                "row_number() OVER (ORDER BY encoded_version) AS encoded_row "
                f"FROM ({encoded})) WHERE encoded_bytes <= %(pub_bytes)s OR encoded_row = 1"
            )
        rows = self.execute(
            f"SELECT encoded{keys} FROM ({encoded}) ORDER BY encoded_version",
            {
                **params,
                "pub_limit": row_limit,
                "pub_bytes": MAX_COMPACTION_BYTES
                if row_limit == MAX_COMPACTION_ROWS
                else MAX_BATCH_BYTES,
            },
        )
        return FrozenRows(
            tuple(canonical_sketch_row(table, row[0]) for row in rows),
            tuple(rows[-1][1:]) if rows and key_columns else None,
            row_limit=row_limit,
        )

    def insert(self, table, payload):
        if payload.rows:
            if table == FIELD_CHECKPOINT:
                self.assert_checkpoint_schema()
            structure = ", ".join(f"{name} {kind}" for name, kind in table.schema)
            self.execute(
                f"INSERT INTO {table.name} ({', '.join(table.columns)}) "
                "SELECT * FROM format(RowBinary, %(pub_structure)s, unhex(%(pub_payload)s))",
                {"pub_structure": structure, "pub_payload": "".join(payload.rows)},
            )

    def verify(self, table, attempt, batch):
        """Hash complete shard rows, normalizing only unordered sketch hashes.

        Canonical full-state equality retains arbitrary future merge behavior;
        equal finalized cardinalities alone would not detect different members.
        Non-sketch payloads and every other byte keep the original exact check.
        """
        lo = (attempt.fencing_epoch << 32) | batch.row_sequence_first
        hi = (attempt.fencing_epoch << 32) | batch.row_sequence_last
        columns = ", ".join(table.columns)
        rows = self.execute(
            f"SELECT hex(formatRow('RowBinary', {columns})) FROM ("
            f"SELECT DISTINCT {columns} FROM {table.name} "
            "WHERE project_id = toUUID(%(project)s) AND attempt_id = toUUID(%(attempt)s) "
            f"AND generation = %(generation)s AND {table.version_column} BETWEEN %(lo)s AND %(hi)s) "
            f"ORDER BY {table.version_column}",
            {
                "project": str(attempt.project_id),
                "attempt": str(attempt.id),
                "generation": attempt.generation,
                "lo": lo,
                "hi": hi,
            },
        )
        actual = FrozenRows(
            tuple(dict.fromkeys(canonical_sketch_row(table, row[0]) for row in rows)),
            row_limit=MAX_COMPACTION_ROWS
            if attempt.purpose == "compaction"
            else MAX_BATCH_ROWS,
        )
        if (len(actual.rows), actual.digest) != (batch.row_count, batch.payload_sha256):
            raise MetricVisibilityError("frozen batch not visible on selected shard")
        return {"count": len(actual.rows), "sha256": actual.digest}

    def assert_checkpoint_schema(self):
        """IF NOT EXISTS cannot change an existing preview table's partition key.

        Fail before writing; a nonempty old table needs a reviewed shadow copy
        and cutover retaining every reachable attempt. Never auto-drop it.
        """
        rows = self.execute(
            "SELECT partition_key FROM system.tables WHERE database = currentDatabase() AND name = %(table)s",
            {"table": FIELD_CHECKPOINT.name},
        )
        expected = FIELD_CHECKPOINT_PARTITION.replace(" ", "")
        actual = rows[0][0].replace(" ", "") if len(rows) == 1 else ""
        if actual not in {expected, "tuple" + expected}:
            raise MetricVisibilityError(
                "checkpoint partition schema requires reviewed cutover"
            )

    def checkpoint_group(self, project_id):
        return self.execute(
            "SELECT cityHash64(toUUID(%(project)s)) %% 32",
            {"project": str(project_id)},
        )[0][0]

    def checkpoint_layout(self, project_id):
        """Actual tuple IDs and ALL colliding-project/pinned-attempt bytes.

        Tuple partition IDs are opaque; use system.parts.partition_id rather
        than constructing IDs from the displayed partition expression.
        """
        self.assert_checkpoint_schema()
        rows = self.execute(
            "SELECT partition, partition_id, count(), sum(rows), sum(bytes_on_disk) "
            "FROM system.parts WHERE database = currentDatabase() AND table = %(table)s "
            "AND active AND replaceAll(partition, ' ', '') IN "
            "(SELECT concat('(', toString(cityHash64(toUUID(%(project)s)) %% 32), "
            f"',', toString(number), ')') FROM numbers({FIELD_CHECKPOINT_BUCKETS})) "
            "GROUP BY partition, partition_id ORDER BY partition",
            {"table": FIELD_CHECKPOINT.name, "project": str(project_id)},
        )
        if len(rows) > FIELD_CHECKPOINT_BUCKETS:
            raise MetricVisibilityError("checkpoint partition layout is invalid")
        return tuple(CheckpointPartition(*row) for row in rows)

    def optimize_checkpoint(self, partition_id, query_id, seconds):
        """Bound the whole client receive loop, not only each idle socket read.

        A timeout disconnects this client but is NOT proof the merge stopped.
        The caller retains its durable query-id obligation for reconciliation.
        """
        if not 0 < seconds <= 60:
            raise ValueError("invalid checkpoint statement deadline")
        deadline = monotonic() + seconds
        connection = self.client.connection
        # The preceding identity/layout reads established this local connection.
        # force_connect also claims the driver's query-execution lock, so only
        # execute_with_progress may call it when submitting this statement.
        socket = connection.socket
        if socket is None:
            raise MetricVisibilityError("checkpoint local connection unavailable")
        old_timeout = socket.gettimeout()
        try:
            socket.settimeout(seconds)
            progress = self.client.execute_with_progress(
                f"OPTIMIZE TABLE {FIELD_CHECKPOINT.name} PARTITION ID %(partition)s FINAL",
                {"partition": partition_id},
                query_id=query_id,
                settings={**WRITE_SETTINGS, "max_execution_time": seconds},
            )
            packets = iter(progress.packet_generator)
            while True:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise MetricVisibilityError("checkpoint layout statement timed out")
                socket.settimeout(remaining)
                try:
                    next(packets)
                except StopIteration:
                    break
        except BaseException:
            self.client.disconnect()
            raise
        finally:
            if connection.socket is socket:
                socket.settimeout(old_timeout)

    def consolidate_checkpoint(self, project_id, *, deadline=None):
        from tracer.services.derived_metrics.compaction import consolidate_checkpoint

        return consolidate_checkpoint(self, project_id, deadline=deadline)

    def assert_visible(self, manifest):
        receipts = manifest.visibility_receipts
        if receipts.get("shard") != self.identity() or "batches" not in receipts:
            raise MetricVisibilityError(
                "manifest visibility is not proven on this shard"
            )
        ranges = receipts.get("ranges")
        if ranges is not None or receipts.get("format") == 2:
            from tracer.services.derived_metrics.compaction_ranges import ALGORITHM

            allowed = {str(attempt) for _, attempt in manifest.allowed_attempts()}
            if (
                receipts.get("format") != 2
                or not isinstance(ranges, list)
                or not ranges
                or any(
                    not isinstance(receipt, dict)
                    or receipt.get("attempt") not in allowed
                    or not isinstance(receipt.get("registry"), dict)
                    or receipt["registry"].get("algorithm") != ALGORITHM
                    or not isinstance(receipt["registry"].get("sha256"), str)
                    or len(receipt["registry"]["sha256"]) != 64
                    or type(receipt["registry"].get("count")) is not int
                    or receipt["registry"]["count"] < 56
                    for receipt in ranges
                )
            ):
                raise MetricVisibilityError("invalid range visibility envelope")


@contextmanager
def pinned_read(store, project_id, *, metric_id=None, pin=None, now=None):
    """Freeze ONE definition/head/remap; native reads never own lifecycle locks.

    A live pin roots immutable history against GC. Erasure may revoke it and
    purge during a multi-table read: the final serialized validation then rejects
    the entire assembled response, never returns partial or pre-erasure evidence.
    Completed snapshots are a separate store and are not read here.
    """
    owned = pin is None
    with serialized_project(project_id):
        if owned:
            pin = capture_pin(
                project_id,
                metric_id=metric_id,
                consumer_kind="request",
                ttl=timedelta(minutes=5),
                now=now,
            )
        if str(pin.project_id) != str(project_id) or not pin.is_usable(now):
            raise MetricStateConflict("expired generation")
        manifest = pin.manifest
    try:
        store.assert_visible(manifest)
        yield manifest, pin.definition_version
        with serialized_project(project_id):
            if not pin.is_usable(now):
                raise MetricStateConflict("expired generation")
    finally:
        if owned:
            MetricGenerationPin.objects.filter(id=pin.id).delete()
