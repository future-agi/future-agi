"""Bounded PREPARED span capture, shared by committed evidence and bucket totals.

No new persistent store: span rows use FIELD_VALUES' existing level/unit identity,
immutable attempts, receipts, checkpoints, pins and all-revision privacy cleanup.
The canonical unit id is the compiler's JSON [trace_id, span_id], never span_id
alone. Temporary source rows are connection-owned and cannot survive a failed
preparation. Only frozen publication batches are recoverable.
"""

from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime, timedelta
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
from tracer.services.derived_metrics.unit_reads import (
    span_values_select,
    span_window_floor_select,
    span_window_version_select,
)

# Operational admission bounds, not a measured production capacity envelope.
# Overflow aborts preparation; it must never truncate and publish a partial bucket.
SPAN_CAPTURE_ROWS = 50_000
SPAN_CAPTURE_BYTES = 64 * 1024 * 1024


def require_retirement_evidence(
    writer, head, definitions, *, draft=None, cutoff=None, proposed=None
):
    """Prove coverage of every bucket this publication can replace or expire.

    A bounded backfill may use its generation-pinned frozen proposal to upgrade a
    fully retained legacy bucket. Untouched retained buckets can wait for the next
    pass; expiring buckets need independently committed coverage. Proposed counts
    cannot distinguish new retained arrivals from missing legacy identities.
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
            # The caller's pass-scoped proposal (sql, bindings) when it has one
            # (overlay captured, head inline); never re-inline frozen bytes.
            relation, bound = (
                proposed
                if proposed is not None
                else _proposal_rows(
                    writer, draft, head, FIELD_VALUES, "retirement_proof_proposed"
                )
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
                f"FROM ({relation}) WHERE is_deleted = 0 AND level = 'span' "
                "AND status != 'open' AND field_id = toUUID(%(proof_field)s) "
                "AND field_version = %(proof_version)s GROUP BY bucket_start) p "
                "ON t.bucket_start = p.bucket_start"
            )
            uncovered_proposal = (
                "t.total > ifNull(p.proposed, 0) + ifNull(r.retired, 0)"
            )
            proposal_proof = (
                f" AND (t.bucket_start < %(proof_cutoff)s OR {uncovered_proposal})"
                if cutoff is not None
                else f" AND ({uncovered_proposal})"
            )
        # Complete parents permit terminal corrections/retractions. A proposal
        # may repair a deficient parent only where the whole bucket is retained:
        # at a partial cutoff, captured fields can omit uncovered old identities.
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


QUARTER = timedelta(minutes=15)
MAX_RUN_QUARTERS = 96


def _bucket_runs(dirty):
    """Coalesce sorted dirty quarter starts into runs of adjacent quarters.

    Each run is a half-open [first, last + 15 min) window: exactly the union of
    its quarters' windows, never a skipped (clean) quarter between them.
    """
    runs = []
    for at in sorted(dirty):
        if runs and runs[-1][1] == at and runs[-1][2] < MAX_RUN_QUARTERS:
            runs[-1] = (runs[-1][0], at + QUARTER, runs[-1][2] + 1)
        else:
            runs.append((at, at + QUARTER, 1))
    return [(start, end) for start, end, _ in runs]


def _span_window_floor(store, scope, at, until):
    """Exact physical start_time floor of a span window's revision reads."""
    version_sql, version_params = span_window_version_select(scope, start=at, end=until)
    version = store.execute(
        version_sql, version_params, query_id="metric-span-version-" + uuid4().hex
    )[0][0]
    if version is None:
        return at
    floor_sql, floor_params = span_window_floor_select(
        scope, start=at, end=until, version=version
    )
    moved = store.execute(
        floor_sql, floor_params, query_id="metric-span-floor-" + uuid4().hex
    )[0][0]
    if moved is None:
        return at
    aware = (
        value if value.tzinfo else value.replace(tzinfo=UTC) for value in (at, moved)
    )
    return min(aware)


def _created_after_purge(store, scope, head, unproven, params, floors):
    """These unproven spans cannot be evidence a retention purge retired.

    A purged span's committed revision started before the retention floor. So
    a span is exempt from proof (R02, TH-8975) only if it has no physical
    revision starting before the floor, and every revision in its window
    (each window's floor bounds those, span_window_floor_select) was created
    after the latest purge, so none existed when evidence was retired. Older
    revisions between the two floors start inside retention: never purged.
    Unknown purge time: nothing is exempt.
    """
    at = head.published_by.source_ceilings.get("retention_floor_at")
    floor = head.published_by.source_ceilings.get("retention_floor")
    if not at or not floor or not floors:
        return False
    keys = (
        "(trace_id, id) IN (SELECT JSONExtractString(unit_id, 1), "
        f"JSONExtractString(unit_id, 2) FROM ({unproven}))"
    )
    bound = merge_bindings(
        params,
        {
            "created_project": str(scope.project),
            "created_floor": min(floors.values()),
            "created_retention_floor": datetime.fromisoformat(str(floor)),
        },
    )
    expired, first = store.execute(
        "SELECT countIf(start_time < %(created_retention_floor)s), "
        "minIf(created_at, start_time >= %(created_floor)s) FROM spans "
        "PREWHERE project_id = toUUID(%(created_project)s) "
        "AND (start_time < %(created_retention_floor)s "
        "OR start_time >= %(created_floor)s) "
        f"WHERE {keys}",
        bound,
        query_id="metric-admission-created-" + uuid4().hex,
    )[0]
    if expired or first is None:
        return False
    first = first if first.tzinfo else first.replace(tzinfo=UTC)
    return first > datetime.fromisoformat(str(at))


def _dirty_buckets(attempt):
    return sorted(
        {
            datetime.fromisoformat(str(entry["bucket_start"]))
            for chunk in attempt.ledger_chunks.filter(
                part__in=["dirty_before", "dirty_after"]
            )
            for entry in chunk.payload
            if "bucket_start" in entry
        }
    )


def _previous_fields(writer, head, definition, dirty, unit_range=None):
    """Committed span fields in the dirty buckets, optionally one identity page."""
    predicate = "bucket_start IN %(span_dirty_buckets)s" if dirty else "0"
    params = {"span_dirty_buckets": tuple(dirty)} if dirty else {}
    if unit_range is not None:
        after, through = unit_range
        predicate += " AND unit_id > %(span_page_after)s"
        params["span_page_after"] = after
        if through is not None:
            predicate += " AND unit_id <= %(span_page_through)s"
            params["span_page_through"] = through
    return windowed_rows_select(
        FIELD_VALUES,
        head,
        org_id=writer.org_id,
        project_id=writer.lease.project_id,
        predicate=predicate,
        params=params,
        key_in={
            "field_id": (definition.metric_id,),
            "field_version": (definition.version,),
            "level": ("span",),
        },
        param_prefix="span_previous_fields",
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
    dirty=None,
    unit_range=None,
    floors=None,
    sampling_context=None,
):
    """Capture the dirty buckets' span fields into one bounded native table.

    ``unit_range`` (after, through] restricts the capture and the committed
    preimage to one page of span identities (freeze_span_fields); ``floors``
    caches each run's start floor across those pages.
    """
    store = writer.store
    if dirty is None:
        dirty = _dirty_buckets(attempt)
    if previous_page is not None and unit_range is not None:
        raise ValueError("a retirement page is already an identity page")
    previous, previous_params = _previous_fields(
        writer, head, definition, dirty, unit_range
    )
    floors = {} if floors is None else floors
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
        for at, until in _bucket_runs(dirty):
            writer.lease.keepalive()
            check_session()
            # One compiler execution per run of ADJACENT dirty quarters: their
            # half-open windows tile [at, until) exactly, and sampling ambiguity
            # stays partitioned per quarter, so rows equal the per-quarter union.
            if (at, until) not in floors:
                floors[at, until] = _span_window_floor(store, scope, at, until)
            fields, params = span_values_select(
                definition,
                scope,
                start=at,
                end=until,
                as_of=as_of,
                start_floor=floors[at, until],
                unit_range=unit_range,
                sampling_context=sampling_context,
            )
            if preserve_pruned:
                previous_columns = ", ".join(
                    c for c, _ in SOURCE_SCHEMA if c != "source_rows"
                )
                fields = (
                    f"(SELECT {columns} FROM ({fields}) WHERE unit_id IN "
                    f"(SELECT unit_id FROM ({previous}) WHERE is_deleted = 0)) "
                    f"UNION ALL SELECT {previous_columns}, toUInt64(1) AS source_rows "
                    f"FROM ({previous}) WHERE is_deleted = 0 "
                    "AND bucket_start >= %(capture_bucket)s "
                    "AND bucket_start < %(capture_bucket_end)s "
                    f"AND unit_id NOT IN ({physical_keys})"
                )
                params = merge_bindings(
                    params,
                    previous_params,
                    source_scope,
                    {"capture_bucket": at, "capture_bucket_end": until},
                )
            if cutoff is not None:
                fields = (
                    f"SELECT * FROM ({fields}) WHERE unit_start >= %(span_capture_cutoff)s "
                    f"OR unit_id IN (SELECT unit_id FROM ({previous}) WHERE is_deleted = 0)"
                )
                params = merge_bindings(
                    params, previous_params, {"span_capture_cutoff": cutoff}
                )
            if unit_range is not None:
                # Fresh identity pages only. Retirement has its own budget and
                # must never depend on the busy-slot admission settings.
                from tracer.services.derived_metrics.busy_guard import counts

                writer.pass_span_captures = getattr(writer, "pass_span_captures", 0) + 1
                counts(writer)
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
            if count != unique:
                raise MetricVisibilityError("span capture is duplicate")
            if count > SPAN_CAPTURE_ROWS or size > SPAN_CAPTURE_BYTES:
                raise MetricVisibilityError("span capture exceeds its budget")
        from tracer.services.derived_metrics.refresh import admission_requires_proof

        if admission_requires_proof(head):
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
            unproven = (
                f"SELECT unit_id FROM {name} WHERE unit_id NOT IN "
                f"(SELECT toJSONString(tuple(trace_id, span_id)) FROM ({deps}) WHERE is_deleted = 0) "
                f"AND unit_id NOT IN (SELECT unit_id FROM ({previous}) WHERE is_deleted = 0)"
            )
            unproven_params = merge_bindings(bound, previous_params)
            if store.execute(f"SELECT count() FROM ({unproven})", unproven_params)[0][
                0
            ] and not _created_after_purge(
                store, scope, head, unproven, unproven_params, floors
            ):
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
            # A correction can be wider than its committed preimage. Overflow
            # discards the native session, including every prior accumulator.
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
                    current.close()
                    lower, parts = "", None
                    # Restart the complete pinned scan and keep the smaller cap
                    # for all subsequent pages, or the same later overflow can
                    # repeatedly destroy a successfully rebuilt prefix.
                    target_rows = max(1, count // 2)
                    continue
                raise
            # Both accumulators coexist only for this checked merge. Closing the
            # prior registry cannot remove the newly materialized replacement.
            current.close()
            current.enter_context(replacement.pop_all())
            parts, lower = next_parts, upper
        yield (
            parts
            if parts is not None
            else live_parts_select(definition, f"SELECT * FROM ({expired}) WHERE 0"),
            params,
        )


def _span_page_through(writer, head, definition, dirty, after, rows):
    """The last identity of the next page of at most ``rows`` span identities.

    Identities sort by unit_id, the span's JSON (trace_id, span_id). The page
    population is every span with a revision in a dirty bucket (all the capture
    can nominate) plus every committed field there (all it can tombstone), so a
    page never holds more captured or retracted rows than ``rows``. None when
    fewer than ``rows`` identities remain: the last page takes the rest.
    """
    if not dirty:
        return None
    previous, params = _previous_fields(writer, head, definition, dirty)
    found = writer.store.execute(
        "SELECT unit_id FROM ("
        "SELECT toJSONString(tuple(toString(trace_id), toString(id))) AS unit_id "
        "FROM spans PREWHERE project_id = toUUID(%(span_page_project)s) "
        "AND start_time >= %(span_page_first)s AND start_time < %(span_page_last)s "
        "WHERE toStartOfFifteenMinutes(start_time) IN %(span_dirty_buckets)s "
        "AND (org_id = toUUID(%(span_page_org)s) OR isNull(org_id)) "
        f"UNION DISTINCT SELECT unit_id FROM ({previous}) WHERE is_deleted = 0"
        ") WHERE unit_id > %(span_page_from)s ORDER BY unit_id "
        f"LIMIT 1 OFFSET {int(rows) - 1}",
        merge_bindings(
            params,
            {
                "span_page_project": str(writer.lease.project_id),
                "span_page_org": str(writer.org_id),
                "span_page_first": dirty[0],
                "span_page_last": dirty[-1] + QUARTER,
                "span_page_from": after,
            },
        ),
    )
    return found[0][0] if found else None


def _over_budget(exc):
    from clickhouse_driver.errors import ServerException

    return (
        isinstance(exc, ServerException) and "metric_span_capture_budget" in str(exc)
    ) or (isinstance(exc, MetricVisibilityError) and "exceeds" in str(exc))


def freeze_span_fields(
    writer, attempt, head, definition, scope, *, as_of, cutoff, sampling
):
    """Freeze one replacement per key, including vanished/filtered/moved sources.

    Pages are disjoint, key-ordered ranges of span identities, each its own
    bounded native capture of EVERY dirty bucket for those identities. A span
    moving between two dirty buckets is in exactly one page, so it still gets
    exactly one live row, never a later old-bucket tombstone. A dense quarter
    above the row budget is therefore paged, never refused. A page over the byte
    budget is retried at half the identities; only one identity over the byte
    budget on its own fails the pass. Bucket reconstruction subsequently
    consumes these frozen bytes, NOT mutable spans.
    """
    dirty = sampling.dirty
    after, rows, floors = "", SPAN_CAPTURE_ROWS, sampling.floors
    with writer.store.compaction_budget(monotonic() + 600):
        while True:
            writer.lease.keepalive()
            context = sampling.select(definition)
            through = _span_page_through(writer, head, definition, dirty, after, rows)
            writer.yield_if_late(attempt)
            try:
                _freeze_span_page(
                    writer,
                    attempt,
                    head,
                    definition,
                    scope,
                    as_of=as_of,
                    cutoff=cutoff,
                    unit_range=(after, through),
                    floors=floors,
                    sampling_context=context,
                    dirty=dirty,
                )
                sampling.check()
            except Exception as exc:
                if rows > 1 and _over_budget(exc):
                    # Nothing of this page was frozen: budgets are checked
                    # before either freeze. The native error discarded scratch
                    # tables; replay frozen collisions, not mutable source.
                    sampling.restore_for_retry()
                    rows = max(1, rows // 2)
                    continue
                raise
            if through is None:
                return
            after = through


def _freeze_span_page(
    writer,
    attempt,
    head,
    definition,
    scope,
    *,
    as_of,
    cutoff,
    unit_range,
    floors,
    dirty,
    sampling_context=None,
):
    with captured_span_fields(
        writer,
        attempt,
        definition,
        scope,
        head=head,
        as_of=as_of,
        cutoff=cutoff,
        unit_range=unit_range,
        floors=floors,
        sampling_context=sampling_context,
        dirty=dirty,
    ) as (fields, previous, params, count, size):
        tombstones = (
            "SELECT * REPLACE(toUInt8(1) AS is_deleted, 'no_value' AS status, "
            "NULL AS value_bool, NULL AS value_number, NULL AS value_text, "
            "NULL AS part_n, NULL AS part_d, map() AS inputs, map() AS inputs_bool, "
            "map() AS inputs_text) "
            f"FROM ({previous}) WHERE is_deleted = 0 AND unit_id NOT IN (SELECT unit_id FROM ({fields}))"
        )
        # Retractions count toward the same page budget. A small/empty new
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
