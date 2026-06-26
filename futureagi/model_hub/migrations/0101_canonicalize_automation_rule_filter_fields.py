from django.db import migrations


FILTER_ITEM_KEY_ALIASES = {
    "columnId": "column_id",
    "displayName": "display_name",
    "outputType": "output_type",
    "filterConfig": "filter_config",
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
    "agentDefinition": "agent_definition",
    "callType": "call_type",
    "datasetName": "dataset_name",
    "createdAt": "created_at",
    "updatedAt": "updated_at",
    "latencyMs": "latency_ms",
    "totalTokens": "total_tokens",
    "inputTokens": "input_tokens",
    "outputTokens": "output_tokens",
}
SYSTEM_FIELD_COL_TYPES = {None, "", "SYSTEM_METRIC", "NORMAL"}

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


def _canonical_field_id(value):
    return SYSTEM_FIELD_ID_ALIASES.get(value, value)


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


def _canonical_filter(filter_item):
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


def _canonical_rule(rule):
    if not isinstance(rule, dict):
        return rule, False

    next_rule = dict(rule)
    field = next_rule.get("field")
    canonical_field = _canonical_field_id(field)
    if canonical_field != field:
        next_rule["field"] = canonical_field
        return next_rule, True
    return next_rule, False


def _canonical_conditions(conditions):
    if not isinstance(conditions, dict):
        return conditions, False

    changed = False
    next_conditions = dict(conditions)

    if "filters" in next_conditions:
        if "filter" not in next_conditions:
            next_conditions["filter"] = next_conditions["filters"]
        del next_conditions["filters"]
        changed = True

    for key in ("filter", "filters"):
        filters = next_conditions.get(key)
        if not isinstance(filters, list):
            continue
        next_filters = []
        filters_changed = False
        for filter_item in filters:
            next_item, item_changed = _canonical_filter(filter_item)
            next_filters.append(next_item)
            filters_changed = filters_changed or item_changed
        if filters_changed:
            next_conditions[key] = next_filters
            changed = True

    rules = next_conditions.get("rules")
    if isinstance(rules, list):
        next_rules = []
        rules_changed = False
        for rule in rules:
            next_rule, rule_changed = _canonical_rule(rule)
            next_rules.append(next_rule)
            rules_changed = rules_changed or rule_changed
        if rules_changed:
            next_conditions["rules"] = next_rules
            changed = True

    return next_conditions, changed


def canonicalize_automation_rule_filters(apps, schema_editor):
    AutomationRule = apps.get_model("model_hub", "AutomationRule")
    for rule in AutomationRule.objects.all().only("id", "conditions").iterator():
        next_conditions, changed = _canonical_conditions(rule.conditions or {})
        if changed:
            rule.conditions = next_conditions
            rule.save(update_fields=["conditions"])


class Migration(migrations.Migration):
    dependencies = [
        ("model_hub", "0100_score_queue_scoped_uniqueness"),
    ]

    operations = [
        migrations.RunPython(canonicalize_automation_rule_filters, migrations.RunPython.noop),
    ]
