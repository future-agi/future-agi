"""Bounded PREPARED span capture, shared by committed evidence and bucket totals.

No new persistent store: span rows use FIELD_VALUES' existing level/unit identity,
immutable attempts, receipts, checkpoints, pins and all-revision privacy cleanup.
The canonical unit id is the compiler's JSON [trace_id, span_id], never span_id
alone. Temporary source rows are connection-owned and cannot survive a failed
preparation. Only frozen publication batches are recoverable.
"""

from contextlib import contextmanager
from datetime import datetime, timedelta
from time import monotonic
from uuid import uuid4

from tracer.services.derived_metrics.bounded_reads import (
    SOURCE_SCHEMA,
    merge_bindings,
    windowed_rows_select,
)
from tracer.services.derived_metrics.publication import (
    MetricVisibilityError,
    committed_rows_select,
)
from tracer.services.derived_metrics.storage import FIELD_VALUES, SOURCE_DEPENDENCIES
from tracer.services.derived_metrics.unit_reads import span_values_select

# Operational admission bounds, not a measured production capacity envelope.
# Overflow aborts preparation; it must never truncate and publish a partial bucket.
SPAN_CAPTURE_ROWS = 50_000
SPAN_CAPTURE_BYTES = 64 * 1024 * 1024


def require_retirement_evidence(writer, head, definitions):
    """Do not erase legacy bucket-only history while converting it to retirement.

    Readers can honestly return unavailable until a backfill supplies fields;
    privacy maintenance must also fail closed rather than silently dropping the
    previously published totals. Retired anonymous counts are not live evidence.
    """
    if head is None:
        return
    from tracer.services.derived_metrics.refresh import MetricAdmissionUnproven
    from tracer.services.derived_metrics.storage import BUCKET_TOTALS, RETIRED_PARTS

    for definition in definitions:
        if definition.level != "span":
            continue
        counts, params = [], {}
        for table in (FIELD_VALUES, BUCKET_TOTALS, RETIRED_PARTS):
            prefix = "field" if table == FIELD_VALUES else "metric"
            sql, bound = committed_rows_select(
                table,
                head,
                org_id=writer.org_id,
                project_id=writer.lease.project_id,
                key_in={
                    f"{prefix}_id": (definition.metric_id,),
                    f"{prefix}_version": (definition.version,),
                },
                param_prefix="retirement_proof_" + table.name,
            )
            params = merge_bindings(params, bound)
            counts.append(
                f"(SELECT count() FROM ({sql}) WHERE is_deleted = 0 AND level = 'span' AND status != 'open')"
                if table == FIELD_VALUES
                else f"(SELECT sum(unit_count) FROM ({sql}) WHERE is_deleted = 0)"
            )
        live, total, retired = writer.store.execute(
            "SELECT " + ", ".join(counts), params
        )[0]
        # Subject erasure deliberately drops aggregate history while keeping
        # unrelated live fields. Only a deficit of evidence blocks retirement;
        # surplus live evidence must remain refreshable after that erasure.
        if total > live + retired:
            raise MetricAdmissionUnproven(
                "committed span evidence requires backfill before retirement"
            )


@contextmanager
def captured_span_fields(
    writer, attempt, definition, scope, *, head, as_of, cutoff, preserve_pruned=False
):
    store = writer.store
    dirty = sorted(
        {
            datetime.fromisoformat(str(entry["bucket_start"]))
            for chunk in attempt.ledger_chunks.filter(
                part__in=["dirty_before", "dirty_after"]
            )
            for entry in chunk.payload
            if "bucket_start" in entry
        }
    )
    fixed = {
        "field_id": (definition.metric_id,),
        "field_version": (definition.version,),
        "level": ("span",),
    }
    previous, previous_params = windowed_rows_select(
        FIELD_VALUES,
        head,
        org_id=writer.org_id,
        project_id=writer.lease.project_id,
        predicate="bucket_start IN %(span_dirty_buckets)s" if dirty else "0",
        params={"span_dirty_buckets": tuple(dirty)} if dirty else {},
        key_in=fixed,
        param_prefix="span_previous_fields",
    )
    source_scope = {
        "capture_project": str(scope.project),
        "capture_org": str(scope.organization),
        "capture_as_of": as_of,
    }
    physical_keys = (
        "SELECT toJSONString(tuple(trace_id, id)) FROM spans "
        "PREWHERE project_id = toUUID(%(capture_project)s) "
        "AND (trace_id, id) IN (SELECT JSONExtractString(unit_id, 1), JSONExtractString(unit_id, 2) "
        f"FROM ({previous}) WHERE is_deleted = 0) "
        "WHERE (org_id = toUUID(%(capture_org)s) OR isNull(org_id)) "
        "AND created_at <= %(capture_as_of)s AND updated_at <= %(capture_as_of)s"
    )
    if preserve_pruned and dirty:
        # Retirement is a writer publication too: capture terminal corrections
        # at their new bucket, then retire exactly those captured bytes. Missing
        # physical source is not proof that committed evidence was deleted.
        current_starts = store.execute(
            "SELECT DISTINCT toStartOfFifteenMinutes(start_time) FROM ("
            "SELECT trace_id, id, start_time FROM spans "
            "PREWHERE project_id = toUUID(%(capture_project)s) "
            "AND (trace_id, id) IN (SELECT JSONExtractString(unit_id, 1), JSONExtractString(unit_id, 2) "
            f"FROM ({previous}) WHERE is_deleted = 0) "
            "WHERE (org_id = toUUID(%(capture_org)s) OR isNull(org_id)) "
            "AND created_at <= %(capture_as_of)s AND updated_at <= %(capture_as_of)s "
            "ORDER BY _version DESC LIMIT 1 BY trace_id, id)",
            merge_bindings(previous_params, source_scope),
        )
        dirty = sorted(set(dirty) | {row[0] for row in current_starts})
        previous_params["span_dirty_buckets"] = tuple(dirty)
    store.execute("SELECT 1")
    socket = store.client.connection.socket
    name = "metric_span_capture_" + uuid4().hex
    columns = ", ".join(column for column, _ in SOURCE_SCHEMA)
    schema = ", ".join(f"{column} {kind}" for column, kind in SOURCE_SCHEMA)
    casts = ", ".join(
        "CAST(" + column + ", '" + kind.replace("'", "\\'") + "') AS " + column
        for column, kind in SOURCE_SCHEMA
    )

    def check_session():
        if socket is None or store.client.connection.socket is not socket:
            raise MetricVisibilityError("span capture session is no longer valid")

    try:
        check_session()
        store.execute(f"CREATE TEMPORARY TABLE {name} ({schema}) ENGINE=Memory")
        count = size = 0
        for at in dirty:
            writer.lease.keepalive()
            check_session()
            fields, params = span_values_select(
                definition, scope, start=at, end=at + timedelta(minutes=15), as_of=as_of
            )
            if preserve_pruned:
                previous_columns = ", ".join(
                    c for c, _ in SOURCE_SCHEMA if c != "source_rows"
                )
                fields = (
                    f"(SELECT {columns} FROM ({fields}) WHERE unit_id IN "
                    f"(SELECT unit_id FROM ({previous}) WHERE is_deleted = 0)) "
                    f"UNION ALL SELECT {previous_columns}, toUInt64(1) AS source_rows "
                    f"FROM ({previous}) WHERE is_deleted = 0 AND bucket_start = %(capture_bucket)s "
                    f"AND unit_id NOT IN ({physical_keys})"
                )
                params = merge_bindings(
                    params, previous_params, source_scope, {"capture_bucket": at}
                )
            if cutoff is not None:
                fields = (
                    f"SELECT * FROM ({fields}) WHERE unit_start >= %(span_capture_cutoff)s "
                    f"OR unit_id IN (SELECT unit_id FROM ({previous}) WHERE is_deleted = 0)"
                )
                params = merge_bindings(
                    params, previous_params, {"span_capture_cutoff": cutoff}
                )
            # The source compiler executes ONCE per whole dirty bucket. Bound
            # typed bytes before inserting into Memory, not after a lossy LIMIT.
            store.execute(
                f"INSERT INTO {name} ({columns}) SELECT {columns} FROM ("
                "SELECT *, count() OVER () AS capture_rows, "
                f"sum(length(formatRow('RowBinary', {columns}))) OVER () AS capture_bytes "
                f"FROM (SELECT {casts} FROM ({fields}))) "
                f"WHERE throwIf(capture_rows > {SPAN_CAPTURE_ROWS - count} "
                f"OR capture_bytes > {SPAN_CAPTURE_BYTES - size}, 'metric_span_capture_budget') = 0",
                params,
            )
            check_session()
            count, unique, size = store.execute(
                f"SELECT count(), uniqExact(tuple(level, unit_id)), "
                f"sum(length(formatRow('RowBinary', {columns}))) FROM {name}"
            )[0]
            if (
                count != unique
                or count > SPAN_CAPTURE_ROWS
                or size > SPAN_CAPTURE_BYTES
            ):
                raise MetricVisibilityError(
                    "span capture is duplicate or exceeds its budget"
                )
        if head and head.published_by.source_ceilings.get("retention_floor"):
            from tracer.services.derived_metrics.bounded_reads import KeyRelation
            from tracer.services.derived_metrics.refresh import MetricAdmissionUnproven

            deps, bound = committed_rows_select(
                SOURCE_DEPENDENCIES,
                head,
                org_id=writer.org_id,
                project_id=writer.lease.project_id,
                key_relation=KeyRelation(
                    ("trace_id", "span_id"),
                    f"SELECT JSONExtractString(unit_id, 1), JSONExtractString(unit_id, 2) FROM {name}",
                    {},
                ),
                param_prefix="span_capture_dependencies",
            )
            if store.execute(
                f"SELECT count() FROM {name} WHERE unit_id NOT IN "
                f"(SELECT toJSONString(tuple(trace_id, span_id)) FROM ({deps}) WHERE is_deleted = 0)",
                bound,
            )[0][0]:
                raise MetricAdmissionUnproven("metric_admission_unproven")
        check_session()
        yield f"SELECT {columns} FROM {name}", previous, previous_params, count, size
        check_session()
    finally:
        if store.client.connection.socket is socket:
            try:
                store.execute(f"DROP TEMPORARY TABLE IF EXISTS {name}")
            except Exception:
                store.client.disconnect()
                raise


@contextmanager
def retiring_span_fields(
    writer, attempt, head, definition, expired, params, *, cutoff, as_of
):
    """Terminal corrections are frozen before anonymous publication, not at read time.

    Only previously committed expiring identities may enter retirement. Preserve
    their committed value if raw storage has already physically pruned the source;
    a present authoritative deletion/filter change is not such an absence.
    """
    from tracer.services.derived_metrics.queries import Scope

    with (
        writer.store.compaction_budget(monotonic() + 600),
        captured_span_fields(
            writer,
            attempt,
            definition,
            Scope(writer.org_id, writer.workspace_id, writer.lease.project_id),
            head=head,
            as_of=as_of,
            cutoff=None,
            preserve_pruned=True,
        ) as (fields, _previous, _params, _count, _size),
    ):
        yield (
            f"SELECT * FROM ({fields}) WHERE unit_id IN (SELECT unit_id FROM ({expired})) "
            "AND unit_start < %(retiring_span_cutoff)s",
            merge_bindings(params, {"retiring_span_cutoff": cutoff}),
        )


def freeze_span_fields(writer, attempt, head, definition, scope, *, as_of, cutoff):
    """Freeze one replacement per key, including vanished/filtered/moved sources.

    All dirty buckets share a capture. A span moving between two dirty buckets
    therefore gets exactly one live row, never a later old-bucket tombstone.
    Bucket reconstruction subsequently consumes these bytes, NOT mutable spans.
    """
    with (
        writer.store.compaction_budget(monotonic() + 600),
        captured_span_fields(
            writer, attempt, definition, scope, head=head, as_of=as_of, cutoff=cutoff
        ) as (fields, previous, params, count, size),
    ):
        tombstones = (
            "SELECT * REPLACE(toUInt8(1) AS is_deleted, 'no_value' AS status, "
            "NULL AS value_bool, NULL AS value_number, NULL AS value_text, "
            "NULL AS part_n, NULL AS part_d, map() AS inputs, map() AS inputs_bool, "
            "map() AS inputs_text) "
            f"FROM ({previous}) WHERE is_deleted = 0 AND unit_id NOT IN (SELECT unit_id FROM ({fields}))"
        )
        # Retractions count toward the same pass budget. A small/empty new
        # source must not turn into an unbounded historical tombstone stream.
        retired_count, retired_bytes = writer.store.execute(
            f"SELECT count(), sum(length(formatRow('RowBinary', {', '.join(FIELD_VALUES.columns)}))) "
            f"FROM ({tombstones})",
            params,
        )[0]
        if (
            count + retired_count > SPAN_CAPTURE_ROWS
            or size + retired_bytes > SPAN_CAPTURE_BYTES
        ):
            raise MetricVisibilityError("span replacement exceeds its capture budget")
        writer.freeze(attempt, FIELD_VALUES, tombstones, params, collect_payloads=False)
        writer.freeze(
            attempt,
            FIELD_VALUES,
            fields,
            {
                "span_field": str(definition.metric_id),
                "span_field_version": definition.version,
            },
            extra={
                "field_id": "toUUID(%(span_field)s)",
                "field_version": "toUInt32(%(span_field_version)s)",
            },
            collect_payloads=False,
        )
