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
from uuid import UUID

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
MAX_BATCH_BYTES = 16 * 1024 * 1024
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

    def __post_init__(self):
        if len(self.rows) > MAX_BATCH_ROWS:
            raise ValueError("metric batch row cap exceeded")
        try:
            size = sum(len(bytes.fromhex(row)) for row in self.rows)
        except (ValueError, TypeError):
            raise ValueError("invalid frozen metric payload") from None
        if size > MAX_BATCH_BYTES:
            raise ValueError("metric batch byte cap exceeded")

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
    table, manifest, *, org_id, project_id, overlay=None, key_after=None
):
    """Collapse an exact allowlist BEFORE any mutable/time/deletion predicate.

    An optional writer-owned overlay is an already frozen attempt, never a raw
    latest-row read. All columns include tombstones, including for compaction.
    Identifiers come exclusively from storage.py. Callers may wrap this SQL with
    post-collapse filters, or immutable-key restrictions only.
    """
    table = TABLES[table.name if hasattr(table, "name") else table]
    params = {
        "pub_org": str(UUID(str(org_id))),
        "pub_project": str(UUID(str(project_id))),
    }
    columns = ", ".join(table.columns)
    scope = "org_id = toUUID(%(pub_org)s) AND project_id = toUUID(%(pub_project)s)"
    if key_after:
        from tracer.services.derived_metrics.changes import keyset_after

        if set(key_after) - set(table.logical_key):
            raise ValueError(
                "only immutable logical keys can be pushed before collapse"
            )
        keys = tuple(c for c in table.logical_key if c in key_after)
        predicate, key_params = keyset_after(
            keys, tuple(key_after[c] for c in keys), "pub_after"
        )
        scope += f" AND ({predicate})"
        params.update(key_params)
    branches = []
    if manifest is not None:
        if str(manifest.project_id) != str(project_id):
            raise MetricStateConflict("manifest project mismatch")
        pairs = manifest.allowed_attempts()
        checkpoint = manifest.checkpoint_attempt_id
        for is_checkpoint in (True, False):
            selected = [
                (g, str(a)) for g, a in pairs if (a == checkpoint) == is_checkpoint
            ]
            if not selected:
                continue
            name = "pub_base" if is_checkpoint else "pub_deltas"
            params[name] = tuple((g, UUID(a)) for g, a in selected)
            physical = physical_table(table, checkpoint=is_checkpoint)
            branches.append(
                f"SELECT {columns} FROM {physical.name} PREWHERE {scope} "
                f"WHERE (generation, attempt_id) IN %({name})s"
            )
    if overlay is not None:
        params["pub_overlay"] = UUID(str(overlay.id))
        params["pub_overlay_generation"] = overlay.generation
        physical = physical_table(table, checkpoint=overlay.purpose == "compaction")
        branches.append(
            f"SELECT {columns} FROM {physical.name} PREWHERE {scope} "
            "WHERE attempt_id = %(pub_overlay)s AND generation = %(pub_overlay_generation)s"
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

    def freeze_select(self, table, sql, params, *, key_columns=()):
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
        keys = (", " + ", ".join(key_columns)) if key_columns else ""
        rows = self.execute(
            f"SELECT hex(formatRow('RowBinary', {columns})){keys} FROM ({sql}) "
            f"ORDER BY {table.version_column} LIMIT %(pub_limit)s",
            {**params, "pub_limit": MAX_BATCH_ROWS},
        )
        return FrozenRows(
            tuple(row[0] for row in rows),
            tuple(rows[-1][1:]) if rows and key_columns else None,
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
        actual = FrozenRows(tuple(row[0] for row in rows))
        if (len(actual.rows), actual.digest) != (batch.row_count, batch.payload_sha256):
            raise MetricVisibilityError("frozen batch not visible on selected shard")
        return {"count": len(actual.rows), "sha256": actual.digest}

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
