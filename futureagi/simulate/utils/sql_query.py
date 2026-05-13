"""SQL query utilities for simulate module"""


def get_chat_metrics_aggregation_query(
    call_execution_id,
    *,
    user_role,
    assistant_role,
    turn_count_role,
):
    """
    Get SQL query + params to aggregate chat metrics for a single call execution.

    Returns:
        tuple: (query, params)
    """
    query = """
    SELECT
        COALESCE(SUM(tokens), 0) AS total_tokens,
        -- input_tokens = simulator tokens (USER role under our dashboard convention)
        COALESCE(SUM(CASE WHEN role = %s THEN tokens ELSE 0 END), 0) AS input_tokens,
        -- output_tokens = agent tokens (ASSISTANT role under our dashboard convention)
        COALESCE(SUM(CASE WHEN role = %s THEN tokens ELSE 0 END), 0) AS output_tokens,
        COALESCE(AVG(latency_ms), NULL) AS avg_latency_ms,
        COUNT(CASE WHEN role = %s THEN 1 END) AS turn_count
    FROM simulate_chat_message
    WHERE call_execution_id = %s
    """
    params = [
        user_role,  # simulator tokens
        assistant_role,  # agent tokens
        turn_count_role,  # turn_count = number of agent turns
        str(call_execution_id),
    ]
    return query, params


def get_combined_call_executions_and_snapshots_query(
    run_test_id, search_pattern=None, status_filter=None, page_size=10, offset=0
):
    """
    Get combined call executions and snapshots with pagination using SQL UNION.

    Args:
        run_test_id: UUID of the run test
        search_pattern: Optional search pattern for phone number or scenario name
        status_filter: Optional status filter
        page_size: Number of items per page
        offset: Offset for pagination

    Returns:
        tuple: (query, params) where query is the SQL string and params is the list of parameters
    """
    # Build search conditions
    search_conditions = ""
    search_params = []
    if search_pattern:
        search_conditions = "AND (ce.phone_number ILIKE %s OR s.name ILIKE %s)"
        search_params = [f"%{search_pattern}%", f"%{search_pattern}%"]

    # Build status conditions
    status_conditions = ""
    status_params = []
    if status_filter:
        status_conditions = "AND ce.status = %s"
        status_params = [status_filter]

    # Main UNION query for pagination
    union_query = f"""
    WITH combined_items AS (
        SELECT
            ce.id,
            ce.updated_at as timestamp,
            'call_execution' as item_type,
            ce.status,
            ce.phone_number,
            ce.scenario_id,
            ce.test_execution_id
        FROM simulate_call_execution ce
        JOIN simulate_test_execution te ON ce.test_execution_id = te.id
        JOIN simulate_scenarios s ON ce.scenario_id = s.id
        WHERE te.run_test_id = %s
        {search_conditions}
        {status_conditions}

        UNION ALL

        SELECT
            ces.id,
            ces.snapshot_timestamp as timestamp,
            'snapshot' as item_type,
            ces.status,
            ce2.phone_number,
            ce2.scenario_id,
            ce2.test_execution_id
        FROM simulate_call_execution_snapshot ces
        JOIN simulate_call_execution ce2 ON ces.call_execution_id = ce2.id
        JOIN simulate_test_execution te2 ON ce2.test_execution_id = te2.id
        JOIN simulate_scenarios s2 ON ce2.scenario_id = s2.id
        WHERE te2.run_test_id = %s
        AND ces.rerun_type = 'call_and_eval'
        {search_conditions.replace('ce.', 'ce2.').replace('s.name', 's2.name')}
        {status_conditions.replace('ce.status', 'ces.status')}
    )
    SELECT * FROM combined_items
    ORDER BY timestamp DESC
    LIMIT %s OFFSET %s
    """

    # Combine all parameters
    params = (
        [str(run_test_id)]
        + search_params
        + status_params
        + [str(run_test_id)]
        + search_params
        + status_params
        + [page_size, offset]
    )

    return union_query, params


def get_combined_call_executions_and_snapshots_count_query(
    run_test_id, search_pattern=None, status_filter=None
):
    """
    Get total count of combined call executions and snapshots.

    Args:
        run_test_id: UUID of the run test
        search_pattern: Optional search pattern for phone number or scenario name
        status_filter: Optional status filter

    Returns:
        tuple: (query, params) where query is the SQL string and params is the list of parameters
    """
    # Build search conditions
    search_conditions = ""
    search_params = []
    if search_pattern:
        search_conditions = "AND (ce.phone_number ILIKE %s OR s.name ILIKE %s)"
        search_params = [f"%{search_pattern}%", f"%{search_pattern}%"]

    # Build status conditions
    status_conditions = ""
    status_params = []
    if status_filter:
        status_conditions = "AND ce.status = %s"
        status_params = [status_filter]

    # Count query
    count_query = f"""
    SELECT COUNT(*) FROM (
        SELECT ce.id FROM simulate_call_execution ce
        JOIN simulate_test_execution te ON ce.test_execution_id = te.id
        JOIN simulate_scenarios s ON ce.scenario_id = s.id
        WHERE te.run_test_id = %s
        {search_conditions}
        {status_conditions}

        UNION ALL

        SELECT ces.id FROM simulate_call_execution_snapshot ces
        JOIN simulate_call_execution ce2 ON ces.call_execution_id = ce2.id
        JOIN simulate_test_execution te2 ON ce2.test_execution_id = te2.id
        JOIN simulate_scenarios s2 ON ce2.scenario_id = s2.id
        WHERE te2.run_test_id = %s
        AND ces.rerun_type = 'call_and_eval'
        {search_conditions.replace('ce.', 'ce2.').replace('s.name', 's2.name')}
        {status_conditions.replace('ce.status', 'ces.status')}
    ) as total_count
    """

    # Combine all parameters
    params = (
        [str(run_test_id)]
        + search_params
        + status_params
        + [str(run_test_id)]
        + search_params
        + status_params
    )

    return count_query, params


def get_kpi_eval_metrics_query(test_execution_id):
    """
    Aggregate eval_outputs for a test execution using SQL jsonb_each.

    Returns two result sets via UNION ALL:
      1. Scalar metrics (Pass/Fail, score) -- already averaged per metric_name.
      2. Choice metrics -- one row per (metric_id, metric_name, choice_value)
         with the count of occurrences.

    Each row has: metric_id, metric_name, output_type, avg_value, choice_value, choice_count

    Returns:
        tuple: (query, params)
    """
    query = """
    WITH eval_entries AS (
        SELECT
            e.key                                              AS metric_id,
            COALESCE(e.value->>'name', 'metric_' || e.key)    AS metric_name,
            e.value->>'output_type'                            AS output_type,
            e.value->'output'                                  AS output_raw,
            e.value->>'output'                                 AS output_text
        FROM simulate_call_execution ce,
             jsonb_each(ce.eval_outputs) AS e(key, value)
        WHERE ce.test_execution_id = %s
          AND ce.eval_outputs IS NOT NULL
          AND jsonb_typeof(ce.eval_outputs) = 'object'
          AND e.value ? 'output'
          AND e.value ? 'output_type'
    ),

    -- Pass/Fail and score: aggregate to a single avg per metric_name
    scalar_agg AS (
        SELECT
            metric_id,
            metric_name,
            output_type,
            ROUND(AVG(
                CASE
                    WHEN output_type = 'Pass/Fail' AND output_text = 'Passed' THEN 100.0
                    WHEN output_type = 'Pass/Fail' AND output_text = 'Failed' THEN 0.0
                    WHEN output_type = 'score' AND jsonb_typeof(output_raw) IN ('number')
                         THEN (output_text)::numeric * 100
                END
            )::numeric, 1) AS avg_value,
            NULL::text AS choice_value,
            0 AS choice_count
        FROM eval_entries
        WHERE output_type IN ('Pass/Fail', 'score')
        GROUP BY metric_id, metric_name, output_type
    ),

    -- Choices: unnest strings, numbers, and arrays into individual rows
    choice_rows AS (
        -- string values
        SELECT metric_id, metric_name,
               TRIM(BOTH ' []' FROM output_text) AS choice_value
        FROM eval_entries
        WHERE output_type = 'choices' AND jsonb_typeof(output_raw) = 'string'

        UNION ALL

        -- array values: unnest each element
        SELECT metric_id, metric_name,
               elem AS choice_value
        FROM eval_entries,
             jsonb_array_elements_text(output_raw) AS elem
        WHERE output_type = 'choices' AND jsonb_typeof(output_raw) = 'array'
    ),

    choice_agg AS (
        SELECT
            metric_id,
            metric_name,
            'choices' AS output_type,
            NULL::numeric AS avg_value,
            choice_value,
            COUNT(*)::int AS choice_count
        FROM choice_rows
        GROUP BY metric_id, metric_name, choice_value
    ),

    -- Choices that are numeric (no choice_value, just avg the number)
    choice_numeric_agg AS (
        SELECT
            metric_id,
            metric_name,
            'choices' AS output_type,
            ROUND(AVG((output_text)::numeric)::numeric, 1) AS avg_value,
            NULL::text AS choice_value,
            0 AS choice_count
        FROM eval_entries
        WHERE output_type = 'choices' AND jsonb_typeof(output_raw) = 'number'
        GROUP BY metric_id, metric_name
    ),

    -- Choices metrics where every entry errored (output is null). Without
    -- this branch the metric drops out of the response entirely and the UI
    -- renders "No evaluation metrics added"; emitting a zero row lets the
    -- handler register the metric so a zeroed chart card shows instead.
    choice_errored_agg AS (
        SELECT
            metric_id,
            metric_name,
            'choices' AS output_type,
            NULL::numeric AS avg_value,
            NULL::text AS choice_value,
            0 AS choice_count
        FROM eval_entries
        WHERE output_type = 'choices'
        GROUP BY metric_id, metric_name
        HAVING bool_and(
            output_raw IS NULL OR jsonb_typeof(output_raw) = 'null'
        )
    )

    SELECT * FROM scalar_agg
    UNION ALL
    SELECT * FROM choice_agg
    UNION ALL
    SELECT * FROM choice_numeric_agg
    UNION ALL
    SELECT * FROM choice_errored_agg
    """
    params = [str(test_execution_id)]
    return query, params


def get_kpi_metrics_query(test_execution_id):
    """
    Aggregate all KPI metrics for a test execution in a single SQL query.

    Returns counts, averages for voice metrics (direct columns), and
    averages for chat metrics (from conversation_metrics_data JSON field).

    Returns:
        tuple: (query, params)
    """
    query = """
    SELECT
        -- Counts
        COUNT(*) AS total_calls,
        COUNT(*) FILTER (WHERE status = 'pending') AS pending_calls,
        COUNT(*) FILTER (WHERE status = 'registered') AS queued_calls,
        COUNT(*) FILTER (WHERE status = 'failed') AS failed_calls,
        COUNT(*) FILTER (WHERE status = 'completed') AS completed_calls,
        COUNT(*) FILTER (WHERE duration_seconds > 0) AS connected_voice_calls,

        -- Common metrics
        ROUND(AVG(overall_score)::numeric, 1) AS avg_score,
        ROUND(AVG(response_time_ms)::numeric) AS avg_response,
        COALESCE(SUM(duration_seconds), 0) AS total_duration,

        -- Voice metrics (direct columns)
        ROUND(AVG(avg_agent_latency_ms)::numeric) AS avg_agent_latency,
        ROUND(AVG(user_interruption_count)::numeric, 1) AS avg_user_interruption_count,
        ROUND(AVG(user_interruption_rate)::numeric, 2) AS avg_user_interruption_rate,
        ROUND(AVG(user_wpm)::numeric, 1) AS avg_user_wpm,
        ROUND(AVG(bot_wpm)::numeric, 1) AS avg_bot_wpm,
        ROUND(AVG(talk_ratio)::numeric, 2) AS avg_talk_ratio,
        ROUND(AVG(ai_interruption_count)::numeric, 1) AS avg_ai_interruption_count,
        ROUND(AVG(ai_interruption_rate)::numeric, 2) AS avg_ai_interruption_rate,
        ROUND(AVG(avg_stop_time_after_interruption_ms)::numeric) AS avg_stop_time,

        -- Chat metrics (from JSON field)
        ROUND(AVG((conversation_metrics_data->>'total_tokens')::integer)::numeric) AS avg_total_tokens,
        ROUND(AVG((conversation_metrics_data->>'input_tokens')::integer)::numeric) AS avg_input_tokens,
        ROUND(AVG((conversation_metrics_data->>'output_tokens')::integer)::numeric) AS avg_output_tokens,
        ROUND(AVG((conversation_metrics_data->>'avg_latency_ms')::numeric), 1) AS avg_chat_latency_ms,
        ROUND(AVG(COALESCE(
            (conversation_metrics_data->>'turn_count')::numeric,
            (conversation_metrics_data->>'bot_message_count')::numeric
        ))) AS avg_turn_count,
        ROUND(AVG((conversation_metrics_data->>'csat_score')::numeric), 2) AS avg_csat_score
    FROM simulate_call_execution
    WHERE test_execution_id = %s
    """
    params = [str(test_execution_id)]
    return query, params


def get_grouped_call_execution_metrics_query(
    *, select_clause: str, where_clause: str, group_by_clause: str
):
    """
    Build the grouped call execution metrics query used for grouped/aggregated execution tables.

    Note: This intentionally accepts pre-built SQL fragments, as the caller builds dynamic grouping
    columns/filters (including dataset-cell subqueries). Keep behavior identical to the previous
    inline f-string query in `test_execution_utils.py`.
    """
    query = f"""
    SELECT {select_clause},
            COUNT(simulate_call_execution.id) as count,
            AVG(simulate_call_execution.overall_score) as avg_overall_score,
            AVG(simulate_call_execution.response_time_ms) as avg_response_time,
            -- Chat metrics from conversation_metrics_data JSONField
            AVG((simulate_call_execution.conversation_metrics_data->>'total_tokens')::integer) as total_tokens,
            AVG((simulate_call_execution.conversation_metrics_data->>'input_tokens')::integer) as input_tokens,
            AVG((simulate_call_execution.conversation_metrics_data->>'output_tokens')::integer) as output_tokens,
            AVG((simulate_call_execution.conversation_metrics_data->>'avg_latency_ms')::integer) as avg_latency_ms,
            AVG((simulate_call_execution.conversation_metrics_data->>'turn_count')::integer) as turn_count,
            AVG((simulate_call_execution.conversation_metrics_data->>'csat_score')::numeric) as csat_score
    FROM simulate_call_execution
    LEFT JOIN simulate_scenarios ON simulate_call_execution.scenario_id = simulate_scenarios.id
    WHERE {where_clause}
    GROUP BY {group_by_clause}
    """
    return query
