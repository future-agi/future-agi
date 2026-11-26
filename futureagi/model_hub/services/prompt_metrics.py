from contextlib import contextmanager

import structlog
from django.conf import settings
from django.db import connection, transaction
from django.shortcuts import get_object_or_404

from model_hub.models.run_prompt import PromptTemplate
from model_hub.queries.prompt.prompt_metrics import (
    PromptMetricsQueryLimitExceeded,
    completed_prompt_eval_logs,
    fetch_prompt_metrics_query_sql_cte,
    fetch_prompt_metrics_span_query,
    get_prompt_aggregate_filter_contract,
    get_prompt_span_filter_contract,
)
from model_hub.schema.prompt.prompt_metrics import (
    FetchPromptMetricsRequest,
    FetchPromptSpanMetricsRequest,
)
from model_hub.utils.helpers import (
    get_default_prompt_metrics_config,
    get_default_span_prompt_metrics_config,
)
from model_hub.utils.workspace_scope import request_workspace_filter
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import EvalLogger, EvalTargetType
from tracer.services.clickhouse.read_budget import ReadDeadline
from tracer.utils.helper import update_column_config_based_on_eval_config

logger = structlog.get_logger(__name__)

PROMPT_METRICS_REQUEST_WALL_MS = settings.INTERACTIVE_READ_DEFAULT_WALL_MS
PROMPT_METRICS_MAX_EVAL_COLUMNS = settings.PROMPT_METRICS_MAX_EVAL_COLUMNS
PROMPT_METRICS_MAX_CHOICE_UTF8_BYTES = settings.PROMPT_METRICS_MAX_CHOICE_UTF8_BYTES
PROMPT_METRICS_MAX_TOTAL_CHOICE_UTF8_BYTES = (
    settings.PROMPT_METRICS_MAX_TOTAL_CHOICE_UTF8_BYTES
)
PROMPT_METRICS_MAX_RESPONSE_UNITS = settings.INTERACTIVE_READ_DEFAULT_MAX_RESPONSE_UNITS
PROMPT_METRICS_MAX_OFFSET = settings.PROMPT_METRICS_MAX_OFFSET
PROMPT_METRICS_MAX_PAGE_SIZE = settings.INTERACTIVE_READ_DEFAULT_MAX_PAGE_SIZE


class PromptMetricsReadLimitExceeded(RuntimeError):
    """The requested prompt table is too wide for one interactive read."""


def _publish_prompt_filter_contract(column_config, *, aggregate):
    """Attach the same endpoint-specific contract enforced by the query layer."""

    for column in column_config:
        is_eval = column.get("property_kind") == "eval_config"
        if aggregate:
            contract = get_prompt_aggregate_filter_contract(
                column.get("id"), is_eval=is_eval
            )
        else:
            contract = get_prompt_span_filter_contract(
                column.get("id"),
                eval_output_type=column.get("output_type") if is_eval else None,
            )
        if contract is None:
            continue
        filter_type, supported_filter_ops = contract
        column["filter_type"] = filter_type
        column["supported_filter_ops"] = list(supported_filter_ops)
    return column_config


def _bounded_prompt_metric_configs(queryset):
    configs = list(queryset[: PROMPT_METRICS_MAX_EVAL_COLUMNS + 1])
    if len(configs) > PROMPT_METRICS_MAX_EVAL_COLUMNS:
        raise PromptMetricsReadLimitExceeded(
            f"Prompt metrics support at most {PROMPT_METRICS_MAX_EVAL_COLUMNS} "
            "evaluation columns per request."
        )

    output_columns = 0
    total_choice_bytes = 0
    for config in configs:
        eval_template_config = config.eval_template.config or {}
        choices = config.eval_template.choices or []
        is_choice_metric = (
            bool(choices) and eval_template_config.get("output", "score") == "choices"
        )
        if is_choice_metric:
            if not isinstance(choices, list):
                raise PromptMetricsReadLimitExceeded(
                    "Prompt metric choices must be a string list."
                )
            for choice in choices:
                if not isinstance(choice, str) or not choice:
                    raise PromptMetricsReadLimitExceeded(
                        "Prompt metric choices must be non-empty strings."
                    )
                choice_bytes = len(choice.encode("utf-8"))
                if choice_bytes > PROMPT_METRICS_MAX_CHOICE_UTF8_BYTES:
                    raise PromptMetricsReadLimitExceeded(
                        "A prompt metric choice label exceeds the interactive limit."
                    )
                total_choice_bytes += choice_bytes
            if len(set(choices)) != len(choices):
                raise PromptMetricsReadLimitExceeded(
                    "Prompt metric choices must be unique."
                )
            output_columns += len(choices)
        else:
            output_columns += 1

        if (
            output_columns > PROMPT_METRICS_MAX_EVAL_COLUMNS
            or total_choice_bytes > PROMPT_METRICS_MAX_TOTAL_CHOICE_UTF8_BYTES
        ):
            raise PromptMetricsReadLimitExceeded(
                f"Prompt metrics support at most {PROMPT_METRICS_MAX_EVAL_COLUMNS} "
                "expanded evaluation columns per request."
            )
    return configs


def _validate_prompt_metrics_page(page_number, page_size):
    page_number = int(page_number)
    page_size = int(page_size)
    if page_number < 0 or page_size < 1 or page_size > PROMPT_METRICS_MAX_PAGE_SIZE:
        raise PromptMetricsReadLimitExceeded(
            f"Prompt metrics page_size must be between 1 and "
            f"{PROMPT_METRICS_MAX_PAGE_SIZE}."
        )
    offset = page_number * page_size
    if offset > PROMPT_METRICS_MAX_OFFSET:
        raise PromptMetricsReadLimitExceeded(
            "This prompt metrics page is too deep for an interactive read."
        )
    return offset


def _ensure_prompt_metrics_response_bounded(value):
    """Bound JSON-renderer work without allocating a duplicate encoded body."""

    remaining = PROMPT_METRICS_MAX_RESPONSE_UNITS
    stack = [value]
    while stack:
        item = stack.pop()
        if item is None or isinstance(item, bool):
            remaining -= 4
        elif isinstance(item, str):
            remaining -= 4 * len(item) + 2
        elif isinstance(item, int | float):
            remaining -= 32
        elif isinstance(item, dict):
            remaining -= 2 + 2 * len(item)
            for key, child in item.items():
                remaining -= 4 * len(str(key)) + 2
                stack.append(child)
        elif isinstance(item, list | tuple):
            remaining -= 2 + len(item)
            stack.extend(item)
        else:
            remaining -= 4 * len(str(item)) + 2
        if remaining < 0:
            raise PromptMetricsReadLimitExceeded(
                "Prompt metrics response exceeds the interactive payload limit."
            )


def _execute_prompt_metrics_query_with_deadline(
    deadline, execute, sql, params, many, context
):
    """Shrink PostgreSQL's per-statement timeout to one request wall."""

    remaining_ms = deadline.remaining_ms(floor_ms=1)
    context["cursor"].cursor.execute(
        "SELECT set_config('statement_timeout', %s, true)",
        (f"{remaining_ms}ms",),
    )
    result = execute(sql, params, many, context)
    deadline.remaining_ms(floor_ms=1)
    return result


@contextmanager
def bounded_prompt_metrics_read(deadline):
    """Bound every ORM/raw-SQL phase to the same monotonic request wall."""

    def execute_with_remaining_timeout(execute, sql, params, many, context):
        return _execute_prompt_metrics_query_with_deadline(
            deadline, execute, sql, params, many, context
        )

    with transaction.atomic():
        if connection.vendor != "postgresql":
            yield
            deadline.remaining_ms(floor_ms=1)
            return
        with connection.execute_wrapper(execute_with_remaining_timeout):
            yield


def _get_prompt_template_for_metrics(prompt_template_id, organization_id):
    queryset = PromptTemplate.no_workspace_objects.filter(
        request_workspace_filter(),
        id=prompt_template_id,
        organization=organization_id,
        deleted=False,
    ).select_related("workspace")
    return get_object_or_404(queryset)


def _get_eval_configs_for_prompt(prompt_template):
    """Get eval configs associated with a prompt template's spans."""
    visible_config_ids = (
        completed_prompt_eval_logs(
            EvalLogger.objects.filter(
                target_type=EvalTargetType.SPAN,
                observation_span__prompt_version__original_template=prompt_template,
                observation_span__prompt_version__deleted=False,
                observation_span__project__organization_id=(
                    prompt_template.organization_id
                ),
                observation_span__project__deleted=False,
                observation_span__deleted=False,
            )
        )
        .order_by()
        .values_list("custom_eval_config_id", flat=True)
        .distinct()
    )
    return _bounded_prompt_metric_configs(
        CustomEvalConfig.objects.filter(
            request_workspace_filter(field_name="project__workspace"),
            project__organization_id=prompt_template.organization_id,
            project__deleted=False,
            eval_template__deleted=False,
            id__in=visible_config_ids,
            deleted=False,
        )
        .select_related("eval_template")
        .order_by("-created_at", "id")
    )


def fetch_prompt_metrics(
    request: FetchPromptMetricsRequest, *, deadline: ReadDeadline | None = None
):
    """
    Fetch prompt metrics using validated Pydantic request model.

    Args:
        request (FetchPromptMetricsRequest): Validated request containing prompt_template_id and filters

    Returns:
        Dict containing the prompt metrics data
    """
    try:
        deadline = deadline or ReadDeadline.start(PROMPT_METRICS_REQUEST_WALL_MS)
        deadline.remaining_ms(floor_ms=1)
        prompt_template_id = str(request.prompt_template_id)
        organization_id = str(
            request.organization_id
        )  # Fixed: Using 'organization_id' to match Pydantic model
        filters = request.filters
        page_number = request.page_number if request.page_number else 0
        page_size = request.page_size if request.page_size else 10
        _validate_prompt_metrics_page(page_number, page_size)

        prompt_template = _get_prompt_template_for_metrics(
            prompt_template_id,
            organization_id,
        )

        eval_configs = _get_eval_configs_for_prompt(prompt_template)

        results, total_count = fetch_prompt_metrics_query_sql_cte(
            prompt_template, eval_configs, filters, page_number, page_size
        )
        deadline.remaining_ms(floor_ms=1)
        column_config = get_default_prompt_metrics_config()
        column_config = update_column_config_based_on_eval_config(
            column_config, eval_configs
        )
        column_config = _publish_prompt_filter_contract(column_config, aggregate=True)

        # Process results into final format
        table_data = []

        for result in results:
            deadline.remaining_ms(floor_ms=1)
            version_id = str(result["prompt_version_id"])
            row = {
                "prompt_version_id": version_id,
                "prompt_template_version": result["prompt_template_version"],
                "avg_latency": result["row_avg_latency_ms"],
                "avg_input_tokens": result["avg_input_tokens"],
                "avg_output_tokens": result["avg_output_tokens"],
                "total_spans": result["total_spans"],
                "unique_traces": result["unique_traces"],
                "avg_cost": result["row_avg_cost"],
                "first_used": result["first_used"],
                "last_used": result["last_used"],
                "prompt_label_id": result["prompt_label_id"],
                "prompt_label_name": result["prompt_label_name"],
            }

            # Add eval metrics from annotated fields
            for config in eval_configs:
                config_alias = str(config.id).replace("-", "_").replace(" ", "_")
                data = result.get(f"metric_{config_alias}")
                if data and isinstance(data, dict):
                    if "score" in data:
                        row[str(config.id)] = (
                            round(data["score"], 2)
                            if data["score"] is not None
                            else None
                        )
                    else:
                        # Handle choice-based metrics
                        for key, value in data.items():
                            if isinstance(value, dict) and "score" in value:
                                row[str(config.id) + "**" + key] = (
                                    round(value["score"], 2)
                                    if value["score"] is not None
                                    else None
                                )
                else:
                    row[str(config.id)] = None

            table_data.append(row)

        response = {
            "prompt_template_id": str(prompt_template.id),
            "prompt_template_name": prompt_template.name,
            "table": table_data,
            "config": column_config,
            "metadata": {"total_rows": total_count},
        }

        _ensure_prompt_metrics_response_bounded(response)
        deadline.remaining_ms(floor_ms=1)
        return response

    except Exception as e:
        logger.error(
            f"Error while fetching the prompt-observe metrics manager: {str(e)}"
        )
        raise e


def _format_prompt_span_row(result, eval_configs):
    """Format one strict values-projected span row without model traversal."""

    row = {
        "prompt_template_version": result["prompt_template_version"],
        "span_id": str(result["id"]),
        "prompt_label_id": (
            str(result["prompt_label_id"])
            if result["prompt_label_id"] is not None
            else None
        ),
        "prompt_label_name": result["prompt_label_name"],
        "input": result["input"],
        "output": result["output"],
        "name": result["name"],
        "observation_type": result["observation_type"],
        "session_id": (
            str(result["session_id"]) if result["session_id"] is not None else None
        ),
        "created_at": result["created_at"],
        "trace_id": str(result["trace_id"]),
        "project_id": str(result["project_id"]),
    }

    for config in eval_configs:
        value = result.get(f"metric_{config.id}")
        if value is not None:
            row[str(config.id)] = value
    return row


def fetch_prompt_metrics_span_view(
    request: FetchPromptSpanMetricsRequest, *, deadline: ReadDeadline | None = None
):
    """
    Fetch prompt metrics using validated Pydantic request model.

    Args:
        request (FetchPromptSpanMetricsRequest): Validated request containing prompt_template_id and filters

    Returns:
        Dict containing the prompt metrics data
    """

    deadline = deadline or ReadDeadline.start(PROMPT_METRICS_REQUEST_WALL_MS)
    deadline.remaining_ms(floor_ms=1)
    prompt_template_id = request.prompt_template_id
    organization_id = request.organization_id
    filters = request.filters
    search_term = request.search_term
    page_number = request.page_number if request.page_number else 0
    page_size = request.page_size if request.page_size else 10
    _validate_prompt_metrics_page(page_number, page_size)

    prompt_template = _get_prompt_template_for_metrics(
        prompt_template_id,
        organization_id,
    )

    eval_configs = _get_eval_configs_for_prompt(prompt_template)

    try:
        results, total_count = fetch_prompt_metrics_span_query(
            prompt_template,
            eval_configs,
            filters or {},
            search_term,
            page_number=page_number,
            page_size=page_size,
        )
    except PromptMetricsQueryLimitExceeded as exc:
        raise PromptMetricsReadLimitExceeded(str(exc)) from exc
    deadline.remaining_ms(floor_ms=1)
    # Process results into final format
    table_data = []

    column_config = get_default_span_prompt_metrics_config()
    column_config = update_column_config_based_on_eval_config(
        column_config, eval_configs, skip_choices=True
    )
    column_config = _publish_prompt_filter_contract(column_config, aggregate=False)

    for result in results:
        deadline.remaining_ms(floor_ms=1)
        table_data.append(_format_prompt_span_row(result, eval_configs))

    response = {
        "table": table_data,
        "config": column_config,
        "metadata": {"total_rows": total_count},
    }
    _ensure_prompt_metrics_response_bounded(response)
    deadline.remaining_ms(floor_ms=1)
    return response
