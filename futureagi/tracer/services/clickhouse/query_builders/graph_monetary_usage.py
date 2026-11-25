"""Graph-only monetary projection over one bounded latest-live span scan."""

from tracer.services.clickhouse.query_builders.trace_usage import (
    USAGE_COLUMNS,
    repeated_usage_ids_sql,
)


def graph_monetary_rows_sql(
    *,
    source: str,
    columns: tuple[str, ...],
    child_condition: str = "1",
) -> str:
    """Mask repeated summaries' usage without removing traffic/topology rows.

    The caller supplies latest-live, tenant/window-scoped scalar rows. Read that
    population ONCE: materialize one light payload per trace, prove immediate
    Pydantic or descendant GenAI usage, then emit each physical row unchanged
    except for its aggregate cost and token contributions. Raw attribute/output
    maps must not be retained in ``columns``. The child-window flag fences usage
    evidence while keeping intermediate ancestry available. No chronological
    path is inferred.
    """

    def field(column: str, alias: str) -> str:
        return f"tupleElement({alias}, {columns.index(column) + 1})"

    flags = field("graph_cost_flags", "graph_own_span")
    projections = []
    for column in columns:
        value = field(column, "graph_own_span")
        if column == "cost":
            value = f"if(graph_repeated_summary OR NOT ({flags}.1 OR {flags}.2), 0, {value})"
        elif column in USAGE_COLUMNS[1:]:
            value = f"if(graph_repeated_summary, 0, {value})"
        elif column == "graph_cost_flags":
            value = (
                f"tuple(if(graph_repeated_summary, toUInt8(0), {value}.1), "
                f"if(graph_repeated_summary, toUInt8(0), {value}.2))"
            )
        projections.append(f"{value} AS {column}")
    projection = ",\n                ".join(projections)
    payload = ", ".join(
        f"ifNull({column}, 0)" if column in USAGE_COLUMNS else column
        for column in columns
    )
    usage_values = ", ".join(
        field(column, "g")
        for column in (
            "id",
            "parent_span_id",
            "name",
            "observation_type",
            *USAGE_COLUMNS,
        )
    )
    # Older internal callers supply two/three flags. Only the explicit fourth
    # flag proves nonadditivity; unknown pricing alone must never hide tokens.
    usage_flags = f"tupleConcat({field('graph_cost_flags', 'g')}, tuple(0, 0))"
    usage_values += (
        f", tuple(tupleElement({usage_flags}, 4), 0, tupleElement({usage_flags}, 3))"
        f", tupleElement(g, {len(columns) + 1})"
    )
    proof = repeated_usage_ids_sql(child_condition="c.10")
    repeated = (
        f"tupleElement(tupleConcat({flags}, tuple(0, 0)), 4) OR "
        f"({field('observation_type', 'graph_own_span')} != 'llm' "
        f"AND has(graph_usage_state.2, {field('id', 'graph_own_span')}))"
    )
    # ARRAY JOIN the singleton state before its rows: this materializes the
    # shared proof, rather than re-expanding it in every money/count projection.
    return f"""
        SELECT project_id, trace_id, {projection}
        FROM (
            SELECT project_id, trace_id, graph_own_span,
                ({repeated}) AS graph_repeated_summary
            FROM (
                SELECT project_id, trace_id, graph_usage_state
                FROM (
                    SELECT project_id, trace_id,
                        arrayMap(graph_spans ->
                            arrayMap(usage_spans ->
                                arrayMap(repeated_ids -> tuple(graph_spans, repeated_ids),
                                    [{proof}])[1],
                                [arrayMap(g -> tuple({usage_values}), graph_spans)])[1],
                            [groupArray(tuple({payload}, toUInt8({child_condition})))])
                            AS graph_usage_states
                    FROM {source}
                    GROUP BY project_id, trace_id
                ) AS graph_usage_by_trace
                ARRAY JOIN graph_usage_states AS graph_usage_state
            ) AS graph_usage_materialized
            ARRAY JOIN graph_usage_state.1 AS graph_own_span
        ) AS graph_usage_proof
    """
