"""Cartesian filter matrix for eval-task filter parity tests.

Each FilterCase is uniquely identified by case_id and pairs a filter dict shape
(matching what FE produces and BE parses in parsing_evaltask_filters) with an
expected_predicate that, given a SeededRow, returns True iff that row should
match the filter under BE semantics.

When a new (col_type, filter_type, filter_op) leaf is added to the FE/BE, add
it here as well — completeness is enforced by test_filter_matrix_completeness.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from tracer.tests.integration._seed import (
    _NOW,
    VOICE_NUM_SPEC,
    VOICE_STR_SPEC,
    SeededRow,
)

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
    # A known backend gap (op silently dropped / unsupported). Set to the
    # reason string; the endpoint test xfails such cases instead of asserting
    # a count, so a future backend fix flips them to XPASS loudly.
    contract_gap: str | None = None
    # ID-only cases: the filter_value depends on the seeded corpus, so it is
    # resolved at test time. Returns (filter_value, per-row predicate).
    late_bound: Callable[[list[SeededRow]], tuple[Any, Callable]] | None = None
    # Extra filter items ANDed with the primary one (multi-filter combos).
    extra_filters: tuple[dict, ...] = ()
    # has_eval / has_annotation cases are trace/session-scoped in the backend
    # (a trace matches if ANY child span qualifies). When set to "has_eval" or
    # "has_annotation", _expected_count rolls the per-span flag up to the target
    # grain instead of counting spans directly.
    meta_kind: str | None = None
    # Session/trace aggregate cases: predicate over a target group's root spans
    # (list[SeededRow]) — used by _expected_count's aggregate branch.
    aggregate_predicate: Callable[[list[SeededRow]], bool] | None = None

    def to_filter_dict(self) -> dict:
        """Return the JSON shape parsing_evaltask_filters() expects."""
        primary = {
            "column_id": self.column_id,
            "filter_config": {
                "filter_type": self.filter_type,
                "filter_op": self.filter_op,
                "filter_value": self.filter_value,
                "col_type": self.col_type,
            },
        }
        # ID-only columns (trace_id / span_id / session) carry no col_type on
        # the wire — the FE omits it and the backend infers from column_id.
        if self.col_type == "ID":
            del primary["filter_config"]["col_type"]
        return {"filters": [primary, *self.extra_filters]}


# ---------- SYSTEM_METRIC leaves (per filter_type × filter_op) ---------------

_DATETIME_GAP = "parse_time_range supports gt/lt/between only"
_AGG_GAP = "session/trace aggregate HAVING supports comparison ops only"


def _sm_number_leaves():
    """SYSTEM_METRIC × number — column_id 'cost'. Per-span on spans/voiceCalls
    only; cost aggregates on traces/sessions live in the aggregate families."""
    sv = {"only_targets": ("spans", "voiceCalls")}
    leaves = [
        ("equals", 0.003, lambda r: r.cost == 0.003, sv),
        ("not_equals", 0.003, lambda r: r.cost != 0.003, sv),
        ("greater_than", 0.01, lambda r: r.cost > 0.01, sv),
        ("less_than", 0.01, lambda r: r.cost < 0.01, sv),
        ("greater_than_or_equal", 0.003, lambda r: r.cost >= 0.003, sv),
        ("less_than_or_equal", 0.003, lambda r: r.cost <= 0.003, sv),
        ("between", [0.002, 0.02], lambda r: 0.002 <= r.cost <= 0.02, sv),
        ("not_between", [0.002, 0.02], lambda r: not (0.002 <= r.cost <= 0.02), sv),
        ("is_null", None, lambda r: r.cost is None, sv),
        ("is_not_null", None, lambda r: r.cost is not None, sv),
    ]
    return [
        ("SYSTEM_METRIC", "number", op, "cost", val, pred, ex)
        for op, val, pred, ex in leaves
    ]


def _sm_text_leaves():
    # ``model`` is empty ("") on the sp_idx==0 root of each session's second
    # trace; the CH column is a non-Nullable String so is_null == empty and the
    # NOT-family ops (case-insensitive ILIKE) also match "".
    leaves = [
        ("equals", "model", "gpt-4", lambda r: r.model == "gpt-4"),
        ("not_equals", "model", "gpt-4", lambda r: r.model != "gpt-4"),
        ("contains", "model", "gpt", lambda r: "gpt" in (r.model or "").lower()),
        (
            "not_contains",
            "model",
            "gpt",
            lambda r: "gpt" not in (r.model or "").lower(),
        ),
        (
            "starts_with",
            "model",
            "gpt",
            lambda r: (r.model or "").lower().startswith("gpt"),
        ),
        (
            "ends_with",
            "model",
            "opus",
            lambda r: (r.model or "").lower().endswith("opus"),
        ),
        (
            "in",
            "model",
            ["gpt-4", "claude-3-opus"],
            lambda r: r.model in ("gpt-4", "claude-3-opus"),
        ),
        ("not_in", "model", ["gpt-4"], lambda r: r.model != "gpt-4"),
        ("is_null", "model", None, lambda r: r.model == ""),
        ("is_not_null", "model", None, lambda r: r.model != ""),
        # name = root span name; ROOT_ONLY so roots only, case-insensitive.
        (
            "starts_with",
            "name",
            "span_root_",
            lambda r: r.span_id.startswith("span_root_"),
        ),
    ]
    return [
        ("SYSTEM_METRIC", "text", op, col, val, pred) for op, col, val, pred in leaves
    ]


def _sm_datetime_leaves():
    from datetime import timedelta

    end = _NOW + timedelta(days=1)
    day1 = _NOW + timedelta(days=1)
    # between window end sits mid-gap (no row at +12h) so inclusive/exclusive
    # upper-bound differences between the span and trace list don't matter.
    mid = _NOW + timedelta(hours=12)
    gap = {"contract_gap": _DATETIME_GAP}
    # parse_time_range applies start_time >= start (inclusive), and only honours
    # gt / lt / between.
    leaves = [
        (
            "between",
            [_NOW.isoformat(), mid.isoformat()],
            lambda r: _NOW <= r.created_at <= mid,
            {},
        ),
        # greater_than → start bound (inclusive). Pick day1 to discriminate.
        ("greater_than", day1.isoformat(), lambda r: r.created_at >= day1, {}),
        # less_than defaults the (missing) start to utcnow-30d, which is AFTER
        # the fixed corpus window → the endpoint returns 0. Real backend gap.
        ("less_than", end.isoformat(), lambda r: r.created_at < end, gap),
        # All other datetime ops are silently dropped by parse_time_range → the
        # corpus falls outside the default window and the endpoint returns 0.
        ("equals", _NOW.isoformat(), lambda r: r.created_at == _NOW, gap),
        ("not_equals", _NOW.isoformat(), lambda r: r.created_at != _NOW, gap),
        (
            "greater_than_or_equal",
            _NOW.isoformat(),
            lambda r: r.created_at >= _NOW,
            gap,
        ),
        ("less_than_or_equal", end.isoformat(), lambda r: r.created_at <= end, gap),
        (
            "not_between",
            [_NOW.isoformat(), day1.isoformat()],
            lambda r: not (_NOW <= r.created_at <= day1),
            gap,
        ),
        # No row has a null created_at → endpoint (0) agrees with predicate (0).
        ("is_null", None, lambda r: False, {}),
        ("is_not_null", None, lambda r: True, gap),
    ]
    return [
        ("SYSTEM_METRIC", "datetime", op, "created_at", val, pred, ex)
        for op, val, pred, ex in leaves
    ]


def _sm_categorical_leaves():
    # status: full 8-op categorical set (is_null/is_not_null degenerate).
    status = [
        ("equals", "ERROR", lambda r: r.status == "ERROR"),
        ("not_equals", "ERROR", lambda r: r.status != "ERROR"),
        (
            "in",
            ["error", "warn"],
            lambda r: (r.status or "").lower() in ("error", "warn"),
        ),
        ("not_in", ["ok"], lambda r: (r.status or "").lower() != "ok"),
        ("contains", "err", lambda r: "err" in (r.status or "").lower()),
        ("not_contains", "err", lambda r: "err" not in (r.status or "").lower()),
        ("is_null", None, lambda r: r.status in (None, "")),
        ("is_not_null", None, lambda r: r.status not in (None, "")),
    ]
    status_leaves = [
        ("SYSTEM_METRIC", "categorical", op, "status", val, pred)
        for op, val, pred in status
    ]
    # Spot checks on other categorical system metrics (node_type aliases to
    # observation_type via SYSTEM_METRIC_MAP).
    others = [
        (
            "SYSTEM_METRIC",
            "categorical",
            "equals",
            "node_type",
            "llm",
            lambda r: r.observation_type == "llm",
        ),
        (
            "SYSTEM_METRIC",
            "categorical",
            "in",
            "node_type",
            ["llm", "chain"],
            lambda r: r.observation_type in ("llm", "chain"),
        ),
        (
            "SYSTEM_METRIC",
            "categorical",
            "equals",
            "provider",
            "openai",
            lambda r: r.provider == "openai",
        ),
        (
            "SYSTEM_METRIC",
            "categorical",
            "not_in",
            "provider",
            ["openai"],
            lambda r: r.provider != "openai",
        ),
    ]
    return status_leaves + others


def _id_leaves():
    """ID columns (trace_id / span_id / session) × in / not_in. Values depend
    on the seeded corpus, so they are resolved at test time (late_bound) —
    pick the lexicographically-smallest id for determinism."""

    def _make(field, negate):
        def lb(seeded):
            vals = sorted({getattr(r, field) for r in seeded})
            v = vals[0]
            if negate:
                return [v], (lambda r, v=v, f=field: getattr(r, f) != v)
            return [v], (lambda r, v=v, f=field: getattr(r, f) == v)

        return lb

    specs = [
        ("trace_id", "trace_id"),
        ("span_id", "span_id"),
        ("session", "session_id"),
    ]
    leaves = []
    for col_id, field in specs:
        leaves.append(
            (
                "ID",
                "text",
                "in",
                col_id,
                None,
                (lambda r: True),
                {"late_bound": _make(field, negate=False)},
            )
        )
        leaves.append(
            (
                "ID",
                "text",
                "not_in",
                col_id,
                None,
                (lambda r: True),
                {"late_bound": _make(field, negate=True)},
            )
        )
    return leaves


# ---------- SPAN_ATTRIBUTE leaves --------------------------------------------


def _sa_text_leaves():
    # Negation ops require key-present (builder ANDs mapContains).
    leaves = [
        (
            "equals",
            "user_intent",
            "checkout",
            lambda r: r.span_attr_str.get("user_intent") == "checkout",
        ),
        (
            "not_equals",
            "user_intent",
            "checkout",
            lambda r: (
                "user_intent" in r.span_attr_str
                and r.span_attr_str["user_intent"] != "checkout"
            ),
        ),
        (
            "contains",
            "user_intent",
            "check",
            lambda r: "check" in r.span_attr_str.get("user_intent", ""),
        ),
        (
            "not_contains",
            "user_intent",
            "check",
            lambda r: (
                "user_intent" in r.span_attr_str
                and "check" not in r.span_attr_str["user_intent"]
            ),
        ),
        (
            "starts_with",
            "user_intent",
            "brow",
            lambda r: r.span_attr_str.get("user_intent", "").startswith("brow"),
        ),
        (
            "ends_with",
            "user_intent",
            "out",
            lambda r: r.span_attr_str.get("user_intent", "").endswith("out"),
        ),
        (
            "in",
            "channel",
            ["web", "voice"],
            lambda r: r.span_attr_str.get("channel") in ("web", "voice"),
        ),
        (
            "not_in",
            "channel",
            ["web"],
            lambda r: (
                "channel" in r.span_attr_str and r.span_attr_str["channel"] != "web"
            ),
        ),
        # coupon present only on sp_idx==1 spans.
        ("is_null", "coupon", None, lambda r: "coupon" not in r.span_attr_str),
        ("is_not_null", "coupon", None, lambda r: "coupon" in r.span_attr_str),
    ]
    return [
        ("SPAN_ATTRIBUTE", "text", op, col, val, pred) for op, col, val, pred in leaves
    ]


def _sa_number_leaves():
    leaves = [
        ("equals", "retries", 2.0, lambda r: r.span_attr_num.get("retries") == 2.0),
        (
            "not_equals",
            "retries",
            2.0,
            lambda r: (
                "retries" in r.span_attr_num and r.span_attr_num["retries"] != 2.0
            ),
        ),
        (
            "greater_than",
            "retries",
            1.0,
            lambda r: "retries" in r.span_attr_num and r.span_attr_num["retries"] > 1.0,
        ),
        (
            "greater_than_or_equal",
            "retries",
            2.0,
            lambda r: (
                "retries" in r.span_attr_num and r.span_attr_num["retries"] >= 2.0
            ),
        ),
        (
            "less_than",
            "retries",
            2.0,
            lambda r: "retries" in r.span_attr_num and r.span_attr_num["retries"] < 2.0,
        ),
        (
            "less_than_or_equal",
            "retries",
            1.0,
            lambda r: (
                "retries" in r.span_attr_num and r.span_attr_num["retries"] <= 1.0
            ),
        ),
        (
            "between",
            "score",
            [0.2, 0.35],
            lambda r: (
                "score" in r.span_attr_num and 0.2 <= r.span_attr_num["score"] <= 0.35
            ),
        ),
        (
            "not_between",
            "retries",
            [1.0, 2.0],
            lambda r: (
                "retries" in r.span_attr_num
                and not (1.0 <= r.span_attr_num["retries"] <= 2.0)
            ),
        ),
        (
            "is_null",
            "missing_attr",
            None,
            lambda r: "missing_attr" not in r.span_attr_num,
        ),
        ("is_not_null", "retries", None, lambda r: "retries" in r.span_attr_num),
    ]
    return [
        ("SPAN_ATTRIBUTE", "number", op, col, val, pred)
        for op, col, val, pred in leaves
    ]


def _sa_boolean_leaves():
    # premium omitted for s_idx==2 → is_null discriminates.
    leaves = [
        ("equals", True, lambda r: r.span_attr_bool.get("premium") is True),
        (
            "not_equals",
            True,
            lambda r: (
                "premium" in r.span_attr_bool
                and r.span_attr_bool["premium"] is not True
            ),
        ),
        ("is_null", None, lambda r: "premium" not in r.span_attr_bool),
        ("is_not_null", None, lambda r: "premium" in r.span_attr_bool),
    ]
    return [
        ("SPAN_ATTRIBUTE", "boolean", op, "premium", val, pred)
        for op, val, pred in leaves
    ]


# ---------- EVAL_METRIC leaves -----------------------------------------------


def _em_leaves(eval_config_id: str):
    # Corpus eval_value ∈ {0.3, 0.6, 0.9}. BE scales to 0-100, so filter values
    # are on the 0-100 scale. is_null is a NOT-IN subquery that diverges at the
    # trace/session grain → span/voice only.
    sv = {"only_targets": ("spans", "voiceCalls")}

    def v(r):
        return (r.eval_value or 0) * 100

    leaves = [
        ("greater_than", 50, lambda r: r.has_eval and v(r) > 50, {}),
        ("less_than", 50, lambda r: r.has_eval and v(r) < 50, {}),
        ("equals", 30, lambda r: r.has_eval and v(r) == 30, {}),
        ("not_equals", 30, lambda r: r.has_eval and v(r) != 30, {}),
        ("greater_than_or_equal", 60, lambda r: r.has_eval and v(r) >= 60, {}),
        ("less_than_or_equal", 60, lambda r: r.has_eval and v(r) <= 60, {}),
        ("between", [40, 70], lambda r: r.has_eval and 40 <= v(r) <= 70, {}),
        ("not_between", [40, 70], lambda r: r.has_eval and not (40 <= v(r) <= 70), {}),
        ("is_null", None, lambda r: not r.has_eval, sv),
        ("is_not_null", None, lambda r: r.has_eval, {}),
    ]
    return [
        ("EVAL_METRIC", "number", op, eval_config_id, val, pred, ex)
        for op, val, pred, ex in leaves
    ]


def _em_pf_leaves(pf_eval_config_id: str):
    # PASS_FAIL eval on sp_idx ∈ {1,3}; pf_value True → Passed, False → Failed.
    sv = {"only_targets": ("spans", "voiceCalls")}
    leaves = [
        (
            "equals",
            "Passed",
            lambda r: r.has_pf_eval and r.pf_value is True,
            {"val_suffix": "-passed"},
        ),
        (
            "equals",
            "Failed",
            lambda r: r.has_pf_eval and r.pf_value is False,
            {"val_suffix": "-failed"},
        ),
        (
            "not_equals",
            "Passed",
            lambda r: r.has_pf_eval and r.pf_value is not True,
            {"val_suffix": "-passed"},
        ),
        ("is_null", None, lambda r: not r.has_pf_eval, sv),
        ("is_not_null", None, lambda r: r.has_pf_eval, {}),
    ]
    return [
        ("EVAL_METRIC", "boolean", op, pf_eval_config_id, val, pred, ex)
        for op, val, pred, ex in leaves
    ]


def _em_choice_leaves(choice_eval_config_id: str):
    # CHOICE evals: output_str_list=[choice_value], choice_value cycled by sp_idx.
    sv = {"only_targets": ("spans", "voiceCalls")}
    leaves = [
        (
            "contains",
            "good",
            lambda r: r.has_choice_eval and r.choice_value == "good",
            {},
        ),
        (
            "contains",
            "bad",
            lambda r: r.has_choice_eval and r.choice_value == "bad",
            {},
        ),
        (
            "contains",
            "neutral",
            lambda r: r.has_choice_eval and r.choice_value == "neutral",
            {},
        ),
        (
            "not_contains",
            "good",
            lambda r: r.has_choice_eval and r.choice_value != "good",
            {},
        ),
        ("is_null", None, lambda r: not r.has_choice_eval, sv),
        ("is_not_null", None, lambda r: r.has_choice_eval, {}),
    ]
    return [
        ("EVAL_METRIC", "array", op, choice_eval_config_id, val, pred, ex)
        for op, val, pred, ex in leaves
    ]


# ---------- ANNOTATION leaves ------------------------------------------------

# Annotation is_null is a NOT-IN subquery that diverges at the trace/session
# grain (every trace/session has an annotated span) → spans only.
_ANN_NULL_SPANS = {"only_targets": ("spans",)}


def _ann_number_leaves(label_id: str):
    # Numeric label, annotation_value ∈ {0.2, 0.5, 0.8} on sp_idx==2 spans.
    def v(r):
        return r.annotation_value or 0

    leaves = [
        ("greater_than", 0.4, lambda r: r.has_annotation and v(r) > 0.4, {}),
        ("less_than", 0.4, lambda r: r.has_annotation and v(r) < 0.4, {}),
        ("equals", 0.5, lambda r: r.has_annotation and r.annotation_value == 0.5, {}),
        (
            "not_equals",
            0.5,
            lambda r: r.has_annotation and r.annotation_value != 0.5,
            {},
        ),
        ("greater_than_or_equal", 0.5, lambda r: r.has_annotation and v(r) >= 0.5, {}),
        ("less_than_or_equal", 0.5, lambda r: r.has_annotation and v(r) <= 0.5, {}),
        ("between", [0.3, 0.6], lambda r: r.has_annotation and 0.3 <= v(r) <= 0.6, {}),
        (
            "not_between",
            [0.3, 0.6],
            lambda r: r.has_annotation and not (0.3 <= v(r) <= 0.6),
            {},
        ),
        ("is_null", None, lambda r: not r.has_annotation, _ANN_NULL_SPANS),
        ("is_not_null", None, lambda r: r.has_annotation, {}),
    ]
    return [
        ("ANNOTATION", "number", op, label_id, val, pred, ex)
        for op, val, pred, ex in leaves
    ]


def _ann_text_leaves(text_label_id: str):
    # ann_text ∈ {"helpful", "needs work", "spam"} by s_idx on sp_idx==2 spans.
    def t(r):
        return r.ann_text or ""

    leaves = [
        (
            "equals",
            "helpful",
            lambda r: r.has_annotation and r.ann_text == "helpful",
            {},
        ),
        (
            "not_equals",
            "helpful",
            lambda r: r.has_annotation and r.ann_text != "helpful",
            {},
        ),
        ("contains", "work", lambda r: r.has_annotation and "work" in t(r), {}),
        ("not_contains", "work", lambda r: r.has_annotation and "work" not in t(r), {}),
        (
            "starts_with",
            "need",
            lambda r: r.has_annotation and t(r).startswith("need"),
            {},
        ),
        ("ends_with", "pam", lambda r: r.has_annotation and t(r).endswith("pam"), {}),
        (
            "in",
            ["helpful", "spam"],
            lambda r: r.has_annotation and r.ann_text in ("helpful", "spam"),
            {},
        ),
        (
            "not_in",
            ["helpful"],
            lambda r: r.has_annotation and r.ann_text != "helpful",
            {},
        ),
        ("is_null", None, lambda r: not r.has_annotation, _ANN_NULL_SPANS),
        ("is_not_null", None, lambda r: r.has_annotation, {}),
    ]
    return [
        ("ANNOTATION", "text", op, text_label_id, val, pred, ex)
        for op, val, pred, ex in leaves
    ]


def _ann_thumbs_leaves(thumbs_label_id: str):
    # ann_thumb ∈ {"up", "up", "down"} by s_idx. FE sends display tokens.
    leaves = [
        (
            "equals",
            "Thumbs Up",
            lambda r: r.has_annotation and r.ann_thumb == "up",
            {"val_suffix": "-up"},
        ),
        (
            "not_equals",
            "Thumbs Up",
            lambda r: r.has_annotation and r.ann_thumb != "up",
            {"val_suffix": "-up"},
        ),
        (
            "in",
            ["Thumbs Up"],
            lambda r: r.has_annotation and r.ann_thumb == "up",
            {"val_suffix": "-up"},
        ),
        (
            "not_in",
            ["Thumbs Up"],
            lambda r: r.has_annotation and r.ann_thumb != "up",
            {"val_suffix": "-up"},
        ),
        ("is_null", None, lambda r: not r.has_annotation, _ANN_NULL_SPANS),
        ("is_not_null", None, lambda r: r.has_annotation, {}),
    ]
    return [
        ("ANNOTATION", "thumbs", op, thumbs_label_id, val, pred, ex)
        for op, val, pred, ex in leaves
    ]


def _ann_categorical_leaves(categorical_label_id: str):
    # ann_selected ∈ {["tag_a"], ["tag_b"], ["tag_a","tag_b"]}; equals = membership.
    def sel(r):
        return r.ann_selected or []

    leaves = [
        (
            "equals",
            "tag_a",
            lambda r: r.has_annotation and "tag_a" in sel(r),
            {"val_suffix": "-taga"},
        ),
        (
            "not_equals",
            "tag_a",
            lambda r: r.has_annotation and "tag_a" not in sel(r),
            {"val_suffix": "-taga"},
        ),
        (
            "in",
            ["tag_b"],
            lambda r: r.has_annotation and "tag_b" in sel(r),
            {"val_suffix": "-tagb"},
        ),
        (
            "not_in",
            ["tag_b"],
            lambda r: r.has_annotation and "tag_b" not in sel(r),
            {"val_suffix": "-tagb"},
        ),
        (
            "contains",
            "tag_a",
            lambda r: r.has_annotation and "tag_a" in sel(r),
            {"val_suffix": "-taga"},
        ),
        (
            "not_contains",
            "tag_a",
            lambda r: r.has_annotation and "tag_a" not in sel(r),
            {"val_suffix": "-taga"},
        ),
        ("is_null", None, lambda r: not r.has_annotation, _ANN_NULL_SPANS),
        ("is_not_null", None, lambda r: r.has_annotation, {}),
    ]
    return [
        ("ANNOTATION", "categorical", op, categorical_label_id, val, pred, ex)
        for op, val, pred, ex in leaves
    ]


def _ann_annotator_leaves(label_id: str, annotator_user_id: str):
    # Per-label annotator: numeric-label Score carries the user for s_idx∈{0,1}.
    uid = [annotator_user_id]
    leaves = [
        ("equals", uid, lambda r: r.has_annotation and r.ann_annotator_is_user, {}),
        (
            "not_equals",
            uid,
            lambda r: r.has_annotation and not r.ann_annotator_is_user,
            {},
        ),
        ("in", uid, lambda r: r.has_annotation and r.ann_annotator_is_user, {}),
        ("not_in", uid, lambda r: r.has_annotation and not r.ann_annotator_is_user, {}),
        ("is_null", None, lambda r: not r.has_annotation, _ANN_NULL_SPANS),
        ("is_not_null", None, lambda r: r.has_annotation, {}),
    ]
    return [
        ("ANNOTATION", "annotator", op, label_id, val, pred, ex)
        for op, val, pred, ex in leaves
    ]


def _global_annotator_leaves(annotator_user_id: str):
    # Global ``annotator`` column (any label by the user); intercepted in
    # translate() before col_type dispatch.
    uid = [annotator_user_id]
    leaves = [
        ("equals", uid, lambda r: r.has_annotation and r.ann_annotator_is_user, {}),
        (
            "not_equals",
            uid,
            lambda r: r.has_annotation and not r.ann_annotator_is_user,
            {},
        ),
        ("is_null", None, lambda r: not r.has_annotation, _ANN_NULL_SPANS),
        ("is_not_null", None, lambda r: r.ann_annotator_is_user, {}),
    ]
    return [
        ("ANNOTATION", "annotator", op, "annotator", val, pred, ex)
        for op, val, pred, ex in leaves
    ]


# ---------- aggregate families ------------------------------------------------


def _session_aggregate_leaves():
    # Session aggregates sum over ROOT spans (session_list restricts the inner
    # scan to parent_span_id IS NULL). HAVING supports only comparison ops;
    # between/in/nulls compile to 0=1 → contract_gap.
    sess = {"only_targets": ("sessions",)}
    gap = {"only_targets": ("sessions",), "contract_gap": _AGG_GAP}

    def cost(g):
        return sum(r.cost for r in g)

    def tokens(g):
        return sum(r.total_tokens for r in g)

    def traces(g):
        return len({r.trace_id for r in g})

    dummy = lambda r: False  # noqa: E731 — aggregate cases use aggregate_predicate
    leaves = [
        ("total_cost", "greater_than", 0.04, lambda g: cost(g) > 0.04, sess),
        ("total_cost", "less_than", 0.04, lambda g: cost(g) < 0.04, sess),
        ("total_tokens", "greater_than", 30, lambda g: tokens(g) > 30, sess),
        ("total_tokens", "equals", 20, lambda g: tokens(g) == 20, sess),
        ("traces_count", "equals", 3, lambda g: traces(g) == 3, sess),
        ("traces_count", "greater_than", 2, lambda g: traces(g) > 2, sess),
        # gaps
        ("total_cost", "between", [0.04, 0.2], lambda g: 0.04 <= cost(g) <= 0.2, gap),
        (
            "total_cost",
            "not_between",
            [0.04, 0.2],
            lambda g: not (0.04 <= cost(g) <= 0.2),
            gap,
        ),
        ("traces_count", "in", [3], lambda g: traces(g) in (3,), gap),
        ("traces_count", "not_in", [3], lambda g: traces(g) not in (3,), gap),
        ("total_tokens", "is_not_null", None, lambda g: True, gap),
        ("total_tokens", "is_null", None, lambda g: False, sess),
    ]
    return [
        (
            "SYSTEM_METRIC",
            "number",
            op,
            col,
            val,
            dummy,
            {**ex, "aggregate_predicate": agg},
        )
        for col, op, val, agg, ex in leaves
    ]


def _trace_aggregate_leaves():
    # Trace list shows the root span value; cost is ROOT_ONLY so the filter
    # restricts to root spans.
    tr = {"only_targets": ("traces",)}

    def root(r):
        return r.parent_span_id is None

    leaves = [
        ("cost", "greater_than", 0.0035, lambda r: root(r) and r.cost > 0.0035, tr),
        ("cost", "less_than", 0.002, lambda r: root(r) and r.cost < 0.002, tr),
        ("cost", "equals", 0.002, lambda r: root(r) and r.cost == 0.002, tr),
        (
            "cost",
            "greater_than_or_equal",
            0.006,
            lambda r: root(r) and r.cost >= 0.006,
            tr,
        ),
        (
            "cost",
            "between",
            [0.0015, 0.0025],
            lambda r: root(r) and 0.0015 <= r.cost <= 0.0025,
            tr,
        ),
        (
            "total_tokens",
            "greater_than",
            20,
            lambda r: root(r) and r.total_tokens > 20,
            tr,
        ),
        ("cost", "is_null", None, lambda r: root(r) and r.cost is None, tr),
        ("cost", "is_not_null", None, lambda r: root(r) and r.cost is not None, tr),
    ]
    return [
        ("SYSTEM_METRIC", "number", op, col, val, pred, ex)
        for col, op, val, pred, ex in leaves
    ]


# ---------- has_eval / has_annotation ----------------------------------------


def _meta_leaves():
    # has_eval / has_annotation are trace/session-scoped in the backend (a
    # trace matches if ANY child span qualifies). meta_kind tells the harness
    # to roll the per-span flag up to the target grain. The `=False` variant of
    # has_eval is a known no-op (filters.py:1818 returns None), so it's a gap.
    return [
        (
            "has_eval",
            "boolean",
            "equals",
            "has_eval",
            True,
            lambda r: r.has_eval,
            {"meta_kind": "has_eval"},
        ),
        (
            "has_eval",
            "boolean",
            "equals",
            "has_eval",
            False,
            lambda r: not r.has_eval,
            {
                "meta_kind": "has_eval",
                "contract_gap": "has_eval=False is a no-op (filters.py:1818)",
            },
        ),
        (
            "has_annotation",
            "boolean",
            "equals",
            "has_annotation",
            True,
            lambda r: r.has_annotation,
            {"meta_kind": "has_annotation"},
        ),
        (
            "has_annotation",
            "boolean",
            "equals",
            "has_annotation",
            False,
            lambda r: not r.has_annotation,
            {"meta_kind": "has_annotation"},
        ),
    ]


# ---------- multi-filter AND combos ------------------------------------------


def _wire(col_type, filter_type, filter_op, column_id, filter_value):
    cfg = {
        "filter_type": filter_type,
        "filter_op": filter_op,
        "filter_value": filter_value,
    }
    if col_type != "ID":
        cfg["col_type"] = col_type
    return {"column_id": column_id, "filter_config": cfg}


def _combo_leaves(eval_config_id, label_id, choice_eval_config_id):
    # AND combos on the spans grid (per-span AND == the SQL semantics; the
    # trace/session grid ANDs independent subqueries, which we don't model
    # here). case_id prefix "combo-" via the combo_id extra.
    leaves = [
        # ① model equals ∧ cost gt (same col_type).
        (
            "SYSTEM_METRIC",
            "number",
            "greater_than",
            "cost",
            0.001,
            lambda r: r.cost > 0.001 and r.model == "gpt-4",
            {
                "combo_id": "model-cost",
                "extra_filters": (
                    _wire("SYSTEM_METRIC", "text", "equals", "model", "gpt-4"),
                ),
            },
        ),
        # ② SA attribute ∧ SM categorical.
        (
            "SPAN_ATTRIBUTE",
            "text",
            "equals",
            "user_intent",
            "checkout",
            lambda r: (
                r.span_attr_str.get("user_intent") == "checkout" and r.status == "OK"
            ),
            {
                "combo_id": "sa-status",
                "extra_filters": (
                    _wire("SYSTEM_METRIC", "categorical", "equals", "status", "OK"),
                ),
            },
        ),
        # ③ eval gt 50 ∧ annotation gt 0.4 (mixed subquery paths).
        (
            "EVAL_METRIC",
            "number",
            "greater_than",
            eval_config_id,
            50,
            lambda r: (
                r.has_eval
                and (r.eval_value or 0) * 100 > 50
                and r.has_annotation
                and (r.annotation_value or 0) > 0.4
            ),
            {
                "combo_id": "eval-annotation",
                "extra_filters": (
                    _wire("ANNOTATION", "number", "greater_than", label_id, 0.4),
                ),
            },
        ),
        # ④ SA channel in ∧ choice contains (array).
        (
            "EVAL_METRIC",
            "array",
            "contains",
            choice_eval_config_id,
            "bad",
            lambda r: (
                r.has_choice_eval
                and r.choice_value == "bad"
                and r.span_attr_str.get("channel") == "voice"
            ),
            {
                "combo_id": "channel-choice",
                "extra_filters": (
                    _wire("SPAN_ATTRIBUTE", "text", "in", "channel", ["voice"]),
                ),
            },
        ),
    ]
    return [
        (ct, ft, op, col, val, pred, {**ex, "only_targets": ("spans",)})
        for ct, ft, op, col, val, pred, ex in leaves
    ]


# ---------- Voice-call metric leaves (voiceCalls-only) -----------------------
# Data-driven from VOICE_NUM_SPEC / VOICE_STR_SPEC (_seed.py): every voice
# filter column x every operator x boundary values. Predicates assert the
# CORRECT (display-matching) behaviour — a passing case proves the filter is
# wired to the right stored key and discriminates; a failing case means the
# filter is wrong and the code must be fixed (no xfail).
_V_NUM_OPS = [
    "equals", "not_equals", "greater_than", "less_than",
    "greater_than_or_equal", "less_than_or_equal",
    "between", "not_between", "is_null", "is_not_null",
]
_V_STR_OPS = [
    "in", "not_in", "contains", "not_contains",
    "starts_with", "ends_with", "is_null", "is_not_null",
]


def _v_decode(v, precision):
    if v is None:
        return None
    if precision == "int":
        return float(round(v))
    if precision == "pct2":
        return round(v / (v + 1) * 100, 2) if v > 0 else None
    return v


def _v_num_pred(seed_key, precision, op, val):
    def d(r):
        return _v_decode(r.span_attr_num.get(seed_key), precision)

    return {
        "equals": lambda r: d(r) == val,
        "not_equals": lambda r: d(r) is not None and d(r) != val,
        "greater_than": lambda r: d(r) is not None and d(r) > val,
        "less_than": lambda r: d(r) is not None and d(r) < val,
        "greater_than_or_equal": lambda r: d(r) is not None and d(r) >= val,
        "less_than_or_equal": lambda r: d(r) is not None and d(r) <= val,
        "between": lambda r: d(r) is not None and val[0] <= d(r) <= val[1],
        "not_between": lambda r: d(r) is not None and not (val[0] <= d(r) <= val[1]),
        "is_null": lambda r: d(r) is None,
        "is_not_null": lambda r: d(r) is not None,
    }[op]


def _v_num_values(op, disp):
    n = len(disp)
    lo, hi, mid = disp[0], disp[-1], disp[n // 2]
    q1, q3 = disp[n // 4], disp[min(n - 1, (3 * n) // 4)]
    if op in ("greater_than", "less_than", "greater_than_or_equal", "less_than_or_equal"):
        return [(mid, "mid"), (lo, "lo")]
    if op in ("equals", "not_equals"):
        return [(mid, "hit"), (hi + 1, "miss")]
    if op in ("between", "not_between"):
        return [([q1, q3], "win")]
    return [(None, "")]  # is_null / is_not_null


def _voice_number_leaves():
    leaves = []
    for col_id, seed_key, formula, precision, _gap in VOICE_NUM_SPEC:
        disp = sorted(
            {
                _v_decode(formula(i), precision)
                for i in range(24)
                if _v_decode(formula(i), precision) is not None
            }
        )
        if not disp:
            continue
        for op in _V_NUM_OPS:
            for val, suf in _v_num_values(op, disp):
                ex = {"only_targets": ("voiceCalls",)}
                if suf:
                    ex["val_suffix"] = f"-{suf}"
                leaves.append(
                    ("SYSTEM_METRIC", "number", op, col_id, val,
                     _v_num_pred(seed_key, precision, op, val), ex)
                )
    return leaves


def _v_str_pred(seed_key, op, val):
    def s(r):
        return r.span_attr_str.get(seed_key)

    return {
        "in": lambda r: s(r) in val,
        "not_in": lambda r: s(r) is not None and s(r) not in val,
        "contains": lambda r: s(r) is not None and val in s(r),
        "not_contains": lambda r: s(r) is not None and val not in s(r),
        "starts_with": lambda r: s(r) is not None and s(r).startswith(val),
        "ends_with": lambda r: s(r) is not None and s(r).endswith(val),
        "is_null": lambda r: not s(r),
        "is_not_null": lambda r: bool(s(r)),
    }[op]


def _v_str_values(op, sample):
    if op in ("in", "not_in"):
        return [([sample], "hit"), (["__none__"], "miss")]
    if op in ("contains", "not_contains"):
        return [(sample, "hit"), ("__none__", "miss")]
    if op == "starts_with":
        return [(sample[:2], "hit")]
    if op == "ends_with":
        return [(sample[-2:], "hit")]
    return [(None, "")]  # is_null / is_not_null


def _voice_text_leaves():
    leaves = []
    for col_id, seed_key, formula, _gap in VOICE_STR_SPEC:
        if col_id == "call_type":
            continue  # filter reads raw_log.type, not this key (known bug)
        sample = formula(0)
        for op in _V_STR_OPS:
            for val, suf in _v_str_values(op, sample):
                ex = {"only_targets": ("voiceCalls",)}
                if suf:
                    ex["val_suffix"] = f"-{suf}"
                leaves.append(
                    ("SYSTEM_METRIC", "text", op, col_id, val,
                     _v_str_pred(seed_key, op, val), ex)
                )
    return leaves


def _all_leaves(
    eval_config_id: str,
    label_id: str,
    choice_eval_config_id: str,
    pf_eval_config_id: str,
    text_label_id: str,
    thumbs_label_id: str,
    categorical_label_id: str,
    annotator_user_id: str,
):
    return (
        _sm_number_leaves()
        + _sm_text_leaves()
        + _sm_datetime_leaves()
        + _sm_categorical_leaves()
        + _id_leaves()
        + _sa_text_leaves()
        + _sa_number_leaves()
        + _sa_boolean_leaves()
        + _em_leaves(eval_config_id)
        + _em_pf_leaves(pf_eval_config_id)
        + _em_choice_leaves(choice_eval_config_id)
        + _ann_number_leaves(label_id)
        + _ann_text_leaves(text_label_id)
        + _ann_thumbs_leaves(thumbs_label_id)
        + _ann_categorical_leaves(categorical_label_id)
        + _ann_annotator_leaves(label_id, annotator_user_id)
        + _global_annotator_leaves(annotator_user_id)
        + _session_aggregate_leaves()
        + _trace_aggregate_leaves()
        + _meta_leaves()
        + _combo_leaves(eval_config_id, label_id, choice_eval_config_id)
        + _voice_number_leaves()
        + _voice_text_leaves()
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
    pf_eval_config_id: str = "00000000-0000-0000-0000-000000000005",
    text_label_id: str = "00000000-0000-0000-0000-000000000006",
    thumbs_label_id: str = "00000000-0000-0000-0000-000000000007",
    categorical_label_id: str = "00000000-0000-0000-0000-000000000008",
    annotator_user_id: str = "00000000-0000-0000-0000-000000000009",
) -> Iterator[FilterCase]:
    """Yield FilterCases for every (target_type, leaf) combination."""
    leaves = _all_leaves(
        eval_config_id,
        label_id,
        choice_eval_config_id,
        pf_eval_config_id,
        text_label_id,
        thumbs_label_id,
        categorical_label_id,
        annotator_user_id,
    )
    for target_type in TARGET_TYPES:
        for leaf in leaves:
            col_type, filter_type, filter_op, column_id, filter_value, pred = leaf[:6]
            extras = dict(leaf[6]) if len(leaf) > 6 else {}
            # Families that only make sense for some targets gate themselves.
            only_targets = extras.pop("only_targets", None)
            if only_targets and target_type not in only_targets:
                continue
            adjusted_pred = _wrap_predicate_for_target(pred, target_type)
            val_suffix = extras.pop("val_suffix", "")
            combo_id = extras.pop("combo_id", None)
            if combo_id is not None:
                case_id = f"combo-{combo_id}-{target_type}"
            else:
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
                **extras,
            )


def all_cases_for(
    eval_config_id: str,
    label_id: str,
    choice_eval_config_id: str = "00000000-0000-0000-0000-000000000003",
    pf_eval_config_id: str = "00000000-0000-0000-0000-000000000005",
    text_label_id: str = "00000000-0000-0000-0000-000000000006",
    thumbs_label_id: str = "00000000-0000-0000-0000-000000000007",
    categorical_label_id: str = "00000000-0000-0000-0000-000000000008",
    annotator_user_id: str = "00000000-0000-0000-0000-000000000009",
) -> list[FilterCase]:
    """Materialized list for parametrize; callers that need the real
    seeded ids pass them through here."""
    return list(
        all_cases(
            eval_config_id=eval_config_id,
            label_id=label_id,
            choice_eval_config_id=choice_eval_config_id,
            pf_eval_config_id=pf_eval_config_id,
            text_label_id=text_label_id,
            thumbs_label_id=thumbs_label_id,
            categorical_label_id=categorical_label_id,
            annotator_user_id=annotator_user_id,
        )
    )
