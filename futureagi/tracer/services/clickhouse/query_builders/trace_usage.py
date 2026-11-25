"""Conservative usage accounting for Pydantic and OTel GenAI summaries.

Pydantic retains its immediate-child rule. An invoke_agent wrapper is suppressed
only when its complete vector equals its descendant LLM vector. Parent-only,
unequal vectors, unrelated work and independent LLM calls remain additive.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any, Literal, TypedDict

USAGE_COLUMNS = ("cost", "total_tokens", "prompt_tokens", "completion_tokens")
SUMMARY_NAME = "pydantic_ai.agent.run"


def is_usage_summary_candidate(span: Mapping[str, Any]) -> bool:
    attrs = span.get("span_attributes") or span.get("attrs_string") or {}
    return span.get("observation_type") != "llm" and (
        span.get("name") == SUMMARY_NAME
        or attrs.get("gen_ai.operation.name") == "invoke_agent"
    )


def repeated_usage_summary_ids(spans: Sequence[Mapping[str, Any]]) -> set[str]:
    """Resolve ONE trace's summaries, including LLMs beneath event-loop spans.

    Callers select latest-live rows and fence project/trace membership first.
    A visited set bounds malformed cycles. No producer/name guessing or sibling
    value deduplication; only candidates walk their own reachable subtree.
    """
    by_parent: dict[str, list[Mapping[str, Any]]] = {}
    for span in spans:
        by_parent.setdefault(span.get("parent_span_id") or "", []).append(span)
    repeated = set()
    additive_ids = set()
    for span in spans:
        span_id = span.get("id")
        attrs = span.get("span_attributes") or span.get("attrs_string") or {}
        if attrs.get("fi.usage.contribution") == "none":
            # A retry can retain both the legacy unknown and normalized agent
            # rows: kind belongs to the replacement key. The nonadditive row
            # cannot veto a proven legacy summary with the same logical id.
            continue
        if not is_usage_summary_candidate(span):
            if span.get("observation_type") != "llm":
                additive_ids.add(span_id)
            continue
        children = by_parent.get(span_id, [])
        if span.get("name") != SUMMARY_NAME:
            pending = [span_id]
            visited = set()
            children = []
            while pending:
                parent_id = pending.pop()
                if parent_id in visited:
                    continue
                visited.add(parent_id)
                for child in by_parent.get(parent_id, []):
                    child_id = child.get("id")
                    if child_id == span_id:
                        continue
                    # Keep every selected physical LLM row; only ancestry
                    # expansion is deduplicated, never independent usage.
                    if child.get("observation_type") == "llm":
                        children.append(child)
                    pending.append(child_id)
        if is_repeated_usage_summary(span, children):
            repeated.add(span_id)
        else:
            additive_ids.add(span_id)
    # A logical id can have multiple physical rows. An additive unequal or
    # non-summary sibling must not lose usage because one version proved equal.
    return repeated - additive_ids


def is_repeated_usage_summary(
    span: Mapping[str, Any], children: Sequence[Mapping[str, Any]]
) -> bool:
    if (
        not is_usage_summary_candidate(span)
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


def usage_coverage_sql(
    *, attrs: str, prefix: str = "", include_summary: bool = False
) -> str:
    """Stable coverage flags, optionally with a GenAI-summary witness bit.

    Explicit nonadditive summaries contribute neither usage nor uncertainty.
    Unknown tokenless TOOL/CHAIN activity is not a billable request. Unmarked
    legacy spans retain their numeric accounting, including verified zeroes.
    Pass qualified source expressions when the SELECT also aliases usage.
    """
    billable = " OR ".join(
        f"{prefix}{column} > 0"
        for column in ("total_tokens", "prompt_tokens", "completion_tokens")
    )
    flags = (
        f"tuple({attrs}['fi.usage.contribution'] = 'none', "
        f"{attrs}['fi.cost.kind'] = 'unknown' AND "
        f"({billable} OR {prefix}observation_type = 'llm' "
        f"OR {attrs}['fi.usage.scope'] = 'turn'))"
    )
    if include_summary:
        return f"tupleConcat({flags}, tuple({attrs}['gen_ai.operation.name'] = 'invoke_agent'))"
    return flags


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
    known_cost_fact: str | None = None,
    materialized: bool = False,
) -> str:
    """Aggregate light, latest-live span rows of ONE (project, trace) group.

    Callers must project id, parent_span_id, name, observation_type and all four
    usage columns, optionally prefixed (e.g. latest_cost). Coverage is an
    explicitly projected usage_coverage_sql tuple; callers without that column
    keep the legacy numeric behavior. Physical replacement and tenant/membership
    scoping belong to the caller. No content/attribute payload is retained. The
    array is shared by the four reducers in CH. Optional source_summary adds
    one scalar witness and an observed LLM count; it does not alter the existing
    coverage flags, numeric reducers or callers that omit it.
    known_cost_fact selects a singleton array of user money/count/token metrics
    instead; callers must unpack it with ARRAY JOIN after trace grouping.
    """
    fields = ["id", "parent_span_id", "name", "observation_type", *USAGE_COLUMNS]
    values = ", ".join(f"{prefix}{field}" for field in fields)
    values += f", {coverage or 'tuple(0, 0)'}"
    if source_summary is not None:
        values += f", {source_summary}"
    if known_cost_fact is not None:
        if source_summary is not None:
            raise ValueError(
                "User metrics and capture coverage are separate projections"
            )
        values += f", {known_cost_fact}"
    repeated_ids = repeated_usage_ids_sql()
    if known_cost_fact is not None:
        # Bind each intermediate array lexically, rather than SELECT aliases
        # that the analyzer recursively substitutes into every reducer. Return
        # ONE tuple in ONE array; the caller ARRAY JOINs it after grouping, so
        # projecting six fields cannot re-expand the proof or change row count.
        # Known facts intentionally use the original array: the legacy counter
        # removes wrappers only, even if a physical LLM shares a wrapper's id.
        return f"""
            arrayMap(usage_spans ->
                arrayMap(repeated_usage_ids ->
                    arrayMap(billable_usage_spans -> tuple(
                        arraySum(s -> if(tupleElement(s.9, 2), 0, s.5), billable_usage_spans),
                        arraySum(s -> toInt64(s.10
                            AND NOT tupleElement(s.9, 1) AND NOT tupleElement(s.9, 2)
                            AND NOT (s.4 != 'llm'
                                AND has(repeated_usage_ids, s.1))), usage_spans),
                        arrayCount(s -> tupleElement(s.9, 2), billable_usage_spans),
                        arraySum(s -> s.6, billable_usage_spans),
                        arraySum(s -> s.7, billable_usage_spans),
                        arraySum(s -> s.8, billable_usage_spans)
                    ), [arrayFilter(s -> NOT tupleElement(s.9, 1)
                        AND NOT (s.4 != 'llm' AND has(repeated_usage_ids, s.1)), usage_spans)])[1],
                    [{repeated_ids}]
                )[1],
                [groupArray(tuple({values}))]
            ) AS trace_usage_metrics
        """
    if materialized:
        capture = (
            ", arrayExists(s -> s.10, usage_spans), "
            "arrayCount(s -> s.4 = 'llm', billable_usage_spans)"
            if source_summary is not None
            else ""
        )
        return f"""
            arrayMap(usage_spans ->
                arrayMap(repeated_usage_ids ->
                    arrayMap(billable_usage_spans ->
                        arrayMap(cost_unknown -> tuple(cost_unknown,
                            if(cost_unknown, CAST(NULL, 'Nullable(Float64)'),
                                arraySum(s -> s.5, billable_usage_spans)),
                            arraySum(s -> s.6, billable_usage_spans),
                            arraySum(s -> s.7, billable_usage_spans),
                            arraySum(s -> s.8, billable_usage_spans) {capture}),
                            [arrayExists(s -> tupleElement(s.9, 2), billable_usage_spans)])[1],
                        [arrayFilter(s -> NOT tupleElement(s.9, 1)
                            AND NOT (s.4 != 'llm' AND has(repeated_usage_ids, s.1)), usage_spans)])[1],
                    [{repeated_ids}])[1],
                [groupArray(tuple({values}))]) AS trace_usage_metrics
        """
    projections = [
        f"groupArray(tuple({values})) AS usage_spans",
        f"{repeated_ids} AS repeated_usage_ids",
        "arrayFilter(s -> NOT tupleElement(s.9, 1) AND NOT (s.4 != 'llm' AND has(repeated_usage_ids, s.1)), usage_spans) AS billable_usage_spans",
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


def repeated_usage_ids_sql(*, child_condition: str = "1") -> str:
    """Proof over usage_spans: (id, parent, name, kind, cost, total, in, out, flags).

    Only GenAI candidates compute transitive reachability. arrayFold terminates
    logically when the frontier is empty; at most N hops covers an N-row tree.
    IDs already visited never re-enter, so cycles terminate too. Pydantic keeps
    its existing immediate-child proof. No raw attribute maps enter the arrays.
    """
    comparisons = " AND ".join(
        f"abs(s.{i} - arraySum(c -> c.{i}, proof_children)) <= greatest(abs(s.{i}) * 1e-9, 1e-18)"
        if column == "cost"
        else f"s.{i} = arraySum(c -> c.{i}, proof_children)"
        for i, column in enumerate(USAGE_COLUMNS, 5)
    )
    # tupleConcat accepts old two-flag internal callers without fabricating an
    # operation witness. New readers project the third bit at the source.
    pydantic_candidates = (
        f"arrayFilter(s -> s.4 != 'llm' AND s.3 = '{SUMMARY_NAME}', usage_spans)"
    )
    genai_candidates = (
        f"arrayFilter(s -> s.4 != 'llm' AND s.3 != '{SUMMARY_NAME}' "
        "AND NOT tupleElement(s.9, 1) "
        "AND tupleElement(tupleConcat(s.9, tuple(0)), 3), usage_spans)"
    )
    descendants = f"""
        arrayFilter(c -> c.4 = 'llm' AND c.1 != s.1 AND ({child_condition})
            AND has(reachable_ids, c.2), usage_spans)
    """
    # Traverse only spans that themselves parent another row. A wide agent
    # with N leaf LLMs needs one ancestry step, not N scans of N leaf IDs.
    branches = """
        arrayMap(parent_ids -> arrayFilter(c -> has(parent_ids, c.1), usage_spans),
            [arrayDistinct(arrayMap(c -> c.2, usage_spans))])[1]
    """
    reachable = f"""
        arrayMap(usage_branches -> arrayFold((state, hop) ->
            arrayMap(next_ids -> tuple(
                if(empty(next_ids), state.1,
                    arrayConcat(if(empty(next_ids), [], state.1), next_ids)), next_ids),
                [arrayMap(c -> c.1, arrayFilter(c -> has(state.2, c.2)
                    AND NOT has(state.1, c.1),
                    if(empty(state.2), [], usage_branches)))])[1],
            range(length(usage_branches)), tuple([s.1], [s.1])).1,
            [{branches}])[1]
    """

    immediate = f"arrayFilter(c -> c.2 = s.1 AND ({child_condition}), usage_spans)"
    legacy_comparison = comparisons.replace("proof_children", immediate)
    legacy = (
        "arrayMap(s -> s.1, arrayFilter(s -> "
        f"arrayExists(c -> c.2 = s.1 AND ({child_condition}), usage_spans) "
        f"AND {legacy_comparison}, {pydantic_candidates}))"
    )
    # Pydantic keeps its original lightweight proof; only GenAI candidates
    # enter the lexically bound descendant/collision proof. Nonadditive physical
    # siblings cannot veto an equal legacy wrapper after a normalization retry.
    genai = f"""
        arrayMap(summary_proofs ->
            arrayMap(proof -> proof.1, arrayFilter(proof -> proof.2
                AND arrayCount(p -> p.1 = proof.1 AND p.2, summary_proofs)
                    = arrayCount(c -> c.1 = proof.1 AND c.4 != 'llm'
                        AND NOT tupleElement(c.9, 1), usage_spans), summary_proofs)),
            [arrayMap(s -> tuple(s.1,
                arrayMap(proof_children -> notEmpty(proof_children) AND {comparisons},
                    [arrayMap(reachable_ids -> {descendants}, [{reachable}])[1]])[1]),
                {genai_candidates})])[1]
    """
    return f"arrayConcat({legacy}, {genai})"
