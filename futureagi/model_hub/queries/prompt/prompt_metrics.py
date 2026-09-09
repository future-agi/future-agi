import uuid
from datetime import date, datetime

import structlog
from django.conf import settings
from django.db import connection
from django.db.models import (
    BigIntegerField,
    F,
    Func,
    OuterRef,
    Q,
    Subquery,
    TextField,
)
from django.db.models.functions import Cast, JSONObject, Round
from django.utils.dateparse import parse_date, parse_datetime

from model_hub.models.run_prompt import PromptTemplate
from model_hub.utils.SQL_queries import (
    MODEL_COST_CALCULATION_SQL,
    prompt_metrics_cte_base_query,
)
from model_hub.utils.workspace_scope import request_workspace, request_workspace_filter
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import (
    EvalEntryStatus,
    EvalLogger,
    EvalTargetType,
    ObservationSpan,
)
from tracer.utils.filters import ColType, FilterEngine

logger = structlog.get_logger(__name__)


PROMPT_SPAN_PAGE_DB_PAYLOAD_BYTES = settings.PROMPT_METRICS_SPAN_PAGE_DB_PAYLOAD_BYTES


class PromptMetricsQueryLimitExceeded(RuntimeError):
    """The selected prompt-metric page cannot fit an interactive response."""


def completed_prompt_eval_logs(queryset):
    """Apply the terminal, successful EvalLogger contract used by prompt metrics."""

    return queryset.filter(
        deleted=False,
        status=EvalEntryStatus.COMPLETED,
        error=False,
        skipped_reason__isnull=True,
    ).exclude(output_str="ERROR")


_PROMPT_METRIC_SYSTEM_FILTER_IDS = frozenset(
    {
        "avg_cost",
        "avg_latency",
        "avg_input_tokens",
        "avg_output_tokens",
        "unique_traces",
        "first_used",
        "last_used",
        "prompt_template_version",
        "prompt_label_id",
        "prompt_label_name",
    }
)
PROMPT_NUMERIC_FILTER_OPS = (
    "greater_than",
    "less_than",
    "equals",
    "not_equals",
    "greater_than_or_equal",
    "less_than_or_equal",
    "between",
    "not_between",
)
PROMPT_TEXT_FILTER_OPS = (
    "contains",
    "not_contains",
    "equals",
    "not_equals",
    "starts_with",
    "ends_with",
)
PROMPT_AGGREGATE_TEXT_FILTER_OPS = ("contains", "equals", "not_equals")
PROMPT_UUID_FILTER_OPS = ("equals", "not_equals")
PROMPT_BOOLEAN_FILTER_OPS = ("equals", "not_equals")
PROMPT_CHOICE_FILTER_OPS = ("contains", "not_contains")
_PROMPT_METRIC_NUMERIC_FILTER_IDS = frozenset(
    {
        "avg_cost",
        "avg_latency",
        "avg_input_tokens",
        "avg_output_tokens",
        "unique_traces",
    }
)
_PROMPT_METRIC_DATETIME_FILTER_IDS = frozenset({"first_used", "last_used"})
_PROMPT_METRIC_UUID_FILTER_IDS = frozenset({"prompt_label_id"})

_PROMPT_METRIC_SYSTEM_SQL_EXPRESSIONS = {
    "avg_cost": f"COALESCE(ROUND(AVG({MODEL_COST_CALCULATION_SQL}), 6), 0.0)",
    "avg_latency": "COALESCE(ROUND(AVG(os.latency_ms), 2), 0.0)",
    "avg_input_tokens": "COALESCE(ROUND(AVG(os.prompt_tokens), 2), 0.0)",
    "avg_output_tokens": "COALESCE(ROUND(AVG(os.completion_tokens), 2), 0.0)",
    "unique_traces": "COUNT(DISTINCT os.trace_id)",
    "first_used": "MIN(os.created_at)",
    "last_used": "MAX(os.created_at)",
    "prompt_template_version": "pv.template_version",
    "prompt_label_id": "os.prompt_label_id",
    "prompt_label_name": "pl.name",
}


def _prompt_metric_eval_filter_ids(eval_configs):
    """Return the exact dynamic columns emitted for these configurations."""

    allowed = set()
    for config in eval_configs:
        config_id = str(config.id)
        eval_template_config = config.eval_template.config or {}
        choices = config.eval_template.choices or []
        if choices and eval_template_config.get("output", "score") == "choices":
            allowed.update(f"{config_id}**{choice}" for choice in choices)
        else:
            allowed.add(config_id)
    return allowed


def _normalize_prompt_eval_output_type(output_type):
    return (
        str(output_type or "score").strip().lower().replace("/", "_").replace(" ", "_")
    )


def get_prompt_aggregate_filter_contract(column_id, *, is_eval=False):
    """Return the aggregate endpoint's exact filter type/operator contract."""

    if is_eval or column_id in _PROMPT_METRIC_NUMERIC_FILTER_IDS:
        return "number", PROMPT_NUMERIC_FILTER_OPS
    if column_id in _PROMPT_METRIC_DATETIME_FILTER_IDS:
        return "datetime", PROMPT_NUMERIC_FILTER_OPS
    if column_id in _PROMPT_METRIC_UUID_FILTER_IDS:
        return "text", PROMPT_UUID_FILTER_OPS
    if column_id in _PROMPT_METRIC_SYSTEM_FILTER_IDS:
        return "text", PROMPT_AGGREGATE_TEXT_FILTER_OPS
    return None


def get_prompt_span_filter_contract(column_id, *, eval_output_type=None):
    """Return the linked-span endpoint's exact filter type/operator contract."""

    if eval_output_type is not None:
        output_type = _normalize_prompt_eval_output_type(eval_output_type)
        if output_type == "pass_fail":
            return "boolean", PROMPT_BOOLEAN_FILTER_OPS
        if output_type == "choices":
            return "array", PROMPT_CHOICE_FILTER_OPS
        if output_type in {"score", "float", "numeric", "percentage", "reason"}:
            return "number", PROMPT_NUMERIC_FILTER_OPS
        return None

    if column_id == "created_at":
        return "datetime", PROMPT_NUMERIC_FILTER_OPS
    if column_id in {"trace_id", "session_id"}:
        return "text", PROMPT_UUID_FILTER_OPS
    if column_id in _PROMPT_SPAN_SYSTEM_FIELD_MAP:
        return "text", PROMPT_TEXT_FILTER_OPS
    return None


def _prompt_filter_date(value):
    """Normalize API date/datetime values to the endpoint's date semantics."""

    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    parsed_date = parse_date(value)
    if parsed_date is not None:
        return parsed_date
    parsed_datetime = parse_datetime(value)
    return parsed_datetime.date() if parsed_datetime is not None else None


def _validated_prompt_aggregate_filter(filter_item, allowed_eval_ids):
    column_id, filter_config = FilterEngine._normalize_filter_params(filter_item)
    if not column_id or not filter_config:
        raise ValueError("Each prompt metric filter must name a column.")
    if filter_config.get("col_type") == ColType.SPAN_ATTRIBUTE.value:
        raise ValueError(
            "Span-attribute filters are not supported by prompt aggregate metrics."
        )
    is_eval = column_id in allowed_eval_ids
    if column_id not in _PROMPT_METRIC_SYSTEM_FILTER_IDS and not is_eval:
        raise ValueError(f"Unsupported prompt metric filter column: {column_id}")

    contract = get_prompt_aggregate_filter_contract(column_id, is_eval=is_eval)
    if contract is None:
        raise ValueError(f"Unsupported prompt metric filter column: {column_id}")
    expected_type, supported_ops = contract
    filter_type = filter_config.get("filter_type")
    filter_op = filter_config.get("filter_op")
    filter_value = filter_config.get("filter_value")
    if filter_type != expected_type:
        raise ValueError(
            f"Prompt metric {column_id} requires a {expected_type} filter."
        )
    if filter_op not in supported_ops:
        raise ValueError(
            f"Unsupported prompt metric operation for {column_id}: {filter_op}"
        )

    if expected_type == "number":
        if isinstance(filter_value, bool):
            raise ValueError("Invalid prompt metric numeric filter value.")
        is_valid, converted_value = FilterEngine._validate_and_convert_filter_value(
            filter_value, filter_type, filter_op
        )
        if not is_valid:
            raise ValueError("Invalid prompt metric numeric filter value.")
    elif expected_type == "datetime":
        values = filter_value if isinstance(filter_value, list) else [filter_value]
        if filter_op in {"between", "not_between"} and len(values) != 2:
            raise ValueError("Prompt metric date ranges require exactly two dates.")
        if filter_op not in {"between", "not_between"} and len(values) != 1:
            raise ValueError("Prompt metric date comparisons require one date.")
        converted_value = [_prompt_filter_date(value) for value in values]
        if any(value is None for value in converted_value):
            raise ValueError("Invalid prompt metric datetime filter value.")
        if filter_op not in {"between", "not_between"}:
            converted_value = converted_value[0]
    else:
        if not isinstance(filter_value, str) or not filter_value:
            raise ValueError("Invalid prompt metric text filter value.")
        converted_value = filter_value
        if column_id in _PROMPT_METRIC_UUID_FILTER_IDS:
            try:
                converted_value = str(uuid.UUID(filter_value))
            except (ValueError, AttributeError) as exc:
                raise ValueError("Invalid prompt metric UUID filter value.") from exc

    return column_id, is_eval, filter_op, converted_value


def _validate_prompt_metric_filters(filters, eval_configs):
    """Reject unknown aggregate filters and operations instead of ignoring them."""

    allowed_eval_ids = _prompt_metric_eval_filter_ids(eval_configs)
    for filter_item in filters or []:
        _validated_prompt_aggregate_filter(filter_item, allowed_eval_ids)


def _prompt_metric_system_having(filters, eval_configs):
    """Compile only validated aggregate system filters into a HAVING clause."""

    allowed_eval_ids = _prompt_metric_eval_filter_ids(eval_configs)
    operator_map = {
        "greater_than": ">",
        "less_than": "<",
        "equals": "=",
        "not_equals": "!=",
        "greater_than_or_equal": ">=",
        "less_than_or_equal": "<=",
        "between": "BETWEEN",
        "not_between": "NOT BETWEEN",
        "contains": "ILIKE",
    }
    conditions = []
    params = []
    for filter_item in filters or []:
        column_id, is_eval, filter_op, converted_value = (
            _validated_prompt_aggregate_filter(filter_item, allowed_eval_ids)
        )
        if is_eval:
            continue
        sql_expression = _PROMPT_METRIC_SYSTEM_SQL_EXPRESSIONS[column_id]
        if column_id in _PROMPT_METRIC_DATETIME_FILTER_IDS:
            sql_expression = f"({sql_expression})::date"
        operator = operator_map[filter_op]
        if filter_op in {"between", "not_between"}:
            conditions.append(f"{sql_expression} {operator} %s AND %s")
            params.extend(converted_value)
        elif filter_op == "contains":
            conditions.append(f"{sql_expression} ILIKE %s")
            params.append(f"%{converted_value}%")
        else:
            conditions.append(f"{sql_expression} {operator} %s")
            params.append(converted_value)

    if not conditions:
        return None, []
    return " HAVING " + " AND ".join(conditions), params


def _prompt_eval_cte(config):
    """Build one page-restricted eval aggregate CTE and its bound parameters."""

    config_id = str(config.id)
    try:
        config_alias = str(uuid.UUID(config_id)).replace("-", "_")
    except (ValueError, AttributeError) as exc:
        raise ValueError("Invalid evaluation configuration identifier.") from exc

    eval_template_config = config.eval_template.config or {}
    choices = config.eval_template.choices or []
    output_type = eval_template_config.get("output", "score")

    base_join = """INNER JOIN base
            ON base.prompt_version_id = os.prompt_version_id
            AND base.prompt_label_id = os.prompt_label_id"""
    common_where = f"""el.custom_eval_config_id = %s
            AND el.deleted = FALSE
            AND el.target_type = '{EvalTargetType.SPAN}'
            AND el.status = '{EvalEntryStatus.COMPLETED}'
            AND el.error = FALSE
            AND el.skipped_reason IS NULL
            AND os.deleted = FALSE
            AND (el.output_str IS NULL OR el.output_str != 'ERROR')"""

    if choices and output_type == "choices":
        choice_objects = ", ".join(
            [
                "%s, json_build_object("
                "'score', ROUND((100.0 * COUNT(CASE WHEN "
                "el.output_str_list ? %s THEN 1 END) / "
                "NULLIF(COUNT(el.output_str_list), 0))::numeric, 2))"
                for _choice in choices
            ]
        )
        params = [value for choice in choices for value in (choice, choice)]
        params.append(config_id)
        return (
            f"""eval_list_{config_alias} AS (
        SELECT
            os.prompt_version_id,
            os.prompt_label_id,
            json_build_object({choice_objects}) AS metric_{config_alias}
        FROM tracer_eval_logger el
        INNER JOIN tracer_observation_span os ON el.observation_span_id = os.id
        {base_join}
        WHERE {common_where}
            AND el.output_str_list IS NOT NULL
        GROUP BY os.prompt_version_id, os.prompt_label_id
    )""",
            f"eval_list_{config_alias}",
            params,
        )

    if output_type == "Pass/Fail":
        return (
            f"""eval_bool_{config_alias} AS (
        SELECT
            os.prompt_version_id,
            os.prompt_label_id,
            json_build_object(
                'score', ROUND(AVG(CASE
                    WHEN el.output_bool = TRUE THEN 100.0
                    WHEN el.output_bool = FALSE THEN 0.0
                    ELSE NULL
                END)::numeric, 2)
            ) AS metric_{config_alias}
        FROM tracer_eval_logger el
        INNER JOIN tracer_observation_span os ON el.observation_span_id = os.id
        {base_join}
        WHERE {common_where}
            AND el.output_bool IN (TRUE, FALSE)
        GROUP BY os.prompt_version_id, os.prompt_label_id
    )""",
            f"eval_bool_{config_alias}",
            [config_id],
        )

    return (
        f"""eval_float_{config_alias} AS (
        SELECT
            os.prompt_version_id,
            os.prompt_label_id,
            json_build_object(
                'score', ROUND((AVG(el.output_float) * 100)::numeric, 2)
            ) AS metric_{config_alias}
        FROM tracer_eval_logger el
        INNER JOIN tracer_observation_span os ON el.observation_span_id = os.id
        {base_join}
        WHERE {common_where}
            AND el.output_float IS NOT NULL
        GROUP BY os.prompt_version_id, os.prompt_label_id
    )""",
        f"eval_float_{config_alias}",
        [config_id],
    )


_PROMPT_SPAN_SYSTEM_FIELD_MAP = {
    "prompt_template_version": "prompt_template_version",
    "prompt_label_name": "prompt_label_name",
    "name": "span_name",
    "span_name": "span_name",
    "trace_id": "trace_id",
    "span_id": "id",
    "session_id": "session_id",
    "input": "input",
    "output": "output",
    "created_at": "created_at",
}

_PROMPT_SPAN_PROJECTED_FIELDS = (
    "prompt_template_version",
    "id",
    "prompt_label_id",
    "prompt_label_name",
    "input",
    "output",
    "name",
    "observation_type",
    "session_id",
    "created_at",
    "trace_id",
    "project_id",
)


def _prompt_span_metric_aliases(eval_configs):
    aliases = []
    for config in eval_configs:
        output_type = _normalize_prompt_eval_output_type(
            (config.eval_template.config or {}).get("output", "score")
        )
        if output_type in {
            "score",
            "float",
            "numeric",
            "percentage",
            "reason",
            "pass_fail",
            "choices",
        }:
            aliases.append((str(config.id), f"metric_{config.id}"))
    return aliases


def _prompt_span_projection(eval_configs):
    return _PROMPT_SPAN_PROJECTED_FIELDS + tuple(
        alias for _response_key, alias in _prompt_span_metric_aliases(eval_configs)
    )


def _prompt_span_payload_expression(eval_configs):
    """Measure the same projected fields that the service will hydrate."""

    response_fields = {
        "prompt_template_version": F("prompt_template_version"),
        "span_id": F("id"),
        "prompt_label_id": F("prompt_label_id"),
        "prompt_label_name": F("prompt_label_name"),
        "input": F("input"),
        "output": F("output"),
        "name": F("name"),
        "observation_type": F("observation_type"),
        "session_id": F("session_id"),
        "created_at": F("created_at"),
        "trace_id": F("trace_id"),
        "project_id": F("project_id"),
    }
    response_fields.update(
        {
            response_key: F(alias)
            for response_key, alias in _prompt_span_metric_aliases(eval_configs)
        }
    )
    return Func(
        Cast(JSONObject(**response_fields), output_field=TextField()),
        function="OCTET_LENGTH",
        output_field=BigIntegerField(),
    )


def _prompt_span_eval_condition(config, filter_config):
    """Compile one dynamic prompt-span metric against its primitive annotation."""

    alias = f"metric_{config.id}"
    output_type = _normalize_prompt_eval_output_type(
        (config.eval_template.config or {}).get("output", "score")
    )
    filter_type = filter_config.get("filter_type")
    filter_op = filter_config.get("filter_op")
    filter_value = filter_config.get("filter_value")
    contract = get_prompt_span_filter_contract(
        str(config.id), eval_output_type=output_type
    )
    if contract is None:
        raise ValueError(
            f"Unsupported prompt-span evaluation output type: {output_type}"
        )
    expected_type, supported_ops = contract
    if filter_type != expected_type:
        raise ValueError(
            f"Prompt-span evaluation column requires a {expected_type} filter."
        )
    if filter_op not in supported_ops:
        raise ValueError(f"Unsupported prompt-span evaluation operation: {filter_op}")

    if output_type in {"score", "float", "numeric", "percentage", "reason"}:
        if isinstance(filter_value, bool):
            raise ValueError("Invalid score evaluation filter value.")
        is_valid, value = FilterEngine._validate_and_convert_filter_value(
            filter_value, filter_type, filter_op
        )
        if not is_valid:
            raise ValueError("Invalid score evaluation filter value.")
        if filter_op == "not_equals":
            return Q(**{f"{alias}__isnull": False}) & ~Q(**{alias: value})
        if filter_op in {"between", "not_between"}:
            if not isinstance(value, list) or len(value) != 2:
                raise ValueError("Score ranges require exactly two values.")
            inside = Q(**{f"{alias}__gte": value[0]}) & Q(**{f"{alias}__lte": value[1]})
            return (
                Q(**{f"{alias}__isnull": False}) & ~inside
                if filter_op == "not_between"
                else inside
            )
        lookup = {
            "equals": "exact",
            "greater_than": "gt",
            "less_than": "lt",
            "greater_than_or_equal": "gte",
            "less_than_or_equal": "lte",
        }.get(filter_op)
        if lookup is None:
            raise ValueError(f"Unsupported score evaluation operation: {filter_op}")
        return Q(**{f"{alias}__{lookup}": value})

    if output_type == "pass_fail":
        if not isinstance(filter_value, bool):
            raise ValueError("Pass/fail evaluation filters require true or false.")
        if filter_op == "equals":
            return Q(**{alias: filter_value})
        if filter_op == "not_equals":
            return Q(**{f"{alias}__isnull": False}) & ~Q(**{alias: filter_value})
        raise ValueError(f"Unsupported pass/fail evaluation operation: {filter_op}")

    if output_type == "choices":
        values = filter_value if isinstance(filter_value, list) else [filter_value]
        values = [value for value in values if value not in (None, "")]
        if not values:
            raise ValueError("Choice evaluation filters require a value.")
        allowed_choices = set(config.eval_template.choices or [])
        if any(value not in allowed_choices for value in values):
            raise ValueError("Unknown choice evaluation filter value.")
        positive = Q()
        for value in values:
            positive |= Q(**{f"{alias}__contains": [value]})
        if filter_op in {"equals", "in", "contains"}:
            return positive
        if filter_op in {"not_equals", "not_in", "not_contains"}:
            return Q(**{f"{alias}__isnull": False}) & ~positive
        raise ValueError(f"Unsupported choice evaluation operation: {filter_op}")

    raise ValueError(f"Unsupported prompt-span evaluation output type: {output_type}")


def _prompt_span_filter_conditions(filters, eval_configs):
    """Compile every accepted linked-span filter or fail instead of ignoring it."""

    configs_by_id = {str(config.id): config for config in eval_configs}
    system_filters = []
    eval_conditions = Q()
    for filter_item in filters or []:
        column_id, filter_config = FilterEngine._normalize_filter_params(filter_item)
        if not column_id or not filter_config:
            raise ValueError("Each prompt span filter must name a column.")
        if filter_config.get("col_type") == ColType.SPAN_ATTRIBUTE.value:
            raise ValueError(
                "Span-attribute filters are not supported by linked prompt spans."
            )
        if column_id in _PROMPT_SPAN_SYSTEM_FIELD_MAP:
            contract = get_prompt_span_filter_contract(column_id)
            expected_type, supported_ops = contract
            filter_type = filter_config.get("filter_type")
            filter_op = filter_config.get("filter_op")
            filter_value = filter_config.get("filter_value")
            if filter_type != expected_type:
                raise ValueError(
                    f"Unsupported filter type for prompt span column: {column_id}"
                )
            if filter_op not in supported_ops:
                raise ValueError(
                    f"Unsupported filter operation for prompt span column: {column_id}"
                )
            if expected_type == "datetime":
                values = (
                    filter_value if isinstance(filter_value, list) else [filter_value]
                )
                required_count = 2 if filter_op in {"between", "not_between"} else 1
                if len(values) != required_count or any(
                    _prompt_filter_date(value) is None for value in values
                ):
                    raise ValueError("Invalid prompt span datetime filter value.")
            elif not isinstance(filter_value, str) or not filter_value:
                raise ValueError("Invalid prompt span text filter value.")
            if column_id in {"trace_id", "session_id"}:
                try:
                    uuid.UUID(filter_value)
                except (ValueError, AttributeError) as exc:
                    raise ValueError("Invalid prompt span UUID filter value.") from exc
            system_filters.append(filter_item)
            continue
        config = configs_by_id.get(column_id)
        if config is None:
            raise ValueError(f"Unsupported prompt span filter column: {column_id}")
        eval_conditions &= _prompt_span_eval_condition(config, filter_config)

    system_conditions = FilterEngine.get_filter_conditions_for_system_metrics(
        system_filters,
        field_map=_PROMPT_SPAN_SYSTEM_FIELD_MAP,
    )
    return system_conditions & eval_conditions


def fetch_prompt_metrics_query_sql_cte(
    prompt_template: PromptTemplate,
    eval_configs: list[CustomEvalConfig],
    filters: dict,
    page_number: int | None = 0,
    page_size: int | None = 10,
):
    """
    Fetch prompt metrics using raw SQL with CTE (Common Table Expression) approach.
    This avoids GROUP BY correlation issues by pre-computing eval metrics in separate CTEs.

    Args:
        prompt_template: The prompt template to fetch metrics for
        eval_configs: List of evaluation configurations to include
        filters: Filter conditions to apply
        page_number: Page number (0-based), defaults to 0
        page_size: Number of results per page, defaults to 10

    Returns:
        List of dictionaries containing the prompt metrics data in the same format as Django ORM

    Raises:
        ValueError: If page_number or page_size are negative
    """

    try:
        # Validate pagination parameters
        if page_number is not None and page_number < 0:
            raise ValueError(f"page_number must be non-negative, got {page_number}")
        if page_size is not None and page_size < 0:
            raise ValueError(f"page_size must be non-negative, got {page_size}")

        # Set defaults if None
        page_number = page_number if page_number is not None else 0
        page_size = page_size if page_size is not None else 10

        _validate_prompt_metric_filters(filters, eval_configs)

        # Build CTEs and track joins/selects for each eval config. The base CTE
        # comes first, so every eval aggregate joins only this prompt's finite
        # version/label population instead of scanning and grouping unrelated
        # tenants' evaluation rows.
        cte_parts = []
        cte_joins = []
        cte_selects = []
        eval_params = []

        for config in eval_configs:
            config_alias = str(config.id).replace("-", "_").replace(" ", "_")
            cte_sql, cte_name, cte_params = _prompt_eval_cte(config)
            cte_parts.append(cte_sql)
            cte_joins.append(
                f"LEFT JOIN {cte_name} ON base.prompt_version_id = "
                f"{cte_name}.prompt_version_id AND base.prompt_label_id = "
                f"{cte_name}.prompt_label_id"
            )
            cte_selects.append(f"{cte_name}.metric_{config_alias}")
            eval_params.extend(cte_params)

        # Compile only this endpoint's validated system fields. In particular,
        # UUIDs never receive ILIKE and datetime values compare as calendar dates.
        system_metrics_having, system_params = _prompt_metric_system_having(
            filters, eval_configs
        )

        # Add base metrics CTE
        base_cte = prompt_metrics_cte_base_query.replace(
            "    FROM tracer_observation_span os\n",
            "    FROM tracer_observation_span os\n"
            "    INNER JOIN tracer_project project_scope "
            "ON os.project_id = project_scope.id\n"
            "    LEFT JOIN accounts_workspace workspace_scope "
            "ON project_scope.workspace_id = workspace_scope.id\n",
            1,
        ).replace(
            "        AND pv.deleted = FALSE\n",
            "        AND pv.deleted = FALSE\n"
            "        AND os.deleted = FALSE\n"
            "        AND pl.deleted = FALSE\n",
            1,
        )

        base_scope_params = []
        organization_id = getattr(prompt_template, "organization_id", None)
        if organization_id is not None:
            scope_sql = (
                "        AND project_scope.organization_id = %s\n"
                "        AND project_scope.deleted = FALSE\n"
            )
            base_scope_params.append(str(organization_id))
            workspace = request_workspace()
            if workspace is not None and getattr(workspace, "is_default", False):
                scope_sql += (
                    "        AND (project_scope.workspace_id = %s "
                    "OR project_scope.workspace_id IS NULL "
                    "OR (workspace_scope.is_default = TRUE "
                    "AND workspace_scope.organization_id = %s "
                    "AND workspace_scope.deleted = FALSE))\n"
                )
                base_scope_params.extend(
                    [str(workspace.id), str(workspace.organization_id)]
                )
            elif workspace is not None:
                scope_sql += "        AND project_scope.workspace_id = %s\n"
                base_scope_params.append(str(workspace.id))
            base_cte = base_cte.replace(
                "        AND pl.deleted = FALSE\n",
                "        AND pl.deleted = FALSE\n" + scope_sql,
                1,
            )

        # Add HAVING clause if there are system metrics filters
        if system_metrics_having:
            base_cte += system_metrics_having

        # Close the base CTE
        base_cte += "\n)"

        # Build the complete SQL with base first so eval CTEs can join it.
        full_sql = "WITH " + base_cte
        if cte_parts:
            full_sql += ",\n" + ",\n".join(cte_parts)

        # Get filter conditions for eval metrics (WHERE clause for final SELECT)
        eval_metrics_where, eval_params_filter = (
            FilterEngine.get_sql_filter_conditions_for_cte_eval_metrics(filters)
        )

        # Materialize the filtered population once, then derive a count and a
        # page from it. A LEFT JOIN from the one-row count CTE preserves the
        # true total even when OFFSET is beyond the final row; a window count
        # disappears entirely on an empty page and incorrectly reports zero.
        filtered_select = (
            "\n, filtered AS (\nSELECT base.*, count(*) OVER () AS __total_rows"
        )
        if cte_selects and len(cte_selects) > 0:
            filtered_select += ", " + ", ".join(cte_selects)
        filtered_select += "\nFROM base\n"
        if cte_joins and len(cte_joins) > 0:
            filtered_select += "\n".join(cte_joins) + "\n"

        # Add eval metrics filter (WHERE clause after joins)
        if eval_metrics_where:
            filtered_select += eval_metrics_where + "\n"

        filtered_select += "\n)"
        filtered_select += """,
paged AS (
    SELECT *
    FROM filtered
    ORDER BY version_created_at DESC, prompt_version_id, prompt_label_id
    LIMIT %s OFFSET %s
),
counted AS (
    SELECT count(*) AS __counted_total_rows FROM filtered
)
SELECT paged.*,
       COALESCE(paged.__total_rows, counted.__counted_total_rows)
           AS __resolved_total_rows
FROM counted
LEFT JOIN paged ON TRUE
ORDER BY paged.version_created_at DESC, paged.prompt_version_id, paged.prompt_label_id"""

        full_sql += filtered_select

        # Prepare parameters:
        # 1. prompt_template_id and system params (base CTE appears first),
        # 2. eval params (page-restricted eval CTEs),
        # 4. eval filter params (for WHERE clause),
        # 5. pagination
        params = (
            [str(prompt_template.id)]
            + base_scope_params
            + system_params
            + eval_params
            + eval_params_filter
            + [page_size, page_number * page_size]
        )

        # Execute the query
        with connection.cursor() as cursor:
            cursor.execute(full_sql, params)

            # Get column names
            columns = [col[0] for col in cursor.description]

            # Convert results to list of dictionaries
            results = []
            total_count = 0
            for row in cursor.fetchall():
                row_dict = dict(zip(columns, row, strict=True))
                resolved_total = row_dict.pop("__resolved_total_rows", None)
                window_total = row_dict.pop("__total_rows", None)
                total_count = int(
                    resolved_total
                    if resolved_total is not None
                    else (window_total if window_total is not None else total_count)
                )

                # The count-preserving LEFT JOIN yields one all-null page row
                # when OFFSET is past the end. It carries metadata only.
                if row_dict.get("prompt_version_id") is None:
                    continue

                # Convert any datetime objects to strings for JSON serialization
                for key, value in row_dict.items():
                    if hasattr(value, "isoformat"):
                        row_dict[key] = value.isoformat()

                results.append(row_dict)

        return results, total_count

    except (ValueError, TypeError) as e:
        logger.exception("Invalid parameters for prompt metrics query")
        raise ValueError(f"Invalid filter or configuration parameters: {str(e)}") from e
    except Exception:
        logger.exception("Database error while fetching prompt metrics with CTE SQL")
        raise


def fetch_prompt_metrics_span_query(
    prompt_template: PromptTemplate,
    eval_configs: list[CustomEvalConfig],
    filters: dict,
    search_term: str | None = None,
    page_number: int | None = 0,
    page_size: int | None = 10,
):
    base_query = ObservationSpan.objects.filter(
        request_workspace_filter(field_name="project__workspace"),
        prompt_version__original_template=prompt_template,
        prompt_version__deleted=False,
        prompt_version_id__isnull=False,
        prompt_label_id__isnull=False,
        prompt_label__deleted=False,
        project__organization_id=prompt_template.organization_id,
        project__deleted=False,
        trace__deleted=False,
        deleted=False,
    ).annotate(
        prompt_template_version=F("prompt_version__template_version"),
        # A span belongs to exactly one trace/session. Joining every
        # project session duplicated each span and made count/page results
        # grow with unrelated sessions in the project.
        session_id=F("trace__session_id"),
        span_name=F("name"),
        prompt_label_name=F("prompt_label__name"),
    )

    # Add annotations for each eval config dynamically
    for config in eval_configs:
        annotation_value: Round | F | None = None
        output_type = _normalize_prompt_eval_output_type(
            (config.eval_template.config or {}).get("output", "score")
        )

        if output_type in {"score", "float", "numeric", "percentage", "reason"}:
            annotation_value = Round(F("output_float") * 100, 2)
        elif output_type == "pass_fail":
            annotation_value = F("output_bool")
        elif output_type == "choices":
            annotation_value = F("output_str_list")
        else:
            continue

        base_query = base_query.annotate(
            **{
                f"metric_{config.id}": Subquery(
                    completed_prompt_eval_logs(
                        EvalLogger.objects.filter(
                            observation_span_id=OuterRef("id"),
                            custom_eval_config_id=config.id,
                            target_type=EvalTargetType.SPAN,
                        )
                    )
                    .annotate(transformed_value=annotation_value)
                    .order_by("-created_at", "-id")
                    .values("transformed_value")[:1]
                )
            }
        )

    base_query = base_query.order_by("-created_at", "id")

    if filters or search_term:
        # Combine all filter conditions into a single Q object
        combined_filter_conditions = Q()

        # Handle search term with OR condition
        if search_term:
            search_conditions = Q(span_name__icontains=search_term) | Q(
                prompt_template_version__icontains=search_term
            )
            combined_filter_conditions &= search_conditions

        if filters:
            combined_filter_conditions &= _prompt_span_filter_conditions(
                filters, eval_configs
            )

        # Apply combined filters in a single operation
        if combined_filter_conditions:
            base_query = base_query.filter(combined_filter_conditions)

    # Move pagination outside the filters block so it always applies
    start = page_number * page_size
    end = start + page_size
    total_count = base_query.count()
    page_query = base_query.annotate(
        _response_payload_bytes=_prompt_span_payload_expression(eval_configs)
    )
    page_refs = list(page_query.values_list("id", "_response_payload_bytes")[start:end])
    # Include the response array delimiters and one separator per projected row.
    page_payload_bytes = 2 + sum(
        int(payload_bytes or 0) + 1 for _span_id, payload_bytes in page_refs
    )
    if page_payload_bytes > PROMPT_SPAN_PAGE_DB_PAYLOAD_BYTES:
        raise PromptMetricsQueryLimitExceeded(
            "Prompt span page exceeds the interactive payload limit."
        )

    page_ids = [span_id for span_id, _payload_bytes in page_refs]
    if not page_ids:
        return [], total_count

    # A values projection is intentional: it prevents wide ObservationSpan or
    # related model columns from being hydrated outside the payload preflight.
    projected_rows = base_query.filter(id__in=page_ids).values(
        *_prompt_span_projection(eval_configs)
    )
    rows_by_id = {str(row["id"]): row for row in projected_rows}
    results = [
        rows_by_id[str(span_id)] for span_id in page_ids if str(span_id) in rows_by_id
    ]

    return results, total_count
