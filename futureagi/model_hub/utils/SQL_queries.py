from datetime import datetime

import pandas as pd
from django.db import connection

from accounts.models.workspace import Workspace
from tracer.models.observation_span import EndUser

# Shared SQL expression for calculating model costs
# This prevents duplication and ensures consistency across all cost calculations
MODEL_COST_CALCULATION_SQL = """
    CASE
        -- GPT-4o models
        WHEN os.model IN (
            'gpt-4o',
            'gpt-4o-audio-preview',
            'gpt-4o-2024-11-20',
            'gpt-4o-2024-08-06'
        ) THEN (os.prompt_tokens * 0.0025 / 1000) + (os.completion_tokens * 0.01 / 1000)
        WHEN os.model = 'gpt-4o-2024-05-13'
            THEN (os.prompt_tokens * 0.005 / 1000) + (os.completion_tokens * 0.015 / 1000)

        -- GPT-4o-mini models
        WHEN os.model IN (
            'gpt-4o-mini',
            'gpt-4o-mini-2024-07-18'
        ) THEN (os.prompt_tokens * 0.00015 / 1000) + (os.completion_tokens * 0.0006 / 1000)

        -- O1 models
        WHEN os.model IN (
            'o1',
            'o1-2024-12-17',
            'o1-preview',
            'o1-preview-2024-09-12'
        ) THEN (os.prompt_tokens * 0.015 / 1000) + (os.completion_tokens * 0.06 / 1000)
        WHEN os.model IN (
            'o1-mini',
            'o1-mini-2024-09-12'
        ) THEN (os.prompt_tokens * 0.003 / 1000) + (os.completion_tokens * 0.012 / 1000)

        -- GPT-4 models
        WHEN os.model = 'chatgpt-4o-latest'
            THEN (os.prompt_tokens * 0.005 / 1000) + (os.completion_tokens * 0.015 / 1000)
        WHEN os.model IN (
            'gpt-4-turbo',
            'gpt-4-turbo-2024-04-09'
        ) THEN (os.prompt_tokens * 0.01 / 1000) + (os.completion_tokens * 0.03 / 1000)
        WHEN os.model = 'gpt-4'
            THEN (os.prompt_tokens * 0.03 / 1000) + (os.completion_tokens * 0.06 / 1000)
        WHEN os.model = 'gpt-4-32k'
            THEN (os.prompt_tokens * 0.06 / 1000) + (os.completion_tokens * 0.12 / 1000)
        WHEN os.model IN (
            'gpt-4-0125-preview',
            'gpt-4-1106-preview',
            'gpt-4-vision-preview'
        ) THEN (os.prompt_tokens * 0.01 / 1000) + (os.completion_tokens * 0.03 / 1000)

        -- GPT-3.5 models
        WHEN os.model = 'gpt-3.5-turbo-0125'
            THEN (os.prompt_tokens * 0.0005 / 1000) + (os.completion_tokens * 0.0015 / 1000)
        WHEN os.model = 'gpt-3.5-turbo-instruct'
            THEN (os.prompt_tokens * 0.0015 / 1000) + (os.completion_tokens * 0.002 / 1000)
        WHEN os.model = 'gpt-3.5-turbo-1106'
            THEN (os.prompt_tokens * 0.001 / 1000) + (os.completion_tokens * 0.002 / 1000)
        WHEN os.model IN (
            'gpt-3.5-turbo-0613',
            'gpt-3.5-turbo-0301'
        ) THEN (os.prompt_tokens * 0.0015 / 1000) + (os.completion_tokens * 0.002 / 1000)
        WHEN os.model = 'gpt-3.5-turbo-16k-0613'
            THEN (os.prompt_tokens * 0.003 / 1000) + (os.completion_tokens * 0.004 / 1000)

        -- Other models
        WHEN os.model = 'davinci-002'
            THEN (os.prompt_tokens * 0.002 / 1000) + (os.completion_tokens * 0.002 / 1000)
        WHEN os.model = 'babbage-002'
            THEN (os.prompt_tokens * 0.0004 / 1000) + (os.completion_tokens * 0.0004 / 1000)

        -- Default to gpt-4o-mini pricing for unknown models
        ELSE (os.prompt_tokens * 0.00015 / 1000) + (os.completion_tokens * 0.0006 / 1000)
    END
"""


def build_sql_filters(filters=[], column_map={}):
    filter_clauses = []
    params = []
    for f in filters:
        if f["column_id"] == "created_at":
            continue
        col = column_map.get(f["column_id"])
        if not col:
            raise Exception("Invalid Column for filter")
        config = f["filter_config"]
        op = config["filter_op"]
        values = config["filter_value"]
        data_type = config.get("filter_type", "number")  # default to number

        if data_type == "text":
            if op == "contains":
                filter_clauses.append(f"{col} ILIKE %s")
                params.append(f"%{values}%")
            elif op == "not_contains":
                filter_clauses.append(f"{col} NOT ILIKE %s")
                params.append(f"%{values}%")
            elif op == "equals":
                filter_clauses.append(f"{col} ILIKE %s")
                params.append(values)
            elif op == "not_equals":
                filter_clauses.append(f"{col} NOT ILIKE %s")
                params.append(values)
            elif op == "starts_with":
                filter_clauses.append(f"{col} ILIKE %s")
                params.append(f"{values}%")
            elif op == "ends_with":
                filter_clauses.append(f"{col} ILIKE %s")
                params.append(f"%{values}")
            elif op == "in":
                placeholders = ", ".join(["%s"] * len(values))
                filter_clauses.append(f"{col} IN ({placeholders})")
                params.extend(values)
            elif op == "not_in":
                placeholders = ", ".join(["%s"] * len(values))
                filter_clauses.append(f"{col} NOT IN ({placeholders})")
                params.extend(values)

        elif data_type == "number":
            if op == "between":
                filter_clauses.append(f"{col} BETWEEN %s AND %s")
                params.extend([values[0], values[1]])
            elif op == "not_between":
                filter_clauses.append(f"{col} NOT BETWEEN %s AND %s")
                params.extend([values[0], values[1]])
            elif op == "equals":
                filter_clauses.append(f"{col} = %s")
                params.append(values)
            elif op == "not_equals":
                filter_clauses.append(f"{col} != %s")
                params.append(values)
            elif op == "greater_than":
                filter_clauses.append(f"{col} > %s")
                params.append(values)
            elif op == "greater_than_or_equal":
                filter_clauses.append(f"{col} >= %s")
                params.append(values)
            elif op == "less_than":
                filter_clauses.append(f"{col} < %s")
                params.append(values)
            elif op == "less_than_or_equal":
                filter_clauses.append(f"{col} <= %s")
                params.append(values)

        elif data_type == "datetime":
            if isinstance(values, list) and len(values) > 1:
                values = ",".join(values)
            sql, dt_params = build_datetime_filter_sql(values, op, col)
            filter_clauses.append(sql)
            params.extend(dt_params)

    sql_query = ""
    if filter_clauses:
        sql_query = " AND " + " AND ".join(filter_clauses)

    return sql_query, params


def parse_datetime_iso(dt_str):
    """
    Parses ISO 8601 datetime string like '2025-08-05T18:30:00.000Z' into datetime object.
    """
    if dt_str.endswith("Z"):
        dt_str = dt_str[:-1]  # remove 'Z'
    try:
        return datetime.strptime(dt_str, "%Y-%m-%dT%H:%M:%S.%f")
    except ValueError:
        return datetime.strptime(dt_str, "%Y-%m-%dT%H:%M:%S")


def build_datetime_filter_sql(
    value, filter_op="equals", col_name="start_time", where_or_and="AND"
):
    """
    Build SQL filter snippet for datetime filtering.

    Args:
        value (str): single datetime or comma-separated datetime range string
        filter_op (str): filter operation ("equals", "not_equals", "between", etc.)
        col_name (str): column name to filter on
        where_or_and (str): "WHERE" or "AND" prefix for the SQL snippet

    Returns:
        tuple: (sql_snippet: str, params: list)
    """
    if not value:
        return "", []

    op_map = {
        "equals": "=",
        "not_equals": "!=",
        "greater_than": ">",
        "less_than": "<",
        "greater_than_or_equal": ">=",
        "less_than_or_equal": "<=",
    }

    try:
        if filter_op in ["between", "not_between"]:
            parts = value.split(",")
            if len(parts) != 2:
                raise ValueError("Between operation requires two datetime values")

            date1 = parse_datetime_iso(parts[0].strip())
            date2 = parse_datetime_iso(parts[1].strip())

            if filter_op == "between":
                sql = f"{col_name} BETWEEN %s AND %s"
            else:
                sql = f"{col_name} NOT BETWEEN %s AND %s"

            return sql, [date1, date2]

        # single date operations
        if filter_op not in op_map:
            raise ValueError(f"Invalid filter operation: {filter_op}")

        date = parse_datetime_iso(value.strip())

        if filter_op == "not_equals":
            sql = f"{col_name} != %s"
        else:
            sql = f"{col_name} {op_map[filter_op]} %s"

        return sql, [date]

    except ValueError as e:
        raise ValueError(  # noqa: B904
            f"Invalid datetime format. Expected 'YYYY-MM-DD HH:MM:SS'. Error: {str(e)}"
        )


class SQLQueryHandler:
    @staticmethod
    def build_ilike_pattern(search_key: str) -> str:
        """Escape LIKE wildcards so user input is treated as a literal substring."""
        escaped = (
            search_key.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        return f"%{escaped}%"

    @classmethod
    def execute_query(cls, query, params=None, fetch_results=True, return_df=False):
        """Generic method to execute SQL queries and return results"""
        with connection.cursor() as cursor:
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)
            if fetch_results:
                rows = cursor.fetchall()
                if return_df:
                    columns = [col[0] for col in cursor.description]
                    df = pd.DataFrame(rows, columns=columns)
                    return df
                return rows
            else:
                return None

    @classmethod
    def search_cells_by_text(cls, search_key, dataset_id):
        """Searches for cells containing the search_key in their value and returns match indices."""

        # SQL to create the function if it doesn't exist
        create_function_sql = """
        CREATE OR REPLACE FUNCTION get_substring_positions(main_text TEXT, sub_text TEXT)
        RETURNS TABLE(start_pos INT) AS $$
        DECLARE
            pos INT := 1;
            sub_text_len INT := LENGTH(sub_text);
            main_text_len INT := LENGTH(main_text);
        BEGIN
            WHILE pos <= main_text_len - sub_text_len + 1 LOOP
                IF LOWER(SUBSTRING(main_text FROM pos FOR sub_text_len)) = LOWER(sub_text) THEN
                    start_pos := pos;
                    RETURN NEXT;
                END IF;
                pos := pos + 1;
            END LOOP;
        END;
        $$ LANGUAGE plpgsql IMMUTABLE;
        """

        # Execute the function creation query
        cls.execute_query(create_function_sql, fetch_results=False)

        # Main query to search cells
        pattern = cls.build_ilike_pattern(search_key)
        query = """
        SELECT
            id,
            TRUE AS key_exists,
            ARRAY(
                SELECT
                    ARRAY[start_pos - 1, start_pos + LENGTH(%s) - 1]
                    FROM
                        get_substring_positions(value, %s)
                ) AS indices
        FROM
            model_hub_cell
        WHERE
            value ILIKE %s ESCAPE '\\'
            AND deleted = FALSE
            AND dataset_id = %s
            AND status = 'pass';
        """

        return cls.execute_query(query, (search_key, search_key, pattern, dataset_id))

    @classmethod
    def get_all_templates(
        cls,
        used_template_ids,
        search_name,
        org_id,
        sort_order,
        sort_by,
        limit,
        offset,
        workspace_id=None,
    ):
        sort_dict = {
            "last_30_run": "last30run",
            "updated_at": "updated_at",
            "eval_template_name": "template_name",
            # Legacy camelCase support
            "last30Run": "last30run",
            "updatedAt": "updated_at",
            "evalTemplateName": "template_name",
        }

        sort_order = sort_order if sort_order else "DESC"
        sort_by = (
            sort_dict.get(sort_by, sort_by)
            if sort_dict.get(sort_by, sort_by)
            else "last30run"
        )
        limit = limit if limit else 10
        offset = offset if offset else 0
        query = f"""
        WITH filtered_templates AS (
            SELECT *
            FROM model_hub_evaltemplate
            WHERE NOT deleted
            AND name != 'deterministic_evals'
            AND (%s IS NULL OR name ILIKE '%%' || %s || '%%')
            AND (%s IS NULL OR id = ANY(%s))
        ),
        filtered_logs AS (
            SELECT *
            FROM usage_apicalllog
            WHERE NOT deleted
            AND organization_id = %s
            AND (
                %s IS NULL
                OR workspace_id = %s
                OR (%s::boolean = TRUE AND workspace_id IS NULL)
            )
            AND created_at >= (CURRENT_DATE - INTERVAL '30 days')
        ),
        joined AS (
            SELECT
                t.id AS template_id,
                t.name AS template_name,
                t.updated_at AS template_updated_at,
                l.id AS log_id,
                l.status,
                t.config as template_config,
                t.multi_choice as template_multi_choice
            FROM filtered_templates t
            LEFT JOIN filtered_logs l
                ON t.id::text = l.source_id
        ),
        agg AS (
            SELECT
                template_id,
                template_name,
                COUNT(log_id) AS last30run,
                template_updated_at AS updated_at,
                template_config,
                template_multi_choice
            FROM joined
            GROUP BY template_id, template_name, template_updated_at, template_config, template_multi_choice
        )
        SELECT
            template_id AS id,
            template_name AS "evalTemplateName",
            last30run AS "last30Run",
            updated_at AS "updatedAt",
            COUNT(*) OVER() AS total_count
        FROM agg
        ORDER BY {sort_by} {sort_order}, updated_at {sort_order}
        LIMIT %s
        OFFSET %s;
        """
        is_default_ws = None
        if workspace_id:
            ws = Workspace.objects.filter(id=workspace_id).first()
            is_default_ws = ws.is_default if ws else None

        params = [
            search_name,  # search (for IS NULL)
            search_name,  # search (for ILIKE)
            (
                used_template_ids if used_template_ids else None
            ),  # Check if the list is empty
            used_template_ids if used_template_ids else None,
            org_id,  # org_id
            workspace_id,  # ws filter: parameter 1
            workspace_id,  # ws filter: parameter 2
            is_default_ws,  # ws filter: is default -> include NULLs
            limit,  # limit
            offset,  # offset
        ]

        return cls.execute_query(query, params)

    @classmethod
    def get_cells_percentile_distribution(
        cls, dataset_id, eval_template_id, user_eval_metric_ids=None, row_ids=None
    ):
        """
        Fetches cells based on UserEvalMetric IDs and calculates percentile distributions.

        Args:
            dataset_id: UUID of the dataset
            eval_template_id: UUID of the evaluation template
            user_eval_metric_ids: Optional list of UserEvalMetric IDs to filter by

        Returns:
            List of tuples containing percentile distribution data grouped by column
        """

        # Build the query with conditional filtering
        user_eval_metrics_condition = ""
        row_ids_condition = ""
        params = [dataset_id, eval_template_id]

        if user_eval_metric_ids and len(user_eval_metric_ids) > 0:
            user_eval_metrics_condition = "AND uem.id = ANY(%s)"
            params.append(user_eval_metric_ids)

        params.append(dataset_id)

        if row_ids and len(row_ids) > 0:
            row_ids_condition = "AND c.row_id = ANY(%s)"
            params.append(row_ids)

        query = rf"""
        WITH user_eval_metrics AS (
            -- Step 1: Fetch all UserEvalMetric IDs with given dataset_id and eval_template_id
            SELECT
                uem.id as user_eval_metric_id,
                uem.name as metric_name
            FROM model_hub_userevalmetric uem
            WHERE uem.dataset_id = %s
            AND uem.template_id = %s
            AND uem.deleted = FALSE
            {user_eval_metrics_condition}
        ),
        relevant_cells AS (
            -- Step 2: Fetch all cells whose dataset_id matches and column.source_id
            -- either equals user_eval_metric_id or contains eval_metric_id
            SELECT
                c.id as cell_id,
                c.value,
                c.column_id,
                col.name as column_name,
                col.source_id as column_source_id,
                uem.user_eval_metric_id,
                uem.metric_name
            FROM model_hub_cell c
            INNER JOIN model_hub_column col ON c.column_id = col.id
            INNER JOIN user_eval_metrics uem ON (
                col.source_id = uem.user_eval_metric_id::text
                OR col.source_id LIKE '%%' || uem.user_eval_metric_id::text || '%%'
            )
            WHERE c.dataset_id = %s
            AND c.value IS NOT NULL
            AND c.status = 'pass'
            AND c.value != ''
            AND col.source != 'evaluation_reason'
            AND col.source != 'experiment_evaluation'
            AND col.source != 'experiment'
            {row_ids_condition}
        ),
        numeric_cells AS (
            -- Convert value to numeric for percentile calculations
            SELECT
                cell_id,
                column_id,
                column_name,
                user_eval_metric_id,
                metric_name,
                CASE
                    WHEN value ~ '^[0-9]+(\.[0-9]+)?$' THEN CAST(value AS NUMERIC)
                    ELSE NULL
                END as numeric_value,
                value as original_value
            FROM relevant_cells
            WHERE value ~ '^[0-9]+(\.[0-9]+)?$'  -- Only numeric values with proper decimal format
        ),
        column_percentiles AS (
            -- Step 3: Group by column and calculate percentiles
            SELECT
                column_id,
                column_name,
                user_eval_metric_id,
                metric_name,
                COUNT(*) as total_cells,
                ROUND(AVG(numeric_value), 2) as column_avg,
                ROUND(CAST(PERCENTILE_CONT(0.05) WITHIN GROUP (ORDER BY numeric_value) AS NUMERIC), 2) as p5,
                ROUND(CAST(PERCENTILE_CONT(0.10) WITHIN GROUP (ORDER BY numeric_value) AS NUMERIC), 2) as p10,
                ROUND(CAST(PERCENTILE_CONT(0.20) WITHIN GROUP (ORDER BY numeric_value) AS NUMERIC), 2) as p20,
                ROUND(CAST(PERCENTILE_CONT(0.30) WITHIN GROUP (ORDER BY numeric_value) AS NUMERIC), 2) as p30,
                ROUND(CAST(PERCENTILE_CONT(0.40) WITHIN GROUP (ORDER BY numeric_value) AS NUMERIC), 2) as p40,
                ROUND(CAST(PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY numeric_value) AS NUMERIC), 2) as p50,
                ROUND(CAST(PERCENTILE_CONT(0.60) WITHIN GROUP (ORDER BY numeric_value) AS NUMERIC), 2) as p60,
                ROUND(CAST(PERCENTILE_CONT(0.70) WITHIN GROUP (ORDER BY numeric_value) AS NUMERIC), 2) as p70,
                ROUND(CAST(PERCENTILE_CONT(0.80) WITHIN GROUP (ORDER BY numeric_value) AS NUMERIC), 2) as p80,
                ROUND(CAST(PERCENTILE_CONT(0.90) WITHIN GROUP (ORDER BY numeric_value) AS NUMERIC), 2) as p90,
                ROUND(CAST(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY numeric_value) AS NUMERIC), 2) as p95,
                ROUND(CAST(PERCENTILE_CONT(1.00) WITHIN GROUP (ORDER BY numeric_value) AS NUMERIC), 2) as p100,
                ROUND(MIN(numeric_value), 2) as min_value,
                ROUND(MAX(numeric_value), 2) as max_value
            FROM numeric_cells
            GROUP BY column_id, column_name, user_eval_metric_id, metric_name
        ),
        overall_stats AS (
            -- Calculate overall average across all cells
            SELECT
                COUNT(*) as total_cells_overall,
                ROUND(AVG(numeric_value), 2) as overall_avg,
                ROUND(MIN(numeric_value), 2) as overall_min,
                ROUND(MAX(numeric_value), 2) as overall_max
            FROM numeric_cells
        )
        SELECT
            cp.column_id,
            cp.column_name,
            cp.user_eval_metric_id,
            cp.metric_name,
            cp.total_cells,
            cp.column_avg,
            cp.p5,
            cp.p10,
            cp.p20,
            cp.p30,
            cp.p40,
            cp.p50,
            cp.p60,
            cp.p70,
            cp.p80,
            cp.p90,
            cp.p95,
            cp.p100,
            cp.min_value,
            cp.max_value,
            os.total_cells_overall,
            os.overall_avg,
            os.overall_min,
            os.overall_max
        FROM column_percentiles cp
        CROSS JOIN overall_stats os
        ORDER BY cp.column_name, cp.metric_name;
        """

        # Add dataset_id for the relevant_cells CTE

        return cls.execute_query(query, params)

    @classmethod
    def get_cells_pass_fail_rates(
        cls, dataset_id, eval_template_id, user_eval_metric_ids=None, row_ids=None
    ):
        """
        Fetches cells based on UserEvalMetric IDs and calculates pass/fail rates.

        Args:
            dataset_id: UUID of the dataset
            eval_template_id: UUID of the evaluation template

        Returns:
            List of tuples containing pass/fail rate data grouped by column
        """
        user_eval_metrics_condition = ""
        row_ids_condition = ""
        params = [dataset_id, eval_template_id]

        if user_eval_metric_ids and len(user_eval_metric_ids) > 0:
            user_eval_metrics_condition = "AND uem.id = ANY(%s)"
            params.append(user_eval_metric_ids)

        # Add dataset_id for the relevant_cells CTE
        params.append(dataset_id)

        if row_ids and len(row_ids) > 0:
            row_ids_condition = "AND c.row_id = ANY(%s)"
            params.append(row_ids)

        query = f"""
        WITH user_eval_metrics AS (
            -- Step 1: Fetch all UserEvalMetric IDs with given dataset_id and eval_template_id
            SELECT
                uem.id as user_eval_metric_id,
                uem.name as metric_name
            FROM model_hub_userevalmetric uem
            WHERE uem.dataset_id = %s
            AND uem.template_id = %s
            AND uem.deleted = FALSE
            {user_eval_metrics_condition}
        ),
        relevant_cells AS (
            -- Step 2: Fetch all cells whose dataset_id matches and column.source_id
            -- either equals user_eval_metric_id or contains eval_metric_id
            SELECT
                c.id as cell_id,
                c.value,
                c.column_id,
                col.name as column_name,
                col.source_id as column_source_id,
                uem.user_eval_metric_id,
                uem.metric_name
            FROM model_hub_cell c
            INNER JOIN model_hub_column col ON c.column_id = col.id
            INNER JOIN user_eval_metrics uem ON (
                col.source_id = uem.user_eval_metric_id::text
                OR col.source_id LIKE '%%' || uem.user_eval_metric_id::text || '%%'
            )
            WHERE c.dataset_id = %s
            AND c.value IS NOT NULL
            AND c.status = 'pass'
            AND c.value != ''
            AND col.source != 'evaluation_reason'
            AND col.source != 'experiment_evaluation'
            AND col.source != 'experiment'
            {row_ids_condition}
        ),
        pass_fail_counts AS (
            -- Step 3: Group by column and calculate pass/fail counts
            SELECT
                column_id,
                column_name,
                user_eval_metric_id,
                metric_name,
                COUNT(*) as total_cells,
                COUNT(CASE WHEN LOWER(TRIM(value)) = 'passed' THEN 1 END) as passed_count,
                COUNT(CASE WHEN LOWER(TRIM(value)) != 'passed' THEN 1 END) as failed_count
            FROM relevant_cells
            GROUP BY column_id, column_name, user_eval_metric_id, metric_name
        ),
        column_rates AS (
            -- Calculate pass and fail rates
            SELECT
                column_id,
                column_name,
                user_eval_metric_id,
                metric_name,
                total_cells,
                passed_count,
                failed_count,
                CASE
                    WHEN total_cells > 0 THEN ROUND((passed_count::NUMERIC / total_cells::NUMERIC) * 100, 2)
                    ELSE 0
                END as pass_rate,
                CASE
                    WHEN total_cells > 0 THEN ROUND((failed_count::NUMERIC / total_cells::NUMERIC) * 100, 2)
                    ELSE 0
                END as fail_rate
            FROM pass_fail_counts
        ),
        overall_stats AS (
            -- Calculate overall pass rate across all groups
            SELECT
                SUM(total_cells) as total_cells_overall,
                SUM(passed_count) as total_passed_overall,
                SUM(failed_count) as total_failed_overall,
                CASE
                    WHEN SUM(total_cells) > 0 THEN ROUND((SUM(passed_count)::NUMERIC / SUM(total_cells)::NUMERIC) * 100, 2)
                    ELSE 0
                END as overall_pass_rate
            FROM pass_fail_counts
        )
        SELECT
            cr.column_id,
            cr.column_name,
            cr.user_eval_metric_id,
            cr.metric_name,
            cr.total_cells,
            cr.passed_count,
            cr.failed_count,
            cr.pass_rate,
            cr.fail_rate,
            os.total_cells_overall,
            os.total_passed_overall,
            os.total_failed_overall,
            os.overall_pass_rate
        FROM column_rates cr
        CROSS JOIN overall_stats os
        ORDER BY cr.column_name, cr.metric_name;
        """

        return cls.execute_query(query, params)

    @classmethod
    def get_cells_choices_analysis(
        cls,
        dataset_id,
        eval_template_id,
        choices,
        user_eval_metric_ids=None,
        row_ids=None,
    ):
        """
        Fetches cells based on UserEvalMetric IDs and analyzes choice distributions.

        Args:
            dataset_id: UUID of the dataset
            eval_template_id: UUID of the evaluation template
            choices: List of choice strings to analyze

        Returns:
            List of tuples containing choice analysis data grouped by column
        """
        user_eval_metrics_condition = ""
        row_ids_condition = ""
        params = [dataset_id, eval_template_id]

        if user_eval_metric_ids and len(user_eval_metric_ids) > 0:
            user_eval_metrics_condition = "AND uem.id = ANY(%s)"
            params.append(user_eval_metric_ids)

        # Add dataset_id for the relevant_cells CTE
        params.append(dataset_id)

        if row_ids and len(row_ids) > 0:
            row_ids_condition = "AND c.row_id = ANY(%s)"
            params.append(row_ids)

        # Convert choices list to SQL array format
        choices_array = "ARRAY[" + ",".join([f"'{choice}'" for choice in choices]) + "]"

        # Check if the function exists first
        check_function_sql = """
        SELECT EXISTS(
            SELECT 1 FROM pg_proc
            WHERE proname = 'safe_jsonb_parse'
            AND pronamespace = (SELECT oid FROM pg_namespace WHERE nspname = 'public')
        );
        """

        function_exists = cls.execute_query(check_function_sql)

        # Only create the function if it doesn't exist
        if not function_exists or not function_exists[0][0]:
            # Create a function to safely parse JSON
            create_json_parser_function = """
            CREATE FUNCTION safe_jsonb_parse(input_text TEXT)
            RETURNS JSONB AS $func_body$
            DECLARE
                result JSONB;
            BEGIN
                -- Try direct parsing first
                BEGIN
                    result := input_text::jsonb;
                    RETURN result;
                EXCEPTION WHEN OTHERS THEN
                    -- Try replacing single quotes with double quotes
                    BEGIN
                        result := REPLACE(input_text, '''', '"')::jsonb;
                        RETURN result;
                    EXCEPTION WHEN OTHERS THEN
                        -- Try additional quote replacements
                        BEGIN
                            result := REPLACE(REPLACE(input_text, '''', '"'), '"', '"')::jsonb;
                            RETURN result;
                        EXCEPTION WHEN OTHERS THEN
                            -- If all parsing attempts fail, return NULL
                            RETURN NULL;
                        END;
                    END;
                END;
            END;
            $func_body$ LANGUAGE plpgsql IMMUTABLE;
            """

            # Execute the function creation with error handling
            try:
                cls.execute_query(create_json_parser_function, fetch_results=False)
            except Exception as e:
                # If function creation fails due to concurrent access, we can still proceed
                # as the function might have been created by another process
                if (
                    "already exists" not in str(e).lower()
                    and "concurrently updated" not in str(e).lower()
                ):
                    raise e

        query = f"""
        WITH user_eval_metrics AS (
            -- Step 1: Fetch all UserEvalMetric IDs with given dataset_id and eval_template_id
            SELECT
                uem.id as user_eval_metric_id,
                uem.name as metric_name
            FROM model_hub_userevalmetric uem
            WHERE uem.dataset_id = %s
            AND uem.template_id = %s
            AND uem.deleted = FALSE
            {user_eval_metrics_condition}
        ),
        relevant_cells AS (
            -- Step 2: Fetch all cells whose dataset_id matches and column.source_id
            -- either equals user_eval_metric_id or contains eval_metric_id
            SELECT
                c.id as cell_id,
                c.value,
                c.column_id,
                col.name as column_name,
                col.source_id as column_source_id,
                uem.user_eval_metric_id,
                uem.metric_name
            FROM model_hub_cell c
            INNER JOIN model_hub_column col ON c.column_id = col.id
            INNER JOIN user_eval_metrics uem ON (
                col.source_id = uem.user_eval_metric_id::text
                OR col.source_id LIKE '%%' || uem.user_eval_metric_id::text || '%%'
            )
            WHERE c.dataset_id = %s
            AND c.value IS NOT NULL
            AND c.status = 'pass'
            AND c.value != ''
            AND col.source != 'evaluation_reason'
            AND col.source != 'experiment_evaluation'
            AND col.source != 'experiment'
            {row_ids_condition}
        ),
        valid_json_cells AS (
            -- Step 3: Filter cells with valid JSON array values using safer parsing
            SELECT
                cell_id,
                column_id,
                column_name,
                user_eval_metric_id,
                metric_name,
                value,
                CASE
                    WHEN value ~ '^\\[.*\\]$' THEN
                        CASE
                            WHEN jsonb_typeof(safe_jsonb_parse(value)) = 'array' THEN safe_jsonb_parse(value)
                            ELSE NULL
                        END
                    ELSE NULL
                END as json_array
            FROM relevant_cells
            WHERE value ~ '^\\[.*\\]$'
        ),
        choice_counts AS (
            -- Step 4: Count occurrences of each choice in each cell
            SELECT
                cell_id,
                column_id,
                column_name,
                user_eval_metric_id,
                metric_name,
                json_array,
                choice,
                CASE
                    WHEN json_array ? choice THEN 1
                    ELSE 0
                END as choice_present
            FROM valid_json_cells
            CROSS JOIN unnest({choices_array}::text[]) as choice
            WHERE json_array IS NOT NULL
        ),
        column_choice_stats AS (
            -- Step 5: Group by column and calculate choice averages
            SELECT
                column_id,
                column_name,
                user_eval_metric_id,
                metric_name,
                choice,
                COUNT(*) as total_cells,
                SUM(choice_present) as choice_count,
                CASE
                    WHEN COUNT(*) > 0 THEN ROUND((SUM(choice_present)::NUMERIC / COUNT(*)::NUMERIC) * 100, 2)
                    ELSE 0
                END as choice_percentage
            FROM choice_counts
            GROUP BY column_id, column_name, user_eval_metric_id, metric_name, choice
        ),
        overall_choice_stats AS (
            -- Step 6: Calculate overall choice averages across all columns
            SELECT
                choice,
                SUM(total_cells) as total_cells_overall,
                SUM(choice_count) as total_choice_count_overall,
                CASE
                    WHEN SUM(total_cells) > 0 THEN ROUND((SUM(choice_count)::NUMERIC / SUM(total_cells)::NUMERIC) * 100, 2)
                    ELSE 0
                END as overall_choice_percentage
            FROM column_choice_stats
            GROUP BY choice
        )
        SELECT
            ccs.column_id,
            ccs.column_name,
            ccs.user_eval_metric_id,
            ccs.metric_name,
            ccs.choice,
            ccs.total_cells,
            ccs.choice_count,
            ccs.choice_percentage,
            ocs.total_cells_overall,
            ocs.total_choice_count_overall,
            ocs.overall_choice_percentage
        FROM column_choice_stats ccs
        CROSS JOIN overall_choice_stats ocs
        WHERE ccs.choice = ocs.choice
        ORDER BY ccs.column_name, ccs.metric_name, ccs.choice;
        """

        return cls.execute_query(query, params)

    @classmethod
    def get_user_default_details(cls, org_id, end_user_id=None, project_id=None):
        query = f"""
            WITH filtered_end_users AS (
                    SELECT id, user_id, created_at, project_id
                    FROM tracer_enduser
                    WHERE organization_id = %s
                    {"AND id = %s" if end_user_id else ""}
                    {"AND project_id = %s" if project_id else ""}
                    AND user_id IS NOT NULL
            ),filtered_spans AS (
                    SELECT *
                    FROM tracer_observation_span
                    WHERE end_user_id IN (SELECT id FROM filtered_end_users)
                    AND end_user_id IS NOT NULL
                    AND deleted = false
                    {"AND project_id = %s" if project_id else ""}
            ),last_active AS (
                    SELECT
                        end_user_id,
                        MAX(end_time) AS last_active
                    FROM filtered_spans
                    GROUP BY end_user_id
            ),
            active_days AS (
                SELECT
                    end_user_id,
                    COUNT(DISTINCT DATE(start_time)) AS num_active_days
                FROM filtered_spans
                WHERE start_time IS NOT NULL
                GROUP BY end_user_id
            )
                SELECT
                    a.user_id,
                    ad.num_active_days,
                    la.last_active
                FROM filtered_end_users a
                LEFT JOIN last_active la ON a.id = la.end_user_id
                LEFT JOIN active_days ad ON a.id = ad.end_user_id
        """

        params = [org_id]
        if end_user_id:
            params.append(end_user_id)
        if project_id:
            params.extend([project_id, project_id])

        return cls.execute_query(query, params)

    @classmethod
    def get_spans_by_end_users(
        cls,
        org_id,
        search_name=None,
        sort_order="DESC",
        sort_by="last_active",
        limit=None,
        offset=None,
        project_id=None,
        filters=None,
        end_user_id=None,
        column_map=None,
        workspace_id=None,
    ):
        if column_map is None:
            column_map = {}
        if filters is None:
            filters = []
        datetime_filter = "", []
        if filters:
            for filter_data in filters:
                column_id = filter_data.get("column_id")
                filter_config = filter_data.get("filter_config", {})
                if column_id == "created_at":
                    filter_value = filter_config.get("filter_value", [])
                    if not isinstance(filter_value, list):
                        filter_value = [filter_value]
                    datetime_filter = build_datetime_filter_sql(
                        value=",".join(filter_value),
                        filter_op=filter_config.get("filter_op"),
                        col_name="start_time",
                    )

        end_filter, end_filter_params = build_sql_filters(
            filters=filters, column_map=column_map
        )

        if workspace_id:
            ws = Workspace.objects.get(id=workspace_id)
            if (
                ws.is_default
                and EndUser.objects.filter(
                    workspace=None, organization=ws.organization
                ).exists()
            ):
                EndUser.objects.filter(
                    workspace=None, organization=ws.organization
                ).update(workspace=ws)

        query = f"""
            WITH filtered_end_users AS (
                SELECT id, user_id, created_at, project_id, user_id_type, user_id_hash
                FROM tracer_enduser
                WHERE organization_id = %s and workspace_id = %s
                {"AND id = %s" if end_user_id else ""}
                {"AND user_id ILIKE %s" if search_name else ""}
                {"AND project_id = %s" if project_id else ""}
                AND user_id IS NOT NULL
            ),
            filtered_spans AS (
                SELECT s.*
                FROM tracer_observation_span s
                JOIN tracer_project p ON s.project_id = p.id
                WHERE s.end_user_id IN (SELECT id FROM filtered_end_users)
                AND s.end_user_id IS NOT NULL
                AND s.deleted = false
                AND p.trace_type = 'observe'
                {"AND s.project_id = %s" if project_id else ""}
                {f"AND {datetime_filter[0]}" if datetime_filter[0] else ""}
            ),
            aggregated_usage AS (
                SELECT
                    end_user_id,
                    SUM(cost) AS total_cost,
                    SUM(total_tokens) AS total_tokens,
                    SUM(prompt_tokens) AS input_tokens,
                    SUM(completion_tokens) AS output_tokens
                FROM filtered_spans
                GROUP BY end_user_id
            ),
            guardrails_triggered AS (
                SELECT
                    end_user_id,
                    COUNT(DISTINCT trace_id) AS num_guardrails_triggered
                FROM filtered_spans
                WHERE observation_type = 'guardrail'
                GROUP BY end_user_id
            ),
            total_llm_calls AS (
                SELECT
                    end_user_id,
                    COUNT(*) AS num_llm_calls
                FROM filtered_spans
                WHERE observation_type = 'llm'
                GROUP BY end_user_id
            ),
            num_traces AS (
                SELECT
                    end_user_id,
                    COUNT(DISTINCT trace_id) AS num_traces
                FROM filtered_spans
                GROUP BY end_user_id
            ),
            session_durations AS (
                SELECT
                    tt.session_id,
                    fs.end_user_id,  -- FIXED: use from filtered_spans
                    MIN(fs.start_time) AS session_start,
                    MAX(fs.end_time) AS session_end,
                    EXTRACT(EPOCH FROM MAX(fs.end_time) - MIN(fs.start_time)) AS session_duration_seconds
                FROM filtered_spans fs
                JOIN tracer_trace tt ON fs.trace_id = tt.id
                WHERE fs.start_time IS NOT NULL AND fs.end_time IS NOT NULL AND tt.session_id IS NOT NULL
                GROUP BY tt.session_id, fs.end_user_id
            ),
            latency_aggregates AS (
                SELECT
                    fs.end_user_id,  -- FIXED
                    ROUND(AVG(fs.latency_ms), 2) AS avg_latency
                FROM filtered_spans fs
                JOIN tracer_trace tt ON fs.trace_id = tt.id
                WHERE fs.latency_ms IS NOT NULL
                GROUP BY fs.end_user_id
            ),
            trace_level_details AS (
                SELECT
                    sd.end_user_id,
                    COUNT(*) AS num_sessions,
                    ROUND(AVG(sd.session_duration_seconds), 2) AS avg_session_duration_seconds
                FROM session_durations sd
                GROUP BY sd.end_user_id
            ),
            final_output AS (
                SELECT
                    tld.*,
                    la.avg_latency as avg_latency
                FROM trace_level_details tld
                LEFT JOIN latency_aggregates la ON tld.end_user_id = la.end_user_id
            ),
            last_active AS (
                SELECT
                    end_user_id,
                    MAX(end_time) AS last_active
                FROM filtered_spans
                GROUP BY end_user_id
            ),
            active_days AS (
                SELECT
                    end_user_id,
                    COUNT(DISTINCT DATE(start_time)) AS num_active_days
                FROM filtered_spans
                WHERE start_time IS NOT NULL
                GROUP BY end_user_id
            ),
            traces_with_errors AS (
                SELECT
                    end_user_id,
                    COUNT(DISTINCT trace_id) AS num_traces_with_errors
                FROM filtered_spans
                WHERE status = 'ERROR'
                GROUP BY end_user_id
            ),
            eval_pass_rate AS (
                SELECT
                    s.end_user_id,
                    COUNT(*) FILTER (
                        WHERE e.output_bool IS NOT NULL
                    ) AS total_bool_evals,
                    COUNT(*) FILTER (
                        WHERE e.output_bool = TRUE
                    ) AS passed_bool_evals,
                    ROUND(
                        100.0 * COUNT(*) FILTER (
                            WHERE e.output_bool = TRUE
                        ) / NULLIF(COUNT(*) FILTER (
                            WHERE e.output_bool IS NOT NULL
                        ), 0),
                        2
                    ) AS bool_pass_rate,
                    ROUND(
                        AVG(e.output_float)::numeric,
                        2
                    ) AS avg_output_float
                FROM tracer_eval_logger e
                JOIN tracer_trace t ON e.trace_id = t.id
                JOIN filtered_spans s ON s.trace_id = t.id
                WHERE e.deleted = false
                GROUP BY s.end_user_id
            ),
            unsuccessful_sessions AS (
                SELECT
                    s.end_user_id,
                    COUNT(DISTINCT session_id) AS num_sessions
                FROM tracer_trace t
                JOIN filtered_spans s ON s.trace_id = t.id
                WHERE t.deleted = false
                GROUP BY s.end_user_id
            )
            SELECT
                a.user_id,  -- user_id for output
                COALESCE(au.total_cost, 0) AS total_cost,
                COALESCE(au.total_tokens, 0) AS total_tokens,
                COALESCE(au.input_tokens, 0) AS input_tokens,
                COALESCE(au.output_tokens, 0) AS output_tokens,
                COALESCE(nt.num_traces, 0) AS num_traces,
                COALESCE(fo.num_sessions, 0) AS num_sessions,
                COALESCE(fo.avg_session_duration_seconds, 0) as avg_session_duration_seconds,
                COALESCE(fo.avg_latency, 0) as avg_latency_trace,
                COALESCE(tc.num_llm_calls, 0) as num_llm_calls,
                COALESCE(gt.num_guardrails_triggered, 0) as num_guardrails_triggered,
                a.created_at,
                la.last_active AS last_active,
                COALESCE(ad.num_active_days, 0) AS num_active_days,
                COALESCE(te.num_traces_with_errors, 0) AS num_traces_with_errors,
                COALESCE(epr.bool_pass_rate, 0) AS bool_eval_pass_rate,
                COALESCE(epr.avg_output_float, 0) AS avg_output_float,
                a.project_id,
                COUNT(*) OVER() AS total_count,
                a.user_id_type,
                a.user_id_hash,
                a.id as end_user_id
            FROM filtered_end_users a
            LEFT JOIN aggregated_usage au ON a.id = au.end_user_id   -- UUID to UUID
            LEFT JOIN num_traces nt ON a.id = nt.end_user_id         -- UUID to UUID
            LEFT JOIN final_output fo ON a.id = fo.end_user_id       -- UUID to UUID
            LEFT JOIN last_active la ON a.id = la.end_user_id        -- UUID to UUID
            LEFT JOIN total_llm_calls tc ON a.id = tc.end_user_id    -- UUID to UUID
            LEFT JOIN guardrails_triggered gt ON a.id = gt.end_user_id
            LEFT JOIN active_days ad ON a.id = ad.end_user_id
            LEFT JOIN traces_with_errors te ON a.id = te.end_user_id
            LEFT JOIN eval_pass_rate epr ON a.id = epr.end_user_id
            WHERE a.id IN (SELECT end_user_id FROM filtered_spans)
            {end_filter if end_filter else ""}
            ORDER BY {sort_by} {sort_order} NULLS LAST
            {"LIMIT %s OFFSET %s" if limit is not None and offset is not None else ""};
        """
        params = [org_id, workspace_id]
        if end_user_id:
            params.append(end_user_id)
        if search_name:
            params.append(f"%{search_name}%")

        if project_id:
            params.extend([project_id, project_id])

        if datetime_filter[1]:
            params.extend(datetime_filter[1])

        if end_filter:
            params.extend(end_filter_params)

        if limit is not None and offset is not None:
            params.extend([limit, offset])

        return cls.execute_query(query, params)

    @classmethod
    def get_annotation_summary_stats(cls, dataset_id, r_type="header_data"):
        """
        Get annotation summary statistics for a dataset.
        """
        params = []
        query = """WITH annotation_ids AS (
                        SELECT id
                        FROM model_hub_annotations
                        WHERE dataset_id = %s
                        AND deleted = false
                    ),

                    annotation_labels AS (
                        SELECT DISTINCT
                            anot.annotationslabels_id,
                            l."type",
                            l."name",
                            l.settings ->> 'min' AS min_num,
                            l.settings ->> 'max' AS max_num,
                            (
                                SELECT array_agg(opt->>'label')
                                FROM jsonb_array_elements(l.settings->'options') opt
                            ) AS cat_labels,
                            l.settings ->> 'min_length' AS min_text,
                            l.settings ->> 'max_length' AS max_text
                        FROM model_hub_annotations_labels anot
                        JOIN model_hub_annotationslabels l
                        ON anot.annotationslabels_id = l.id
                        WHERE anot.annotations_id IN (SELECT id FROM annotation_ids)
                    ),

                    columns AS (
                        SELECT column_id
                        FROM model_hub_annotations_columns
                        WHERE annotations_id IN (SELECT id FROM annotation_ids)
                    ),

                    rows AS (
                        SELECT r.id AS row_id
                        FROM model_hub_row r
                        WHERE r.dataset_id = %s
                        AND r.deleted = false
                    ),

                    cells AS (
                        SELECT DISTINCT ON (c.row_id,
                                            feedback_info -> 'annotation' ->> 'user_id',
                                            feedback_info -> 'annotation' ->> 'label_id')
                            c.value,
                            feedback_info -> 'annotation' ->> 'label_id'   AS label_id,
                            feedback_info -> 'annotation' ->> 'user_id'    AS user_id,
                            feedback_info -> 'annotation' ->> 'time_taken' AS time_taken,
                            c.row_id,
                            c.column_id
                        FROM model_hub_cell c
                        JOIN model_hub_row r
                        ON c.row_id = r.id
                        WHERE c.dataset_id = %s
                        AND r.deleted = false
                        AND c.value IS NOT NULL
                        AND c.column_id IN (SELECT column_id FROM columns)
                    )"""

        select_q = ""

        if r_type == "header_data":
            select_q = r""",
                        agg_cells AS (
                            SELECT
                                label_id,
                                COUNT(*) AS count_records,
                                SUM(CASE WHEN c.value ~ '^[0-9]+(\.[0-9]+)?$' THEN c.value::numeric END)::float8 AS sum_value,
                                AVG(CASE WHEN c.value ~ '^[0-9]+(\.[0-9]+)?$' THEN c.value::numeric END)::float8 AS avg_value,
                                AVG(CASE WHEN c.time_taken ~ '^[0-9]+(\.[0-9]+)?$' THEN c.time_taken::numeric END)::float8 AS avg_time_taken,
                                MODE() WITHIN GROUP (ORDER BY CASE WHEN c.value ~ '^[0-9]+(\.[0-9]+)?$' THEN c.value::numeric END)::float8 AS mode_value,
                                ROUND(STDDEV(CASE WHEN c.value ~ '^[0-9]+(\.[0-9]+)?$' THEN c.value::numeric END), 2)::float8 AS stddev_value,
                                100.0 * COUNT(*) FILTER (WHERE c.value IS NOT NULL) / COUNT(*) AS label_coverage
                            FROM cells c
                            GROUP BY label_id
                        )
                        SELECT
                            agg.*,
                            nal.type,
                            nal.cat_labels,
                            JSONB_OBJECT_AGG(
                                COALESCE(cat_labels.cat_label, '_null'),
                                cat_labels.cat_label_count
                            ) AS cat_label_counts,
                            MAX(CASE WHEN nal.type = 'numeric' THEN nal.min_num ELSE nal.min_text END) AS min_value,
                            MAX(CASE WHEN nal.type = 'numeric' THEN nal.max_num ELSE nal.max_text END) AS max_value,
                            nal.name AS label_name
                        FROM agg_cells agg
                        JOIN annotation_labels nal
                        ON agg.label_id::uuid = nal.annotationslabels_id
                        LEFT JOIN LATERAL (
                            SELECT cat_label,
                                COUNT(*) FILTER (WHERE c.value ILIKE '%%' || cat_label || '%%') AS cat_label_count
                            FROM UNNEST(nal.cat_labels) AS cat_label
                            JOIN cells c ON c.label_id::uuid = nal.annotationslabels_id
                            GROUP BY cat_label
                        ) cat_labels ON TRUE
                        GROUP BY agg.label_id, nal.type, nal.cat_labels, nal.name, agg.sum_value, agg.avg_value,
                                agg.avg_time_taken, agg.count_records, agg.mode_value, agg.stddev_value, agg.label_coverage;"""

        elif r_type == "annotator_performance":
            select_q = r"""
                SELECT
                    c.user_id,
                    u.name,
                    AVG(CASE WHEN time_taken ~ '^[0-9]+(\.[0-9]+)?$'
                                THEN time_taken::numeric END) AS avg_time,
                    COUNT(c.value) AS annotations
                FROM cells c
                JOIN accounts_user u ON c.user_id = u.id::text
                GROUP BY u.name, c.user_id;"""

        elif r_type == "dataset_annot_summary":
            select_q = """
                SELECT
                    COUNT(*) AS not_deleted_rows,
                    COUNT(*) FILTER (
                        WHERE num_required > 0 AND num_filled = num_required
                    ) AS fully_annotated_rows
                FROM (
                    SELECT r.row_id,
                            COUNT(DISTINCT c.column_id) AS num_filled,
                            (SELECT COUNT(*) FROM columns) AS num_required
                    FROM rows r
                    LEFT JOIN cells c ON r.row_id = c.row_id
                    GROUP BY r.row_id
                ) row_stats;
            """

        elif r_type == "get_text_data":
            select_q = """
                SELECT c.value
                FROM cells c
                JOIN annotation_labels a ON c.label_id = a.annotationslabels_id::text
                WHERE a.type='text';"""

        elif r_type == "metric_calc":
            select_q = """
                SELECT
                    label_id,
                    row_id,
                    user_id,
                    value
                FROM cells c
                JOIN annotation_labels a ON c.label_id = a.annotationslabels_id::text;
                """

        elif r_type in ["graph", "heatmap"]:
            select_q = r""",
                bounds AS (
                    SELECT annotationslabels_id::text as label_id,
                        MIN(min_num::numeric) AS min_val,
                        MAX(max_num::numeric) AS max_val
                    FROM annotation_labels
                    WHERE type = 'numeric'
                    GROUP BY label_id
                ),
                bucket_defs AS (
                    SELECT
                        b.label_id,
                        gs AS bucket,
                        ROUND(b.min_val + (gs - 1) * (b.max_val - b.min_val) / 8, 2) AS bucket_min,
                        ROUND(b.min_val + gs * (b.max_val - b.min_val) / 8, 2)       AS bucket_max,
                        COALESCE(
                            COUNT(c.value),
                            0
                        ) AS count
                    FROM bounds b
                    CROSS JOIN generate_series(1, 8) gs
                    LEFT JOIN cells c
                    ON c.label_id = b.label_id
                    AND c.value ~ '^[0-9]+(\.[0-9]+)?$'
                    AND c.value::numeric >= (b.min_val + (gs - 1) * (b.max_val - b.min_val) / 8)
                    AND c.value::numeric <  (b.min_val + gs       * (b.max_val - b.min_val) / 8)
                    GROUP BY b.label_id, gs, b.min_val, b.max_val
                )
                """

            if r_type == "graph":
                select_q += r"""
                    SELECT
                        bd.label_id,
                        bd.bucket,
                        bd.bucket_min,
                        bd.bucket_max,
                        COUNT(c.value) AS count
                    FROM bucket_defs bd
                    LEFT JOIN cells c
                    ON c.label_id = bd.label_id
                    AND c.value ~ '^[0-9]+(\.[0-9]+)?$'
                    AND c.value::numeric >= bd.bucket_min
                    AND c.value::numeric <  bd.bucket_max
                    GROUP BY bd.label_id, bd.bucket, bd.bucket_min, bd.bucket_max
                    ORDER BY bd.label_id, bd.bucket;"""

            if r_type == "heatmap":
                select_q += r""",
                    user_buckets AS (
                        SELECT DISTINCT
                            c.user_id,
                            bd.label_id,
                            bd.bucket,
                            bd.bucket_min,
                            bd.bucket_max
                        FROM bucket_defs bd
                        CROSS JOIN (SELECT DISTINCT user_id FROM cells WHERE user_id IS NOT NULL) c
                    )
                    SELECT
                        ub.label_id,
                        ub.user_id,
                        ub.bucket,
                        ub.bucket_min,
                        ub.bucket_max,
                        COUNT(c.value) AS count
                    FROM user_buckets ub
                    LEFT JOIN cells c
                        ON c.label_id = ub.label_id
                        AND c.user_id = ub.user_id
                        AND c.value ~ '^[0-9]+(\.[0-9]+)?$'
                        AND c.value::numeric >= ub.bucket_min
                        AND c.value::numeric < ub.bucket_max
                    GROUP BY ub.label_id, ub.user_id, ub.bucket, ub.bucket_min, ub.bucket_max
                    ORDER BY ub.label_id, ub.user_id, ub.bucket;"""

        query = query + select_q
        params.extend([dataset_id, dataset_id, dataset_id])
        return cls.execute_query(query, params, return_df=True)


def create_float_eval_cte(config_alias):
    return f"""eval_float_{config_alias} AS (
        SELECT
            os.prompt_version_id,
            os.prompt_label_id,
            json_build_object('score', ROUND((AVG(el.output_float) * 100)::numeric, 2)) as metric_{config_alias}
        FROM tracer_eval_logger el
        INNER JOIN tracer_observation_span os ON el.observation_span_id = os.id
        WHERE el.custom_eval_config_id = %s
            AND el.output_float IS NOT NULL
            AND (el.output_str IS NULL OR el.output_str != 'ERROR')
            AND (el.error IS NULL OR el.error != TRUE)
        GROUP BY os.prompt_version_id, os.prompt_label_id
    )"""


def create_boolean_eval_cte(config_alias):
    return f"""eval_bool_{config_alias} AS (
        SELECT
            os.prompt_version_id,
            os.prompt_label_id,
            json_build_object(
                'score', ROUND(
                    AVG(
                        CASE
                            WHEN el.output_bool = TRUE THEN 100.0
                            WHEN el.output_bool = FALSE THEN 0.0
                            ELSE NULL
                        END
                    )::numeric, 2
                )
            ) as metric_{config_alias}
        FROM tracer_eval_logger el
        INNER JOIN tracer_observation_span os ON el.observation_span_id = os.id
        WHERE el.custom_eval_config_id = %s
            AND el.output_bool IN (TRUE, FALSE)
            AND (el.output_str IS NULL OR el.output_str != 'ERROR')
            AND (el.error IS NULL OR el.error != TRUE)
        GROUP BY os.prompt_version_id, os.prompt_label_id
    )"""


def create_list_eval_cte(config_alias, choices):
    choice_objects = ", ".join(
        [
            f"'{choice}', json_build_object('score', ROUND((100.0 * COUNT(CASE WHEN el.output_str_list ? '{choice}' THEN 1 END) / NULLIF(COUNT(el.output_str_list), 0))::numeric, 2))"
            for choice in choices
        ]
    )

    return f"""eval_list_{config_alias} AS (
        SELECT
            os.prompt_version_id,
            os.prompt_label_id,
            json_build_object(
                {choice_objects}
            ) as metric_{config_alias}
        FROM tracer_eval_logger el
        INNER JOIN tracer_observation_span os ON el.observation_span_id = os.id
        WHERE el.custom_eval_config_id = %s
            AND el.output_str_list IS NOT NULL
            AND (el.output_str IS NULL OR el.output_str != 'ERROR')
            AND (el.error IS NULL OR el.error != TRUE)
        GROUP BY os.prompt_version_id, os.prompt_label_id
    )"""


prompt_metrics_cte_base_query = f"""
base AS (
    SELECT
        os.prompt_version_id,
        os.prompt_label_id,
        pl.name AS prompt_label_name,
        pv.template_version AS prompt_template_version,
        COALESCE(ROUND(AVG(os.latency_ms), 2), 0.0) AS row_avg_latency_ms,
        COALESCE(ROUND(AVG(os.prompt_tokens), 2), 0.0) AS avg_input_tokens,
        COALESCE(ROUND(AVG(os.completion_tokens), 2), 0.0) AS avg_output_tokens,
        MIN(os.created_at) AS first_used,
        MAX(os.created_at) AS last_used,
        COUNT(os.id) AS total_spans,
        COUNT(DISTINCT os.trace_id) AS unique_traces,
        MAX(pv.created_at) AS version_created_at,
        COALESCE(
            ROUND(
                AVG({MODEL_COST_CALCULATION_SQL}), 6
            ),
            0.0
        ) AS row_avg_cost
    FROM tracer_observation_span os
    INNER JOIN model_hub_promptversion pv ON os.prompt_version_id = pv.id
    INNER JOIN model_hub_promptlabel pl ON os.prompt_label_id = pl.id
    WHERE
        pv.original_template_id = %s
        AND pv.deleted = FALSE
    GROUP BY
        os.prompt_version_id,
        os.prompt_label_id,
        pl.name,
        pv.template_version
"""
