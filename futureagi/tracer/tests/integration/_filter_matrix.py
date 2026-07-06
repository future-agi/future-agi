"""Cartesian filter matrix for eval-task filter parity tests.

Each FilterCase is uniquely identified by case_id and pairs a filter dict shape
(matching what FE produces and BE parses in parsing_evaltask_filters) with an
expected_predicate that, given a SeededRow, returns True iff that row should
match the filter under BE semantics.

When a new (col_type, filter_type, filter_op) leaf is added to the FE/BE, add
it here as well — completeness is enforced by test_filter_matrix_completeness.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterator

from tracer.tests.integration._seed import _NOW, SeededRow

TARGET_TYPES = ("spans", "traces", "sessions", "voiceCalls")
COL_TYPES = (
    "SYSTEM_METRIC",
    "SPAN_ATTRIBUTE",
    "EVAL_METRIC",
    "ANNOTATION",
    "has_eval",
    "has_annotation",
)


@dataclass(frozen=True)
class FilterCase:
    case_id: str
    target_type: str
    col_type: str
    filter_type: str
    filter_op: str
    column_id: str
    filter_value: Any
    expected_predicate: Callable[[SeededRow], bool]

    def to_filter_dict(self) -> dict:
        """Return the JSON shape parsing_evaltask_filters() expects."""
        return {
            "filters": [
                {
                    "column_id": self.column_id,
                    "col_type": self.col_type,
                    "filter_config": {
                        "filter_type": self.filter_type,
                        "filter_op": self.filter_op,
                        "filter_value": self.filter_value,
                    },
                }
            ],
        }


# ---------- SYSTEM_METRIC leaves (per filter_type × filter_op) ---------------


def _sm_number_leaves():
    """SYSTEM_METRIC × number — column_id 'cost'."""
    leaves = [
        ("equals", 0.003, lambda r: r.cost == 0.003),
        ("not_equals", 0.003, lambda r: r.cost != 0.003),
        ("greater_than", 0.01, lambda r: r.cost > 0.01),
        ("less_than", 0.01, lambda r: r.cost < 0.01),
        ("greater_than_or_equal", 0.003, lambda r: r.cost >= 0.003),
        ("less_than_or_equal", 0.003, lambda r: r.cost <= 0.003),
        ("between", [0.002, 0.02], lambda r: 0.002 <= r.cost <= 0.02),
        ("not_between", [0.002, 0.02], lambda r: not (0.002 <= r.cost <= 0.02)),
        ("is_null", None, lambda r: r.cost is None),
        ("is_not_null", None, lambda r: r.cost is not None),
    ]
    return [("SYSTEM_METRIC", "number", op, "cost", val, pred) for op, val, pred in leaves]


def _sm_text_leaves():
    leaves = [
        ("equals", "gpt-4", lambda r: r.model == "gpt-4"),
        ("not_equals", "gpt-4", lambda r: r.model != "gpt-4"),
        ("contains", "gpt", lambda r: "gpt" in (r.model or "")),
        ("in", ["gpt-4", "claude-3-opus"], lambda r: r.model in ("gpt-4", "claude-3-opus")),
        ("not_in", ["gpt-4"], lambda r: r.model != "gpt-4"),
    ]
    return [("SYSTEM_METRIC", "text", op, "model", val, pred) for op, val, pred in leaves]


def _sm_datetime_leaves():
    from datetime import timedelta

    # _NOW = the corpus' base anchor; end = +1 day so the range covers session 0
    # spans (s_idx=0 → created_at in day 0).
    end = _NOW + timedelta(days=1)
    leaves = [
        (
            "between",
            [_NOW.isoformat(), end.isoformat()],
            lambda r: _NOW <= r.created_at <= end,
        ),
        ("greater_than", _NOW.isoformat(), lambda r: r.created_at > _NOW),
        ("less_than", end.isoformat(), lambda r: r.created_at < end),
        ("equals", _NOW.isoformat(), lambda r: r.created_at == _NOW),
    ]
    return [("SYSTEM_METRIC", "datetime", op, "created_at", val, pred) for op, val, pred in leaves]


# ---------- SPAN_ATTRIBUTE leaves --------------------------------------------


def _sa_leaves():
    return [
        (
            "SPAN_ATTRIBUTE",
            "text",
            "equals",
            "user_intent",
            "checkout",
            lambda r: r.span_attr_str.get("user_intent") == "checkout",
        ),
        (
            "SPAN_ATTRIBUTE",
            "text",
            "contains",
            "user_intent",
            "check",
            lambda r: "check" in r.span_attr_str.get("user_intent", ""),
        ),
        (
            "SPAN_ATTRIBUTE",
            "text",
            "in",
            "channel",
            ["web", "voice"],
            lambda r: r.span_attr_str.get("channel") in ("web", "voice"),
        ),
        (
            "SPAN_ATTRIBUTE",
            "number",
            "greater_than",
            "retries",
            1.0,
            lambda r: r.span_attr_num.get("retries", 0) > 1.0,
        ),
        (
            "SPAN_ATTRIBUTE",
            "number",
            "between",
            "score",
            [0.2, 0.35],
            lambda r: 0.2 <= r.span_attr_num.get("score", 0) <= 0.35,
        ),
        (
            "SPAN_ATTRIBUTE",
            "number",
            "equals",
            "retries",
            2.0,
            lambda r: r.span_attr_num.get("retries") == 2.0,
        ),
        (
            "SPAN_ATTRIBUTE",
            "number",
            "is_null",
            "missing_attr",
            None,
            lambda r: "missing_attr" not in r.span_attr_num,
        ),
        (
            "SPAN_ATTRIBUTE",
            "boolean",
            "equals",
            "premium",
            True,
            lambda r: r.span_attr_bool.get("premium") is True,
        ),
    ]


# ---------- EVAL_METRIC leaves -----------------------------------------------


def _em_leaves(eval_config_id: str):
    # Corpus eval_value ∈ {0.3, 0.6, 0.9}. BE annotates score = output_float * 100
    # (views/observation_span.py:1659), so filter values are on the 0-100 scale.
    return [
        (
            "EVAL_METRIC",
            "number",
            "greater_than",
            eval_config_id,
            50,
            lambda r: r.has_eval and (r.eval_value or 0) * 100 > 50,
        ),
        (
            "EVAL_METRIC",
            "number",
            "less_than",
            eval_config_id,
            50,
            lambda r: r.has_eval and (r.eval_value or 0) * 100 < 50,
        ),
        (
            "EVAL_METRIC",
            "number",
            "equals",
            eval_config_id,
            30,
            lambda r: r.has_eval and (r.eval_value or 0) * 100 == 30,
        ),
        (
            "EVAL_METRIC",
            "number",
            "between",
            eval_config_id,
            [40, 70],
            lambda r: r.has_eval and 40 <= (r.eval_value or 0) * 100 <= 70,
        ),
        (
            "EVAL_METRIC",
            "boolean",
            "equals",
            eval_config_id,
            True,
            lambda r: r.has_eval and (r.eval_value or 0) >= 0.5,
        ),
    ]


# ---------- ANNOTATION leaves ------------------------------------------------


def _ann_leaves(label_id: str):
    # Corpus annotation_value ∈ {0.2, 0.5, 0.8} (one per session).
    return [
        (
            "ANNOTATION",
            "number",
            "greater_than",
            label_id,
            0.4,
            lambda r: r.has_annotation and (r.annotation_value or 0) > 0.4,
        ),
        (
            "ANNOTATION",
            "number",
            "less_than",
            label_id,
            0.4,
            lambda r: r.has_annotation and (r.annotation_value or 0) < 0.4,
        ),
        (
            "ANNOTATION",
            "number",
            "equals",
            label_id,
            0.5,
            lambda r: r.has_annotation and r.annotation_value == 0.5,
        ),
        (
            "ANNOTATION",
            "number",
            "between",
            label_id,
            [0.3, 0.6],
            lambda r: r.has_annotation and 0.3 <= (r.annotation_value or 0) <= 0.6,
        ),
    ]


# ---------- has_eval / has_annotation ----------------------------------------


def _meta_leaves():
    return [
        ("has_eval", "boolean", "equals", "has_eval", True, lambda r: r.has_eval),
        ("has_eval", "boolean", "equals", "has_eval", False, lambda r: not r.has_eval),
        (
            "has_annotation",
            "boolean",
            "equals",
            "has_annotation",
            True,
            lambda r: r.has_annotation,
        ),
        (
            "has_annotation",
            "boolean",
            "equals",
            "has_annotation",
            False,
            lambda r: not r.has_annotation,
        ),
    ]


def _em_choice_leaves(choice_eval_config_id: str):
    # CHOICE / CHOICES evals: spans seeded with output_str_list=[choice_value]
    # where choice_value ∈ {"good", "bad", "neutral"} cycled by sp_idx.
    # Production filter handler routes EVAL_METRIC+array through
    # _eval_choice_condition (filters.py:1482-1505), which builds
    # Q(metric_<id>__<value>__score__gt=0). My _build_metric_annotation
    # exposes str_list_score = {choice: {"score": pct_present}} so the
    # JSON navigation lines up.
    return [
        (
            "EVAL_METRIC",
            "array",
            "contains",
            choice_eval_config_id,
            "good",
            lambda r: r.has_choice_eval and r.choice_value == "good",
        ),
        (
            "EVAL_METRIC",
            "array",
            "contains",
            choice_eval_config_id,
            "bad",
            lambda r: r.has_choice_eval and r.choice_value == "bad",
        ),
        (
            "EVAL_METRIC",
            "array",
            "contains",
            choice_eval_config_id,
            "neutral",
            lambda r: r.has_choice_eval and r.choice_value == "neutral",
        ),
    ]


def _all_leaves(eval_config_id: str, label_id: str, choice_eval_config_id: str):
    return (
        _sm_number_leaves()
        + _sm_text_leaves()
        + _sm_datetime_leaves()
        + _sa_leaves()
        + _em_leaves(eval_config_id)
        + _em_choice_leaves(choice_eval_config_id)
        + _ann_leaves(label_id)
        + _meta_leaves()
    )


def _short(s: str) -> str:
    """Stable suffix for case_id from a column_id (full UUIDs are noisy)."""
    if "-" in s and len(s) > 16:
        return s.replace("-", "")[-8:]
    return s


def _wrap_predicate_for_target(span_pred, target_type):
    """Given a per-span predicate, return a per-span predicate adjusted for the
    target type. For traces / sessions the aggregation to trace_id / session_id
    happens in the test harness; here we only need to narrow voiceCalls to root
    conversation spans."""
    if target_type == "voiceCalls":
        return lambda r: (
            r.observation_type == "conversation"
            and r.parent_span_id is None
            and span_pred(r)
        )
    return span_pred


def all_cases(
    eval_config_id: str = "00000000-0000-0000-0000-000000000001",
    label_id: str = "00000000-0000-0000-0000-000000000002",
    choice_eval_config_id: str = "00000000-0000-0000-0000-000000000003",
) -> Iterator[FilterCase]:
    """Yield FilterCases for every (target_type, leaf) combination."""
    for target_type in TARGET_TYPES:
        for (
            col_type,
            filter_type,
            filter_op,
            column_id,
            filter_value,
            pred,
        ) in _all_leaves(eval_config_id, label_id, choice_eval_config_id):
            adjusted_pred = _wrap_predicate_for_target(pred, target_type)
            val_suffix = ""
            if col_type in ("has_eval", "has_annotation"):
                # Disambiguate the True/False variants in the case_id.
                val_suffix = f"-{str(filter_value).lower()}"
            elif filter_type == "array" and isinstance(filter_value, str):
                # CHOICE leaves share column_id (eval_config_id) and differ
                # only on filter_value (e.g. "good"/"bad"/"neutral").
                val_suffix = f"-{filter_value}"
            case_id = (
                f"{target_type}-{col_type.lower()}-{filter_type}-"
                f"{filter_op}-{_short(column_id)}{val_suffix}"
            )
            yield FilterCase(
                case_id=case_id,
                target_type=target_type,
                col_type=col_type,
                filter_type=filter_type,
                filter_op=filter_op,
                column_id=column_id,
                filter_value=filter_value,
                expected_predicate=adjusted_pred,
            )


def all_cases_for(
    eval_config_id: str,
    label_id: str,
    choice_eval_config_id: str = "00000000-0000-0000-0000-000000000003",
) -> list[FilterCase]:
    """Materialized list for parametrize; callers that need the real
    eval_config_id / label_id pass them through here."""
    return list(
        all_cases(
            eval_config_id=eval_config_id,
            label_id=label_id,
            choice_eval_config_id=choice_eval_config_id,
        )
    )
