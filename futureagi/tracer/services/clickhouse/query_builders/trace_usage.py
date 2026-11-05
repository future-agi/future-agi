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
from typing import Any

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


def usage_aggregate_sql(*, prefix: str = "") -> str:
    """Aggregate light, latest-live span rows of ONE (project, trace) group.

    Callers must project id, parent_span_id, name, observation_type and all four
    usage columns, optionally prefixed (e.g. latest_cost). Physical replacement
    and tenant/membership scoping belong to the caller. No content/attribute
    payload is retained. The array is shared by the four reducers in CH.
    """
    fields = ["id", "parent_span_id", "name", "observation_type", *USAGE_COLUMNS]
    values = ", ".join(f"{prefix}{field}" for field in fields)
    # The fixed tuple layout is shared by each reducer: identity, parent, name,
    # kind, cost, total, prompt, completion. Siblings are never deduplicated.
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
        "arrayFilter(s -> NOT has(repeated_usage_ids, s.1), usage_spans) AS billable_usage_spans",
    ]
    projections.extend(
        f"arraySum(s -> s.{index}, billable_usage_spans) AS usage_{column}"
        for index, column in enumerate(USAGE_COLUMNS, 5)
    )
    return ",\n".join(projections)
