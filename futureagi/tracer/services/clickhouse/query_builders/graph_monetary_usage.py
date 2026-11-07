"""Graph-only monetary projection over a bounded latest-live span population."""

from tracer.services.clickhouse.query_builders.trace_usage import (
    SUMMARY_NAME,
    USAGE_COLUMNS,
    repeated_usage_ids_sql,
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
    repeated = (
        f"({repeated}) OR ({span('observation_type')} != 'llm' "
        f"AND has(genai_repeated_ids, {span('id')}))"
    )
    flags = span("graph_cost_flags")
    projections = []
    for column in columns:
        value = span(column)
        if column == "cost":
            value = f"if(graph_repeated_summary OR NOT ({flags}.1 OR {flags}.2), 0, {value})"
        elif column == "graph_cost_flags":
            value = (
                f"tuple(if(graph_repeated_summary, toUInt8(0), {value}.1), "
                f"if(graph_repeated_summary, toUInt8(0), {value}.2))"
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
    genai_proof = repeated_usage_ids_sql()
    # Keep Pydantic's two-event reduction intact. The separate light proof is
    # trace-scoped and retains intermediate ancestry even outside a graph's
    # contributing request window; only selected LLMs carry proof usage.
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
            LEFT JOIN (
                SELECT project_id, trace_id, {genai_proof} AS genai_repeated_ids
                FROM (
                    SELECT project_id, trace_id,
                        groupArray(tuple(id, parent_span_id, '',
                            if(({child_condition}) OR observation_type != 'llm', observation_type, 'excluded'),
                            ifNull(cost, 0), ifNull(total_tokens, 0),
                            ifNull(prompt_tokens, 0), ifNull(completion_tokens, 0),
                            tuple(0, 0, tupleElement(tupleConcat(graph_cost_flags, tuple(0)), 3)))) AS usage_spans
                    FROM {source}
                    WHERE (project_id, trace_id) IN (
                        SELECT project_id, trace_id FROM {source}
                        WHERE tupleElement(tupleConcat(graph_cost_flags, tuple(0)), 3)
                            AND observation_type != 'llm'
                    )
                    GROUP BY project_id, trace_id
                ) AS genai_usage_population
            ) AS genai_usage_proof USING (project_id, trace_id)
            ARRAY JOIN graph_own_spans AS graph_own_span
        ) AS graph_usage_proof
    """
