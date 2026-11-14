"""Frozen server-side checkpoint ranges, independent of legacy batch hashes.

Only immutable manifest rows are sources. PostgreSQL stores private key bounds
and fixed-size receipts, never RowBinary payloads. The ordered-row Merkle format
is explicitly versioned; it is not SHA256(concatenated batch bytes).
"""

import json
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from time import monotonic
from uuid import uuid4

from clickhouse_driver.errors import ServerException
from django.db import connection, transaction

from tracer.models.derived_metric import (
    MetricCompactionRange,
    MetricErasureRequest,
    MetricLedgerConflict,
    MetricProjectRefresh,
    MetricRefreshAttempt,
    MetricStateConflict,
)
from tracer.services.derived_metrics.bounded_reads import (
    KeyRelation,
    _member,
    committed_branches,
    merge_bindings,
)
from tracer.services.derived_metrics.changes import keyset_after
from tracer.services.derived_metrics.deletion_protocol import (
    _intent,
    serialized_project,
)
from tracer.services.derived_metrics.publication import (
    MetricVisibilityError,
    committed_rows_select,
    physical_table,
)
from tracer.services.derived_metrics.storage import (
    BUCKET_TOTALS,
    FIELD_VALUES,
    RETIRED_PARTS,
    SOURCE_DEPENDENCIES,
    UNIT_STATE,
)

ALGORITHM = "rowbinary-sha256-merkle-v1"
LEAF_ROWS = 4096
RANGE_ROWS = 250_000
RANGE_BYTES = 128 * 1024 * 1024
RECEIPT_BYTES = 16 * 1024 * 1024
BUCKETS = 8


class RangePlanningOverflow(MetricVisibilityError):
    """Calibration failed before staging; sealed descriptors never change."""

    def __init__(self, count, byte_count):
        self.count = count
        self.byte_count = byte_count
        super().__init__("checkpoint range exceeds planning budget; abort and replan")


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str
    ).encode()


def fingerprint(value):
    return sha256(canonical(value)).hexdigest()


def schema_fingerprint(table):
    return fingerprint([table.name, table.schema, table.logical_key])


def keys(table):
    return tuple(c for c in table.logical_key if c not in {"org_id", "project_id"})


def bucket_expression(table):
    if table in (FIELD_VALUES, UNIT_STATE):
        return "cityHash64(unit_id) %% 8"
    columns = ("trace_id", "span_id") if table == SOURCE_DEPENDENCIES else keys(table)
    return f"cityHash64(tuple({', '.join(columns)})) %% 8"


def decode_bound(table, value):
    if value is None:
        return None
    schema = dict(table.schema)
    converted = tuple(
        datetime.fromisoformat(item) if schema[name] == "DateTime('UTC')" else item
        for name, item in zip(keys(table), value, strict=True)
    )
    return _member(table, keys(table), converted)


def bound_json(value):
    return json.loads(canonical(value))


@dataclass(frozen=True, repr=False)
class RangeRestriction:
    """Immutable hash/month plus a complete-key (lower, upper] interval."""

    table: object
    bucket: int
    month: int = 0
    lower: tuple | None = None
    upper: tuple | None = None

    def relation(self):
        table = self.table
        if type(self.bucket) is not int or not 0 <= self.bucket < BUCKETS:
            raise ValueError("invalid checkpoint range bucket")
        if type(self.month) is not int or self.month < 0:
            raise ValueError("invalid checkpoint range month")
        clauses, params = (
            [bucket_expression(table) + " = %(range_bucket)s"],
            {"range_bucket": self.bucket},
        )
        if table in (BUCKET_TOTALS, RETIRED_PARTS):
            clauses.append("toYYYYMM(bucket_start) = %(range_month)s")
            params["range_month"] = self.month
        elif self.month:
            raise ValueError("unexpected checkpoint range month")
        for side, value in (("lower", self.lower), ("upper", self.upper)):
            if value is not None:
                _member(table, keys(table), value)
                sql, bound = keyset_after(keys(table), value, "range_" + side)
                clauses.append(f"({sql})" if side == "lower" else f"NOT ({sql})")
                params = merge_bindings(params, bound)
        # KeyRelation's positive membership must not read unqualified live rows.
        # This SELECT has no FROM: each outer immutable key is tested directly.
        return ImmutableRangePredicate(keys(table), " AND ".join(clauses), params)


@dataclass(frozen=True)
class ImmutableRangePredicate(KeyRelation):
    """Internal KeyRelation backend; no caller-provided raw predicate API."""

    def predicate(self, table):
        if self.columns != keys(table):
            raise ValueError("checkpoint range logical key mismatch")
        return self.sql


def restriction(row, table):
    return RangeRestriction(
        table,
        row.bucket,
        row.month,
        decode_bound(table, row.lower_key),
        decode_bound(table, row.upper_key),
    )


def check_source(writer, attempt, *, state):
    """Called under project serialization at every durable stage boundary."""
    current = MetricRefreshAttempt._locked_open(
        attempt.id, state=state, **writer.lease.check()
    )
    current.refresh_from_db()
    parent = current.parent_manifest
    if (
        current.expires_at <= writer.lease.clock()
        or parent is None
        or parent.revoked_at is not None
        or str(parent.id) != current.source_ceilings.get("manifest")
        or parent.manifest_sha256 != current.parent_manifest_sha256
        or MetricProjectRefresh.for_project(attempt.project_id).head_manifest_id
        != parent.id
    ):
        raise MetricStateConflict("compaction source is no longer usable")
    return current


def source_select(writer, attempt, table, region):
    sql, params = committed_rows_select(
        table,
        attempt.parent_manifest,
        org_id=writer.org_id,
        project_id=attempt.project_id,
        key_relation=region.relation(),
    )
    obsolete = attempt.source_ceilings.get("range_obsolete", [])
    if obsolete and table in (FIELD_VALUES, BUCKET_TOTALS, RETIRED_PARTS):
        prefix = "field" if table == FIELD_VALUES else "metric"
        from uuid import UUID

        params["range_obsolete"] = tuple(
            (UUID(str(metric)), version) for metric, version in obsolete
        )
        sql = f"SELECT * FROM ({sql}) WHERE ({prefix}_id, {prefix}_version) NOT IN %(range_obsolete)s"
    return sql, params


def stamped_select(writer, attempt, row, table):
    sql, params = source_select(writer, attempt, table, restriction(row, table))
    metadata = {
        "generation": "toUInt64(%(range_generation)s)",
        "attempt_id": "toUUID(%(range_attempt)s)",
        "fencing_epoch": "toUInt32(%(range_epoch)s)",
        "row_sequence": "toUInt32(%(range_first)s + range_row - 1)",
        table.version_column: "toUInt64(%(range_epoch)s) * 4294967296 + %(range_first)s + range_row - 1",
    }
    # Preserve workspace/org and all physical types, including aggregate states.
    columns = ", ".join(
        "CAST("
        + metadata.get(name, name)
        + ", '"
        + kind.replace("'", "\\'")
        + "') AS "
        + name
        for name, kind in table.schema
    )
    return (
        f"SELECT {columns} FROM (SELECT *, row_number() OVER (ORDER BY {', '.join(table.logical_key)}) AS range_row FROM ({sql}))",
        {
            **params,
            "range_generation": attempt.generation,
            "range_attempt": str(attempt.id),
            "range_epoch": attempt.fencing_epoch,
            "range_first": row.sequence_first,
        },
    )


def descriptor(row):
    return {
        "table": row.table_kind,
        "bucket": row.bucket,
        "month": row.month,
        "ordinal": row.ordinal,
        "lower": row.lower_key,
        "upper": row.upper_key,
        "source": row.source_sha256,
        "schema": row.schema_sha256,
        "algorithm": row.algorithm,
        "attempt": str(row.attempt_id),
        "generation": row.attempt.generation,
        "epoch": row.attempt.fencing_epoch,
        "first": row.sequence_first,
        "last": row.sequence_last,
        "count": row.row_count,
        "shard": row.shard_identity,
    }


def digest_select(store, table, sql, params, row):
    """At most 4096 fixed-size row hashes per leaf, never groupArray(payload)."""
    root = sha256(b"metric-range-root-v1\0" + canonical(descriptor(row)) + b"\0")
    if row.row_count == 0:
        # Exhaustion ranges still prove actual emptiness on every source and
        # destination check. No row exists to encode/order/hash: COUNT preserves
        # the exact v1 empty root while avoiding payload/window work on the tail.
        count = store.execute(
            f"SELECT count() FROM ({sql})",
            params,
            query_id="metric-range-digest-" + uuid4().hex,
        )[0][0]
        if count:
            raise MetricVisibilityError("empty checkpoint range gained rows")
        return {"count": 0, "bytes": 0, "sha256": root.hexdigest()}
    columns = ", ".join(table.columns)
    key = ", ".join(table.logical_key)
    encoded = (
        f"SELECT formatRow('RowBinary', {columns}) AS range_bytes, "
        f"row_number() OVER (ORDER BY {key}) AS range_position FROM ({sql})"
    )
    leaves = store.execute(
        "SELECT intDiv(range_position - 1, 4096) AS leaf, count(), sum(length(range_bytes)), "
        "min(range_position), max(range_position), "
        "hex(SHA256(concat('metric-range-leaf-v1', arrayStringConcat(arrayMap(x -> x.2, "
        "arraySort(x -> x.1, groupArray(tuple(range_position, SHA256(range_bytes))))))))) "
        f"FROM ({encoded}) GROUP BY leaf ORDER BY leaf",
        params,
        query_id="metric-range-digest-" + uuid4().hex,
    )
    count = size = 0
    for ordinal, leaf in enumerate(leaves):
        number, n, byte_count, first, last, digest = leaf
        if (
            number != ordinal
            or not 1 <= n <= LEAF_ROWS
            or first != count + 1
            or last != count + n
        ):
            raise MetricVisibilityError("invalid checkpoint digest leaf coverage")
        if ordinal < len(leaves) - 1 and n != LEAF_ROWS:
            raise MetricVisibilityError("incomplete checkpoint digest leaf")
        root.update(canonical([number, n, byte_count, digest.lower()]) + b"\n")
        count += n
        size += byte_count
    if count > RANGE_ROWS or size > RANGE_BYTES:
        raise RangePlanningOverflow(count, size)
    return {"count": count, "bytes": size, "sha256": root.hexdigest()}


def destination_select(writer, attempt, row, table):
    physical = physical_table(table, checkpoint=True)
    region = restriction(row, table).relation()
    # Duplicates are not silently normalized: an extra physical row must fail
    # independent verification until a synchronous cleanup/replay is proven safe.
    return (
        f"SELECT {', '.join(table.columns)} FROM {physical.name} WHERE "
        "org_id = toUUID(%(range_org)s) AND project_id = toUUID(%(range_project)s) "
        "AND attempt_id = toUUID(%(range_attempt)s) AND generation = %(range_generation)s "
        f"AND ({region.predicate(table)})",
        {
            **region.params,
            "range_org": str(writer.org_id),
            "range_project": str(attempt.project_id),
            "range_attempt": str(attempt.id),
            "range_generation": attempt.generation,
        },
    )


def enumerate_regions(writer, attempt, table, *, target_rows=RANGE_ROWS):
    if type(target_rows) is not int or not 1 <= target_rows <= RANGE_ROWS:
        raise ValueError("invalid checkpoint range size")
    months = [0]
    if table in (BUCKET_TOTALS, RETIRED_PARTS):
        branches, params = committed_branches(
            table,
            attempt.parent_manifest,
            org_id=writer.org_id,
            project_id=attempt.project_id,
            columns=("bucket_start",),
        )
        months = (
            [
                r[0]
                for r in writer.store.execute(
                    "SELECT DISTINCT toYYYYMM(bucket_start) FROM ("
                    + " UNION ALL ".join(branches)
                    + ") ORDER BY 1",
                    params,
                )
            ]
            if branches
            else []
        )
        months = months or [0]
    for month in months:
        for bucket in range(BUCKETS):
            lower, ordinal = None, 0
            while True:
                region = RangeRestriction(table, bucket, month, lower)
                branches, params = committed_branches(
                    table,
                    attempt.parent_manifest,
                    org_id=writer.org_id,
                    project_id=attempt.project_id,
                    columns=keys(table),
                    key_relation=region.relation(),
                )
                names = ", ".join(keys(table))
                # Bound each branch by DISTINCT logical keys, not physical
                # revisions. Duplicate checkpoint rows could otherwise consume
                # its whole prefix while a later delta key stretches the child
                # past many unseen checkpoint keys, exceeding target_rows.
                prefixes = " UNION ALL ".join(
                    f"SELECT * FROM (SELECT DISTINCT {names} FROM ({branch}) ORDER BY {names} LIMIT %(range_limit)s)"
                    for branch in branches
                )
                rows = (
                    writer.store.execute(
                        f"SELECT tuple({names}) FROM (SELECT DISTINCT {names} FROM ({prefixes}) "
                        f"ORDER BY {names} LIMIT %(range_limit)s) ORDER BY tuple({names}) DESC LIMIT 1",
                        {**params, "range_limit": target_rows},
                    )
                    if branches
                    else []
                )
                upper = tuple(rows[0][0]) if rows else None
                if upper == lower and upper is not None:
                    raise MetricLedgerConflict(
                        "checkpoint range cursor did not advance"
                    )
                yield ordinal, RangeRestriction(table, bucket, month, lower, upper)
                if upper is None:
                    break  # Explicit terminal empty range covers the remaining domain.
                lower, ordinal = upper, ordinal + 1


def prepare_ranges(writer, attempt, *, target_rows=RANGE_ROWS):
    """Commit each immutable plan/expectation before any destination INSERT."""
    from tracer.services.derived_metrics.refresh import TABLE_KINDS

    if connection.in_atomic_block:
        raise MetricStateConflict("range preparation requires durable boundaries")
    writer.store.assert_visible(attempt.parent_manifest)
    shard = writer.store.identity()
    for kind, table in TABLE_KINDS.items():
        for ordinal, region in enumerate_regions(
            writer, attempt, table, target_rows=target_rows
        ):
            writer.lease.keepalive()
            with (
                transaction.atomic(durable=True),
                serialized_project(attempt.project_id),
            ):
                check_source(writer, attempt, state="prepared")
                row = MetricCompactionRange.plan(
                    attempt.id,
                    table_kind=kind,
                    bucket=region.bucket,
                    month=region.month,
                    ordinal=ordinal,
                    lower_key=bound_json(region.lower),
                    upper_key=bound_json(region.upper),
                    source_sha256=fingerprint(
                        [
                            str(attempt.parent_manifest_id),
                            attempt.parent_manifest_sha256,
                            attempt.parent_manifest.allowed_attempts(),
                        ]
                    ),
                    schema_sha256=schema_fingerprint(table),
                    shard_identity=shard,
                    algorithm=ALGORITHM,
                    **writer.lease.check(),
                )
            if row.state != "planned":
                continue
            source, params = source_select(writer, attempt, table, region)
            count = writer.store.execute(f"SELECT count() FROM ({source})", params)[0][
                0
            ]
            if count > target_rows:
                raise MetricVisibilityError(
                    "checkpoint range exceeds nominated key budget"
                )
            with (
                transaction.atomic(durable=True),
                serialized_project(attempt.project_id),
            ):
                check_source(writer, attempt, state="prepared")
                first, last = (
                    MetricProjectRefresh.allocate_row_sequences(
                        attempt.project_id, count=count, **writer.lease.fence()
                    )
                    if count
                    else (0, 0)
                )
                row.row_count, row.sequence_first, row.sequence_last = (
                    count,
                    first,
                    last,
                )
                sql, params = stamped_select(writer, attempt, row, table)
                row.plan_sha256 = fingerprint([descriptor(row), sql, params])
            expected = digest_select(writer.store, table, sql, params, row)
            if expected["count"] != count:
                raise MetricVisibilityError(
                    "checkpoint source changed during preparation"
                )
            with (
                transaction.atomic(durable=True),
                serialized_project(attempt.project_id),
            ):
                check_source(writer, attempt, state="prepared")
                MetricCompactionRange.freeze(
                    row.id,
                    count=count,
                    first=first,
                    last=last,
                    digest=expected["sha256"],
                    byte_count=expected["bytes"],
                    plan_sha256=row.plan_sha256,
                    **writer.lease.check(),
                )
    return registry(attempt, required_state="frozen")


def registry(attempt, *, required_state):
    """Stream descriptors; check exhaustive partitions, contiguous child bounds."""
    from tracer.services.derived_metrics.refresh import TABLE_KINDS

    state = sha256(b"metric-range-registry-v1\0")
    count, receipts_bytes = 0, 0
    previous, upper, ordinal = None, None, 0
    groups = set()
    for row in (
        attempt.compaction_ranges.select_related("attempt")
        .order_by("table_kind", "month", "bucket", "ordinal")
        .iterator(chunk_size=128)
    ):
        group = (row.table_kind, row.month, row.bucket)
        if group != previous:
            if previous is not None and upper is not None:
                raise MetricLedgerConflict(
                    "checkpoint range registry lacks terminal coverage"
                )
            previous, upper, ordinal = group, None, 0
            groups.add(group)
        elif upper is None:
            raise MetricLedgerConflict("checkpoint range follows terminal coverage")
        if row.ordinal != ordinal or row.lower_key != upper:
            raise MetricLedgerConflict("checkpoint range registry has a gap or overlap")
        valid_states = (
            {"frozen", "inserting", "verified"}
            if required_state == "frozen"
            else {"verified"}
        )
        if row.state not in valid_states or row.algorithm != ALGORITHM:
            raise MetricLedgerConflict("checkpoint range registry is incomplete")
        table = TABLE_KINDS[row.table_kind]
        if row.schema_sha256 != schema_fingerprint(table):
            raise MetricLedgerConflict("checkpoint range schema changed")
        data = {
            **descriptor(row),
            "digest": row.digest,
            "bytes": row.expected_bytes,
            "plan": row.plan_sha256,
        }
        encoded = canonical(data)
        receipts_bytes += len(encoded)
        if receipts_bytes > RECEIPT_BYTES:
            raise MetricLedgerConflict("checkpoint receipt metadata budget exceeded")
        state.update(encoded + b"\n")
        upper, ordinal, count = row.upper_key, ordinal + 1, count + 1
    if not count or upper is not None or {g[0] for g in groups} != set(TABLE_KINDS):
        raise MetricLedgerConflict("checkpoint range registry lacks all-store coverage")
    for kind, month, _ in groups:
        if {(kind, month, bucket) for bucket in range(BUCKETS)} - groups:
            raise MetricLedgerConflict(
                "checkpoint range registry lacks bucket coverage"
            )
    return {"algorithm": ALGORITHM, "count": count, "sha256": state.hexdigest()}


def validate_registry(attempt, *, verified=False):
    if not attempt.source_ceilings.get("range_compaction"):
        if attempt.compaction_ranges.exists():
            raise MetricLedgerConflict("unexpected checkpoint range registry")
        return None
    controls = attempt.ledger_chunks.filter(
        part="cleanup_intent", payload__0__kind="publication"
    )
    if controls.count() != 1:
        raise MetricLedgerConflict("missing range publication registry")
    expected = controls.get().payload[0].get("ranges")
    actual = registry(attempt, required_state="verified" if verified else "frozen")
    if expected != actual:
        raise MetricLedgerConflict("sealed checkpoint range registry changed")
    return actual


def current_range_cutoff(writer, attempt):
    """Project lock, then organization row lock, as in final publication."""
    from django.apps import apps

    from tracer.services.derived_metrics.retention import physical_cutoff

    if not connection.in_atomic_block:
        raise MetricStateConflict("range policy validation requires publication lock")
    apps.get_model("accounts", "Organization")._base_manager.select_for_update().get(
        id=writer.org_id
    )
    # Batched like every physical expiry (E1): a row past the exact cutoff but
    # not yet physically expired is copied unchanged; reads mask it.
    cutoff = physical_cutoff(
        writer, now=writer.lease.clock(), ceilings=attempt.source_ceilings
    )
    floor = attempt.source_ceilings.get("retention_floor")
    if floor:
        floor = datetime.fromisoformat(str(floor))
        cutoff = max(cutoff, floor) if cutoff else floor
    return cutoff


def check_submission_policy(writer, attempt, row, oldest):
    """Revalidate current policy after the unlocked immutable source observation."""
    from tracer.services.derived_metrics.refresh import MetricRetentionChanged

    cutoff = current_range_cutoff(writer, attempt)
    if oldest is not None and cutoff is not None and oldest < cutoff:
        raise MetricRetentionChanged("retention policy changed; replan required")


def cleanup_range(writer, attempt, row, table):
    """Delete a terminated partial send before identical replay, local engine only.

    The mutation has its own durable query obligation. A lost response may leave
    a mutation which could delete a replay: it must reach synchronous QueryFinish
    before another INSERT. An absent/failed query is not a drain proof.
    """
    from tracer.services.derived_metrics.compaction import (
        checkpoint_gate,
        require_layout_drained,
    )
    from tracer.services.derived_metrics.storage import FIELD_CHECKPOINT

    destination = physical_table(table, checkpoint=True)
    engine = writer.store.execute(
        "SELECT engine FROM system.tables WHERE database = currentDatabase() AND name = %(table)s",
        {"table": destination.name},
    )
    if engine != [("ReplacingMergeTree",)]:
        MetricRefreshAttempt.abort(
            attempt.id, reason="range_cleanup_topology", **writer.lease.check()
        )
        raise MetricVisibilityError("checkpoint cleanup topology is not qualified")
    group = (
        writer.store.checkpoint_group(attempt.project_id)
        if destination == FIELD_CHECKPOINT
        else None
    )
    query_id = "metric-range-cleanup-" + uuid4().hex
    with transaction.atomic(durable=True), serialized_project(attempt.project_id):
        check_source(writer, attempt, state="staging")
        obligation = _intent(
            organization_id=writer.org_id,
            project_id=attempt.project_id,
            scope="history",
            store_payload={
                "protocol": "metric_submission",
                "operation": "range_cleanup",
                "attempt": str(attempt.id),
                "generation": attempt.generation,
                "compaction_range_id": row.id,
                "query_id": query_id,
                "table": destination.name,
                "shard": row.shard_identity,
                "checkpoint_group": group,
            },
        )
    region = restriction(row, table).relation()
    gate = (
        checkpoint_gate(group, row.shard_identity)
        if group is not None
        else nullcontext()
    )
    with gate:
        if group is not None:
            require_layout_drained(group, row.shard_identity)
        writer.store.execute(
            f"ALTER TABLE {destination.name} DELETE WHERE org_id = toUUID(%(cleanup_org)s) "
            "AND project_id = toUUID(%(cleanup_project)s) AND attempt_id = toUUID(%(cleanup_attempt)s) "
            f"AND generation = %(cleanup_generation)s AND ({region.predicate(table)}) SETTINGS mutations_sync=2",
            {
                **region.params,
                "cleanup_org": str(writer.org_id),
                "cleanup_project": str(attempt.project_id),
                "cleanup_attempt": str(attempt.id),
                "cleanup_generation": attempt.generation,
            },
            query_id=query_id,
        )
    MetricErasureRequest.objects.filter(id=obligation.id).update(
        status="done",
        completed_at=writer.lease.clock(),
        receipts={"synchronous_response": {"drained": 1}},
    )
    sql, params = destination_select(writer, attempt, row, table)
    if writer.store.execute(f"SELECT count() FROM ({sql})", params)[0][0]:
        MetricRefreshAttempt.abort(
            attempt.id, reason="range_cleanup_incomplete", **writer.lease.check()
        )
        raise MetricVisibilityError("checkpoint range cleanup is incomplete")


def stage_ranges(writer, attempt):
    """No blind retries. Unresolved sends exclude the namespace until drained."""
    from tracer.services.derived_metrics.compaction import (
        checkpoint_gate,
        require_layout_drained,
    )
    from tracer.services.derived_metrics.refresh import TABLE_KINDS
    from tracer.services.derived_metrics.storage import FIELD_CHECKPOINT

    validate_registry(attempt)
    for row in (
        attempt.compaction_ranges.select_related("attempt")
        .order_by("table_kind", "month", "bucket", "ordinal")
        .iterator(chunk_size=64)
    ):
        writer.lease.keepalive()
        deadline = getattr(writer, "_compaction_deadline", None)
        if deadline is not None and monotonic() >= deadline:
            raise MetricVisibilityError("checkpoint compaction deadline exceeded")
        table = TABLE_KINDS[row.table_kind]
        destination = physical_table(table, checkpoint=True)
        shard = writer.store.identity()
        with transaction.atomic(durable=True), serialized_project(attempt.project_id):
            check_source(writer, attempt, state="staging")
            if row.shard_identity != shard:
                raise MetricVisibilityError("checkpoint range shard mismatch")
        if row.state == "verified":
            continue
        pending = MetricErasureRequest.objects.filter(
            store_payload__protocol="metric_submission",
            store_payload__attempt=str(attempt.id),
            store_payload__compaction_range_id=row.id,
        ).exclude(status="done")
        for submission in pending.iterator(chunk_size=32):
            query_id = submission.store_payload["query_id"]
            finished = writer.store.execute(
                "SELECT count() FROM system.query_log WHERE query_id = %(query)s AND type = 'QueryFinish'",
                {"query": query_id},
            )[0][0]
            running = writer.store.execute(
                "SELECT count() FROM system.processes WHERE query_id = %(query)s",
                {"query": query_id},
            )[0][0]
            if not finished or running:
                # Absence from processes is not proof a delayed socket cannot
                # submit. Retain the outbox and exclude this entire namespace.
                MetricRefreshAttempt.abort(
                    attempt.id,
                    reason="ambiguous_range_submission",
                    **writer.lease.check(),
                )
                raise MetricVisibilityError("checkpoint range submission is unresolved")
            MetricErasureRequest.objects.filter(id=submission.id).update(
                status="done",
                completed_at=writer.lease.clock(),
                receipts={"terminal_query": {"drained": 1}},
            )
        actual_sql, actual_params = destination_select(writer, attempt, row, table)
        if row.state == "inserting":
            actual = digest_select(writer.store, table, actual_sql, actual_params, row)
            if actual == {
                "count": row.row_count,
                "bytes": row.expected_bytes,
                "sha256": row.digest,
            }:
                MetricCompactionRange.verify(row.id, actual, **writer.lease.check())
                continue
            # All original sends (including an earlier cleanup mutation) have
            # terminal receipts above. Keep the sealed descriptor and sequence
            # allocation; cleanup never touches a sibling or pinned predecessor.
            cleanup_range(writer, attempt, row, table)
        sql, params = stamped_select(writer, attempt, row, table)
        if fingerprint([descriptor(row), sql, params]) != row.plan_sha256:
            raise MetricLedgerConflict("checkpoint range query plan changed")
        expected = digest_select(writer.store, table, sql, params, row)
        if expected != {
            "count": row.row_count,
            "bytes": row.expected_bytes,
            "sha256": row.digest,
        }:
            MetricRefreshAttempt.abort(
                attempt.id, reason="range_source_changed", **writer.lease.check()
            )
            raise MetricVisibilityError("checkpoint range source expectation changed")
        group = (
            writer.store.checkpoint_group(attempt.project_id)
            if destination == FIELD_CHECKPOINT
            else None
        )
        oldest = None
        if row.row_count and "unit_start" in table.columns:
            count, oldest = writer.store.execute(
                f"SELECT count(), min(unit_start) FROM ({sql})", params
            )[0]
            if count != row.row_count:
                raise MetricVisibilityError(
                    "checkpoint source changed before submission"
                )
        query_id = "metric-range-insert-" + uuid4().hex
        with transaction.atomic(durable=True), serialized_project(attempt.project_id):
            check_source(writer, attempt, state="staging")
            check_submission_policy(writer, attempt, row, oldest)
            MetricCompactionRange.begin_insert(row.id, **writer.lease.check())
            submission = _intent(
                organization_id=writer.org_id,
                project_id=attempt.project_id,
                scope="history",
                store_payload={
                    "protocol": "metric_submission",
                    "attempt": str(attempt.id),
                    "generation": attempt.generation,
                    "compaction_range_id": row.id,
                    "query_id": query_id,
                    "table": destination.name,
                    "shard": row.shard_identity,
                    "checkpoint_group": group,
                },
            )

        gate = (
            checkpoint_gate(group, row.shard_identity)
            if group is not None
            else nullcontext()
        )
        with gate:
            if group is not None:
                require_layout_drained(group, row.shard_identity)
            if destination == FIELD_CHECKPOINT:
                writer.store.assert_checkpoint_schema()
            if row.row_count:
                writer.store.execute(
                    f"INSERT INTO {destination.name} ({', '.join(table.columns)}) {sql}",
                    params,
                    query_id=query_id,
                )
        MetricErasureRequest.objects.filter(id=submission.id).update(
            status="done",
            completed_at=writer.lease.clock(),
            receipts={"synchronous_response": {"drained": 1}},
        )
        actual = digest_select(writer.store, table, actual_sql, actual_params, row)
        MetricCompactionRange.verify(row.id, actual, **writer.lease.check())


def visibility_receipts(attempt):
    root = validate_registry(attempt, verified=True)
    return [] if root is None else [{"attempt": str(attempt.id), "registry": root}]


def range_retention_evidence(writer, attempt):
    """Observe verified destination minima outside lifecycle serialization."""
    from tracer.services.derived_metrics.refresh import TABLE_KINDS

    validate_registry(attempt, verified=True)
    oldest = None
    for row in (
        attempt.compaction_ranges.select_related("attempt")
        .order_by("id")
        .iterator(chunk_size=128)
    ):
        table = TABLE_KINDS[row.table_kind]
        if "unit_start" not in table.columns or row.row_count == 0:
            continue
        sql, params = destination_select(writer, attempt, row, table)
        count, start = writer.store.execute(
            f"SELECT count(), min(unit_start) FROM ({sql})", params
        )[0]
        if count != row.row_count:
            raise MetricVisibilityError(
                "verified checkpoint range changed before publication"
            )
        oldest = start if oldest is None else min(oldest, start)
    return oldest


def check_range_policy(writer, attempt, oldest):
    """The final source/registry/policy CAS contains no native observations."""
    from tracer.services.derived_metrics.refresh import MetricRetentionChanged

    if not connection.in_atomic_block:
        raise MetricStateConflict("range policy validation requires publication lock")
    check_source(
        writer, attempt, state=MetricRefreshAttempt.objects.get(id=attempt.id).state
    )
    validate_registry(attempt, verified=True)
    cutoff = current_range_cutoff(writer, attempt)
    writer.lease.check()
    if oldest is not None and cutoff is not None and oldest < cutoff:
        raise MetricRetentionChanged("retention policy changed; replan required")


def compact_ranges(writer):
    """Explicit qualification entry point; ordinary callers retain batch replay."""
    deadline = monotonic() + 600
    with writer.store.compaction_budget(deadline):
        target_rows = RANGE_ROWS
        while True:
            try:
                return _compact_ranges(
                    writer, deadline=deadline, target_rows=target_rows
                )
            except ReplanRanges as failure:
                target_rows = failure.target_rows


class ReplanRanges(Exception):
    def __init__(self, target_rows):
        self.target_rows = target_rows


def _compact_ranges(writer, *, deadline, target_rows):
    from tracer.services.derived_metrics.backfill import obsolete_versions

    if connection.in_atomic_block:
        raise MetricStateConflict("range compaction requires durable boundaries")
    writer.store.assert_checkpoint_schema()
    with transaction.atomic(durable=True), serialized_project(writer.lease.project_id):
        head = MetricProjectRefresh.for_project(writer.lease.project_id).head_manifest
        if head is None:
            return None
        if head.revoked_at is not None:
            raise MetricStateConflict("expired base requires source rebuild")
        pending_layout = (
            MetricErasureRequest.objects.filter(
                deletion_key=f"checkpoint-layout:{head.checkpoint_attempt_id}",
            )
            .exclude(status="done")
            .exists()
        )
        if pending_layout:
            attempt = head.checkpoint_attempt
        else:
            # Never rediscover a sealed range registry after a crash. A previous
            # epoch cannot adopt this namespace; abort_stale handles its handoff.
            candidates = MetricRefreshAttempt.objects.filter(
                project_id=writer.lease.project_id,
                parent_manifest=head,
                fencing_epoch=writer.lease.grant.fencing_epoch,
                lease_token=writer.lease.grant.lease_token,
                state__in=MetricRefreshAttempt.OPEN_STATES,
                source_ceilings__range_compaction=True,
                expires_at__gt=writer.lease.clock(),
            )
            if candidates.count() > 1:
                raise MetricStateConflict("multiple open range compactions")
            attempt = candidates.first()
            if attempt is None:
                handoff = {}
                pending = head.published_by.source_ceilings.get(
                    "recovery_pending", False
                )
                if pending:
                    for chunk in head.published_by.ledger_chunks.exclude(
                        part="cleanup_intent"
                    ):
                        handoff.setdefault(chunk.part, []).extend(chunk.payload)
                attempt = writer.begin(
                    purpose="compaction",
                    ledger=handoff,
                    ceilings={
                        "manifest": str(head.id),
                        "range_compaction": True,
                        "range_target_rows": target_rows,
                        "range_obsolete": bound_json(
                            obsolete_versions(
                                writer.lease.project_id, now=writer.lease.clock()
                            )
                        ),
                        "recovery_pending": pending,
                    },
                )
    writer.store.assert_visible(head)
    if pending_layout:
        writer._publish_compaction(attempt, deadline=deadline)
        transaction.on_commit(writer.collect_garbage)
        if head.published_by_id == head.checkpoint_attempt_id:
            return head
        return _compact_ranges(writer, deadline=deadline, target_rows=target_rows)
    if attempt.state == "prepared" and attempt.ledger_sealed_at is None:
        target_rows = attempt.source_ceilings.get("range_target_rows", RANGE_ROWS)
        try:
            root = prepare_ranges(writer, attempt, target_rows=target_rows)
        except (RangePlanningOverflow, ServerException) as failure:
            if isinstance(failure, ServerException) and failure.code not in {159, 241}:
                raise
            # Preparation has no submissions. Preserve its frozen prefix for
            # audit/GC, abort durably, and allocate a fresh namespace. A retry
            # must never edit a previously frozen bound or sequence interval.
            with (
                transaction.atomic(durable=True),
                serialized_project(attempt.project_id),
            ):
                check_source(writer, attempt, state="prepared")
                MetricRefreshAttempt.abort(
                    attempt.id, reason="range_planning_overflow", **writer.lease.check()
                )
            if target_rows == 1 or monotonic() >= deadline:
                raise MetricVisibilityError(
                    "checkpoint range cannot fit planning budget"
                ) from failure
            smaller = max(1, target_rows // 2)
            if isinstance(failure, RangePlanningOverflow) and failure.byte_count:
                # Actual typed output bytes calibrate the next attempt. Leave
                # 50% headroom for skew; a further overflow halves again.
                smaller = max(
                    1,
                    min(
                        smaller, failure.count * RANGE_BYTES // (2 * failure.byte_count)
                    ),
                )
            raise ReplanRanges(smaller) from failure
        with transaction.atomic(durable=True), serialized_project(attempt.project_id):
            check_source(writer, attempt, state="prepared")
            writer.seal(
                attempt,
                publication={
                    "ranges": root,
                    "retire_versions": attempt.source_ceilings["range_obsolete"],
                },
            )
    try:
        return writer._publish_compaction(attempt, deadline=deadline)
    finally:
        from tracer.models.derived_metric import MetricReadManifest

        if MetricReadManifest.objects.filter(published_by=attempt).exists():
            transaction.on_commit(writer.collect_garbage)
