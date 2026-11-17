"""Bounded span group-by, sharing the dashboard's catalog and query population."""
from copy import deepcopy

from django.conf import settings

from tracer.services.clickhouse.v2.property_catalog.reader import (
    ObservedRead,
    observed_table,
)


def bind_dashboard_group_by(
    config, *, organization_id=None, workspace_id=None, deadline=None, executor=None
):
    """Resolve an exact key in the authorized project catalog, never by search.

    Call only after dashboard project-scope authorization. The observed catalog
    is an inventory, not a time-window value scan; the metric query owns the
    requested time bounds. Mixed/structured types cannot become scalar groups.
    """
    group = config.get("group_by")
    if not group:
        return config
    result = deepcopy(config)
    if group["type"] == "tag":
        result["breakdowns"] = [
            {"type": "system_metric", "name": "tag", "source": "traces"}
        ]
        return result
    scope = {
        "organization_id": organization_id or config.get("organization_id"),
        "workspace_id": workspace_id or config.get("workspace_id"),
        "project_ids": tuple(config.get("project_ids") or ()),
    }
    if not scope["organization_id"] or not scope["workspace_id"]:
        raise ValueError("Group-by catalog requires organization and workspace scope.")
    reader = ObservedRead(
        executor, catalog_database=settings.PROPERTY_CATALOG_DATABASE, deadline=deadline
    )
    sql = f"""
SELECT arraySort(groupUniqArray(toString(k.attribute_type))) AS attribute_types
FROM {observed_table(reader.database, 'observed_attribute_keys')} AS k
PREWHERE k.organization_id = %(organization_id)s AND k.workspace_id = %(workspace_id)s
  AND k.project_id IN %(project_ids)s AND k.source_kind = 'custom_attribute'
WHERE k.attribute_key = %(group_by_key)s
LIMIT 1
"""
    rows = reader.execute(sql, {**scope, "group_by_key": group["key"]}, 1)
    types = rows[0].get("attribute_types", []) if rows else []
    if len(types) != 1 or types[0] not in ("string", "number", "boolean"):
        raise ValueError(
            "Group-by key must be a scalar attribute in this project's catalog."
        )
    result["breakdowns"] = [{
        "type": "custom_attribute",
        "name": group["key"],
        "source": "traces",
        "attribute_type": types[0],
    }]
    return result


def grouped_span_sql(
    *, source, bucket, dimension, metric_value, aggregate, predicates,
    params, limit, max_rows, include_period_aggregate=False,
):
    """Rank by observation frequency over the full filtered window, ties by value.

    All SQL fragments are builder-owned expressions. User keys and limits are
    bindings. Aggregate raw observations AFTER bucketing: summing averages or
    percentiles from discarded series would produce an incorrect Other value.
    Prefix retained values so a real value named 'other' cannot collide with
    the synthetic bucket. Both CTE scans use the identical scoped population.
    """
    if type(limit) is not int or not 1 <= limit <= 10:
        raise ValueError("Group-by limit must be between 1 and 10.")
    params = {**params, "group_by_limit": limit, "group_by_max_rows": max_rows}
    period_select = ", GROUPING(time_bucket) AS is_period_aggregate" if include_period_aggregate else ""
    grouping = (
        "GROUP BY GROUPING SETS ((time_bucket, breakdown_value), (breakdown_value))"
        if include_period_aggregate else "GROUP BY time_bucket, breakdown_value"
    )
    sql = f"""WITH dashboard_group_rows AS (
    SELECT {bucket} AS time_bucket,
           toString({dimension}) AS group_dimension,
           {metric_value} AS group_metric_value
    FROM {source}
    WHERE {' AND '.join(predicates)}
), dashboard_top_values AS (
    SELECT group_dimension FROM dashboard_group_rows
    GROUP BY group_dimension
    ORDER BY count() DESC, group_dimension ASC
    LIMIT %(group_by_limit)s
)
SELECT time_bucket,
       if(group_dimension IN (SELECT group_dimension FROM dashboard_top_values),
          concat('value:', group_dimension), 'other') AS breakdown_value,
       {aggregate} AS value{period_select}
FROM dashboard_group_rows
{grouping}
ORDER BY time_bucket, breakdown_value
LIMIT %(group_by_max_rows)s"""
    return sql, params


def group_by_series_label(value):
    # Escape the marker in actual values, making the label mapping injective.
    # In particular 'other', 'other (value)' and the synthetic bucket stay distinct.
    # 'total' is the renderer's ungrouped sentinel, not a real category label.
    if not value.startswith("value:"):
        return value
    label = value[len("value:"):]
    if not label:
        return "(empty)"
    if label in ("other", "total", "(empty)") or label.endswith(" (value)"):
        label += " (value)"
    return label
