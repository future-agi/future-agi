import re
import uuid
from datetime import datetime

from django.db import connection, models

from model_hub.models.develop_dataset import Column, Row
from simulate.models.agent_definition import AgentDefinition
from simulate.models.agent_version import AgentVersion
from simulate.utils.persona_filtering import (
    UnsupportedPersonaFilter,
    apply_persona_filter,
    is_persona_filter_column,
)
from simulate.utils.sql_query import get_grouped_call_execution_metrics_query


class TestExecutionUtils:
    def _apply_filters(
        self,
        call_executions,
        filters,
        error_messages,
        eval_configs_map,
        column_order=None,
    ):
        """Apply filters to call executions with support for new response structure"""
        # Build dynamic column maps from column_order. The simulation grid sends
        # raw scenario dataset column IDs, while older automation rules may still
        # send scenario_<id>_dataset_<column_id>. Post-reconcile the `id` field on
        # each entry is the canonical column name (e.g. "priority"), so we index
        # by both the canonical name AND each raw dataset column UUID so that
        # grid-style filters (raw UUIDs) and rule-style filters (canonical name
        # or scenario_<id>_dataset_<uuid>) all land on the same handler.
        scenario_dataset_columns = {}
        tool_eval_columns = {}
        if column_order:
            for col in column_order:
                column_id = col.get("id")
                if not column_id:
                    continue
                if col.get("type") == "scenario_dataset_column":
                    scenario_dataset_columns[str(column_id)] = col
                    for raw_id in col.get("dataset_column_ids") or []:
                        scenario_dataset_columns[str(raw_id)] = col
                elif col.get("type") == "tool_evaluation":
                    tool_eval_columns[str(column_id)] = col

        def as_list(value):
            if isinstance(value, (list, tuple)):
                return list(value)
            if isinstance(value, str) and "," in value:
                return [item.strip() for item in value.split(",") if item.strip()]
            return [value]

        def apply_text_filter(queryset, field, op, value, *, exact_lookup="iexact"):
            values = as_list(value)
            if op == "equals":
                if len(values) == 1:
                    return queryset.filter(**{f"{field}__{exact_lookup}": values[0]})
                return queryset.filter(**{f"{field}__in": values})
            if op == "not_equals":
                if len(values) == 1:
                    return queryset.exclude(**{f"{field}__{exact_lookup}": values[0]})
                return queryset.exclude(**{f"{field}__in": values})
            if op == "in":
                return queryset.filter(**{f"{field}__in": values})
            if op == "not_in":
                return queryset.exclude(**{f"{field}__in": values})
            if op == "contains":
                return queryset.filter(**{f"{field}__icontains": value})
            if op == "not_contains":
                return queryset.exclude(**{f"{field}__icontains": value})
            return queryset

        def apply_number_filter(queryset, field, op, value, transform=lambda v: v):
            values = as_list(value)
            if op == "equals":
                return queryset.filter(**{field: transform(values[0])})
            if op == "not_equals":
                return queryset.exclude(**{field: transform(values[0])})
            if op == "in":
                return queryset.filter(
                    **{f"{field}__in": [transform(v) for v in values]}
                )
            if op == "not_in":
                return queryset.exclude(
                    **{f"{field}__in": [transform(v) for v in values]}
                )
            if op == "greater_than":
                return queryset.filter(**{f"{field}__gt": transform(value)})
            if op == "less_than":
                return queryset.filter(**{f"{field}__lt": transform(value)})
            if op == "greater_than_or_equal":
                return queryset.filter(**{f"{field}__gte": transform(value)})
            if op == "less_than_or_equal":
                return queryset.filter(**{f"{field}__lte": transform(value)})
            if op in ("between", "not_between") and len(values) >= 2:
                start, end = transform(values[0]), transform(values[1])
                if op == "between":
                    return queryset.filter(**{f"{field}__range": (start, end)})
                return queryset.exclude(**{f"{field}__range": (start, end)})
            if op == "is_null":
                return queryset.filter(**{f"{field}__isnull": True})
            if op == "is_not_null":
                return queryset.filter(**{f"{field}__isnull": False})
            return queryset

        def apply_number_any_field_filter(
            queryset, fields, op, value, transform=lambda v: v
        ):
            values = as_list(value)

            def q_for(field, lookup, val):
                key = field if lookup is None else f"{field}__{lookup}"
                return models.Q(**{key: val})

            def any_field_q(lookup, val):
                condition = models.Q()
                for field in fields:
                    condition |= q_for(field, lookup, val)
                return condition

            if op == "equals":
                return queryset.filter(any_field_q(None, transform(values[0])))
            if op == "not_equals":
                return queryset.exclude(any_field_q(None, transform(values[0])))
            if op == "in":
                return queryset.filter(
                    any_field_q("in", [transform(v) for v in values])
                )
            if op == "not_in":
                return queryset.exclude(
                    any_field_q("in", [transform(v) for v in values])
                )
            if op == "greater_than":
                return queryset.filter(any_field_q("gt", transform(value)))
            if op == "less_than":
                return queryset.filter(any_field_q("lt", transform(value)))
            if op == "greater_than_or_equal":
                return queryset.filter(any_field_q("gte", transform(value)))
            if op == "less_than_or_equal":
                return queryset.filter(any_field_q("lte", transform(value)))
            if op in ("between", "not_between") and len(values) >= 2:
                range_value = (transform(values[0]), transform(values[1]))
                if op == "between":
                    return queryset.filter(any_field_q("range", range_value))
                return queryset.exclude(any_field_q("range", range_value))
            if op == "is_null":
                return queryset.filter(any_field_q("isnull", True))
            if op == "is_not_null":
                return queryset.filter(any_field_q("isnull", False))
            return queryset

        def apply_scenario_dataset_column_filter(
            queryset, dataset_column_ids, op, value, filter_type, scenario_id=None
        ):

            if not isinstance(dataset_column_ids, (list, tuple)):
                dataset_column_ids = [dataset_column_ids]
            dataset_column_ids = [str(cid) for cid in dataset_column_ids if cid]
            base = queryset.filter(row_id__isnull=False)
            if scenario_id:
                base = base.filter(scenario__id=scenario_id)

            def exists(value_sql, params):
                return base.extra(
                    where=[
                        "EXISTS ("
                        "SELECT 1 FROM model_hub_cell "
                        "WHERE model_hub_cell.row_id = simulate_callexecution.row_id "
                        "AND model_hub_cell.column_id = ANY(%s) "
                        f"AND {value_sql}"
                        ")"
                    ],
                    params=[[dataset_column_ids], *params],
                )

            def not_exists(value_sql, params):
                return base.extra(
                    where=[
                        "NOT EXISTS ("
                        "SELECT 1 FROM model_hub_cell "
                        "WHERE model_hub_cell.row_id = simulate_callexecution.row_id "
                        "AND model_hub_cell.column_id = ANY(%s) "
                        f"AND {value_sql}"
                        ")"
                    ],
                    params=[[dataset_column_ids], *params],
                )

            if filter_type == "number":
                if op == "equals":
                    return exists("(model_hub_cell.value)::numeric = %s", [value])
                if op == "not_equals":
                    return not_exists("(model_hub_cell.value)::numeric = %s", [value])
                if op == "greater_than":
                    return exists("(model_hub_cell.value)::numeric > %s", [value])
                if op == "less_than":
                    return exists("(model_hub_cell.value)::numeric < %s", [value])
                if op == "greater_than_or_equal":
                    return exists("(model_hub_cell.value)::numeric >= %s", [value])
                if op == "less_than_or_equal":
                    return exists("(model_hub_cell.value)::numeric <= %s", [value])
                if op in ("between", "not_between"):
                    values = as_list(value)
                    if len(values) >= 2:
                        if op == "between":
                            return exists(
                                "(model_hub_cell.value)::numeric BETWEEN %s AND %s",
                                [values[0], values[1]],
                            )
                        return not_exists(
                            "(model_hub_cell.value)::numeric BETWEEN %s AND %s",
                            [values[0], values[1]],
                        )
                if op == "is_null":
                    return base.extra(
                        where=[
                            "NOT EXISTS ("
                            "SELECT 1 FROM model_hub_cell "
                            "WHERE model_hub_cell.row_id = simulate_callexecution.row_id "
                            "AND model_hub_cell.column_id = ANY(%s) "
                            "AND model_hub_cell.value IS NOT NULL"
                            ")"
                        ],
                        params=[[dataset_column_ids]],
                    )
                if op == "is_not_null":
                    return exists("model_hub_cell.value IS NOT NULL", [])
            elif filter_type == "text":
                if op in ("equals", "in"):
                    return exists(
                        "model_hub_cell.value = ANY(%s)",
                        [as_list(value)],
                    )
                if op in ("not_equals", "not_in"):
                    return not_exists(
                        "model_hub_cell.value = ANY(%s)",
                        [as_list(value)],
                    )
                if op == "contains":
                    return exists(
                        "model_hub_cell.value ILIKE %s",
                        [f"%{value}%"],
                    )
                if op == "not_contains":
                    return not_exists(
                        "model_hub_cell.value ILIKE %s",
                        [f"%{value}%"],
                    )
                if op == "is_null":
                    return base.extra(
                        where=[
                            "NOT EXISTS ("
                            "SELECT 1 FROM model_hub_cell "
                            "WHERE model_hub_cell.row_id = simulate_callexecution.row_id "
                            "AND model_hub_cell.column_id = ANY(%s) "
                            "AND model_hub_cell.value IS NOT NULL"
                            ")"
                        ],
                        params=[[dataset_column_ids]],
                    )
                if op == "is_not_null":
                    return exists("model_hub_cell.value IS NOT NULL", [])
            return queryset

        def apply_eval_metric_filter(
            queryset, eval_config_id, op, value, output_type=None
        ):
            try:
                from ee.simulate.utils.eval_filter import apply_ee_eval_metric_filter

                return apply_ee_eval_metric_filter(
                    queryset, eval_config_id, op, value, output_type=output_type
                )
            except ImportError:
                pass
            return queryset

        def apply_tool_eval_metric_filter(
            queryset, eval_config_id, scenario_id, op, value, output_type=None
        ):
            try:
                from ee.simulate.utils.eval_filter import (
                    apply_ee_tool_eval_metric_filter,
                )

                return apply_ee_tool_eval_metric_filter(
                    queryset,
                    eval_config_id,
                    scenario_id,
                    op,
                    value,
                    output_type=output_type,
                )
            except ImportError:
                pass
            return queryset

        error_messages = []
        eval_configs_map = eval_configs_map or {}

        for filter_item in filters:
            try:
                column_id = filter_item.get("column_id")
                filter_config = filter_item.get("filter_config", {})

                if not column_id or not filter_config:
                    continue

                filter_type = filter_config.get("filter_type")
                filter_op = filter_config.get("filter_op")
                filter_value = filter_config.get("filter_value")

                # Handle different column types based on new response structure
                if column_id in ["timestamp", "created_at"]:
                    # Filter by timestamp
                    if filter_op == "greater_than" and filter_value:
                        call_executions = call_executions.filter(
                            created_at__gte=filter_value
                        )
                    elif filter_op == "less_than" and filter_value:
                        call_executions = call_executions.filter(
                            created_at__lte=filter_value
                        )
                    elif filter_op == "between" and isinstance(filter_value, list) and len(filter_value) >= 2:
                        call_executions = call_executions.filter(
                            created_at__range=(filter_value[0], filter_value[1])
                        )

                elif column_id == "status":
                    call_executions = apply_text_filter(
                        call_executions, "status", filter_op, filter_value, exact_lookup="iexact"
                    )

                elif column_id == "scenario_name":
                    call_executions = apply_text_filter(
                        call_executions,
                        "scenario__name",
                        filter_op,
                        filter_value,
                        exact_lookup="iexact",
                    )

                elif column_id == "persona_name":
                    try:
                        call_executions = apply_persona_filter(
                            call_executions, filter_op, filter_value
                        )
                    except UnsupportedPersonaFilter:
                        error_messages.append(
                            f"Unsupported persona filter op: {filter_op}"
                        )

                elif column_id == "duration":
                    call_executions = apply_number_filter(
                        call_executions,
                        "call_duration",
                        filter_op,
                        filter_value,
                        transform=float,
                    )

                elif column_id == "cost":
                    call_executions = apply_number_filter(
                        call_executions,
                        "total_cost",
                        filter_op,
                        filter_value,
                        transform=float,
                    )

                elif column_id == "token_count":
                    call_executions = apply_number_filter(
                        call_executions,
                        "total_tokens",
                        filter_op,
                        filter_value,
                        transform=int,
                    )

                elif column_id in ("latency", "average_latency"):
                    call_executions = apply_number_filter(
                        call_executions,
                        "latency",
                        filter_op,
                        filter_value,
                        transform=float,
                    )

                elif column_id == "turns":
                    call_executions = apply_number_any_field_filter(
                        call_executions,
                        ["total_turns", "num_turns"],
                        filter_op,
                        filter_value,
                        transform=int,
                    )

                elif column_id in scenario_dataset_columns:
                    col_info = scenario_dataset_columns[column_id]
                    dataset_column_ids = col_info.get("dataset_column_ids") or []
                    scenario_id = col_info.get("scenario_id")
                    col_filter_type = col_info.get("filter_type") or filter_type or "text"
                    call_executions = apply_scenario_dataset_column_filter(
                        call_executions,
                        dataset_column_ids,
                        filter_op,
                        filter_value,
                        col_filter_type,
                        scenario_id=scenario_id,
                    )

                elif column_id in tool_eval_columns:
                    col_info = tool_eval_columns[column_id]
                    eval_config_id = col_info.get("eval_config_id")
                    scenario_id = col_info.get("scenario_id")
                    output_type = col_info.get("output_type")
                    if eval_config_id:
                        call_executions = apply_tool_eval_metric_filter(
                            call_executions,
                            eval_config_id,
                            scenario_id,
                            filter_op,
                            filter_value,
                            output_type=output_type,
                        )

                elif column_id in eval_configs_map:
                    eval_config = eval_configs_map[column_id]
                    eval_config_id = eval_config.get("id") if isinstance(eval_config, dict) else getattr(eval_config, "id", None)
                    output_type = eval_config.get("output_type") if isinstance(eval_config, dict) else getattr(eval_config, "output_type", None)
                    if eval_config_id:
                        call_executions = apply_eval_metric_filter(
                            call_executions,
                            eval_config_id,
                            filter_op,
                            filter_value,
                            output_type=output_type,
                        )

            except Exception as e:
                error_messages.append(str(e))
                continue

        return call_executions

    def _apply_search(self, call_executions, search_query):
        """Apply search filter to call executions"""
        if not search_query:
            return call_executions
        return call_executions.filter(
            models.Q(scenario__name__icontains=search_query)
            | models.Q(status__icontains=search_query)
        )

    def _apply_grouping(
        self,
        call_executions,
        row_groups,
        group_keys,
        eval_configs_map,
        column_order=None,
    ):
        """Apply grouping to call executions"""
        try:
            from ee.simulate.utils.grouping import apply_ee_grouping

            return apply_ee_grouping(
                call_executions,
                row_groups,
                group_keys,
                eval_configs_map,
                column_order=column_order,
            )
        except ImportError:
            pass
        return call_executions

    def _apply_sorting(
        self,
        call_executions,
        sort_params,
        eval_configs_map,
        column_order=None,
    ):
        """Apply sorting to call executions"""
        try:
            from ee.simulate.utils.sorting import apply_ee_sorting

            return apply_ee_sorting(
                call_executions,
                sort_params,
                eval_configs_map,
                column_order=column_order,
            )
        except ImportError:
            pass
        return call_executions


def get_column_order_for_test_execution(
    test_execution, include_eval_cols=True, include_dataset_cols=True
):
    """Get the ordered list of columns for a test execution.

    Fetches the column order from the run test configuration, returning a
    stable list that includes system columns, scenario dataset columns, and
    eval metric columns depending on the flags passed.
    """
    run_test = test_execution.run_test
    column_order = []

    if include_dataset_cols:
        scenarios = run_test.scenarios.prefetch_related("dataset_columns").all()
        for scenario in scenarios:
            for col in scenario.dataset_columns.all():
                column_order.append(
                    {
                        "id": canonical_scenario_column_name(scenario.id, col.id),
                        "type": "scenario_dataset_column",
                        "dataset_column_ids": [str(col.id)],
                        "scenario_id": str(scenario.id),
                        "filter_type": col.column_type,
                    }
                )

    if include_eval_cols:
        eval_configs = SimulateEvalConfig.objects.filter(run_test=run_test)
        for eval_config in eval_configs:
            column_order.append(
                {
                    "id": str(eval_config.id),
                    "type": "eval_metric",
                    "eval_config_id": str(eval_config.id),
                    "output_type": eval_config.output_type,
                }
            )

    return column_order


def get_run_test_column_order(
    run_test, include_eval_cols=True, include_dataset_cols=True
):
    column_order = []
    if include_dataset_cols:
        scenarios = run_test.scenarios.prefetch_related("dataset_columns").all()
        for scenario in scenarios:
            for col in scenario.dataset_columns.all():
                column_order.append(
                    {
                        "id": canonical_scenario_column_name(scenario.id, col.id),
                        "type": "scenario_dataset_column",
                        "dataset_column_ids": [str(col.id)],
                        "scenario_id": str(scenario.id),
                        "filter_type": col.column_type,
                    }
                )
    if include_eval_cols:
        eval_configs = SimulateEvalConfig.objects.filter(run_test=run_test)
        for eval_config in eval_configs:
            column_order.append(
                {
                    "id": str(eval_config.id),
                    "type": "eval_metric",
                    "eval_config_id": str(eval_config.id),
                    "output_type": eval_config.output_type,
                }
            )
    return column_order


def canonical_scenario_column_name(scenario_id, column_id):
    return f"scenario_{scenario_id}_dataset_{column_id}"


def reconcile_column_order(stored_order, live_order):
    """Merge a stored column order with a freshly-computed live order.

    Columns that already exist in `stored_order` keep their position and
    metadata (pinned, hidden, width …) while new columns from `live_order`
    are appended in the order they appear in `live_order`.  Columns that are
    in `stored_order` but no longer in `live_order` are removed so stale
    ghost columns do not accumulate.

    Returns ``(reconciled, changed)`` where ``changed`` is ``True`` when the
    result differs from ``stored_order``.
    """
    live_ids = {col["id"]: col for col in live_order}
    reconciled = []
    changed = False

    # Keep existing columns that still exist in the live order, preserving
    # stored metadata but refreshing the live-computed fields such as
    # ``dataset_column_ids`` and ``filter_type``.
    for col in stored_order:
        col_id = col.get("id")
        if col_id not in live_ids:
            changed = True
            continue
        live_col = live_ids[col_id]
        merged = {**live_col, **col}
        if merged != col:
            changed = True
        reconciled.append(merged)

    # Append any new live columns not yet in stored_order.
    stored_ids = {col.get("id") for col in stored_order}
    for col in live_order:
        if col["id"] not in stored_ids:
            reconciled.append(col)
            changed = True
    return reconciled, changed
