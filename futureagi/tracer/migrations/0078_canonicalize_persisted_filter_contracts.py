import logging

from django.db import migrations

logger = logging.getLogger(__name__)


FILTER_ITEM_KEY_ALIASES = {
    "columnId": "column_id",
    "displayName": "display_name",
    "outputType": "output_type",
    "filterConfig": "filter_config",
}
FILTER_CONFIG_KEY_ALIASES = {
    "colType": "col_type",
    "filterType": "filter_type",
    "filterOp": "filter_op",
    "filterValue": "filter_value",
}
FILTER_OP_ALIASES = {
    "is": "equals",
    "is_not": "not_equals",
    "equal_to": "equals",
    "not_equal_to": "not_equals",
    "inBetween": "between",
    "notBetween": "not_between",
    "not_in_between": "not_between",
}
SYSTEM_FIELD_ID_ALIASES = {
    "traceId": "trace_id",
    "spanId": "span_id",
    "sessionId": "session_id",
    "traceName": "trace_name",
    "spanName": "span_name",
    "nodeType": "node_type",
    "userId": "user_id",
    "projectName": "project_name",
    "totalCost": "total_cost",
    "startTime": "start_time",
    "endTime": "end_time",
    "createdAt": "created_at",
    "updatedAt": "updated_at",
    "agentDefinition": "agent_definition",
    "callType": "call_type",
    "datasetName": "dataset_name",
    "latencyMs": "latency_ms",
    "totalTokens": "total_tokens",
    "inputTokens": "input_tokens",
    "outputTokens": "output_tokens",
}
SYSTEM_FIELD_COL_TYPES = {None, "", "SYSTEM_METRIC", "NORMAL"}
SAVED_VIEW_CONFIG_KEY_ALIASES = {
    "compareFilters": "compare_filters",
    "compareDateFilter": "compare_date_filter",
    "extraFilters": "extra_filters",
    "compareExtraFilters": "compare_extra_filters",
    "subTab": "sub_tab",
}
SAVED_VIEW_FILTER_KEYS = (
    "filters",
    "compare_filters",
    "extra_filters",
    "compare_extra_filters",
)
FILTER_WRAPPER_KEY_ALIASES = {
    "projectId": "project_id",
    "dateRange": "date_range",
    "createdAt": "created_at",
    "sessionId": "session_id",
    "observationType": "observation_type",
    "spanAttributesFilters": "span_attributes_filters",
}


def _move_alias_keys(value, aliases):
    if not isinstance(value, dict):
        return value, False

    changed = False
    next_value = dict(value)
    for old_key, new_key in aliases.items():
        if old_key not in next_value:
            continue
        if new_key not in next_value:
            next_value[new_key] = next_value[old_key]
        del next_value[old_key]
        changed = True
    return next_value, changed


def _canonicalize_filter_item(filter_item):
    if not isinstance(filter_item, dict):
        return filter_item, False

    changed = False
    next_item, item_changed = _move_alias_keys(filter_item, FILTER_ITEM_KEY_ALIASES)
    changed = changed or item_changed

    root_col_type = next_item.pop("colType", None)
    if "col_type" in next_item:
        root_col_type = root_col_type or next_item.pop("col_type")
        changed = True
    if root_col_type is not None:
        changed = True
    for ui_key in ("id", "_meta"):
        if ui_key in next_item:
            del next_item[ui_key]
            changed = True

    config = next_item.get("filter_config")
    if isinstance(config, dict):
        next_config, config_changed = _move_alias_keys(
            config, FILTER_CONFIG_KEY_ALIASES
        )
        changed = changed or config_changed

        if root_col_type and not next_config.get("col_type"):
            next_config["col_type"] = root_col_type
            changed = True

        filter_op = next_config.get("filter_op")
        canonical_op = FILTER_OP_ALIASES.get(filter_op)
        if canonical_op:
            next_config["filter_op"] = canonical_op
            changed = True

        if next_config != config:
            next_item["filter_config"] = next_config

    column_id = next_item.get("column_id")
    config_col_type = (
        next_item.get("filter_config", {}).get("col_type")
        if isinstance(next_item.get("filter_config"), dict)
        else None
    )
    if column_id in SYSTEM_FIELD_ID_ALIASES and config_col_type in SYSTEM_FIELD_COL_TYPES:
        next_item["column_id"] = SYSTEM_FIELD_ID_ALIASES[column_id]
        changed = True

    return next_item, changed


def _canonicalize_filter_list(filters):
    if not isinstance(filters, list):
        return filters, False

    changed = False
    next_filters = []
    for filter_item in filters:
        next_filter, filter_changed = _canonicalize_filter_item(filter_item)
        next_filters.append(next_filter)
        changed = changed or filter_changed
    return next_filters, changed


def _canonicalize_legacy_user_filters_object(config):
    filters_obj = config.get("filters")
    if not isinstance(filters_obj, dict):
        return config, False

    changed = True
    next_config = dict(config)

    extra_filters = filters_obj.get("extra_filters", filters_obj.get("extraFilters"))
    if "extra_filters" not in next_config and isinstance(extra_filters, list):
        next_config["extra_filters"] = extra_filters

    date_filter = filters_obj.get("dateFilter", filters_obj.get("date_filter"))
    if date_filter is not None:
        display = (
            dict(next_config.get("display"))
            if isinstance(next_config.get("display"), dict)
            else {}
        )
        if "dateFilter" not in display:
            display["dateFilter"] = date_filter
            next_config["display"] = display

    main_filters = filters_obj.get("filters", filters_obj.get("filter"))
    if isinstance(main_filters, list):
        next_config["filters"] = main_filters
    else:
        next_config.pop("filters", None)

    return next_config, changed


def _canonicalize_saved_view_config(config):
    if not isinstance(config, dict):
        return config, False

    changed = False
    next_config, alias_changed = _move_alias_keys(
        config, SAVED_VIEW_CONFIG_KEY_ALIASES
    )
    changed = changed or alias_changed

    next_config, legacy_filters_changed = _canonicalize_legacy_user_filters_object(
        next_config
    )
    changed = changed or legacy_filters_changed

    for key in SAVED_VIEW_FILTER_KEYS:
        next_filters, filters_changed = _canonicalize_filter_list(next_config.get(key))
        if filters_changed:
            next_config[key] = next_filters
            changed = True

    return next_config, changed


def _canonicalize_filter_wrapper(filters):
    if isinstance(filters, list):
        return _canonicalize_filter_list(filters)
    if not isinstance(filters, dict):
        return filters, False

    changed = False
    next_filters, alias_changed = _move_alias_keys(filters, FILTER_WRAPPER_KEY_ALIASES)
    changed = changed or alias_changed

    nested, nested_changed = _canonicalize_filter_list(
        next_filters.get("span_attributes_filters")
    )
    if nested_changed:
        next_filters["span_attributes_filters"] = nested
        changed = True

    return next_filters, changed


def _canonicalize_dashboard_query_config(query_config):
    if not isinstance(query_config, dict):
        return query_config, False

    changed = False
    next_config = dict(query_config)

    next_filters, filters_changed = _canonicalize_filter_list(next_config.get("filters"))
    if filters_changed:
        next_config["filters"] = next_filters
        changed = True

    metrics = next_config.get("metrics")
    if isinstance(metrics, list):
        next_metrics = []
        metrics_changed = False
        for metric in metrics:
            if not isinstance(metric, dict):
                next_metrics.append(metric)
                continue
            next_metric = dict(metric)
            metric_filters, metric_changed = _canonicalize_filter_list(
                next_metric.get("filters")
            )
            if metric_changed:
                next_metric["filters"] = metric_filters
                metrics_changed = True
            next_metrics.append(next_metric)
        if metrics_changed:
            next_config["metrics"] = next_metrics
            changed = True

    return next_config, changed


def _migrate_json_field(apps, model_name, field_name, canonicalizer, stats):
    Model = apps.get_model("tracer", model_name)
    for obj in Model.objects.iterator(chunk_size=500):
        try:
            current_value = getattr(obj, field_name)
            next_value, changed = canonicalizer(current_value)
            if not changed:
                continue
            setattr(obj, field_name, next_value)
            obj.save(update_fields=[field_name])
            stats["updated"] += 1
        except Exception as exc:  # pragma: no cover - defensive migration logging
            stats["failed"] += 1
            logger.exception(
                "[canonicalize_persisted_filter_contracts] %s id=%s failed: %s",
                model_name,
                obj.pk,
                exc,
            )


def forwards(apps, schema_editor):
    stats = {"updated": 0, "failed": 0}

    _migrate_json_field(
        apps, "SavedView", "config", _canonicalize_saved_view_config, stats
    )
    _migrate_json_field(
        apps, "EvalTask", "filters", _canonicalize_filter_wrapper, stats
    )
    _migrate_json_field(
        apps, "UserAlertMonitor", "filters", _canonicalize_filter_wrapper, stats
    )
    _migrate_json_field(
        apps, "CustomEvalConfig", "filters", _canonicalize_filter_wrapper, stats
    )
    _migrate_json_field(
        apps,
        "DashboardWidget",
        "query_config",
        _canonicalize_dashboard_query_config,
        stats,
    )

    print(
        "[canonicalize_persisted_filter_contracts] "
        f"{stats['updated']} rows updated, {stats['failed']} rows skipped"
    )


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0077_merge_20260514_1559"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
