"""Diagnostic projection nomination and revision-complete trace/hour closure.

Not a production fast path: ClickHouse 25.3 common-snapshot behavior across all
references has not been qualified against concurrent backdated arrivals. Callers
continue using unit_reads' broad all-revision reader. A frozen as_of, a CTE and
project publication locks do not establish an ingestion snapshot.
"""

from dataclasses import dataclass

from tracer.services.derived_metrics.storage import SOURCE_DEPENDENCIES


@dataclass(frozen=True)
class SpanClosurePlan:
    ctes: tuple[str, ...]
    predicate: str
    parameters: dict
    production_qualified: bool = False
    fallback_reason: str = "common_source_snapshot_unproven"


def diagnostic_span_closure(scope, unit_ids, *, manifest=None, source_identities=()):
    """Only projection-covered columns nominate; closure sees ALL raw sessions.

    The relation stays server-side and every input identity is bounded by the
    existing immutable membership validator. No hour restriction applies to eval
    logger rows. Their separate evaluation-id revision pipeline stays unchanged.
    """
    from tracer.services.derived_metrics.bounded_reads import normalize_key_in
    from tracer.services.derived_metrics.unit_reads import _ids, _source_plan

    ids = _ids(unit_ids)
    identities = normalize_key_in(
        SOURCE_DEPENDENCIES,
        {
            ("trace_id", "span_id"): tuple(
                (str(trace), str(span)) for trace, span in source_identities
            )
        },
    )[("trace_id", "span_id")]
    existing, _, params = _source_plan(scope, ids, manifest, identities)
    # Reuse canonical request/remap closure, not the old payload-based nominee.
    ctes = list(existing[:3])
    traces = [
        "SELECT trace_id FROM spans PREWHERE project_id = %(project_id)s "
        "AND trace_session_id IN (SELECT alias_id FROM unit_aliases)"
    ]
    if identities:
        traces.append(
            "SELECT tupleElement(pair, 1) AS trace_id FROM "
            "(SELECT arrayJoin(%(unit_read_source_ids)s) AS pair)"
        )
    if manifest is not None:
        from uuid import UUID

        if str(manifest.project_id) != str(scope.project):
            raise ValueError("manifest project mismatch")
        params["closure_dependency_pairs"] = tuple(
            (generation, UUID(str(attempt)))
            for generation, attempt in manifest.allowed_attempts()
        )
        if params["closure_dependency_pairs"]:
            traces.append(
                f"SELECT trace_id FROM {SOURCE_DEPENDENCIES.name} "
                "PREWHERE project_id = %(project_id)s AND org_id = %(org_id)s "
                "WHERE (generation, attempt_id) IN %(closure_dependency_pairs)s "
                "AND (canonical_session_id IN (SELECT unit_id FROM requested_units) "
                "OR canonical_session_id IN (SELECT raw_id FROM unit_read_requests))"
            )
    ctes.extend(
        (
            "closure_traces AS (" + " UNION DISTINCT ".join(traces) + ")",
            "closure_hours AS (SELECT DISTINCT trace_id, toStartOfHour(start_time) AS source_hour "
            "FROM spans PREWHERE project_id = %(project_id)s "
            "WHERE trace_id IN (SELECT trace_id FROM closure_traces))",
        )
    )
    # The day set comes from complete trace closure, never requested-session
    # hours. A/H1 -> B/H2 (including a tombstone) must still read revision H2.
    predicate = (
        "toDate(start_time) IN (SELECT DISTINCT toDate(source_hour) FROM closure_hours) "
        "AND (trace_id, toStartOfHour(start_time)) IN "
        "(SELECT trace_id, source_hour FROM closure_hours)"
    )
    return SpanClosurePlan(tuple(ctes), predicate, {**scope.parameters(), **params})
