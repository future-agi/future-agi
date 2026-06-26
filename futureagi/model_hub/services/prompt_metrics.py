import structlog
from django.shortcuts import get_object_or_404

from model_hub.models.run_prompt import PromptTemplate
from model_hub.queries.prompt.prompt_metrics import (
    fetch_prompt_metrics_query_sql_cte,
    fetch_prompt_metrics_span_query,
)
from model_hub.schema.prompt.prompt_metrics import FetchPromptMetricsRequest
from model_hub.utils.helpers import (
    get_default_prompt_metrics_config,
    get_default_span_prompt_metrics_config,
)
from model_hub.utils.workspace_scope import request_workspace_filter
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import EvalLogger
from tracer.utils.helper import update_column_config_based_on_eval_config

logger = structlog.get_logger(__name__)


def _get_prompt_template_for_metrics(prompt_template_id, organization_id):
    queryset = PromptTemplate.no_workspace_objects.filter(
        request_workspace_filter(),
        id=prompt_template_id,
        organization=organization_id,
        deleted=False,
    )
    return get_object_or_404(queryset)


def _get_eval_configs_for_prompt(prompt_template):
    """Get eval configs associated with a prompt template's spans."""
    return list(
        CustomEvalConfig.objects.filter(
            id__in=EvalLogger.objects.filter(
                observation_span__prompt_version__original_template=prompt_template
            )
            .values("custom_eval_config_id")
            .distinct(),
            deleted=False,
        ).select_related("eval_template")
    )


def fetch_prompt_metrics(request: FetchPromptMetricsRequest):
    """
    Fetch prompt metrics using validated Pydantic request model.

    Args:
        request (FetchPromptMetricsRequest): Validated request containing prompt_template_id and filters

    Returns:
        Dict containing the prompt metrics data
    """
    try:
        prompt_template_id = str(request.prompt_template_id)
        organization_id = str(
            request.organization_id
        )  # Fixed: Using 'organization_id' to match Pydantic model
        filters = request.filters
        page_number = request.page_number if request.page_number else 0
        page_size = request.page_size if request.page_size else 10

        prompt_template = _get_prompt_template_for_metrics(
            prompt_template_id,
            organization_id,
        )

        eval_configs = _get_eval_configs_for_prompt(prompt_template)

        results = fetch_prompt_metrics_query_sql_cte(
            prompt_template, eval_configs, filters, page_number, page_size
        )
        column_config = get_default_prompt_metrics_config()
        column_config = update_column_config_based_on_eval_config(
            column_config, eval_configs
        )

        # Process results into final format
        table_data = []

        for result in results:
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
            "metadata": {"total_rows": len(table_data)},
        }

        return response

    except Exception as e:
        logger.error(
            f"Error while fetching the prompt-observe metrics manager: {str(e)}"
        )
        raise e


def fetch_prompt_metrics_span_view(request: FetchPromptMetricsRequest):
    """
    Fetch prompt metrics using validated Pydantic request model.

    Args:
        request (FetchPromptMetricsRequest): Validated request containing prompt_template_id and filters

    Returns:
        Dict containing the prompt metrics data
    """

    prompt_template_id = request.prompt_template_id
    organization_id = request.organization_id
    filters = request.filters
    search_term = request.search_term
    page_number = request.page_number if request.page_number else 0
    page_size = request.page_size if request.page_size else 10

    prompt_template = _get_prompt_template_for_metrics(
        prompt_template_id,
        organization_id,
    )

    eval_configs = _get_eval_configs_for_prompt(prompt_template)

    results, total_count = fetch_prompt_metrics_span_query(
        prompt_template,
        eval_configs,
        filters or {},
        search_term,
        page_number=page_number,
        page_size=page_size,
    )
    # Process results into final format
    table_data = []

    column_config = get_default_span_prompt_metrics_config()
    column_config = update_column_config_based_on_eval_config(
        column_config, eval_configs, skip_choices=True
    )

    for result in results:
        row = {
            "prompt_template_version": result.prompt_template_version,
            "span_id": str(result.id),
            "prompt_label_id": result.prompt_label_id,
            "prompt_label_name": result.prompt_label_name,
            "input": result.input,
            "output": result.output,
            "name": result.name,
            "observation_type": result.observation_type,
            "session_id": str(result.session_id),
            "created_at": result.created_at,
            "trace_id": str(result.trace_id),
            "project_id": str(result.project.id),
        }

        for config in eval_configs:
            value = getattr(result, f"metric_{config.id}", None)
            if value is not None:
                row[str(config.id)] = value

        table_data.append(row)

    return {
        "table": table_data,
        "config": column_config,
        "metadata": {"total_rows": total_count},
    }
