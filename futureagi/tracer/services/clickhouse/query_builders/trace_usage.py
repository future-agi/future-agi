"""Usage accounting for the known Pydantic agent-summary shape.

Pydantic instrumentation can export an agent run's usage on each wrapping run
as well as the real LLM call. Suppress a wrapper only when its complete usage
vector equals its immediate children's vector. This is deliberately NOT
value-based deduplication of calls, nor a blanket exclusion of non-LLM spans:
parent-only usage, different/missing child usage and independent LLMs survive.
Unknown summary conventions remain additive rather than guessing attribution.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any, Literal, TypedDict

USAGE_COLUMNS = ("cost", "total_tokens", "prompt_tokens", "completion_tokens")
SUMMARY_NAME = "pydantic_ai.agent.run"


def is_repeated_usage_summary(
    span: Mapping[str, Any], children: Sequence[Mapping[str, Any]]
) -> bool:
    if (
        span.get("name") != SUMMARY_NAME
        or span.get("observation_type") == "llm"
        or not children
    ):
        return False
    return all(
        math.isclose(
            span.get(column) or 0,
            sum(child.get(column) or 0 for child in children),
            rel_tol=1e-9 if column == "cost" else 0,
            abs_tol=1e-18 if column == "cost" else 0,
        )
        for column in USAGE_COLUMNS
    )


def usage_coverage_sql(*, attrs: str, prefix: str = "") -> str:
    """Two light flags from the same physical winner as its usage vector.

    Explicit nonadditive summaries contribute neither usage nor uncertainty.
    Unknown tokenless TOOL/CHAIN activity is not a billable request. Unmarked
    legacy spans retain their numeric accounting, including verified zeroes.
    Pass qualified source expressions when the SELECT also aliases usage.
    """
    billable = " OR ".join(
        f"{prefix}{column} > 0"
        for column in ("total_tokens", "prompt_tokens", "completion_tokens")
    )
    return (
        f"tuple({attrs}['fi.usage.contribution'] = 'none', "
        f"{attrs}['fi.cost.kind'] = 'unknown' AND "
        f"({billable} OR {prefix}observation_type = 'llm' "
        f"OR {attrs}['fi.usage.scope'] = 'turn'))"
    )


class CaptureCoverage(TypedDict):
    """Read-side shape; CaptureCoverageSerializer owns the public contract."""

    scope: Literal["observed_requests"]
    source_summary_present: bool
    request_count: int
    coverage_unknown: bool


def capture_coverage_from_counts(
    *, source_summary_present: bool, request_count: int
) -> CaptureCoverage | None:
    """Qualify observed usage, never infer missing requests or completeness.

    Omit for legacy reads without a source-summary witness. Counts describe
    selected latest-live LLM spans, not provider requests certified by a ledger.
    """
    if not source_summary_present:
        return None
    return {
        "scope": "observed_requests",
        "source_summary_present": True,
        "request_count": request_count,
        "coverage_unknown": True,
    }


def source_summary_sql(*, attrs: str) -> str:
    """One scalar witness, not source totals or arbitrary attribute content.

    Native normalization marks these summaries before moving their usage to
    fi.source.summary.*. Presence says nothing about capture completeness.
    """
    return (
        f"({attrs}['fi.usage.contribution'] = 'none' "
        f"AND {attrs}['fi.usage.scope'] = 'turn')"
    )


def usage_aggregate_sql(
    *,
    prefix: str = "",
    coverage: str | None = None,
    source_summary: str | None = None,
) -> str:
    """Aggregate light, latest-live span rows of ONE (project, trace) group.

    Callers must project id, parent_span_id, name, observation_type and all four
    usage columns, optionally prefixed (e.g. latest_cost). Coverage is an
    explicitly projected usage_coverage_sql tuple; callers without that column
    keep the legacy numeric behavior. Physical replacement and tenant/membership
    scoping belong to the caller. No content/attribute payload is retained. The
    array is shared by the four reducers in CH. Optional source_summary adds
    one scalar witness and an observed LLM count; it does not alter the existing
    two-flag coverage tuple, numeric reducers or callers that omit it.
    """
    fields = ["id", "parent_span_id", "name", "observation_type", *USAGE_COLUMNS]
    values = ", ".join(f"{prefix}{field}" for field in fields)
    values += f", {coverage or 'tuple(0, 0)'}"
    if source_summary is not None:
        values += f", {source_summary}"
    # Fixed layout: identity, parent, name, kind, cost, total, prompt, completion,
    # (nonadditive, unknown billable cost). Siblings are never deduplicated.
    same_children = " AND ".join(
        (
            f"abs(s.{index} - arraySum(c -> c.{index}, arrayFilter(c -> c.2 = s.1, usage_spans))) "
            f"<= greatest(abs(s.{index}) * 1e-9, 1e-18)"
            if column == "cost"
            else f"s.{index} = arraySum(c -> c.{index}, arrayFilter(c -> c.2 = s.1, usage_spans))"
        )
        for index, column in enumerate(USAGE_COLUMNS, 5)
    )
    # Filter to known wrappers BEFORE inspecting children. Relying on an AND
    # inside a reducer would make every ordinary LLM span scan every other span
    # when ClickHouse's short-circuit evaluation is disabled (quadratic fan-out).
    candidates = (
        f"arrayFilter(s -> s.3 = '{SUMMARY_NAME}' AND s.4 != 'llm', usage_spans)"
    )
    repeated = f"arrayExists(c -> c.2 = s.1, usage_spans) AND {same_children}"
    projections = [
        f"groupArray(tuple({values})) AS usage_spans",
        f"arrayMap(s -> s.1, arrayFilter(s -> {repeated}, {candidates})) AS repeated_usage_ids",
        "arrayFilter(s -> NOT tupleElement(s.9, 1) AND NOT has(repeated_usage_ids, s.1), usage_spans) AS billable_usage_spans",
        "arrayExists(s -> tupleElement(s.9, 2), billable_usage_spans) AS usage_cost_unknown",
        "if(usage_cost_unknown, CAST(NULL, 'Nullable(Float64)'), "
        "arraySum(s -> s.5, billable_usage_spans)) AS usage_cost",
    ]
    projections.extend(
        f"arraySum(s -> s.{index}, billable_usage_spans) AS usage_{column}"
        for index, column in enumerate(USAGE_COLUMNS[1:], 6)
    )
    if source_summary is not None:
        projections.extend(
            [
                "arrayExists(s -> s.10, usage_spans) AS usage_source_summary_present",
                "arrayCount(s -> s.4 = 'llm', billable_usage_spans) AS usage_request_count",
            ]
        )
    return ",\n".join(projections)
