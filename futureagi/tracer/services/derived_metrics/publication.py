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

from tracer.models.derived_metric import MetricGenerationPin, MetricStateConflict
from tracer.services.derived_metrics.deletion_protocol import (
    capture_pin,
    serialized_project,
)
from tracer.services.derived_metrics.storage import (
    BUCKET_CHECKPOINT,
    BUCKET_TOTALS,
    FIELD_CHECKPOINT,
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
    "max_memory_usage": 512 * 1024 * 1024,
    "max_execution_time": 60,
    "use_query_cache": 0,
    "max_block_size": MAX_BATCH_ROWS,
    # RowBinary is bound as one opaque hex string, not parsed as row literals.
    "max_query_size": 64 * 1024 * 1024,
}


class MetricVisibilityError(MetricStateConflict):
    code = "metric_visibility_unavailable"


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

    def __init__(self, client):
        self.client = client

    def execute(self, sql, params=None, **kwargs):
        return self.client.execute(sql, params or {}, settings=WRITE_SETTINGS, **kwargs)

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
            tuple(row[0] for row in rows),
            tuple(rows[-1][1:]) if rows and key_columns else None,
            row_limit=row_limit,
        )

    def insert(self, table, payload):
        if payload.rows:
            structure = ", ".join(f"{name} {kind}" for name, kind in table.schema)
            self.execute(
                f"INSERT INTO {table.name} ({', '.join(table.columns)}) "
                "SELECT * FROM format(RowBinary, %(pub_structure)s, unhex(%(pub_payload)s))",
                {"pub_structure": structure, "pub_payload": "".join(payload.rows)},
            )

    def verify(self, table, attempt, batch):
        """Deduplicate byte-identical retry rows, then hash actual shard bytes."""
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
            tuple(row[0] for row in rows),
            row_limit=MAX_COMPACTION_ROWS
            if attempt.purpose == "compaction"
            else MAX_BATCH_ROWS,
        )
        if (len(actual.rows), actual.digest) != (batch.row_count, batch.payload_sha256):
            raise MetricVisibilityError("frozen batch not visible on selected shard")
        return {"count": len(actual.rows), "sha256": actual.digest}

    def checkpoint_layout(self, project_id):
        """Conservative overlap bound: one active part cannot overlap another.

        Hash partitions can contain other projects and pinned checkpoints. Count
        ALL their parts/bytes before scheduling a merge, not just this attempt.
        """
        partition = self.execute(
            "SELECT toString(cityHash64(toUUID(%(project)s)) %% 32)",
            {"project": str(project_id)},
        )[0][0]
        parts, size = self.execute(
            "SELECT count(), sum(bytes_on_disk) FROM system.parts "
            "WHERE database = currentDatabase() AND table = %(table)s "
            "AND partition_id = %(partition)s AND active",
            {"table": FIELD_CHECKPOINT.name, "partition": partition},
        )[0]
        return partition, parts, size

    def consolidate_checkpoint(self, project_id):
        """One bounded partition merge, never whole-table FINAL or schema tuning.

        The publication remains committed if maintenance times out. Replaying
        stage_and_publish retries this idempotent layout step before reporting
        compaction success. A large shared partition fails closed for scheduling
        rather than issuing an unbounded merge.
        """
        partition, parts, size = self.checkpoint_layout(project_id)
        if parts <= 1:
            return
        if parts > 512 or size > 2 * 1024 * 1024 * 1024:
            raise MetricVisibilityError(
                "checkpoint partition consolidation cap exceeded"
            )
        self.execute(
            f"OPTIMIZE TABLE {FIELD_CHECKPOINT.name} PARTITION ID %(partition)s FINAL",
            {"partition": partition},
        )
        if self.checkpoint_layout(project_id)[1] > 1:
            raise MetricVisibilityError(
                "checkpoint partition still has overlapping parts"
            )

    def assert_visible(self, manifest):
        receipts = manifest.visibility_receipts
        if receipts.get("shard") != self.identity() or "batches" not in receipts:
            raise MetricVisibilityError(
                "manifest visibility is not proven on this shard"
            )


@contextmanager
def pinned_read(store, project_id, *, metric_id=None, pin=None, now=None):
    """Use ONE locked definition/head/remap and recheck revocation on completion.

    The shared lifecycle lock spans the bounded CH reads. Thus erasure cannot
    purge halfway through a multi-table request, even with a pre-existing queued
    snapshot pin. Completed snapshots are a separate store and are not read here.
    """
    with serialized_project(project_id):
        owned = pin is None
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
        store.assert_visible(manifest)
        try:
            yield manifest, pin.definition_version
            if not pin.is_usable(now):
                raise MetricStateConflict("expired generation")
        finally:
            if owned:
                MetricGenerationPin.objects.filter(id=pin.id).delete()
