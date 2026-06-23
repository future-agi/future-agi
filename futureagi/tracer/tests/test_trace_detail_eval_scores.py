"""Unit tests for trace-detail eval-score grouping.

``build_task_grouped_eval_scores`` groups span-level EvalLogger rows into
``eval_task -> eval -> {aggregate, spans}``:
  * root span  -> aggregate + span-wise data across ALL trace spans
  * child span -> same structure scoped to that span only

Per output type the ``aggregate`` is avg% (score), ``{"pass","fail"}`` counts
(Pass/Fail), or ``{label: count}`` zero-filled (Choices); the per-span ``value``
is the raw score / "pass"|"fail" / [labels].
"""

from types import SimpleNamespace

import pytest

from tracer.utils.helper import (
    attach_grouped_eval_scores,
    build_task_grouped_eval_scores,
    fetch_grouped_eval_rows,
)

CONFIG_LOOKUP = {
    "cfg1": {"name": "Eval1", "output": "score", "choices": []},
    "cfg2": {"name": "Eval2", "output": "Pass/Fail", "choices": []},
    "cfg3": {
        "name": "Eval3",
        "output": "choices",
        "choices": ["Pass", "Fail", "Unknown"],
    },
}
TASK_LOOKUP = {"task1": "Eval task1", "task2": "Eval task2"}
SPAN_NAMES = {"SPAN1": "s1", "SPAN2": "s2", "SPAN3": "s3"}


def _row(span, cid, tid, **kw):
    base = {
        "span_id": span,
        "eval_config_id": cid,
        "eval_task_id": tid,
        "target_type": "span",
        "output_float": None,
        "output_bool": None,
        "output_str": None,
        "output_str_list": "[]",
        "error": 0,
        "explanation": None,
    }
    base.update(kw)
    return base


def _three_span_rows():
    return [
        _row("SPAN1", "cfg1", "task1", output_float=0.6),
        _row("SPAN2", "cfg1", "task1", output_float=0.8),
        _row("SPAN3", "cfg1", "task1", output_float=0.9),
        _row("SPAN1", "cfg2", "task1", output_bool=0),
        _row("SPAN2", "cfg2", "task1", output_bool=1),
        _row("SPAN3", "cfg2", "task1", output_bool=0),
        _row("SPAN1", "cfg3", "task1", output_str_list='["Pass"]'),
        _row("SPAN2", "cfg3", "task1", output_str_list='["Pass"]'),
        _row("SPAN3", "cfg3", "task1", output_str_list='["Fail"]'),
    ]


def _evals_by_cid(result, task_index=0):
    return {e["eval_config_id"]: e for e in result["eval_tasks"][task_index]["evals"]}


def test_root_aggregates_across_all_spans():
    out = build_task_grouped_eval_scores(
        _three_span_rows(), CONFIG_LOOKUP, TASK_LOOKUP, SPAN_NAMES, "trace"
    )
    assert out["scope"] == "trace"
    assert out["eval_tasks"][0]["eval_task_id"] == "task1"
    assert out["eval_tasks"][0]["eval_task_name"] == "Eval task1"
    evals = _evals_by_cid(out)

    # score -> avg %
    assert evals["cfg1"]["output_type"] == "score"
    assert evals["cfg1"]["aggregate"] == round((0.6 + 0.8 + 0.9) / 3 * 100, 2)
    # pass/fail -> counts
    assert evals["cfg2"]["aggregate"] == {"pass": 1, "fail": 2}
    # choices -> zero-filled counts
    assert evals["cfg3"]["aggregate"] == {"Pass": 2, "Fail": 1, "Unknown": 0}
    # span-wise data covers all three spans
    assert {s["span_id"] for s in evals["cfg1"]["spans"]} == {"SPAN1", "SPAN2", "SPAN3"}
    # target_type carried per eval (span/trace/session)
    assert evals["cfg1"]["target_type"] == "span"
    assert evals["cfg2"]["target_type"] == "span"


def test_target_type_carried_per_eval():
    rows = [
        _row("SPAN1", "cfg1", "task1", output_float=0.6, target_type="trace"),
        _row("SPAN1", "cfg2", "task1", output_bool=1, target_type="span"),
    ]
    out = build_task_grouped_eval_scores(
        rows, CONFIG_LOOKUP, TASK_LOOKUP, SPAN_NAMES, "trace"
    )
    evals = _evals_by_cid(out)
    assert evals["cfg1"]["target_type"] == "trace"
    assert evals["cfg2"]["target_type"] == "span"


def test_root_span_wise_values_are_raw():
    out = build_task_grouped_eval_scores(
        _three_span_rows(), CONFIG_LOOKUP, TASK_LOOKUP, SPAN_NAMES, "trace"
    )
    evals = _evals_by_cid(out)
    by_span = {s["span_id"]: s for s in evals["cfg2"]["spans"]}
    assert by_span["SPAN1"]["value"] == "fail"
    assert by_span["SPAN2"]["value"] == "pass"
    assert by_span["SPAN2"]["span_name"] == "s2"
    choice_by_span = {s["span_id"]: s["value"] for s in evals["cfg3"]["spans"]}
    assert choice_by_span["SPAN1"] == ["Pass"]
    assert choice_by_span["SPAN3"] == ["Fail"]


def test_child_span_scope_only_that_span():
    rows = [r for r in _three_span_rows() if r["span_id"] == "SPAN1"]
    out = build_task_grouped_eval_scores(
        rows, CONFIG_LOOKUP, TASK_LOOKUP, SPAN_NAMES, "span"
    )
    assert out["scope"] == "span"
    evals = _evals_by_cid(out)
    # Single-span aggregate + single span entry.
    assert evals["cfg2"]["aggregate"] == {"pass": 0, "fail": 1}
    assert len(evals["cfg2"]["spans"]) == 1
    assert evals["cfg2"]["spans"][0]["span_id"] == "SPAN1"
    assert evals["cfg1"]["aggregate"] == 60.0


def test_grouping_separates_eval_tasks():
    rows = [
        _row("SPAN1", "cfg1", "task1", output_float=0.6),
        _row("SPAN1", "cfg1", "task2", output_float=0.9),
    ]
    out = build_task_grouped_eval_scores(
        rows, CONFIG_LOOKUP, TASK_LOOKUP, SPAN_NAMES, "trace"
    )
    tasks = {t["eval_task_id"]: t for t in out["eval_tasks"]}
    assert set(tasks) == {"task1", "task2"}
    assert tasks["task1"]["evals"][0]["aggregate"] == 60.0
    assert tasks["task2"]["evals"][0]["aggregate"] == 90.0


def test_null_eval_task_buckets_under_ungrouped():
    rows = [_row("SPAN1", "cfg1", None, output_float=0.5)]
    out = build_task_grouped_eval_scores(
        rows, CONFIG_LOOKUP, TASK_LOOKUP, SPAN_NAMES, "span"
    )
    assert out["eval_tasks"][0]["eval_task_id"] is None
    assert out["eval_tasks"][0]["eval_task_name"] == "Ungrouped"


def test_errored_rows_excluded_from_aggregate():
    rows = [
        _row("SPAN1", "cfg2", "task1", output_bool=1),
        _row("SPAN2", "cfg2", "task1", output_bool=0, error=1),
        _row("SPAN3", "cfg2", "task1", output_str="ERROR"),
    ]
    out = build_task_grouped_eval_scores(
        rows, CONFIG_LOOKUP, TASK_LOOKUP, SPAN_NAMES, "trace"
    )
    evals = _evals_by_cid(out)
    # Only SPAN1 (pass) counts; the error rows are excluded from the aggregate.
    assert evals["cfg2"]["aggregate"] == {"pass": 1, "fail": 0}
    by_span = {s["span_id"]: s for s in evals["cfg2"]["spans"]}
    assert by_span["SPAN2"]["error"] is True
    assert by_span["SPAN2"]["value"] is None


def test_unknown_config_rows_skipped():
    rows = [_row("SPAN1", "missing_cfg", "task1", output_float=0.5)]
    out = build_task_grouped_eval_scores(
        rows, CONFIG_LOOKUP, TASK_LOOKUP, SPAN_NAMES, "trace"
    )
    assert out["eval_tasks"] == []


# ---------------------------------------------------------------------------
# attach_grouped_eval_scores — the SHARED voice-call-detail / trace-detail span
# wiring. Root span gets the trace-level view (all spans); each other span gets
# its own scope. Returns the trace-level structure for top-level surfacing.
# ---------------------------------------------------------------------------


def _voice_spans():
    # Flat list as built by _voice_call_detail_clickhouse: conversation root
    # first, then children.
    return [
        {"id": "SPAN1", "name": "conversation"},  # root conversation span
        {"id": "SPAN2", "name": "child-a"},
        {"id": "SPAN3", "name": "child-b"},
    ]


def _rows_by_span(rows):
    out = {}
    for r in rows:
        out.setdefault(r["span_id"], []).append(r)
    return out


def _span_targets(spans, root_id="SPAN1"):
    # (span_id, span_name, is_root, target_dict) — same tuple both endpoints
    # build (voice: id == root_span_id; trace: _parent_id is None).
    return [(s["id"], s.get("name"), s["id"] == root_id, s) for s in spans]


def test_attach_root_gets_trace_scope_all_spans():
    rows = _three_span_rows()
    spans = _voice_spans()
    trace_level = attach_grouped_eval_scores(
        _span_targets(spans), rows, _rows_by_span(rows), CONFIG_LOOKUP, TASK_LOOKUP
    )

    # Returned structure == the root span's eval_scores, trace scope.
    assert trace_level["scope"] == "trace"
    root = next(s for s in spans if s["id"] == "SPAN1")
    assert root["eval_scores"] is trace_level
    evals = _evals_by_cid(root["eval_scores"])
    assert evals["cfg2"]["aggregate"] == {"pass": 1, "fail": 2}
    assert {s["span_id"] for s in evals["cfg1"]["spans"]} == {"SPAN1", "SPAN2", "SPAN3"}


def test_attach_children_get_span_scope_only_self():
    rows = _three_span_rows()
    spans = _voice_spans()
    attach_grouped_eval_scores(
        _span_targets(spans), rows, _rows_by_span(rows), CONFIG_LOOKUP, TASK_LOOKUP
    )

    child = next(s for s in spans if s["id"] == "SPAN2")
    assert child["eval_scores"]["scope"] == "span"
    evals = _evals_by_cid(child["eval_scores"])
    # SPAN2 only: cfg2 bool=1 -> pass, cfg1 score 0.8 -> 80.
    assert evals["cfg2"]["aggregate"] == {"pass": 1, "fail": 0}
    assert evals["cfg1"]["aggregate"] == 80.0
    for ev in child["eval_scores"]["eval_tasks"][0]["evals"]:
        assert [s["span_id"] for s in ev["spans"]] == ["SPAN2"]


def test_attach_every_span_has_eval_scores():
    rows = _three_span_rows()
    spans = _voice_spans()
    attach_grouped_eval_scores(
        _span_targets(spans), rows, _rows_by_span(rows), CONFIG_LOOKUP, TASK_LOOKUP
    )
    assert all("eval_scores" in s for s in spans)
    assert spans[0]["eval_scores"]["scope"] == "trace"
    assert all(s["eval_scores"]["scope"] == "span" for s in spans[1:])


def test_attach_single_span_trace_voice_call_shape():
    # Voice calls typically have ONE span per trace: the root conversation span
    # carries the trace-level aggregate over just itself.
    rows = [r for r in _three_span_rows() if r["span_id"] == "SPAN1"]
    spans = [{"id": "SPAN1", "name": "conversation"}]
    trace_level = attach_grouped_eval_scores(
        _span_targets(spans), rows, _rows_by_span(rows), CONFIG_LOOKUP, TASK_LOOKUP
    )
    root = spans[0]
    assert root["eval_scores"] is trace_level
    assert root["eval_scores"]["scope"] == "trace"
    evals = _evals_by_cid(root["eval_scores"])
    assert evals["cfg2"]["aggregate"] == {"pass": 0, "fail": 1}  # SPAN1 bool=0
    for ev in root["eval_scores"]["eval_tasks"][0]["evals"]:
        assert [s["span_id"] for s in ev["spans"]] == ["SPAN1"]


def test_attach_span_with_no_rows_is_empty_but_scoped():
    rows = [r for r in _three_span_rows() if r["span_id"] in ("SPAN1", "SPAN2")]
    spans = _voice_spans()
    attach_grouped_eval_scores(
        _span_targets(spans), rows, _rows_by_span(rows), CONFIG_LOOKUP, TASK_LOOKUP
    )
    span3 = next(s for s in spans if s["id"] == "SPAN3")
    assert span3["eval_scores"] == {"scope": "span", "eval_tasks": []}


# ---------------------------------------------------------------------------
# fetch_grouped_eval_rows — the SHARED CH fetch + batched PG name lookups used
# by both detail endpoints. CH is faked; config/task names come from PG.
# ---------------------------------------------------------------------------


def _fake_analytics(rows):
    return SimpleNamespace(execute_ch_query=lambda *a, **k: SimpleNamespace(data=rows))


def _ch_eval_row(span_id, cid, tid, **kw):
    base = {
        "span_id": span_id,
        "eval_config_id": cid,
        "eval_task_id": tid,
        "target_type": "span",
        "output_float": None,
        "output_bool": None,
        "output_str": None,
        "output_str_list": "[]",
        "error": 0,
        "eval_explanation": None,
    }
    base.update(kw)
    return base


@pytest.mark.django_db
def test_fetch_builds_rows_and_batched_lookups(custom_eval_config, eval_task):
    cid = str(custom_eval_config.id)
    tid = str(eval_task.id)
    ch_rows = [
        _ch_eval_row("SPANA", cid, tid, output_float=0.8, eval_explanation="ok"),
        _ch_eval_row("SPANB", cid, tid, output_float=0.6),
        _ch_eval_row("", cid, tid, output_float=0.5),  # no span_id -> skipped
        # Valid UUID but no matching CustomEvalConfig row -> skipped.
        _ch_eval_row("SPANC", "00000000-0000-0000-0000-000000000099", tid),
    ]
    eval_rows, rows_by_span, config_lookup, task_lookup = fetch_grouped_eval_rows(
        _fake_analytics(ch_rows), "trace-1"
    )

    # Only the two valid span-level rows for known configs survive.
    assert len(eval_rows) == 2
    assert set(rows_by_span) == {"SPANA", "SPANB"}
    # Batched PG name lookups resolved.
    assert config_lookup[cid]["name"] == custom_eval_config.name
    assert config_lookup[cid]["output"] == "score"  # template has no "output"
    assert task_lookup[tid] == eval_task.name
    # Explanation normalised ("" -> None handled at row level).
    assert rows_by_span["SPANA"][0]["explanation"] == "ok"
    assert rows_by_span["SPANB"][0]["explanation"] is None
    # target_type carried into normalised rows.
    assert rows_by_span["SPANA"][0]["target_type"] == "span"


@pytest.mark.django_db
def test_fetch_returns_empty_on_ch_failure():
    def boom(*a, **k):
        raise RuntimeError("CH unavailable")

    analytics = SimpleNamespace(execute_ch_query=boom)
    eval_rows, rows_by_span, config_lookup, task_lookup = fetch_grouped_eval_rows(
        analytics, "trace-1"
    )
    assert eval_rows == []
    assert rows_by_span == {}
    assert config_lookup == {}
    assert task_lookup == {}


# ---------------------------------------------------------------------------
# Parity: trace-detail root span vs voice-detail (single root span) produce the
# SAME eval_scores. Both endpoints build the same (id, name, is_root, target)
# tuples and call the same attach util, so the root span's output is identical.
# ---------------------------------------------------------------------------


def test_trace_root_and_voice_outputs_match():
    # Voice trace == one span (the conversation root). Trace-detail root span is
    # the parent_span_id-null span. Same rows -> same eval_scores.
    rows = [
        _row("ROOT", "cfg1", "task1", output_float=0.8),
        _row("ROOT", "cfg2", "task1", output_bool=1),
        _row("ROOT", "cfg3", "task1", output_str_list='["Pass"]'),
    ]
    rbs = _rows_by_span(rows)

    # trace-detail wiring: root identified by `_parent_id is None`, written to
    # the span-tree entry wrapper.
    trace_entry = {"id": "ROOT"}
    trace_level_t = attach_grouped_eval_scores(
        [("ROOT", "conversation", True, trace_entry)],
        rows,
        rbs,
        CONFIG_LOOKUP,
        TASK_LOOKUP,
    )

    # voice-detail wiring: root identified by `id == root_span_id`, written to
    # the observation_span dict.
    voice_span = {"id": "ROOT", "name": "conversation"}
    trace_level_v = attach_grouped_eval_scores(
        [("ROOT", "conversation", True, voice_span)],
        rows,
        rbs,
        CONFIG_LOOKUP,
        TASK_LOOKUP,
    )

    # The two endpoints emit byte-identical eval_scores for the root span.
    assert trace_entry["eval_scores"] == voice_span["eval_scores"]
    assert trace_level_t == trace_level_v
    # And the root span carries the trace-level aggregate.
    assert trace_entry["eval_scores"] == trace_level_t
    assert trace_entry["eval_scores"]["scope"] == "trace"
    evals = _evals_by_cid(trace_entry["eval_scores"])
    assert evals["cfg1"]["aggregate"] == 80.0
    assert evals["cfg2"]["aggregate"] == {"pass": 1, "fail": 0}
    assert evals["cfg3"]["aggregate"] == {"Pass": 1, "Fail": 0, "Unknown": 0}
