"""Graph-only monetary projection over a bounded latest-live span population."""

from tracer.services.clickhouse.query_builders.trace_usage import (
    SUMMARY_NAME,
    USAGE_COLUMNS,
)


def graph_monetary_rows_sql(
    *,
    source: str,
    columns: tuple[str, ...],
    child_condition: str = "1",
) -> str:
    """Zero repeated summaries' money, never remove their traffic/topology rows.

    ``source`` supplies project_id, trace_id and the named scalar columns,
    including id, parent_span_id, name, observation_type, the raw usage vector
    and graph_cost_flags. It must already resolve versions and tombstones, but
    must not yet filter away a summary's children. ``child_condition`` fences
    the selected request window when the source also retains boundary-hour or
    trace-membership witnesses. It must not contain attribute/output partitions.
    No attribute maps belong in ``columns``.

    Each live row emits exactly two events: its own scalar payload and its
    immediate-parent usage vector. Grouping by (project, trace, target id) proves
    the existing Pydantic summary rule without an all-trace array or a correlated
    sibling scan. Only own rows are retained in arrays, so repeated physical
    identities sharing a logical id survive too. The comparison uses raw usage,
    before nonadditive monetary masking, just like usage_aggregate_sql.
    """

    def span(column: str) -> str:
        return f"tupleElement(graph_own_span, {columns.index(column) + 1})"

    comparisons = " AND ".join(
        (
            f"abs({span(column)} - graph_children_{column}) "
            f"<= greatest(abs({span(column)}) * 1e-9, 1e-18)"
            if column == "cost"
            else f"{span(column)} = graph_children_{column}"
        )
        for column in USAGE_COLUMNS
    )
    repeated = (
        f"{span('name')} = '{SUMMARY_NAME}' "
        f"AND {span('observation_type')} != 'llm' "
        f"AND graph_child_count > 0 AND {comparisons}"
    )
    flags = span("graph_cost_flags")
    projections = []
    for column in columns:
        value = span(column)
        if column == "cost":
            value = f"if(graph_repeated_summary OR NOT ({flags}.1 OR {flags}.2), 0, {value})"
        elif column == "graph_cost_flags":
            value = (
                f"if(graph_repeated_summary, tuple(toUInt8(0), toUInt8(0)), {value})"
            )
        projections.append(f"{value} AS {column}")
    projection = ",\n                ".join(projections)
    child_selected = f"graph_target.2 = 0 AND ({child_condition})"
    child_sums = ",\n                    ".join(
        f"sumIf(ifNull({column}, 0), {child_selected}) AS graph_children_{column}"
        for column in USAGE_COLUMNS
    )
    payload = ", ".join(
        f"ifNull({column}, 0)" if column in USAGE_COLUMNS else column
        for column in columns
    )
    # CH25 does not export an ARRAY JOIN alias through SELECT *; the proof
    # projection must name its payload so the outer scalar projection can read it.
    return f"""
        SELECT
            project_id,
            trace_id,
            {projection}
        FROM (
            SELECT
                project_id,
                trace_id,
                graph_own_span,
                ({repeated}) AS graph_repeated_summary
            FROM (
                SELECT
                    project_id,
                    trace_id,
                    groupArrayIf(tuple({payload}), graph_target.2 = 1) AS graph_own_spans,
                    countIf({child_selected}) AS graph_child_count,
                    {child_sums}
                FROM (
                    SELECT *, arrayJoin([
                        tuple(id, toUInt8(1)),
                        tuple(parent_span_id, toUInt8(0))
                    ]) AS graph_target
                    FROM {source}
                ) AS graph_usage_events
                GROUP BY project_id, trace_id, graph_target.1
            ) AS graph_usage_by_parent
            ARRAY JOIN graph_own_spans AS graph_own_span
        ) AS graph_usage_proof
    """
