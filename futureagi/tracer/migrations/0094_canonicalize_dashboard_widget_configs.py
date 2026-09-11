from django.db import migrations


QUERY_ITEM_KEY_ALIASES = {
    "displayName": "display_name",
}
CHART_CONFIG_KEY_ALIASES = {
    "chartType": "chart_type",
    "axisConfig": "axis_config",
}
AXIS_CONFIG_KEY_ALIASES = {
    "leftY": "left_y",
    "rightY": "right_y",
    "xAxis": "x_axis",
    "seriesAxis": "series_axis",
}
AXIS_KEY_ALIASES = {
    "prefixSuffix": "prefix_suffix",
    "outOfBounds": "out_of_bounds",
}


def _move_alias_keys(value, aliases):
    if not isinstance(value, dict):
        return value, False

    changed = False
    next_value = dict(value)
    for old_key, new_key in aliases.items():
        if old_key not in next_value:
            continue

        old_value = next_value.pop(old_key)
        if new_key not in next_value:
            next_value[new_key] = old_value
        elif isinstance(old_value, dict) and isinstance(next_value[new_key], dict):
            next_value[new_key] = {**old_value, **next_value[new_key]}
        changed = True

    return next_value, changed


def _canonicalize_query_config(query_config):
    if not isinstance(query_config, dict):
        return query_config, False

    changed = False
    next_config = dict(query_config)
    for key in ("metrics", "breakdowns"):
        items = next_config.get(key)
        if not isinstance(items, list):
            continue

        next_items = []
        items_changed = False
        for item in items:
            next_item, item_changed = _move_alias_keys(item, QUERY_ITEM_KEY_ALIASES)
            next_items.append(next_item)
            items_changed = items_changed or item_changed

        if items_changed:
            next_config[key] = next_items
            changed = True

    return next_config, changed


def _canonicalize_chart_config(chart_config):
    next_config, changed = _move_alias_keys(chart_config, CHART_CONFIG_KEY_ALIASES)
    if not isinstance(next_config, dict):
        return next_config, changed

    axis_config = next_config.get("axis_config")
    next_axis_config, axis_changed = _move_alias_keys(
        axis_config, AXIS_CONFIG_KEY_ALIASES
    )
    changed = changed or axis_changed
    if not isinstance(next_axis_config, dict):
        return next_config, changed

    for key in ("left_y", "right_y"):
        next_axis, axis_value_changed = _move_alias_keys(
            next_axis_config.get(key), AXIS_KEY_ALIASES
        )
        if axis_value_changed:
            next_axis_config[key] = next_axis
            changed = True

    if changed:
        next_config["axis_config"] = next_axis_config

    return next_config, changed


def forwards(apps, schema_editor):
    DashboardWidget = apps.get_model("tracer", "DashboardWidget")
    updated = 0

    for widget in DashboardWidget.objects.iterator(chunk_size=500):
        query_config, query_changed = _canonicalize_query_config(widget.query_config)
        chart_config, chart_changed = _canonicalize_chart_config(widget.chart_config)
        update_fields = []

        if query_changed:
            widget.query_config = query_config
            update_fields.append("query_config")
        if chart_changed:
            widget.chart_config = chart_config
            update_fields.append("chart_config")
        if update_fields:
            widget.save(update_fields=update_fields)
            updated += 1

    print(f"[canonicalize_dashboard_widget_configs] {updated} widgets updated")


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0093_register_eval_task_search_attributes"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
