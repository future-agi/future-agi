from typing import Optional

import structlog
from django.db import connection
from django.db.models import (
    Avg,
    Case,
    Count,
    Exists,
    F,
    FloatField,
    IntegerField,
    JSONField,
    Max,
    Min,
    OuterRef,
    Q,
    Subquery,
    Value,
    When,
)
from django.db.models.functions import Coalesce, JSONObject, Round

logger = structlog.get_logger(__name__)
from model_hub.models.run_prompt import PromptTemplate
from model_hub.utils.SQL_queries import (
    create_boolean_eval_cte,
    create_float_eval_cte,
    create_list_eval_cte,
    prompt_metrics_cte_base_query,
)
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.utils.filters import FilterEngine


def fetch_prompt_metrics_query_sql_cte(
    prompt_template: PromptTemplate,
    eval_configs: list[CustomEvalConfig],
    filters: dict,
    page_number: Optional[int] = 0,
    page_size: Optional[int] = 10,
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

        # Build CTEs and track joins/selects for each eval config
        cte_parts = []
        cte_joins = []
        cte_selects = []
        eval_params = []

        for config in eval_configs:
            config_id = config.id
            config_alias = str(config_id).replace("-", "_").replace(" ", "_")
            choices = (
                config.eval_template.choices if config.eval_template.choices else []
            )
            eval_template_config = config.eval_template.config or {}
            output_type = eval_template_config.get("output", "score")

            # Create appropriate CTE and tracking based on eval type
            if choices and output_type == "choices":
                cte_parts.append(create_list_eval_cte(config_alias, choices))
                cte_joins.append(
                    f"LEFT JOIN eval_list_{config_alias} ON base.prompt_version_id = eval_list_{config_alias}.prompt_version_id AND base.prompt_label_id = eval_list_{config_alias}.prompt_label_id"
                )
                cte_selects.append(f"eval_list_{config_alias}.metric_{config_alias}")
            elif output_type == "Pass/Fail":
                cte_parts.append(create_boolean_eval_cte(config_alias))
                cte_joins.append(
                    f"LEFT JOIN eval_bool_{config_alias} ON base.prompt_version_id = eval_bool_{config_alias}.prompt_version_id AND base.prompt_label_id = eval_bool_{config_alias}.prompt_label_id"
                )
                cte_selects.append(f"eval_bool_{config_alias}.metric_{config_alias}")
            else:  # score/float type
                cte_parts.append(create_float_eval_cte(config_alias))
                cte_joins.append(
                    f"LEFT JOIN eval_float_{config_alias} ON base.prompt_version_id = eval_float_{config_alias}.prompt_version_id AND base.prompt_label_id = eval_float_{config_alias}.prompt_label_id"
                )
                cte_selects.append(f"eval_float_{config_alias}.metric_{config_alias}")

            eval_params.append(str(config_id))

        # Get filter conditions for system metrics (HAVING clause for base CTE)
        system_metrics_having, system_params = (
            FilterEngine.get_sql_filter_conditions_for_cte_system_metrics(filters)
        )

        # Add base metrics CTE
        base_cte = prompt_metrics_cte_base_query

        # Add HAVING clause if there are system metrics filters
        if system_metrics_having:
            base_cte += system_metrics_having

        # Close the base CTE
        base_cte += "\n)"

        # Build the complete SQL with CTEs
        if cte_parts and len(cte_parts) > 0:
            full_sql = "WITH " + ",\n".join(cte_parts) + ",\n" + base_cte
        else:
            # Even with no eval configs, we still need WITH keyword for the base CTE
            full_sql = "WITH " + base_cte

        # Get filter conditions for eval metrics (WHERE clause for final SELECT)
        eval_metrics_where, eval_params_filter = (
            FilterEngine.get_sql_filter_conditions_for_cte_eval_metrics(filters)
        )

        # Build final SELECT with joins
        final_select = "\nSELECT base.*"
        if cte_selects and len(cte_selects) > 0:
            final_select += ", " + ", ".join(cte_selects)
        final_select += "\nFROM base\n"
        if cte_joins and len(cte_joins) > 0:
            final_select += "\n".join(cte_joins) + "\n"

        # Add eval metrics filter (WHERE clause after joins)
        if eval_metrics_where:
            final_select += eval_metrics_where + "\n"

        final_select += "ORDER BY base.version_created_at DESC\nLIMIT %s OFFSET %s"

        full_sql += final_select

        # Prepare parameters:
        # 1. eval params (for CTEs),
        # 2. prompt_template_id (for base CTE),
        # 3. system filter params (for HAVING clause),
        # 4. eval filter params (for WHERE clause),
        # 5. pagination
        params = (
            eval_params
            + [str(prompt_template.id)]
            + system_params
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
            for row in cursor.fetchall():
                row_dict = dict(zip(columns, row))

                # Convert any datetime objects to strings for JSON serialization
                for key, value in row_dict.items():
                    if hasattr(value, "isoformat"):
                        row_dict[key] = value.isoformat()

                results.append(row_dict)

        return results

    except (ValueError, TypeError) as e:
        logger.exception(f"Invalid parameters for prompt metrics query")
        raise ValueError(f"Invalid filter or configuration parameters: {str(e)}") from e
    except Exception as e:
        logger.exception(f"Database error while fetching prompt metrics with CTE SQL")
        raise


def fetch_prompt_metrics_span_query(
    prompt_template: PromptTemplate,
    eval_configs: list[CustomEvalConfig],
    filters: dict,
    search_term: str | None = None,
    page_number: int | None = 0,
    page_size: int | None = 10,
):
    # CH25-TODO: this is a deep ORM query that returns Django model
    # instances enriched with F-expression annotations spanning multiple
    # FK chains (prompt_version → original_template, project → sessions,
    # prompt_label → name). The downstream consumer at
    # ``model_hub/services/prompt_metrics.py:159`` reads:
    #   result.id, result.input, result.output, result.name,
    #   result.observation_type, result.created_at, result.trace_id,
    #   result.project.id, result.session_id, result.prompt_label_id,
    #   result.prompt_label_name, result.prompt_template_version,
    #   getattr(result, f"metric_<config_id>", ...)
    # off each row.
    #
    # Wave-3 status (93c5c415f): no reader covering filter-driven
    # span listing by prompt_template was added. The required
    # extension is still:
    #
    #   list_spans_for_prompt_template(prompt_template_id, *,
    #       prompt_version_ids, filters, search_term,
    #       page_number, page_size) -> (rows: list[dict], total_count: int)
    #
    # ... where filters route through the v2 ClickHouseFilterBuilder
    # (same translator as TraceListQueryBuilder uses).
    #
    # Migration plan (deferred):
    #   1. Reader extension as above.
    #   2. Caller refactor:
    #      Replace `result.project.id` (FK traversal) with a Project
    #      lookup keyed on `result["project_id"]`; the in-CH row already
    #      carries project_id. Replace the F-expression annotations with
    #      explicit Python joins on the PG-side caches (prompt_version,
    #      prompt_label) that the caller already needs to resolve for
    #      template_version/label_name fields.
    #   3. EvalLogger metric_<config_id> annotations stay PG (EvalLogger
    #      is a PG table) — the caller would query EvalLogger separately
    #      for the (span_id, custom_eval_config_id) pairs returned from
    #      CH and stitch them in.
    #
    # Cross-cutting blast radius (caller refactor + new reader +
    # EvalLogger zip) makes this a separate commit; KEEP-PG until the
    # reader extension lands.

    base_query = (
        ObservationSpan.objects.filter(
            prompt_version__original_template=prompt_template,
            prompt_version__deleted=False,
            prompt_version_id__isnull=False,
            prompt_label_id__isnull=False,
        )
        .select_related("prompt_version", "project", "prompt_label")
        .prefetch_related("prompt_version__labels", "eval_logs__custom_eval_config")
        .annotate(
            prompt_template_version=F("prompt_version__template_version"),
            session_id=F("project__sessions__id"),
            span_name=F("name"),
            prompt_label_name=F("prompt_label__name"),
        )
    )

    # Add annotations for each eval config dynamically
    for config in eval_configs:

        annotation_value: Round | F | None = None

        if config.eval_template.config.get("output") == "score":
            annotation_value = Round(F("output_float") * 100, 2)
        elif config.eval_template.config.get("output") == "Pass/Fail":
            annotation_value = F("output_bool")
        elif config.eval_template.config.get("output") == "choices":
            annotation_value = F("output_str_list")
        else:
            continue

        base_query = base_query.annotate(
            **{
                f"metric_{config.id}": Subquery(
                    EvalLogger.objects.filter(
                        observation_span_id=OuterRef("id"),
                        custom_eval_config_id=config.id,
                    )
                    .annotate(transformed_value=annotation_value)
                    .values("transformed_value")[:1]
                )
            }
        )

    base_query = base_query.order_by("-created_at")

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
            # Get system metric filters
            system_filter_conditions = (
                FilterEngine.get_filter_conditions_for_system_metrics(filters)
            )
            if system_filter_conditions:
                combined_filter_conditions &= system_filter_conditions

            # Get non-system metric filters (excluding span attributes)
            eval_filter_conditions = (
                FilterEngine.get_filter_conditions_for_non_system_metrics(filters)
            )
            if eval_filter_conditions:
                combined_filter_conditions &= eval_filter_conditions

        # Apply combined filters in a single operation
        if combined_filter_conditions:
            base_query = base_query.filter(combined_filter_conditions)

    # Move pagination outside the filters block so it always applies
    start = page_number * page_size
    end = start + page_size
    total_count = base_query.count()
    base_query = base_query[start:end]

    results = list(base_query)

    return results, total_count
