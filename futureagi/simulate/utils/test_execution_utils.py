"""
Utility functions for generating dynamic prompts for SimulatorAgent based on agent definitions.
"""

import re
from datetime import datetime

from django.db import connection, models

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
        # send scenario_<id>_dataset_<column_id>.
        scenario_dataset_columns = {}
        tool_eval_columns = {}
        if column_order:
            for col in column_order:
                column_id = col.get("id")
                if not column_id:
                    continue
                if col.get("type") == "scenario_dataset_column":
                    scenario_dataset_columns[str(column_id)] = col
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
            if op in ("equals", "eq"):
                if len(values) == 1:
                    return queryset.filter(**{f"{field}__{exact_lookup}": values[0]})
                return queryset.filter(**{f"{field}__in": values})
            if op in ("not_equals", "ne"):
                if len(values) == 1:
                    return queryset.exclude(**{f"{field}__{exact_lookup}": values[0]})
                return queryset.exclude(**{f"{field}__in": values})
            if op == "in":
                return queryset.filter(**{f"{field}__in": values})
            if op == "not_in":
                return queryset.exclude(**{f"{field}__in": values})
            if op in ("contains", "icontains"):
                return queryset.filter(**{f"{field}__icontains": value})
            if op == "not_contains":
                return queryset.exclude(**{f"{field}__icontains": value})
            return queryset

        def apply_number_filter(queryset, field, op, value, transform=lambda v: v):
            values = as_list(value)
            if op in ("equals", "eq"):
                return queryset.filter(**{field: transform(values[0])})
            if op in ("not_equals", "ne"):
                return queryset.exclude(**{field: transform(values[0])})
            if op == "in":
                return queryset.filter(**{f"{field}__in": [transform(v) for v in values]})
            if op == "not_in":
                return queryset.exclude(**{f"{field}__in": [transform(v) for v in values]})
            if op in ("greater_than", "more_than", "gt"):
                return queryset.filter(**{f"{field}__gt": transform(value)})
            if op in ("less_than", "lt"):
                return queryset.filter(**{f"{field}__lt": transform(value)})
            if op in ("greater_than_or_equal", "more_than_or_equal", "gte"):
                return queryset.filter(**{f"{field}__gte": transform(value)})
            if op in ("less_than_or_equal", "lte"):
                return queryset.filter(**{f"{field}__lte": transform(value)})
            if op in ("between", "not_between", "not_in_between") and len(values) >= 2:
                start, end = transform(values[0]), transform(values[1])
                if op == "between":
                    return queryset.filter(**{f"{field}__range": (start, end)})
                return queryset.exclude(**{f"{field}__range": (start, end)})
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

            if op in ("equals", "eq"):
                return queryset.filter(any_field_q(None, transform(values[0])))
            if op in ("not_equals", "ne"):
                return queryset.exclude(any_field_q(None, transform(values[0])))
            if op == "in":
                return queryset.filter(any_field_q("in", [transform(v) for v in values]))
            if op == "not_in":
                return queryset.exclude(any_field_q("in", [transform(v) for v in values]))
            if op in ("greater_than", "more_than", "gt"):
                return queryset.filter(any_field_q("gt", transform(value)))
            if op in ("less_than", "lt"):
                return queryset.filter(any_field_q("lt", transform(value)))
            if op in ("greater_than_or_equal", "more_than_or_equal", "gte"):
                return queryset.filter(any_field_q("gte", transform(value)))
            if op in ("less_than_or_equal", "lte"):
                return queryset.filter(any_field_q("lte", transform(value)))
            if op in ("between", "not_between", "not_in_between") and len(values) >= 2:
                range_value = (transform(values[0]), transform(values[1]))
                if op == "between":
                    return queryset.filter(any_field_q("range", range_value))
                return queryset.exclude(any_field_q("range", range_value))
            return queryset

        def apply_scenario_dataset_column_filter(
            queryset, dataset_column_id, op, value, filter_type, scenario_id=None
        ):
            base = queryset.filter(row_id__isnull=False)
            if scenario_id:
                base = base.filter(scenario__id=scenario_id)

            def exists(value_sql, params):
                return base.extra(
                    where=[
                        "EXISTS ("
                        "SELECT 1 FROM model_hub_cell "
                        "WHERE model_hub_cell.column_id = %s "
                        "AND model_hub_cell.row_id = simulate_call_execution.row_id "
                        "AND model_hub_cell.deleted = false "
                        f"AND {value_sql}"
                        ")"
                    ],
                    params=[dataset_column_id, *params],
                )

            def not_exists(value_sql, params):
                return base.extra(
                    where=[
                        "NOT EXISTS ("
                        "SELECT 1 FROM model_hub_cell "
                        "WHERE model_hub_cell.column_id = %s "
                        "AND model_hub_cell.row_id = simulate_call_execution.row_id "
                        "AND model_hub_cell.deleted = false "
                        f"AND {value_sql}"
                        ")"
                    ],
                    params=[dataset_column_id, *params],
                )

            if filter_type in ("text", "string", "categorical"):
                values = [str(item) for item in as_list(value)]
                if op in ("equals", "eq"):
                    if len(values) == 1:
                        return exists("model_hub_cell.value = %s", [values[0]])
                    return exists("model_hub_cell.value = ANY(%s)", [values])
                if op in ("not_equals", "ne"):
                    if len(values) == 1:
                        return not_exists("model_hub_cell.value = %s", [values[0]])
                    return not_exists("model_hub_cell.value = ANY(%s)", [values])
                if op == "in":
                    return exists("model_hub_cell.value = ANY(%s)", [values])
                if op == "not_in":
                    return not_exists("model_hub_cell.value = ANY(%s)", [values])
                if op in ("contains", "icontains"):
                    return exists("model_hub_cell.value ILIKE %s", [f"%{value}%"])
                if op == "not_contains":
                    return not_exists("model_hub_cell.value ILIKE %s", [f"%{value}%"])

            if filter_type == "number":
                values = as_list(value)
                numeric_expr = "CAST(NULLIF(model_hub_cell.value, '') AS NUMERIC)"
                if op in ("equals", "eq"):
                    return exists(f"{numeric_expr} = %s", [float(values[0])])
                if op in ("not_equals", "ne"):
                    return not_exists(f"{numeric_expr} = %s", [float(values[0])])
                if op in ("greater_than", "more_than", "gt"):
                    return exists(f"{numeric_expr} > %s", [float(value)])
                if op in ("less_than", "lt"):
                    return exists(f"{numeric_expr} < %s", [float(value)])
                if op in ("greater_than_or_equal", "more_than_or_equal", "gte"):
                    return exists(f"{numeric_expr} >= %s", [float(value)])
                if op in ("less_than_or_equal", "lte"):
                    return exists(f"{numeric_expr} <= %s", [float(value)])
                if op in ("between", "not_between", "not_in_between") and len(values) >= 2:
                    params = [float(values[0]), float(values[1])]
                    if op == "between":
                        return exists(f"{numeric_expr} BETWEEN %s AND %s", params)
                    return not_exists(f"{numeric_expr} BETWEEN %s AND %s", params)

            if filter_type == "boolean":
                bool_value = "true" if str(value).lower() in ["true", "1", "yes"] else "false"
                if op in ("equals", "eq"):
                    return exists("LOWER(model_hub_cell.value) = %s", [bool_value])
                if op in ("not_equals", "ne"):
                    return not_exists("LOWER(model_hub_cell.value) = %s", [bool_value])

            return queryset

        def scenario_column_parts(column_id):
            if not column_id.startswith("scenario_") or "_dataset_" not in column_id:
                return None, None
            raw_scenario_id, dataset_column_id = column_id[len("scenario_") :].split(
                "_dataset_", 1
            )
            return raw_scenario_id, dataset_column_id

        for filter_item in filters:
            try:
                column_id = filter_item.get("column_id") or filter_item.get("columnId")
                filter_config = filter_item.get("filter_config", {}) or filter_item.get(
                    "filterConfig", {}
                )

                if not column_id or not filter_config:
                    continue

                filter_type = filter_config.get("filter_type") or filter_config.get(
                    "filterType"
                )
                filter_op = filter_config.get("filter_op") or filter_config.get(
                    "filterOp"
                )
                filter_value = (
                    filter_config.get("filter_value")
                    if "filter_value" in filter_config
                    else filter_config.get("filterValue")
                )

                # Handle different column types based on new response structure
                if column_id in ["timestamp", "created_at"]:
                    # Filter by timestamp
                    if filter_type == "datetime":
                        if filter_op in ["between", "not_between", "not_in_between"]:
                            if (
                                isinstance(filter_value, list)
                                and len(filter_value) == 2
                            ):
                                start_date = filter_value[0]
                                end_date = filter_value[1]
                                if filter_op == "between":
                                    call_executions = call_executions.filter(
                                        created_at__gte=start_date,
                                        created_at__lte=end_date,
                                    )
                                else:
                                    call_executions = call_executions.filter(
                                        ~models.Q(
                                            created_at__gte=start_date,
                                            created_at__lte=end_date,
                                        )
                                    )
                        else:
                            # Single date filtering
                            if filter_op == "equals":
                                # Parse the ISO datetime string and filter by the entire day
                                try:
                                    # Parse the ISO datetime string
                                    filter_datetime = datetime.fromisoformat(
                                        filter_value.replace("Z", "+00:00")
                                    )
                                    # Get the start and end of the day in UTC
                                    start_of_day = filter_datetime.replace(
                                        hour=0, minute=0, second=0, microsecond=0
                                    )
                                    end_of_day = filter_datetime.replace(
                                        hour=23,
                                        minute=59,
                                        second=59,
                                        microsecond=999999,
                                    )

                                    call_executions = call_executions.filter(
                                        created_at__gte=start_of_day,
                                        created_at__lte=end_of_day,
                                    )
                                except (ValueError, AttributeError) as e:
                                    error_messages.append(
                                        f"Invalid datetime format for timestamp filter: {str(e)}"
                                    )
                            elif filter_op == "greater_than":
                                try:
                                    filter_datetime = datetime.fromisoformat(
                                        filter_value.replace("Z", "+00:00")
                                    )
                                    call_executions = call_executions.filter(
                                        created_at__gt=filter_datetime
                                    )
                                except (ValueError, AttributeError) as e:
                                    error_messages.append(
                                        f"Invalid datetime format for timestamp filter: {str(e)}"
                                    )
                            elif filter_op == "less_than":
                                try:
                                    filter_datetime = datetime.fromisoformat(
                                        filter_value.replace("Z", "+00:00")
                                    )
                                    call_executions = call_executions.filter(
                                        created_at__lt=filter_datetime
                                    )
                                except (ValueError, AttributeError) as e:
                                    error_messages.append(
                                        f"Invalid datetime format for timestamp filter: {str(e)}"
                                    )
                            elif filter_op == "greater_than_or_equal":
                                try:
                                    filter_datetime = datetime.fromisoformat(
                                        filter_value.replace("Z", "+00:00")
                                    )
                                    call_executions = call_executions.filter(
                                        created_at__gte=filter_datetime
                                    )
                                except (ValueError, AttributeError) as e:
                                    error_messages.append(
                                        f"Invalid datetime format for timestamp filter: {str(e)}"
                                    )
                            elif filter_op == "less_than_or_equal":
                                try:
                                    filter_datetime = datetime.fromisoformat(
                                        filter_value.replace("Z", "+00:00")
                                    )
                                    call_executions = call_executions.filter(
                                        created_at__lte=filter_datetime
                                    )
                                except (ValueError, AttributeError) as e:
                                    error_messages.append(
                                        f"Invalid datetime format for timestamp filter: {str(e)}"
                                    )

                elif column_id == "call_execution_id":
                    # Filter by call execution IDs
                    if filter_type == "list" and isinstance(filter_value, list):
                        # Handle list of IDs
                        if filter_op == "in":
                            call_executions = call_executions.filter(
                                id__in=filter_value
                            )

                elif column_id in ["overallScore", "overall_score"]:
                    # Filter by overall score
                    if filter_type == "number":
                        call_executions = apply_number_filter(
                            call_executions, "overall_score", filter_op, filter_value, float
                        )

                elif column_id in ["duration_seconds", "duration"]:
                    if filter_type == "number":
                        call_executions = apply_number_filter(
                            call_executions,
                            "duration_seconds",
                            filter_op,
                            filter_value,
                            float,
                        )

                elif column_id in ["avg_agent_latency_ms", "latency", "latency_ms"]:
                    if filter_type == "number":
                        call_executions = apply_number_filter(
                            call_executions,
                            "avg_agent_latency_ms",
                            filter_op,
                            filter_value,
                            float,
                        )

                elif column_id in ["cost_cents", "customer_cost_cents", "cost"]:
                    if filter_type == "number":
                        call_executions = apply_number_any_field_filter(
                            call_executions,
                            ["customer_cost_cents", "cost_cents"],
                            filter_op,
                            filter_value,
                            float,
                        )

                elif column_id in ["responseTime", "response_time"]:
                    # Filter by response time (convert to milliseconds for database comparison)
                    if filter_type == "number":
                        filter_value = float(filter_value)
                        # Convert seconds to milliseconds for database comparison
                        filter_value_ms = filter_value * 1000
                        if filter_op == "greater_than":
                            call_executions = call_executions.filter(
                                response_time_ms__gt=filter_value_ms
                            )
                        elif filter_op == "less_than":
                            call_executions = call_executions.filter(
                                response_time_ms__lt=filter_value_ms
                            )
                        elif filter_op == "equals":
                            call_executions = call_executions.filter(
                                response_time_ms=filter_value_ms
                            )
                        elif filter_op == "greater_than_or_equal":
                            call_executions = call_executions.filter(
                                response_time_ms__gte=filter_value_ms
                            )
                        elif filter_op == "less_than_or_equal":
                            call_executions = call_executions.filter(
                                response_time_ms__lte=filter_value_ms
                            )

                elif column_id == "status":
                    # Filter by status
                    if filter_type in ["text", "string", "categorical"]:
                        call_executions = apply_text_filter(
                            call_executions, "status", filter_op, filter_value
                        )

                elif column_id in ["callType", "call_type"]:
                    # Filter by call type (Inbound/Outbound)
                    if filter_type in ["text", "string", "categorical"]:
                        # Map frontend values to database values
                        def map_call_type(value):
                            normalized = str(value).lower()
                            if normalized == "inbound":
                                return "inboundPhoneCall"
                            if normalized == "outbound":
                                return "outboundPhoneCall"
                            return normalized

                        mapped_value = (
                            [map_call_type(value) for value in filter_value]
                            if isinstance(filter_value, list)
                            else map_call_type(filter_value)
                        )
                        call_executions = apply_text_filter(
                            call_executions,
                            "call_type",
                            filter_op,
                            mapped_value,
                        )

                elif column_id == "simulation_call_type":
                    if filter_type in ["text", "string", "categorical"]:
                        call_executions = apply_text_filter(
                            call_executions,
                            "simulation_call_type",
                            filter_op,
                            filter_value,
                        )

                elif column_id == "agent_definition":
                    if filter_type in ["text", "string", "categorical"]:
                        call_executions = apply_text_filter(
                            call_executions,
                            "test_execution__agent_definition__agent_name",
                            filter_op,
                            filter_value,
                        )

                elif is_persona_filter_column(column_id):
                    try:
                        call_executions = apply_persona_filter(
                            call_executions,
                            column_id,
                            filter_op,
                            filter_value,
                            filter_type,
                        )
                    except UnsupportedPersonaFilter as exc:
                        error_messages.append(str(exc))

                elif column_id == "scenario":
                    # Filter by scenario name
                    if filter_type in ["text", "string", "categorical"]:
                        if filter_op == "equals":
                            call_executions = call_executions.filter(
                                scenario__name=filter_value
                            )
                        elif filter_op == "not_equals":
                            call_executions = call_executions.filter(
                                ~models.Q(scenario__name=filter_value)
                            )
                        elif filter_op == "contains":
                            call_executions = call_executions.filter(
                                scenario__name__icontains=filter_value
                            )
                        elif filter_op == "not_contains":
                            call_executions = call_executions.filter(
                                ~models.Q(scenario__name__icontains=filter_value)
                            )

                elif (
                    column_id in scenario_dataset_columns
                    or column_id.startswith("scenario_") and "dataset" in column_id
                ):
                    column_meta = scenario_dataset_columns.get(str(column_id), {})
                    scenario_id = column_meta.get("scenario_id")
                    dataset_column_id = column_id
                    if column_id not in scenario_dataset_columns:
                        scenario_id, dataset_column_id = scenario_column_parts(column_id)

                    if dataset_column_id:
                        call_executions = apply_scenario_dataset_column_filter(
                            call_executions,
                            dataset_column_id,
                            filter_op,
                            filter_value,
                            filter_type,
                            scenario_id,
                        )

                elif column_id in eval_configs_map or column_id in tool_eval_columns:
                    # Filter by evaluation metric (includes both SimulateEvalConfig and tool evaluations)
                    # eval_outputs structure: {eval_config_id: {"output": value, "reason": "", "output_type": "", "name": ""}}
                    # tool_outputs structure: {tool_eval_id: {"output": value, "reason": "", "output_type": "", "name": ""}}

                    # For tool evaluation columns, use column_id and tool_outputs field
                    # For regular eval configs, use eval_config.id and eval_outputs field
                    if column_id in tool_eval_columns:
                        eval_id = column_id
                        output_field = "tool_outputs"
                    else:
                        eval_config = eval_configs_map[column_id]
                        eval_id = str(eval_config.id)
                        output_field = "eval_outputs"

                    if filter_type == "number":
                        # Handle between/not_in_between operations
                        if filter_op in ["between", "not_in_between"]:
                            if (
                                isinstance(filter_value, list)
                                and len(filter_value) == 2
                            ):
                                start_value = float(filter_value[0])
                                end_value = float(filter_value[1])

                                # Convert percentages to decimals for both values
                                # Filter values from UI are in percentage format (0-100)
                                # Convert to decimal format (0-1) for database comparison
                                db_start_value = start_value / 100.0
                                db_end_value = end_value / 100.0

                                # Use Cast to ensure proper type comparison for numeric values
                                if filter_op == "between":
                                    call_executions = call_executions.filter(
                                        **{f"{output_field}__has_key": eval_id},
                                        **{
                                            f"{output_field}__{eval_id}__output__gte": db_start_value
                                        },
                                        **{
                                            f"{output_field}__{eval_id}__output__lte": db_end_value
                                        },
                                    )
                                else:  # not_in_between
                                    call_executions = call_executions.filter(
                                        **{f"{output_field}__has_key": eval_id}
                                    ).filter(
                                        ~models.Q(
                                            **{
                                                f"{output_field}__{eval_id}__output__gte": db_start_value
                                            }
                                        )
                                        | ~models.Q(
                                            **{
                                                f"{output_field}__{eval_id}__output__lte": db_end_value
                                            }
                                        )
                                    )
                        else:
                            # Handle single value operations
                            filter_value = float(filter_value)

                            # Convert percentage to decimal for score-based evaluations
                            # UI shows 0-100% but database stores 0-1
                            # Filter values from UI are in percentage format (0-100)
                            # Convert to decimal format (0-1) for database comparison
                            db_filter_value = filter_value / 100.0

                            # Filter based on output field (eval_outputs or tool_outputs) - looking at the "output" field
                            if filter_op == "greater_than":
                                call_executions = call_executions.filter(
                                    **{f"{output_field}__has_key": eval_id},
                                    **{
                                        f"{output_field}__{eval_id}__output__gt": db_filter_value
                                    },
                                )
                            elif filter_op == "less_than":
                                call_executions = call_executions.filter(
                                    **{f"{output_field}__has_key": eval_id},
                                    **{
                                        f"{output_field}__{eval_id}__output__lt": db_filter_value
                                    },
                                )
                            elif filter_op == "equals":
                                call_executions = call_executions.filter(
                                    **{f"{output_field}__has_key": eval_id},
                                    **{
                                        f"{output_field}__{eval_id}__output": db_filter_value
                                    },
                                )
                            elif filter_op == "greater_than_or_equal":
                                call_executions = call_executions.filter(
                                    **{f"{output_field}__has_key": eval_id},
                                    **{
                                        f"{output_field}__{eval_id}__output__gte": db_filter_value
                                    },
                                )
                            elif filter_op == "less_than_or_equal":
                                call_executions = call_executions.filter(
                                    **{f"{output_field}__has_key": eval_id},
                                    **{
                                        f"{output_field}__{eval_id}__output__lte": db_filter_value
                                    },
                                )
                    elif filter_type == "text":
                        # Text filtering on outputs (eval_outputs or tool_outputs)
                        if filter_op == "contains":
                            call_executions = call_executions.filter(
                                **{f"{output_field}__has_key": eval_id},
                                **{
                                    f"{output_field}__{eval_id}__output__icontains": filter_value
                                },
                            )
                        elif filter_op == "equals":
                            call_executions = call_executions.filter(
                                **{f"{output_field}__has_key": eval_id},
                                **{
                                    f"{output_field}__{eval_id}__output__iexact": filter_value
                                },
                            )
                        elif filter_op == "not_equals":
                            call_executions = call_executions.filter(
                                **{f"{output_field}__has_key": eval_id}
                            ).exclude(
                                **{
                                    f"{output_field}__{eval_id}__output__iexact": filter_value
                                }
                            )
                    elif filter_type == "boolean":
                        # Boolean filtering on outputs (eval_outputs or tool_outputs)
                        if filter_value.lower() in ["true", "1", "yes", "passed"]:
                            bool_value = True
                        else:
                            bool_value = False

                        if filter_op == "equals":
                            call_executions = call_executions.filter(
                                **{f"{output_field}__has_key": eval_id},
                                **{f"{output_field}__{eval_id}__output": bool_value},
                            )

            except Exception as e:
                error_messages.append(
                    f"Error applying filter for column {column_id}: {str(e)}"
                )

        return call_executions

    def _apply_grouping(
        self,
        call_executions,
        row_groups,
        group_keys,
        eval_configs_map,
        default_columns=None,
    ):
        """Apply grouping to call executions with support for new response structure"""
        if not row_groups:
            return call_executions

        # Check if we need to group by scenario dataset columns
        has_scenario_dataset_grouping = any(
            field.startswith("scenario_") and "dataset" in field for field in row_groups
        )

        if has_scenario_dataset_grouping:
            # Use raw SQL for complex scenario dataset grouping
            return self._apply_scenario_dataset_grouping(
                call_executions, row_groups, group_keys, default_columns
            )
        else:
            # Use Django ORM for basic grouping
            return self._apply_basic_grouping(
                call_executions, row_groups, group_keys, default_columns
            )

    def _apply_basic_grouping(
        self, call_executions, row_groups, group_keys, default_columns=None
    ):
        """Apply basic grouping using Django ORM"""
        # Build group_by_fields from default_columns
        group_by_fields = []

        if default_columns:
            for column in default_columns:
                column_id = column.get("id")
                column_type = column.get("type", "")

                # Map column IDs to Django field names. Accept both snake_case
                # (canonical) and legacy camelCase ids so stored/legacy row_groups
                # payloads still work.
                if column_id == "timestamp":
                    group_by_fields.append("created_at__date")
                elif column_id == "status":
                    group_by_fields.append("status")
                elif column_id in ("call_type", "callType"):
                    group_by_fields.append("call_type")
                elif column_id == "scenario":
                    group_by_fields.append("scenario__name")
                elif column_id in ("overall_score", "overallScore"):
                    group_by_fields.append("overall_score")
                elif column_id in ("response_time", "responseTime"):
                    group_by_fields.append("response_time_ms")
                elif column_type == "evaluation":
                    # For evaluation columns, we'll include them in annotations
                    continue
                elif column_type == "scenario_dataset_column":
                    # For scenario dataset columns, we'll handle them separately
                    continue
                elif column_type == "scenario_field":
                    # For scenario fields, add them to grouping
                    field_name = column.get("field")
                    if field_name:
                        group_by_fields.append(f"scenario__{field_name}")
        else:
            # Fallback to basic fields if no default_columns provided
            group_by_fields = [
                "scenario__name",
                "status",
                "call_type",
                "created_at__date",
            ]

        if group_by_fields:
            # Apply grouping with annotations for counts and chat metrics
            # Extract chat metrics from conversation_metrics_data JSONField
            call_executions = call_executions.values(*group_by_fields).annotate(
                count=models.Count("id"),
                avg_overall_score=models.Avg("overall_score"),
                avg_response_time=models.Avg("response_time_ms"),
                # Chat metrics from conversation_metrics_data JSONField
                total_tokens=models.Avg(
                    models.Cast(
                        models.F("conversation_metrics_data__total_tokens"),
                        models.IntegerField(),
                    )
                ),
                input_tokens=models.Avg(
                    models.Cast(
                        models.F("conversation_metrics_data__input_tokens"),
                        models.IntegerField(),
                    )
                ),
                output_tokens=models.Avg(
                    models.Cast(
                        models.F("conversation_metrics_data__output_tokens"),
                        models.IntegerField(),
                    )
                ),
                avg_latency_ms=models.Avg(
                    models.Cast(
                        models.F("conversation_metrics_data__avg_latency_ms"),
                        models.IntegerField(),
                    )
                ),
                turn_count=models.Avg(
                    models.Cast(
                        models.F("conversation_metrics_data__turn_count"),
                        models.IntegerField(),
                    )
                ),
                csat_score=models.Avg(
                    models.Cast(
                        models.F("conversation_metrics_data__csat_score"),
                        models.FloatField(),
                    )
                ),
            )

            # Apply group_keys filtering if provided
            if group_keys:
                group_filter_conditions = models.Q()

                for i, group_key in enumerate(group_keys):
                    if i < len(row_groups):
                        group_field = row_groups[i]

                        if group_field == "timestamp":
                            group_filter_conditions &= models.Q(
                                created_at__date=group_key
                            )
                        elif group_field == "status":
                            group_filter_conditions &= models.Q(status=group_key)
                        elif group_field in ("call_type", "callType"):
                            group_filter_conditions &= models.Q(
                                call_type__icontains=group_key.lower()
                            )
                        elif group_field == "scenario":
                            group_filter_conditions &= models.Q(
                                scenario__name=group_key
                            )
                        elif group_field in ("overall_score", "overallScore"):
                            try:
                                numeric_key = float(group_key)
                                group_filter_conditions &= models.Q(
                                    overall_score=numeric_key
                                )
                            except (ValueError, TypeError):
                                pass
                        elif group_field in ("response_time", "responseTime"):
                            try:
                                numeric_key = float(group_key)
                                group_filter_conditions &= models.Q(
                                    response_time_ms=numeric_key
                                )
                            except (ValueError, TypeError):
                                pass

                if group_filter_conditions:
                    call_executions = call_executions.filter(group_filter_conditions)

            # Convert QuerySet to list for consistency
            return list(call_executions)

        return call_executions

    def _apply_scenario_dataset_grouping(
        self, call_executions, row_groups, group_keys, default_columns=None
    ):
        """Apply grouping by scenario dataset columns using raw SQL"""
        # Build the SELECT and GROUP BY clauses
        select_fields = []
        group_by_fields = []

        # Build fields from default_columns
        if default_columns:
            for column in default_columns:
                column_id = column.get("id")
                column_type = column.get("type", "")

                # Map column IDs to SQL field names. Accept both snake_case
                # (canonical) and legacy camelCase ids.
                if column_id == "timestamp":
                    select_fields.append(
                        "DATE(simulate_call_execution.created_at) as created_at__date"
                    )
                    group_by_fields.append("DATE(simulate_call_execution.created_at)")
                elif column_id == "status":
                    select_fields.append("simulate_call_execution.status")
                    group_by_fields.append("simulate_call_execution.status")
                elif column_id in ("call_type", "callType"):
                    select_fields.append("simulate_call_execution.call_type")
                    group_by_fields.append("simulate_call_execution.call_type")
                elif column_id == "scenario":
                    select_fields.append("simulate_scenarios.name as scenario__name")
                    group_by_fields.append("simulate_scenarios.name")
                elif column_id in ("overall_score", "overallScore"):
                    select_fields.append("simulate_call_execution.overall_score")
                    group_by_fields.append("simulate_call_execution.overall_score")
                elif column_id in ("response_time", "responseTime"):
                    select_fields.append("simulate_call_execution.response_time_ms")
                    group_by_fields.append("simulate_call_execution.response_time_ms")
                elif column_type == "evaluation":
                    # For evaluation columns, we'll include them in annotations
                    continue
                elif column_type == "scenario_dataset_column":
                    # For scenario dataset columns, we'll handle them separately
                    continue
                elif column_type == "scenario_field":
                    # For scenario fields, add them to grouping
                    field_name = column.get("field")
                    if field_name:
                        select_fields.append(
                            f"simulate_scenarios.{field_name} as scenario__{field_name}"
                        )
                        group_by_fields.append(f"simulate_scenarios.{field_name}")
        else:
            # Fallback to basic fields if no default_columns provided
            select_fields.extend(
                [
                    "simulate_scenarios.name as scenario__name",
                    "simulate_call_execution.status",
                    "simulate_call_execution.call_type",
                    "DATE(simulate_call_execution.created_at) as created_at__date",
                ]
            )
            group_by_fields.extend(
                [
                    "simulate_scenarios.name",
                    "simulate_call_execution.status",
                    "simulate_call_execution.call_type",
                    "DATE(simulate_call_execution.created_at)",
                ]
            )

        # Add scenario dataset columns from default_columns
        if default_columns:
            for column in default_columns:
                column_id = column.get("id")
                column_type = column.get("type", "")

                if column_type == "scenario_dataset_column":
                    # Extract the actual column ID from the prefixed ID
                    actual_column_id = column.get("id")
                    if actual_column_id and "_dataset_" in actual_column_id:
                        # If it's a prefixed ID, extract the actual column ID
                        parts = actual_column_id.split("_dataset_")
                        if len(parts) == 2:
                            actual_column_id = parts[1]

                    # Create a safe column alias (replace hyphens with underscores)
                    safe_alias = column_id.replace("-", "_").replace(".", "_")
                    dataset_id = column.get("dataset_id")

                    # Properly escape SQL string values to prevent SQL injection
                    # Use connection.cursor().mogrify to safely escape parameters
                    cursor = connection.cursor()
                    try:
                        # Use mogrify to get properly escaped SQL string
                        escaped_dataset_id = cursor.mogrify("%s", [dataset_id]).decode(
                            "utf-8"
                        )
                        escaped_column_id = cursor.mogrify(
                            "%s", [actual_column_id]
                        ).decode("utf-8")
                    except AttributeError:
                        # Fallback for backends that don't support mogrify (like SQLite)
                        # Escape single quotes by doubling them (SQL standard)
                        escaped_dataset_id = "'{}'".format(
                            str(dataset_id).replace("'", "''")
                        )
                        escaped_column_id = "'{}'".format(
                            str(actual_column_id).replace("'", "''")
                        )
                    finally:
                        cursor.close()

                    # Add dataset column value to grouping with properly escaped values
                    select_fields.append(
                        f"""
                        (SELECT model_hub_cell.value
                            FROM model_hub_cell
                            WHERE model_hub_cell.dataset_id = {escaped_dataset_id}
                            AND model_hub_cell.column_id = {escaped_column_id}
                            AND model_hub_cell.row_id = simulate_call_execution.row_id
                            AND model_hub_cell.deleted = false
                            LIMIT 1) as {safe_alias}
                    """
                    )
                    group_by_fields.append(
                        f"""
                        (SELECT model_hub_cell.value
                            FROM model_hub_cell
                            WHERE model_hub_cell.dataset_id = {escaped_dataset_id}
                            AND model_hub_cell.column_id = {escaped_column_id}
                            AND model_hub_cell.row_id = simulate_call_execution.row_id
                            AND model_hub_cell.deleted = false
                            LIMIT 1)
                    """
                    )

        for group_field in row_groups:
            # Skip fields that are already included in basic context
            if group_field in [
                "timestamp",
                "status",
                "call_type",
                "callType",
                "scenario",
            ]:
                continue
            elif group_field in ("overall_score", "overallScore"):
                select_fields.append("simulate_call_execution.overall_score")
                group_by_fields.append("simulate_call_execution.overall_score")
            elif group_field in ("response_time", "responseTime"):
                select_fields.append("simulate_call_execution.response_time_ms")
                group_by_fields.append("simulate_call_execution.response_time_ms")
            elif group_field.startswith("scenario_") and "dataset" in group_field:
                # Extract scenario_id and dataset_column_id
                # Format: scenario_{scenario_id}_dataset_{dataset_column_id}
                if "_dataset_" in group_field:
                    parts = group_field.split("_dataset_")
                    if len(parts) == 2:
                        scenario_id = parts[0].replace("scenario_", "")
                        dataset_column_id = parts[1]

                        # Create a safe column alias (replace hyphens with underscores)
                        safe_alias = group_field.replace("-", "_")

                        # Add dataset column value to grouping
                        select_fields.append(
                            f"""
                            (SELECT model_hub_cell.value
                                FROM model_hub_cell
                                WHERE model_hub_cell.dataset_id = (SELECT dataset_id FROM simulate_scenarios WHERE id = '{scenario_id}')
                                AND model_hub_cell.column_id = '{dataset_column_id}'
                                AND model_hub_cell.row_id = simulate_call_execution.row_id
                                AND model_hub_cell.deleted = false
                                LIMIT 1) as {safe_alias}
                        """
                        )
                        group_by_fields.append(
                            f"""
                            (SELECT model_hub_cell.value
                                FROM model_hub_cell
                                WHERE model_hub_cell.dataset_id = (SELECT dataset_id FROM simulate_scenarios WHERE id = '{scenario_id}')
                                AND model_hub_cell.column_id = '{dataset_column_id}'
                                AND model_hub_cell.row_id = simulate_call_execution.row_id
                                AND model_hub_cell.deleted = false
                                LIMIT 1)
                        """
                        )

        if not select_fields:
            return call_executions

        # Build the raw SQL query
        select_clause = ", ".join(select_fields)
        group_by_clause = ", ".join(group_by_fields)

        # Add group_keys filtering if provided
        where_conditions = ["simulate_call_execution.deleted = false"]
        if group_keys:
            for i, group_key in enumerate(group_keys):
                if i < len(row_groups):
                    group_field = row_groups[i]

                    if group_field == "timestamp":
                        where_conditions.append(
                            f"DATE(simulate_call_execution.created_at) = '{group_key}'"
                        )
                    elif group_field == "status":
                        where_conditions.append(
                            f"simulate_call_execution.status = '{group_key}'"
                        )
                    elif group_field in ("call_type", "callType"):
                        where_conditions.append(
                            f"LOWER(simulate_call_execution.call_type) LIKE '%{group_key.lower()}%'"
                        )
                    elif group_field == "scenario":
                        where_conditions.append(
                            f"simulate_scenarios.name = '{group_key}'"
                        )
                    elif group_field in ("overall_score", "overallScore"):
                        try:
                            numeric_key = float(group_key)
                            where_conditions.append(
                                f"simulate_call_execution.overall_score = {numeric_key}"
                            )
                        except (ValueError, TypeError):
                            pass
                    elif group_field in ("response_time", "responseTime"):
                        try:
                            numeric_key = float(group_key)
                            where_conditions.append(
                                f"simulate_call_execution.response_time_ms = {numeric_key}"
                            )
                        except (ValueError, TypeError):
                            pass

        where_clause = " AND ".join(where_conditions)

        raw_sql = get_grouped_call_execution_metrics_query(
            select_clause=select_clause,
            where_clause=where_clause,
            group_by_clause=group_by_clause,
        )

        # Execute raw SQL and return results
        with connection.cursor() as cursor:
            cursor.execute(raw_sql)
            columns = [col[0] for col in cursor.description]
            results = [
                dict(zip(columns, row, strict=False)) for row in cursor.fetchall()
            ]

        return results

    def _apply_search(self, call_executions, search_query):
        """Apply search to call executions with support for new response structure"""
        if not search_query:
            return call_executions

        # Search in phone number, scenario name, customer number, and transcripts
        pattern = rf"(?i){re.escape(search_query)}"

        # Build search query for multiple fields
        search_conditions = models.Q(
            models.Q(phone_number__regex=pattern)
            | models.Q(scenario__name__regex=pattern)
            | models.Q(customer_number__regex=pattern)
            | models.Q(call_summary__regex=pattern)
        )

        # Search in transcripts if they exist
        try:
            transcript_search = models.Q(transcripts__content__regex=pattern)
            search_conditions |= transcript_search
        except ImportError:
            pass

        # Search in scenario dataset columns (if call has row_id)
        try:

            # Search in dataset cell values for calls that have row_id
            # We need to apply this search separately since it uses extra()
            call_executions_with_dataset_search = call_executions.filter(
                row_id__isnull=False
            ).extra(
                where=[
                    "EXISTS (SELECT 1 FROM model_hub_cell WHERE  model_hub_cell.row_id = simulate_call_execution.row_id AND model_hub_cell.value ILIKE %s AND model_hub_cell.deleted = false)"
                ],
                params=[f"%{search_query}%"],
            )

            # Combine the dataset search results with other search conditions using OR
            call_executions = (
                call_executions_with_dataset_search
                | call_executions.filter(search_conditions)
            )

            # Remove duplicates
            call_executions = call_executions.distinct()

            return call_executions
        except Exception:
            # If there's an error with dataset search, log it and continue without it

            # Continue with regular search only
            call_executions = call_executions.filter(search_conditions).distinct()
            return call_executions


def generate_simulator_agent_prompt(
    agent_definition: AgentDefinition | None = None,
    *,
    agent_version: AgentVersion | None = None,
) -> str:
    """
    Deterministic template for a CUSTOMER persona used by the simulator.
    Uses inbound/outbound direction to pick who starts the interaction:

    - inbound=True  -> customer's message/call comes first
    - inbound=False -> agent's message/call comes first

    Inputs:
    - Prefer passing `agent_version=` (keyword-only) so the prompt can use the selected
      version's `configuration_snapshot` as the single source of truth.
    - `agent_definition` remains supported for backwards-compatibility and as a fallback
      when a version is not available yet (e.g., scenario/simulator creation flows) or
      when the version snapshot is missing expected keys.
    """

    if agent_version is not None:
        agent_definition = agent_version.agent_definition

    if agent_definition is None:
        # Prompt-based simulations don't have agent_definition
        # Return a generic prompt that works with {{persona}} and {{situation}} variables
        return (
            "You are a customer with the following characteristics: {{persona}}. "
            "Currently, {{situation}}. "
            "\n\nYou will send the first message to an agent. "
            "Please respond naturally and stay consistent with your persona throughout the conversation."
        )

    version_snapshot: dict = {}
    if agent_version is not None:
        version_snapshot = getattr(agent_version, "configuration_snapshot", {}) or {}

    resolved_agent_name = (
        version_snapshot.get("agent_name")
        or version_snapshot.get("agentName")
        or agent_definition.agent_name
    )
    resolved_agent_type = (
        str(
            version_snapshot.get("agent_type")
            or version_snapshot.get("agentType")
            or agent_definition.agent_type
            or ""
        )
        .strip()
        .lower()
    )
    resolved_inbound = (
        version_snapshot.get("inbound")
        if "inbound" in version_snapshot
        else agent_definition.inbound
    )
    if isinstance(resolved_inbound, str):
        resolved_inbound = resolved_inbound.strip().lower() == "true"
    else:
        resolved_inbound = bool(resolved_inbound)

    is_chat = resolved_agent_type in {"text", "chat"}
    if is_chat:
        if resolved_inbound:
            channel_sentence = f"You will send the first message to an agent named {resolved_agent_name}."
        else:
            channel_sentence = f"You will receive the first message from an agent named {resolved_agent_name}."
    else:
        if resolved_inbound:
            channel_sentence = (
                f"You will make a call to an agent named {resolved_agent_name}."
            )
        else:
            channel_sentence = (
                f"You will receive a call from an agent named {resolved_agent_name}."
            )

    # Keep {{persona}} and {{situation}} placeholders exactly
    # Matches Vapi's approach: wait for mutual conclusion, don't cut off abruptly
    end_call_instruction = (
        "\n\nCALL CLOSING RULES:\n"
        "- Always wait for the reply from the other side before ending the call. Do not cut them off abruptly.\n"
        "- When the conversation is MUTUALLY finished (both sides have exchanged goodbyes and there's nothing left to discuss), "
        "you can trigger the endCall function.\n"
        "- Never say the words 'function', 'tool', or 'endCall' out loud. Simply say your natural closing sentence once, "
        "then silently trigger the endCall function to terminate the call."
    )

    return (
        "You are a customer with the following characteristics: {{persona}}. "
        "Currently, {{situation}}. "
        f"\n\n{channel_sentence} "
        "Please respond naturally and stay consistent with your persona throughout the conversation."
        f"{end_call_instruction}"
    )
