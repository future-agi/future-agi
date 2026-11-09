"""Bounded PREPARED span capture, shared by committed evidence and bucket totals.

No new persistent store: span rows use FIELD_VALUES' existing level/unit identity,
immutable attempts, receipts, checkpoints, pins and all-revision privacy cleanup.
The canonical unit id is the compiler's JSON [trace_id, span_id], never span_id
alone. Temporary source rows are connection-owned and cannot survive a failed
preparation. Only frozen publication batches are recoverable.
"""

from contextlib import ExitStack, contextmanager
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


def require_retirement_evidence(writer, head, definitions, *, draft=None, cutoff=None):
    """Prove coverage of every bucket this publication can replace or expire.

    A bounded backfill may use its generation-pinned frozen proposal to upgrade a
    legacy bucket. Untouched retained buckets can wait for the next pass; missing
    evidence in an expiring bucket cannot. Compare per bucket, so new arrivals in
    another bucket cannot disguise a deficit in legacy history.
    """
    if head is None:
        return
    from tracer.models.derived_metric import MetricStateConflict
    from tracer.services.derived_metrics.refresh import MetricAdmissionUnproven
    from tracer.services.derived_metrics.retention import _proposal_rows
    from tracer.services.derived_metrics.storage import BUCKET_TOTALS, RETIRED_PARTS

    dirty = ()
    if draft is not None:
        if draft.parent_manifest_id != head.id:
            raise MetricStateConflict("span evidence proposal has a different parent")
        dirty = tuple(
            sorted(
                {
                    datetime.fromisoformat(str(entry["bucket_start"]))
                    for chunk in draft.ledger_chunks.filter(
                        part__in=["dirty_before", "dirty_after"]
                    )
                    for entry in chunk.payload
                    if "bucket_start" in entry
                }
            )
        )
    for definition in definitions:
        if definition.level != "span":
            continue
        counts, params = [], {}
        for table, alias in (
            (FIELD_VALUES, "live"),
            (BUCKET_TOTALS, "total"),
            (RETIRED_PARTS, "retired"),
        ):
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
            count = (
                "countIf(level = 'span' AND status != 'open')"
                if table == FIELD_VALUES
                else "sum(unit_count)"
            )
            counts.append(
                f"SELECT bucket_start, {count} AS {alias} FROM ({sql}) "
                "WHERE is_deleted = 0 GROUP BY bucket_start"
            )
        live, total, retired = counts
        predicate = "1"
        if cutoff is not None:
            predicate = "t.bucket_start < %(proof_cutoff)s"
            params["proof_cutoff"] = cutoff
            if dirty:
                predicate += " OR t.bucket_start IN %(proof_dirty)s"
                params["proof_dirty"] = dirty
        proposal_join, proposal_proof = "", ""
        if draft is not None:
            proposed, bound = _proposal_rows(
                writer, draft, head, FIELD_VALUES, "retirement_proof_proposed"
            )
            params = merge_bindings(
                params,
                bound,
                {
                    "proof_field": str(definition.metric_id),
                    "proof_version": definition.version,
                },
            )
            proposal_join = (
                " LEFT JOIN (SELECT bucket_start, count() AS proposed "
                f"FROM ({proposed}) WHERE is_deleted = 0 AND level = 'span' "
                "AND status != 'open' AND field_id = toUUID(%(proof_field)s) "
                "AND field_version = %(proof_version)s GROUP BY bucket_start) p "
                "ON t.bucket_start = p.bucket_start"
            )
            proposal_proof = (
                " AND t.total > ifNull(p.proposed, 0) + ifNull(r.retired, 0)"
            )
        # Complete parents permit terminal corrections/retractions. Deficient
        # parents instead need a complete proposed bucket before replacement.
        if writer.store.execute(
            f"SELECT count() FROM ({total}) t LEFT JOIN ({live}) l "
            "ON t.bucket_start = l.bucket_start "
            f"LEFT JOIN ({retired}) r ON t.bucket_start = r.bucket_start"
            f"{proposal_join} WHERE ({predicate}) "
            "AND t.total > ifNull(l.live, 0) + ifNull(r.retired, 0)"
            f"{proposal_proof}",
            params,
        )[0][0]:
            raise MetricAdmissionUnproven(
                "committed span evidence requires backfill before retirement"
            )


@contextmanager
def captured_span_fields(
    writer,
    attempt,
    definition,
    scope,
    *,
    head,
    as_of,
    cutoff,
    preserve_pruned=False,
    previous_page=None,
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
    if previous_page is not None:
        # Retirement pages are disjoint immutable identities, not bucket slices:
        # a terminal correction can move into another page's original bucket.
        previous, previous_params = previous_page
        dirty = [
            row[0]
            for row in store.execute(
                f"SELECT DISTINCT bucket_start FROM ({previous}) ORDER BY bucket_start",
                previous_params,
            )
        ]
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
            # A published field is also pinned identity evidence. Legacy
            # backfills can supply it without a redundant dependency row. A
            # retired/tombstoned identity is absent here and still needs proof;
            # raw source presence alone never admits an unknown old span.
            if store.execute(
                f"SELECT count() FROM {name} WHERE unit_id NOT IN "
                f"(SELECT toJSONString(tuple(trace_id, span_id)) FROM ({deps}) WHERE is_deleted = 0) "
                f"AND unit_id NOT IN (SELECT unit_id FROM ({previous}) WHERE is_deleted = 0)",
                merge_bindings(bound, previous_params),
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
def retiring_span_parts(
    writer, attempt, head, definition, expired, params, *, cutoff, as_of
):
    """Stream disjoint pinned identities into anonymous PRE-FOLD statistics.

    Each private capture keeps the normal row/byte bounds. Reduce it before the
    next capture, retaining only a bounded anonymous accumulator, never all the
    backlog's private rows or one registry entry per page. Terminal corrections
    and physically pruned sources follow the same rules as ordinary capture.
    Nothing is published until the complete iterator and final freeze succeed.
    """
    from clickhouse_driver.errors import ServerException

    from tracer.services.derived_metrics.buckets import (
        live_parts_select,
        merge_parts_select,
    )
    from tracer.services.derived_metrics.frozen_relations import FrozenRelations
    from tracer.services.derived_metrics.queries import Scope
    from tracer.services.derived_metrics.unit_reads import field_recipe

    formula = field_recipe(definition).formula
    params = merge_bindings(params, formula.parameters if formula is not None else {})
    scope = Scope(writer.org_id, writer.workspace_id, writer.lease.project_id)
    columns = ", ".join(c for c, _ in SOURCE_SCHEMA if c != "source_rows")
    lower, parts = "", None
    target_rows = SPAN_CAPTURE_ROWS
    with writer.store.compaction_budget(monotonic() + 600), ExitStack() as current:
        while True:
            writer.lease.keepalive()
            page_params = merge_bindings(params, {"retiring_after": lower})
            candidates = (
                f"SELECT * FROM ({expired}) WHERE is_deleted = 0 "
                "AND unit_id > %(retiring_after)s ORDER BY unit_id "
                f"LIMIT {target_rows}"
            )
            count, upper = writer.store.execute(
                "SELECT count(), max(unit_id) FROM (SELECT unit_id, "
                f"sum(length(formatRow('RowBinary', {columns}, toUInt64(1)))) "
                "OVER (ORDER BY unit_id ROWS UNBOUNDED PRECEDING) AS capture_bytes "
                f"FROM ({candidates})) WHERE capture_bytes <= {SPAN_CAPTURE_BYTES}",
                page_params,
            )[0]
            if not count:
                if writer.store.execute(
                    f"SELECT count() FROM ({candidates})", page_params
                )[0][0]:
                    raise MetricVisibilityError(
                        "span retirement row exceeds its capture budget"
                    )
                break
            previous = (
                f"SELECT * FROM ({candidates}) WHERE unit_id <= %(retiring_through)s",
                merge_bindings(page_params, {"retiring_through": upper}),
            )
            # A correction can be wider than its committed preimage. Retry only
            # the unconsumed capture, with fewer identities, never weaker limits.
            replacement = ExitStack()
            try:
                with captured_span_fields(
                    writer,
                    attempt,
                    definition,
                    scope,
                    head=head,
                    as_of=as_of,
                    cutoff=None,
                    preserve_pruned=True,
                    previous_page=previous,
                ) as (fields, _previous, _params, _count, _size):
                    live = live_parts_select(
                        definition,
                        f"SELECT * FROM ({fields}) WHERE unit_start < %(retiring_span_cutoff)s",
                    )
                    relations = replacement.enter_context(
                        FrozenRelations(
                            writer.store,
                            org_id=writer.org_id,
                            project_id=writer.lease.project_id,
                            attempt_id=attempt.id,
                            source=[
                                attempt.parent_manifest_sha256,
                                str(definition.metric_id),
                                definition.version,
                                lower,
                                upper,
                            ],
                        )
                    )
                    union = live if parts is None else f"({live}) UNION ALL ({parts})"
                    handle = relations.materialize(
                        merge_parts_select(union),
                        merge_bindings(params, {"retiring_span_cutoff": cutoff}),
                        role="child",
                    )
                    next_parts = relations.select(handle, role="child")
            except Exception as exc:
                replacement.close()
                if (
                    isinstance(exc, ServerException)
                    and "metric_span_capture_budget" in str(exc)
                    and count > 1
                ):
                    target_rows = max(1, count // 2)
                    continue
                raise
            # Both accumulators coexist only for this checked merge. Closing the
            # prior registry cannot remove the newly materialized replacement.
            current.close()
            current.enter_context(replacement.pop_all())
            parts, lower = next_parts, upper
            target_rows = SPAN_CAPTURE_ROWS
        yield (
            parts
            if parts is not None
            else live_parts_select(definition, f"SELECT * FROM ({expired}) WHERE 0"),
            params,
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
