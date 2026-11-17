from django.db import migrations

UPDATE_SAFETY = {
    "schema_version": 1,
    "risk_classes": ["data_migration"],
    "tables": ["postgres:simulate_eval_config"],
    "mitigation": (
        "forward rewrites the filters JSON of every simulate_eval_config row to "
        "snake_case keys and canonical operator names (for example is to equals). A "
        "system column id becomes snake_case (for example traceId to trace_id) when "
        "the filter's column type is empty, SYSTEM_METRIC or NORMAL. It removes the "
        "top-level colType, and the top-level col_type unless colType holds a "
        "non-empty value, and copies that non-empty colType value, or else col_type's, "
        "into filter_config when that is a dict whose col_type is missing or empty. It "
        "drops the id and _meta keys of each filter item. It reads only id and filters "
        "with iterator() and saves only rows that changed (update_fields=['filters']) "
        "on the migration's own connection. A database that applied 1.58.0 already has "
        "this migration recorded and does not run it again. On a fresh install "
        "simulate_eval_config has no rows when it runs, so nothing is written. Safe to "
        "run again: it rewrites only to canonical forms and saves only rows that "
        "changed, and the migration is atomic, so a failed run rolls back whole. The "
        "reverse is a no-op. Row scale: one row per simulate eval config; not a "
        "registered high-volume table."
    ),
    "reviewer": "<actual-github-login>",
    "review": "<actual-PR-review-discussion-permalink>",
    "large_volume_rehearsal": True,
}


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


def _move_alias_keys(value, aliases):
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


def canonicalize_simulate_eval_config_filters(apps, schema_editor):
    SimulateEvalConfig = apps.get_model("simulate", "SimulateEvalConfig")
    for config in SimulateEvalConfig.objects.using(schema_editor.connection.alias).all().only("id", "filters").iterator():
        next_filters, changed = _canonicalize_filter_list(config.filters or [])
        if changed:
            config.filters = next_filters
            config.save(using=schema_editor.connection.alias, update_fields=["filters"])


class Migration(migrations.Migration):
    dependencies = [
        ("simulate", "0073_merge_20260416_1406"),
    ]

    operations = [
        migrations.RunPython(
            canonicalize_simulate_eval_config_filters, migrations.RunPython.noop
        ),
    ]
