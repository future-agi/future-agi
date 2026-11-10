def get_usage_summary_sql(organization_id, target_year, target_month):
    """
    Generate optimized SQL query for usage summary with CTEs.

    Args:
        organization_id: ID of the organization
        target_year: Target year (e.g., 2025)
        target_month: Target month (1-12)

    Returns:
        SQL query string with placeholders
    """

    sql = """
    WITH
    -- CTE 1: Define date boundaries for current and previous month
    date_boundaries AS (
        SELECT
            DATE_TRUNC('month', MAKE_DATE(%(target_year)s, %(target_month)s, 1)) AS current_month_start,
            DATE_TRUNC('month', MAKE_DATE(%(target_year)s, %(target_month)s, 1)) + INTERVAL '1 month' AS current_month_end,
            DATE_TRUNC('month', MAKE_DATE(%(target_year)s, %(target_month)s, 1) - INTERVAL '1 month') AS prev_month_start,
            DATE_TRUNC('month', MAKE_DATE(%(target_year)s, %(target_month)s, 1)) AS prev_month_end
    ),

    -- CTE 2: Define API call type categories
    api_call_categories AS (
        SELECT
            act.id,
            act.name,
            CASE
                WHEN act.name IN ('observe_add', 'prototype_add') THEN 'traces'
                WHEN act.name IN (
                    'turing_large_evaluator', 'turing_small_evaluator', 'turing_flash_evaluator',
                    'protect_evaluator', 'protect_flash_evaluator', 'code_evaluator',
                    'dataset_evaluation', 'experiment_evaluation', 'optimisation_evaluation'
                ) THEN 'evaluations'
                WHEN act.name = 'error_localizer' THEN 'error_localizations'
                WHEN act.name = 'trace_error_analysis' THEN 'agent_compass'
                WHEN act.name IN ('voice_call', 'text_call') THEN 'simulate'
                ELSE 'other'
            END AS category
        FROM usage_apicalltype act
    ),

    -- CTE 3: Filter base data for current month
    current_month_data AS (
        SELECT
            acl.id,
            acl.deducted_cost,
            acc.category
        FROM usage_apicalllog acl
        INNER JOIN api_call_categories acc ON acl.api_call_type_id = acc.id
        CROSS JOIN date_boundaries db
        WHERE
            acl.organization_id = %(organization_id)s
            AND acl.created_at >= db.current_month_start
            AND acl.created_at < db.current_month_end
            AND acl.status IN ('success', 'processing')
            AND acl.deducted_cost > 0
    ),

    -- CTE 4: Filter base data for previous month
    prev_month_data AS (
        SELECT
            acl.id,
            acl.deducted_cost,
            acc.category
        FROM usage_apicalllog acl
        INNER JOIN api_call_categories acc ON acl.api_call_type_id = acc.id
        CROSS JOIN date_boundaries db
        WHERE
            acl.organization_id = %(organization_id)s
            AND acl.created_at >= db.prev_month_start
            AND acl.created_at < db.prev_month_end
            AND acl.status IN ('success', 'processing')
            AND acl.deducted_cost > 0
    ),

    -- CTE 5: Aggregate current month metrics
    current_month_metrics AS (
        SELECT
            COALESCE(SUM(deducted_cost), 0) AS total_cost,
            COUNT(*) AS total_count,
            COALESCE(SUM(CASE WHEN category = 'traces' THEN deducted_cost ELSE 0 END), 0) AS traces_cost,
            COUNT(CASE WHEN category = 'traces' THEN 1 END) AS traces_count,
            COALESCE(SUM(CASE WHEN category = 'evaluations' THEN deducted_cost ELSE 0 END), 0) AS evaluations_cost,
            COUNT(CASE WHEN category = 'evaluations' THEN 1 END) AS evaluations_count,
            COALESCE(SUM(CASE WHEN category = 'error_localizations' THEN deducted_cost ELSE 0 END), 0) AS error_localizations_cost,
            COUNT(CASE WHEN category = 'error_localizations' THEN 1 END) AS error_localizations_count,
            COALESCE(SUM(CASE WHEN category = 'agent_compass' THEN deducted_cost ELSE 0 END), 0) AS agent_compass_cost,
            COUNT(CASE WHEN category = 'agent_compass' THEN 1 END) AS agent_compass_count,
            COALESCE(SUM(CASE WHEN category = 'simulate' THEN deducted_cost ELSE 0 END), 0) AS simulate_cost,
            COUNT(CASE WHEN category = 'simulate' THEN 1 END) AS simulate_count
        FROM current_month_data
    ),

    -- CTE 6: Aggregate previous month metrics
    prev_month_metrics AS (
        SELECT
            COALESCE(SUM(deducted_cost), 0) AS total_cost,
            COUNT(*) AS total_count,
            COALESCE(SUM(CASE WHEN category = 'traces' THEN deducted_cost ELSE 0 END), 0) AS traces_cost,
            COUNT(CASE WHEN category = 'traces' THEN 1 END) AS traces_count,
            COALESCE(SUM(CASE WHEN category = 'evaluations' THEN deducted_cost ELSE 0 END), 0) AS evaluations_cost,
            COUNT(CASE WHEN category = 'evaluations' THEN 1 END) AS evaluations_count,
            COALESCE(SUM(CASE WHEN category = 'error_localizations' THEN deducted_cost ELSE 0 END), 0) AS error_localizations_cost,
            COUNT(CASE WHEN category = 'error_localizations' THEN 1 END) AS error_localizations_count,
            COALESCE(SUM(CASE WHEN category = 'agent_compass' THEN deducted_cost ELSE 0 END), 0) AS agent_compass_cost,
            COUNT(CASE WHEN category = 'agent_compass' THEN 1 END) AS agent_compass_count,
            COALESCE(SUM(CASE WHEN category = 'simulate' THEN deducted_cost ELSE 0 END), 0) AS simulate_cost,
            COUNT(CASE WHEN category = 'simulate' THEN 1 END) AS simulate_count
        FROM prev_month_data
    ),

    -- CTE 7: Count total workspaces for the organization
    workspace_count AS (
        SELECT COUNT(DISTINCT id) AS total_workspaces
        FROM accounts_workspace
        WHERE organization_id = %(organization_id)s
    )

    -- Final SELECT: Combine current and previous month metrics with workspace count
    SELECT
        -- Current month metrics (rounded to 3 decimal places)
        ROUND(cm.total_cost::numeric, 3) AS current_total_cost,
        cm.total_count AS current_total_count,
        ROUND(cm.traces_cost::numeric, 3) AS current_traces_cost,
        cm.traces_count AS current_traces_count,
        ROUND(cm.evaluations_cost::numeric, 3) AS current_evaluations_cost,
        cm.evaluations_count AS current_evaluations_count,
        ROUND(cm.error_localizations_cost::numeric, 3) AS current_error_localizations_cost,
        cm.error_localizations_count AS current_error_localizations_count,
        ROUND(cm.agent_compass_cost::numeric, 3) AS current_agent_compass_cost,
        cm.agent_compass_count AS current_agent_compass_count,
        ROUND(cm.simulate_cost::numeric, 3) AS current_simulate_cost,
        cm.simulate_count AS current_simulate_count,

        -- Previous month metrics (rounded to 3 decimal places)
        ROUND(pm.total_cost::numeric, 3) AS prev_total_cost,
        pm.total_count AS prev_total_count,
        ROUND(pm.traces_cost::numeric, 3) AS prev_traces_cost,
        pm.traces_count AS prev_traces_count,
        ROUND(pm.evaluations_cost::numeric, 3) AS prev_evaluations_cost,
        pm.evaluations_count AS prev_evaluations_count,
        ROUND(pm.error_localizations_cost::numeric, 3) AS prev_error_localizations_cost,
        pm.error_localizations_count AS prev_error_localizations_count,
        ROUND(pm.agent_compass_cost::numeric, 3) AS prev_agent_compass_cost,
        pm.agent_compass_count AS prev_agent_compass_count,
        ROUND(pm.simulate_cost::numeric, 3) AS prev_simulate_cost,
        pm.simulate_count AS prev_simulate_count,

    -- Calculate month-over-month changes (percentage)
    CASE
        WHEN pm.total_cost > 0 THEN
            ROUND(((cm.total_cost - pm.total_cost) / pm.total_cost * 100)::numeric, 2)
        WHEN cm.total_cost > 0 THEN
            100.00
        ELSE
            0.00
    END AS total_cost_change_pct,

    CASE
        WHEN pm.total_count > 0 THEN
            ROUND(((cm.total_count::numeric - pm.total_count::numeric) / pm.total_count::numeric * 100)::numeric, 2)
        WHEN cm.total_count > 0 THEN
            100.00
        ELSE
            0.00
    END AS total_count_change_pct,

        -- Has data flags
        CASE WHEN cm.total_count > 0 THEN TRUE ELSE FALSE END AS current_month_has_data,
        CASE WHEN pm.total_count > 0 THEN TRUE ELSE FALSE END AS prev_month_has_data,

        -- Workspace count
        wc.total_workspaces AS total_workspaces_count

    FROM current_month_metrics cm
    CROSS JOIN prev_month_metrics pm
    CROSS JOIN workspace_count wc;
    """

    return sql


def get_usage_summary_sql_params(organization_id, target_year, target_month):
    """
    Get parameters for the SQL query.

    Args:
        organization_id: ID of the organization
        target_year: Target year (e.g., 2025)
        target_month: Target month (1-12)

    Returns:
        Dictionary of parameters
    """
    return {
        "organization_id": organization_id,
        "target_year": target_year,
        "target_month": target_month,
    }


def get_workspace_usage_sql(organization_id, target_year=None, target_month=None):
    """
    Generate optimized SQL query for workspace-level usage metrics.

    Args:
        organization_id: ID of the organization
        target_year: Optional target year (e.g., 2025). If None, returns all-time data.
        target_month: Optional target month (1-12). If None, returns all-time data.

    Returns:
        SQL query string with placeholders
    """

    # Build date filter clause based on whether year/month are provided
    if target_year is not None and target_month is not None:
        date_filter = """
            AND acl.created_at >= DATE_TRUNC('month', MAKE_DATE(%(target_year)s, %(target_month)s, 1))
            AND acl.created_at < DATE_TRUNC('month', MAKE_DATE(%(target_year)s, %(target_month)s, 1)) + INTERVAL '1 month'
        """
    else:
        date_filter = ""

    sql = f"""
    WITH
    -- CTE 1: Define API call type categories
    api_call_categories AS (
        SELECT
            act.id,
            act.name,
            CASE
                -- Trace types
                WHEN act.name IN ('observe_add', 'prototype_add') THEN 'traces'

                -- Evaluation types
                WHEN act.name IN (
                    'turing_large_evaluator',
                    'turing_small_evaluator',
                    'turing_flash_evaluator',
                    'protect_evaluator',
                    'protect_flash_evaluator',
                    'dataset_evaluation',
                    'experiment_evaluation',
                    'optimisation_evaluation'
                ) THEN 'evaluations'

                -- Error localization types
                WHEN act.name = 'error_localizer' THEN 'error_localizations'

                -- Agent compass types
                WHEN act.name = 'trace_error_analysis' THEN 'agent_compass'

                -- Simulate types
                WHEN act.name IN ('voice_call', 'text_call') THEN 'simulate'

                -- Other types
                ELSE 'other'
            END AS category
        FROM usage_apicalltype act
    ),

    -- CTE 2: Filter and categorize data for the organization (with optional date filter)
    workspace_data AS (
        SELECT
            acl.workspace_id,
            acl.deducted_cost,
            acc.category
        FROM usage_apicalllog acl
        INNER JOIN api_call_categories acc ON acl.api_call_type_id = acc.id
        WHERE
            acl.organization_id = %(organization_id)s
            AND acl.status IN ('success', 'processing')
            AND acl.deducted_cost > 0
            AND acl.workspace_id IS NOT NULL
            {date_filter}
    ),

    -- CTE 3: Aggregate metrics per workspace
    workspace_metrics AS (
        SELECT
            workspace_id,

            -- Total metrics
            COALESCE(SUM(deducted_cost), 0) AS total_cost,
            COUNT(*) AS total_count,

            -- Traces metrics
            COALESCE(SUM(CASE WHEN category = 'traces' THEN deducted_cost ELSE 0 END), 0) AS traces_cost,
            COUNT(CASE WHEN category = 'traces' THEN 1 END) AS traces_count,

            -- Evaluations metrics
            COALESCE(SUM(CASE WHEN category = 'evaluations' THEN deducted_cost ELSE 0 END), 0) AS evaluations_cost,
            COUNT(CASE WHEN category = 'evaluations' THEN 1 END) AS evaluations_count,

            -- Error localizations metrics
            COALESCE(SUM(CASE WHEN category = 'error_localizations' THEN deducted_cost ELSE 0 END), 0) AS error_localizations_cost,
            COUNT(CASE WHEN category = 'error_localizations' THEN 1 END) AS error_localizations_count,

            -- Agent compass metrics
            COALESCE(SUM(CASE WHEN category = 'agent_compass' THEN deducted_cost ELSE 0 END), 0) AS agent_compass_cost,
            COUNT(CASE WHEN category = 'agent_compass' THEN 1 END) AS agent_compass_count,

            -- Simulate metrics
            COALESCE(SUM(CASE WHEN category = 'simulate' THEN deducted_cost ELSE 0 END), 0) AS simulate_cost,
            COUNT(CASE WHEN category = 'simulate' THEN 1 END) AS simulate_count
        FROM workspace_data
        GROUP BY workspace_id
    )

    -- Final SELECT: Join with workspace info and return metrics
    SELECT
        w.id AS workspace_id,
        COALESCE(NULLIF(w.display_name, ''), w.name) AS workspace_name,
        ROUND(COALESCE(wm.total_cost, 0)::numeric, 3) AS total_cost,
        COALESCE(wm.total_count, 0) AS total_count,
        ROUND(COALESCE(wm.traces_cost, 0)::numeric, 3) AS traces_cost,
        COALESCE(wm.traces_count, 0) AS traces_count,
        ROUND(COALESCE(wm.evaluations_cost, 0)::numeric, 3) AS evaluations_cost,
        COALESCE(wm.evaluations_count, 0) AS evaluations_count,
        ROUND(COALESCE(wm.error_localizations_cost, 0)::numeric, 3) AS error_localizations_cost,
        COALESCE(wm.error_localizations_count, 0) AS error_localizations_count,
        ROUND(COALESCE(wm.agent_compass_cost, 0)::numeric, 3) AS agent_compass_cost,
        COALESCE(wm.agent_compass_count, 0) AS agent_compass_count,
        ROUND(COALESCE(wm.simulate_cost, 0)::numeric, 3) AS simulate_cost,
        COALESCE(wm.simulate_count, 0) AS simulate_count
    FROM accounts_workspace w
    LEFT JOIN workspace_metrics wm ON w.id = wm.workspace_id
    WHERE w.organization_id = %(organization_id)s
    ORDER BY wm.total_cost DESC NULLS LAST, w.name ASC;
    """

    return sql


def get_workspace_usage_sql_params(
    organization_id, target_year=None, target_month=None
):
    """
    Get parameters for the workspace usage SQL query.

    Args:
        organization_id: ID of the organization
        target_year: Optional target year (e.g., 2025)
        target_month: Optional target month (1-12)

    Returns:
        Dictionary of parameters
    """
    params = {"organization_id": organization_id}

    # Add date parameters only if provided
    if target_year is not None and target_month is not None:
        params["target_year"] = target_year
        params["target_month"] = target_month

    return params
