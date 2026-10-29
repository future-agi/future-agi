"""Bounded drill-down: which frozen units move one persisted rollup value.

Reads only the run's immutable snapshot (never mutable raw telemetry), so the
answer matches the persisted value and still works after raw retention. The
group's weighted totals come from the persisted result row, so one scan of the
snapshot ranks the units:

* ``influence`` is the exact leave-one-out change: the persisted value minus
  the value the same group would have without this unit. For a total it is the
  unit's weighted contribution ``y / p``. It is null when removing the unit
  would leave no denominator, or when the group has no value.
* Units are ranked by ``abs(influence)`` (by ``abs(y / p)`` when there is no
  value), then by unit id, and at most ``limit`` (<= 100) are returned.

Missing and invalid units are counted by reason, with at most five example unit
ids per reason (the smallest ids, so the answer is deterministic).
"""

from __future__ import annotations

import json
import math

from tracer.services.clickhouse.client import get_clickhouse_client
from tracer.services.derived_metrics.errors import MetricInputError
from tracer.services.derived_metrics.formula import compile_formula
from tracer.services.derived_metrics.queries import (
    QUERY_SETTINGS,
    SCOPE_SQL,
    Scope,
    rollup_group_sql,
)

MAX_CONTRIBUTORS = 100
EXAMPLES_PER_REASON = 5


def _units_source(formula, rollup: str) -> str:
    group, group_filter = rollup_group_sql(rollup)
    unpack = ", ".join(
        f"input_values['{alias}'] AS v_{alias}" for alias in formula.required
    )
    valid = f"({formula.complete_sql}) AND isNotNull(n) AND isFinite(n) AND isNotNull(d) AND isFinite(d)"
    return f"""
        SELECT *, ifNull({valid}, 0) AS complete, complete AND invalid_probability = 0 AS ok
        FROM (
            SELECT *, {formula.numerator} AS n, {formula.denominator} AS d
            FROM (
                SELECT *, {unpack} FROM derived_metric_inputs
                WHERE {SCOPE_SQL} AND snapshot_id = %(snapshot_id)s
            )
        ) WHERE {group} = %(group_key)s AND {group_filter}
    """


def contributors_select(formula, rollup: str) -> str:
    ratio_kind = formula.kind not in {"sum", "count_if"}
    if ratio_kind:
        weighted_d = "d / probability"
        influence = (
            "if(isNull(%(value)s) OR %(denominator)s - d / probability = 0, NULL, "
            "%(value)s - (%(numerator)s - n / probability) / (%(denominator)s - d / probability))"
        )
    else:
        weighted_d = "CAST(NULL, 'Nullable(Float64)')"
        influence = "if(isNull(%(value)s), NULL, n / probability)"
    return f"""
        SELECT unit_id, session_id, user_id, cohort, toString(day) AS day, probability,
            1 / probability AS weight, n AS numerator, {"d" if ratio_kind else "NULL"} AS denominator,
            n / probability AS weighted_numerator, {weighted_d} AS weighted_denominator,
            {influence} AS influence
        FROM ({_units_source(formula, rollup)})
        WHERE ok
        ORDER BY abs(ifNull(influence, n / probability)) DESC, unit_id
        LIMIT %(limit)s
    """


def diagnostics_select(formula, rollup: str) -> str:
    ratio_kind = formula.kind not in {"sum", "count_if"}
    missing_inputs = [
        f"countIf(isNull(v_{alias}) OR NOT isFinite(v_{alias})) AS missing_{alias}"
        for alias in formula.required
    ]
    examples = EXAMPLES_PER_REASON
    return f"""
        SELECT count() AS unit_count, countIf(ok) AS valid_count,
            countIf(NOT complete) AS missing_count,
            countIf(invalid_probability != 0) AS invalid_probability_count,
            countIf(ok AND {"d = 0" if ratio_kind else "0"}) AS outside_denominator_count,
            countIf(NOT complete AND ({formula.complete_sql})) AS undefined_arithmetic_count,
            {", ".join(missing_inputs)},
            groupArraySortedIf({examples})(unit_id, NOT complete) AS missing_examples,
            groupArraySortedIf({examples})(unit_id, invalid_probability != 0) AS invalid_probability_examples
        FROM ({_units_source(formula, rollup)})
    """


def _trace_id(unit_id: str, selection_unit: str) -> str | None:
    if selection_unit != "span":
        return None
    try:
        trace, _span = json.loads(unit_id)
    except (ValueError, TypeError):
        return None
    return str(trace)


def read_contributors(
    run, rollup: str = "overall", group_key: str = "", limit: int = 20
) -> dict:
    from tracer.services.derived_metrics.service import _authorized_metric

    if not 1 <= limit <= MAX_CONTRIBUTORS:
        raise MetricInputError({"limit": f"Use 1 to {MAX_CONTRIBUTORS} contributors."})
    if run.result_id is None or run.snapshot_id is None:
        raise MetricInputError("This run does not have a committed result yet.")
    definition = run.definition
    metric = definition.metric
    if not _authorized_metric(metric):
        raise MetricInputError("Metric project scope is no longer available.")
    formula = compile_formula(
        definition.formula, [item["alias"] for item in definition.inputs]
    )
    params = {
        **Scope(
            metric.organization_id, metric.workspace_id, metric.project_id
        ).parameters(),
        **formula.parameters,
        "metric_id": str(metric.id),
        "run_id": str(run.id),
        "result_id": str(run.result_id),
        "snapshot_id": str(run.snapshot_id),
        "rollup": rollup,
        "group_key": group_key,
    }
    client = get_clickhouse_client()
    persisted = client.execute(
        f"""SELECT value, numerator, denominator, status FROM derived_metric_results
        WHERE {SCOPE_SQL} AND metric_id = %(metric_id)s AND run_id = %(run_id)s
          AND result_id = %(result_id)s AND rollup = %(rollup)s AND group_key = %(group_key)s
        LIMIT 2""",
        params,
        settings=QUERY_SETTINGS,
    )
    if len(persisted) != 1:
        raise MetricInputError(
            {"group_key": "This group has no persisted result in this run and rollup."}
        )
    value, numerator, denominator, status = persisted[0]
    params.update(
        value=value if status == "ok" else None,
        numerator=numerator,
        denominator=denominator,
        limit=limit + 1,
    )
    rows, columns = client.execute(
        contributors_select(formula, rollup),
        params,
        with_column_types=True,
        settings=QUERY_SETTINGS,
    )
    names = [column[0] for column in columns]
    units = []
    for row in rows[:limit]:
        item = dict(zip(names, row, strict=True))
        if any(
            isinstance(value, float) and not math.isfinite(value)
            for value in item.values()
        ):
            raise MetricInputError(
                "Contributor weights or influence cannot be represented as finite numbers for this group."
            )
        item["trace_id"] = _trace_id(item["unit_id"], definition.selection_unit)
        units.append(item)
    counts, count_columns = client.execute(
        diagnostics_select(formula, rollup),
        params,
        with_column_types=True,
        settings=QUERY_SETTINGS,
    )
    diagnostics = dict(
        zip([column[0] for column in count_columns], counts[0], strict=True)
    )
    diagnostics["missing_by_input"] = {
        alias: diagnostics.pop(f"missing_{alias}") for alias in formula.required
    }
    return {
        "result_id": params["result_id"],
        "rollup": rollup,
        "group_key": group_key,
        "kind": formula.kind,
        "selection_unit": definition.selection_unit,
        "status": status,
        "value": value if status == "ok" else None,
        "numerator": numerator,
        "denominator": denominator,
        "results": units,
        "truncated": len(rows) > limit,
        "diagnostics": diagnostics,
    }
