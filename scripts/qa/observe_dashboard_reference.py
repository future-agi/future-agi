"""Opt-in, bounded QA SQL and full-row comparison; never an app query path.

No client, I/O, DDL, application compiler, candidate identities or sampling.
The whole-window array can be expensive: callers must use reference_diagnostic
throw-on-limit guards, and native-prove the barrier on their engine first.
Intentionally not dispatched by the production replay while that gate is open.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any, Mapping, Sequence
from uuid import UUID

from observe_trace_id_reference import membership_having
from replay_observe_filters import ReplayError, utc


AGGREGATES = {"avg": "avg", "min": "min", "max": "max",
              "p25": "quantileExact(0.25)", "p50": "quantileExact(0.5)"}
MAPS = {"number": "attrs_number", "boolean": "attrs_bool",
        "string": "attrs_string", "text": "attrs_string"}


@dataclass(frozen=True)
class DashboardReference:
    sql: str
    params: dict[str, Any]
    aggregations: tuple[str, ...]
    breakdown_type: str | None


def build_dashboard_reference_query(
    config: Mapping[str, Any], *, authorized_project_ids: Sequence[str],
    enabled: bool = False,
) -> DashboardReference:
    """Independent scalar FINAL -> groupArray -> ARRAY JOIN, pending engine gate.

    Unsupported input raises a static ReplayError for UNVERIFIED, never an
    empty reference. Takes the canonical request, not builder-normalized filters.
    """
    if not enabled:
        raise ReplayError("DASHBOARD_REFERENCE_OPT_IN_REQUIRED")
    try:
        projects = [str(UUID(str(p))) for p in config["project_ids"]]
        authorized = {str(UUID(str(p))) for p in authorized_project_ids}
        start, end = (utc(config["time_range"][k]) for k in ("custom_start", "custom_end"))
        metrics = config["metrics"]
        breakdowns = config.get("breakdowns", [])
        if (not projects or not set(projects) <= authorized or start >= end
                or config.get("granularity") != "day" or config.get("allow_sampled", False)
                or not 1 <= len(metrics) <= 5 or len(breakdowns) > 1):
            raise ValueError
        first = metrics[0]
        key = first["attribute_key"]
        per_metric = first.get("filters", [])
        aggregations = tuple(m.get("aggregation", "avg") for m in metrics)
        if (not isinstance(key, str) or not key or any(
                m.get("type") != "custom_attribute" or m.get("source", "traces") != "traces"
                or m.get("attribute_type", "number") != "number"
                or m.get("attribute_key") != key or m.get("filters", []) != per_metric
                or agg not in AGGREGATES for m, agg in zip(metrics, aggregations))):
            raise ValueError
        breakdown = breakdowns[0] if breakdowns else None
        kind = breakdown.get("attribute_type", "string") if breakdown else None
        if breakdown and (breakdown.get("type") != "custom_attribute"
                or breakdown.get("source", "traces") != "traces" or kind not in MAPS
                or not isinstance(breakdown.get("name"), str) or not breakdown["name"]):
            raise ValueError
        leaves = []
        for leaf in config.get("filters", []) + per_metric:
            cfg = leaf["filter_config"]
            if leaf.get("source", "traces") != "traces":
                raise ValueError
            if (leaf["column_id"] == "created_at" and cfg.get("col_type") == "SYSTEM_METRIC"
                    and cfg.get("filter_type") == "datetime" and cfg.get("filter_op") == "between"):
                if tuple(map(utc, cfg["filter_value"])) != (start, end):
                    raise ValueError
                continue
            if (cfg.get("col_type") != "SPAN_ATTRIBUTE" or cfg.get("filter_type") not in MAPS
                    or cfg.get("filter_op") not in {"equals", "in", "greater_than"}
                    or (cfg["filter_op"] == "greater_than" and cfg["filter_type"] != "number")
                    or not isinstance(leaf["column_id"], str) or not leaf["column_id"]):
                raise ValueError
            leaves.append(leaf)
        predicate, params = membership_having(leaves, scalar_span=True) if leaves else ("1", {})
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError) as exc:
        raise ReplayError("DASHBOARD_REFERENCE_UNSUPPORTED_REQUEST") from exc
    params.update(project_ids=projects, metric_key=key,
                  start=start.replace(tzinfo=None).isoformat(sep=" "),
                  end=end.replace(tzinfo=None).isoformat(sep=" "))
    presence, value = "1", "''"
    if breakdown:
        params["breakdown_key"] = breakdown["name"]
        presence = f"mapContains({MAPS[kind]}, %(breakdown_key)s)"
        value = f"{MAPS[kind]}[%(breakdown_key)s]"
    dimensions = "toStartOfDay(winner.2) AS time_bucket"
    grain = "time_bucket"
    if breakdown:
        dimensions += ", winner.6 AS breakdown_value"
        grain += ", breakdown_value"
    expressions = ", ".join(
        f"{AGGREGATES[agg]}(winner.4) AS ref_value_{i}" for i, agg in enumerate(aggregations)
    )
    sql = f"""SELECT {dimensions}, {expressions}
        FROM (SELECT groupArray(tuple(is_deleted, start_time,
            mapContains(attrs_number, %(metric_key)s), attrs_number[%(metric_key)s],
            {presence}, {value}, ({predicate}))) AS winners
          FROM spans FINAL
          PREWHERE project_id IN %(project_ids)s
            AND toStartOfHour(start_time) >= toStartOfHour(toDateTime64(%(start)s, 6, 'UTC'))
            AND toStartOfHour(start_time) < toStartOfHour(toDateTime64(%(end)s, 6, 'UTC')) + INTERVAL 1 HOUR)
        ARRAY JOIN winners AS winner
        WHERE winner.1 = 0 AND winner.3 AND winner.5 AND winner.7
          AND winner.2 >= toDateTime64(%(start)s, 6, 'UTC')
          AND winner.2 < toDateTime64(%(end)s, 6, 'UTC')
        GROUP BY {grain} ORDER BY {grain}
        SETTINGS optimize_move_to_prewhere=0, optimize_move_to_prewhere_if_final=0"""
    return DashboardReference(sql, params, aggregations, kind)


def compare_dashboard_rows(
    plan: DashboardReference, actual: Sequence[Mapping[str, Any]],
    reference: Sequence[Mapping[str, Any]],
    *, value_columns: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Complete grain/cell comparison, no rounding or tolerant average promotion.

    Numeric differences are observations, not a bug diagnosis on moving data.
    JSONEachRow strings and native UTC datetimes represent the same day grain.
    Errors/nonfinite values stay UNVERIFIED; no caller may substitute [] for them.
    """
    n = len(plan.aggregations)
    columns = tuple(value_columns) if value_columns is not None else (
        ("value",) if n == 1 else tuple(f"dashboard_metric_value_{i}" for i in range(n)))
    if len(columns) != n or len(set(columns)) != n:
        raise ReplayError("DASHBOARD_REFERENCE_COLUMN_MAPPING")

    def numeric(value):
        if type(value) not in (int, float):
            raise ReplayError("DASHBOARD_REFERENCE_NUMERIC_TYPE")
        try:
            finite = math.isfinite(value)
        except OverflowError:
            finite = False
        if not finite:
            raise ReplayError("DASHBOARD_REFERENCE_NONFINITE")
        return value

    def rows_by_grain(rows, values):
        result = {}
        fields = {"time_bucket", *values} | ({"breakdown_value"} if plan.breakdown_type else set())
        for row in rows:
            if not isinstance(row, Mapping) or set(row) != fields:
                raise ReplayError("DASHBOARD_REFERENCE_ROW_SHAPE")
            try:
                bucket = row["time_bucket"]
                bucket = datetime.fromisoformat(bucket) if isinstance(bucket, str) else bucket
                bucket = bucket.replace(tzinfo=timezone.utc) if bucket.tzinfo is None else bucket.astimezone(timezone.utc)
                if any((bucket.hour, bucket.minute, bucket.second, bucket.microsecond)):
                    raise ValueError
            except (TypeError, ValueError, AttributeError) as exc:
                raise ReplayError("DASHBOARD_REFERENCE_BUCKET_TYPE") from exc
            dimension = row.get("breakdown_value")
            kind = plan.breakdown_type
            if kind == "number":
                dimension = numeric(dimension)
            elif kind and type(dimension) is not (bool if kind == "boolean" else str):
                raise ReplayError("DASHBOARD_REFERENCE_BREAKDOWN_TYPE")
            grain = (bucket, kind, dimension)
            if grain in result:
                raise ReplayError("DASHBOARD_REFERENCE_DUPLICATE_GRAIN")
            result[grain] = tuple(numeric(row[v]) for v in values)
        return result

    left = rows_by_grain(actual, columns)
    right = rows_by_grain(reference, tuple(f"ref_value_{i}" for i in range(n)))
    missing, extra = len(right.keys() - left.keys()), len(left.keys() - right.keys())
    different = sum(a != b for k in left.keys() & right.keys()
                    for a, b in zip(left[k], right[k], strict=True))
    matched = not (missing or extra or different)
    return {
        "status": "AGGREGATE_MATCH" if matched else "AGGREGATE_MISMATCH",
        "candidate_rows": len(left), "reference_rows": len(right),
        "missing_rows": missing, "extra_rows": extra, "different_cells": different,
        "compared_cells": len(left.keys() & right.keys()) * n,
        "numeric_positive": bool(right), "full_row_metrics_verified": matched and bool(right),
        "same_transaction_snapshot": False, "qualification": False,
    }
