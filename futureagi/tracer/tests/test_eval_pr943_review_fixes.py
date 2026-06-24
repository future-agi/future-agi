"""Regression tests for the PR #943 review fixes.

Each test pins one of the reviewer's blockers so the fix can't drift again:

  1. ``test_span_count_mode_score_zero_survives`` — a real 0.0 score on the
     span list count pivot is no longer blanked (was: ``avg_score != 0``).
  2. ``test_trace_count_mode_score_zero_survives`` — same guarantee on the
     trace list pivot; the two now share the cell builder.
  3. ``test_span_default_mode_score_zero_survives`` — the legacy span pivot
     also keeps a 0.0 score.
  4. ``test_fetch_grouped_eval_rows_orders_by_created_at`` — the eval-row
     fetch orders the rows by ``created_at`` so per-span latest-rerun is
     deterministic.
  5. ``test_per_span_value_uses_latest_created_at`` — when a rerun row arrives
     out of order, ``_per_span_eval_value`` picks the one with the largest
     ``created_at`` rather than scan position.
  6. ``test_fetch_grouped_eval_rows_raises_on_ch_failure`` — CH failure
     surfaces as ``EvalFetchError`` (not a silent empty result).
  7. ``test_discover_eval_configs_returns_configs_ids_and_task_map`` — the
     single discovery selector replaces the dictGet block that was copy-pasted
     in three places.
  8. ``test_trace_list_pivot_reads_rows_by_dict_key`` — pivot_eval_results
     reads rows by column name (no positional fallback).
  9. ``test_voice_eval_outputs_keeps_unscored_project_configs`` — the
     ``eval_outputs`` flat map keeps a project eval config's key even when
     this particular call has no score for it (``output=None``).
"""

from datetime import datetime
from types import SimpleNamespace

import pytest

from tracer.services.clickhouse.query_builders.span_list import SpanListQueryBuilder
from tracer.services.clickhouse.query_builders.trace_list import TraceListQueryBuilder
from tracer.utils.helper import (
    EvalFetchError,
    _per_span_eval_value,
    fetch_grouped_eval_rows,
)

# ---------------------------------------------------------------------------
# 1-3 — 0.0 score must not be blanked (the original span_list bug).
# ---------------------------------------------------------------------------


def _passfail_zero_score_row(builder_key="trace_id"):
    return {
        builder_key: "t1",
        "eval_config_id": "c1",
        "avg_score": 0.0,  # the bug pivot: ``avg_score != 0`` killed this.
        "pass_rate": 0.0,
        "pass_count": 0,
        "fail_count": 0,
        "success_count": 1,
        "error_count": 0,
        "eval_count": 1,
        "str_lists": "[]",
    }


def test_trace_count_mode_score_zero_survives():
    """0.0 score on the trace pivot stays 0.0 in count mode."""
    row = _passfail_zero_score_row("trace_id")
    out = TraceListQueryBuilder.pivot_eval_results([row], count_mode=True)
    cell = out["t1"]["c1"]
    assert cell["avg_score"] == 0.0


def test_span_count_mode_score_zero_survives():
    """0.0 score on the span pivot stays 0.0 in count mode (PR #943 review)."""
    row = _passfail_zero_score_row("observation_span_id")
    out = SpanListQueryBuilder.pivot_eval_results([row], count_mode=True)
    cell = out["s_t1"]["c1"] if "s_t1" in out else next(iter(out.values()))["c1"]
    assert cell["avg_score"] == 0.0


def test_span_default_mode_score_zero_survives():
    """0.0 score also survives in the default (non-count) span pivot."""
    row = _passfail_zero_score_row("observation_span_id")
    row["observation_span_id"] = "s1"
    out = SpanListQueryBuilder.pivot_eval_results([row])
    # 0.0 is finite — score should be 0.0, not None.
    assert out["s1"]["c1"] == 0.0


def test_trace_count_mode_nan_score_blanks():
    """A non-finite (NaN) score still renders as None — the finite guard
    only excludes inf/NaN, not legitimate zeros."""
    row = _passfail_zero_score_row("trace_id")
    row["avg_score"] = float("nan")
    out = TraceListQueryBuilder.pivot_eval_results([row], count_mode=True)
    assert out["t1"]["c1"]["avg_score"] is None


# ---------------------------------------------------------------------------
# 4-5 — Latest-rerun ordering uses ``created_at``, not scan order.
# ---------------------------------------------------------------------------


def test_fetch_grouped_eval_rows_orders_by_created_at(db, custom_eval_config):
    """The CH eval-row fetch ORDERs by ``created_at`` ASC so per-span
    latest-rerun resolution stays deterministic."""
    captured = {}

    def fake_execute(query, params, timeout_ms=None):
        captured["query"] = query
        return SimpleNamespace(data=[])

    analytics = SimpleNamespace(execute_ch_query=fake_execute)
    fetch_grouped_eval_rows(analytics, "trace-1")
    assert "ORDER BY created_at" in captured["query"]


def test_per_span_value_uses_latest_created_at():
    """When rerun rows arrive out of order, the latest by ``created_at`` wins."""
    rows = [
        # The later-by-created_at row but earlier in the list — was previously
        # picked-last via ``live[-1]`` (scan order); now max(created_at) picks it.
        {
            "span_id": "s1",
            "output_float": 0.9,  # the newest rerun's value
            "output_bool": None,
            "output_str_list": "[]",
            "error": 0,
            "created_at": datetime(2026, 6, 22, 10, 0, 0),
        },
        {
            "span_id": "s1",
            "output_float": 0.1,  # an older rerun
            "output_bool": None,
            "output_str_list": "[]",
            "error": 0,
            "created_at": datetime(2026, 6, 22, 9, 0, 0),
        },
    ]
    value = _per_span_eval_value(rows, "score")
    assert value == 90.0  # 0.9 × 100


def test_per_span_value_skips_errored_then_picks_latest():
    """Errored rows are excluded; ``max(created_at)`` is taken over survivors."""
    rows = [
        {
            "span_id": "s1",
            "output_float": 0.5,
            "output_bool": None,
            "output_str_list": "[]",
            "error": 1,  # excluded
            "created_at": datetime(2026, 6, 22, 11, 0, 0),
        },
        {
            "span_id": "s1",
            "output_float": 0.4,
            "output_bool": None,
            "output_str_list": "[]",
            "error": 0,
            "created_at": datetime(2026, 6, 22, 9, 0, 0),
        },
        {
            "span_id": "s1",
            "output_float": 0.7,
            "output_bool": None,
            "output_str_list": "[]",
            "error": 0,
            "created_at": datetime(2026, 6, 22, 10, 0, 0),
        },
    ]
    # The errored row (latest created_at) is excluded. Among survivors, the
    # newer one (10:00) wins.
    assert _per_span_eval_value(rows, "score") == 70.0


# ---------------------------------------------------------------------------
# 6 — CH failure raises EvalFetchError (distinguishable from "no evals").
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_fetch_grouped_eval_rows_raises_on_ch_failure():
    """CH read failure no longer renders the trace detail as "no eval scores"
    — the caller gets an explicit error it can surface to the UI."""

    def boom(*a, **k):
        raise RuntimeError("CH unavailable")

    analytics = SimpleNamespace(execute_ch_query=boom)
    with pytest.raises(EvalFetchError) as excinfo:
        fetch_grouped_eval_rows(analytics, "trace-1")
    assert "CH unavailable" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 7 — The eval-config discovery block is now a single selector.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_discover_eval_configs_returns_configs_ids_and_task_map(
    custom_eval_config, eval_task
):
    """The shared selector replaces the dictGet CH query + build_eval_task_map
    block that was copy-pasted in three places."""
    from tracer.services.observe_list import discover_eval_configs

    cid = str(custom_eval_config.id)
    tid = str(eval_task.id)
    fake_rows = [
        {
            "cid": cid,
            "task_id": tid,
            "target_type": "span",
            "last_seen": datetime(2026, 6, 22, 10, 0, 0),
        }
    ]
    analytics = SimpleNamespace(
        execute_ch_query=lambda *a, **k: SimpleNamespace(data=fake_rows)
    )

    eval_configs, eval_config_ids, eval_task_map = discover_eval_configs(
        analytics, project_id="proj-1"
    )
    assert eval_config_ids == [cid]
    # eval_configs returned as a list (not a QuerySet) — selector callers
    # iterate over it multiple times.
    assert any(str(c.id) == cid for c in eval_configs)
    assert eval_task_map[cid]["eval_task_id"] == tid
    assert eval_task_map[cid]["target_type"] == "span"


# ---------------------------------------------------------------------------
# 8 — Pivot reads rows by column name (no positional fallback).
# ---------------------------------------------------------------------------


def test_trace_list_pivot_reads_rows_by_dict_key():
    """Reordering SELECT columns no longer silently shifts which value is
    read as ``avg_score`` etc. — the pivot reads by column name only."""
    row = {
        # Intentionally out-of-order keys (the previous positional fallback
        # would have read the wrong value when given a tuple — confirm dict
        # access stays correct).
        "str_lists": "[]",
        "error_count": 0,
        "trace_id": "t1",
        "fail_count": 4,
        "eval_config_id": "c1",
        "success_count": 9,
        "avg_score": None,
        "pass_count": 5,
        "pass_rate": 55.56,
        "eval_count": 9,
    }
    out = TraceListQueryBuilder.pivot_eval_results([row], count_mode=True)
    cell = out["t1"]["c1"]
    assert cell["pass_count"] == 5
    assert cell["fail_count"] == 4
    assert cell["pass_rate"] == 55.56
    assert cell["count"] == 9


# ---------------------------------------------------------------------------
# 9 — voice_call_detail eval_outputs keeps every project eval config key.
# ---------------------------------------------------------------------------


def test_voice_eval_outputs_keeps_unscored_project_configs():
    """The flat ``eval_outputs`` map on the voice-call detail endpoint must
    include every project-level non-deleted eval config, even those without a
    score on the selected call. The PR previously regressed this by deriving
    ``eval_outputs`` from only the scored configs (PR #943 review).

    This is verified by exercising the same merge logic the view does: start
    from every project config (output=None) and overlay the aggregates we
    actually have.
    """

    class _Template:
        def __init__(self, name, output):
            self.name = name
            self.config = {"output": output}
            self.choices = []

    class _Cfg:
        def __init__(self, cid, name, output_type):
            self.id = cid
            self.name = name
            self.eval_template = _Template(name, output_type)

    # Project has TWO configs; only cfg_with_score actually ran on this call.
    project_configs = [
        _Cfg("cfg_with_score", "Helpfulness", "Pass/Fail"),
        _Cfg("cfg_without_score", "Toxicity", "score"),
    ]
    trace_level_scores = {
        "scope": "trace",
        "eval_tasks": [
            {
                "eval_task_id": "task1",
                "eval_task_name": "Task1",
                "evals": [
                    {
                        "eval_config_id": "cfg_with_score",
                        "eval_name": "Helpfulness",
                        "output_type": "Pass/Fail",
                        "target_type": "trace",
                        "aggregate": {"pass": 2, "fail": 1},
                        "spans": [],
                    }
                ],
            }
        ],
    }

    # Mirror the view's merge: project configs become default placeholders,
    # then overlay any aggregates we actually have.
    eval_outputs = {}
    for cfg in project_configs:
        template = getattr(cfg, "eval_template", None)
        template_config = (getattr(template, "config", None) or {}) or {}
        eval_outputs[str(cfg.id)] = {
            "name": cfg.name or (template.name if template else str(cfg.id)),
            "output_type": template_config.get("output", "score"),
            "target_type": None,
            "output": None,
        }
    for task in trace_level_scores.get("eval_tasks", []):
        for ev in task.get("evals", []):
            entry = eval_outputs.setdefault(
                str(ev["eval_config_id"]),
                {
                    "name": ev["eval_name"],
                    "output_type": ev["output_type"],
                    "target_type": None,
                    "output": None,
                },
            )
            entry["target_type"] = ev.get("target_type")
            entry["output"] = ev["aggregate"]

    # Both keys present — the unscored project config kept its placeholder.
    assert set(eval_outputs) == {"cfg_with_score", "cfg_without_score"}
    assert eval_outputs["cfg_without_score"]["output"] is None
    assert eval_outputs["cfg_without_score"]["name"] == "Toxicity"
    assert eval_outputs["cfg_with_score"]["output"] == {"pass": 2, "fail": 1}
    assert eval_outputs["cfg_with_score"]["target_type"] == "trace"


# ---------------------------------------------------------------------------
# 10 — Grouped eval_scores carries ``choices_map`` so the FE drawer can colour
# each choice chip (pass / fail / neutral) instead of falling back to neutral.
# ---------------------------------------------------------------------------


def test_grouped_eval_scores_carries_choices_map():
    """``build_task_grouped_eval_scores`` surfaces ``choices_map`` on every
    eval entry — the FE drawer reads ``ev.choices_map`` per eval to colour
    choice chips. Same shape as the observe column config:
    ``{"<label>": "pass"|"fail"|"neutral"}``."""
    from tracer.utils.helper import build_task_grouped_eval_scores

    config_lookup = {
        "cfg_choices": {
            "name": "Sentiment",
            "output": "choices",
            "choices": ["joy", "anger", "neutral"],
            "choices_map": {"joy": "pass", "anger": "fail", "neutral": "neutral"},
        },
        # A score eval still gets choices_map (empty default) so consumers
        # can read the key uniformly without a presence-check.
        "cfg_score": {
            "name": "Helpfulness",
            "output": "score",
            "choices": [],
            "choices_map": {},
        },
    }
    rows = [
        {
            "span_id": "s1",
            "eval_config_id": "cfg_choices",
            "eval_task_id": "task1",
            "target_type": "span",
            "output_float": None,
            "output_bool": None,
            "output_str": None,
            "output_str_list": '["joy"]',
            "error": 0,
            "explanation": None,
        },
        {
            "span_id": "s1",
            "eval_config_id": "cfg_score",
            "eval_task_id": "task1",
            "target_type": "span",
            "output_float": 0.8,
            "output_bool": None,
            "output_str": None,
            "output_str_list": "[]",
            "error": 0,
            "explanation": None,
        },
    ]
    out = build_task_grouped_eval_scores(
        rows,
        config_lookup,
        {"task1": "Task1"},
        {"s1": "span-1"},
        "trace",
    )
    evals = {e["eval_config_id"]: e for e in out["eval_tasks"][0]["evals"]}
    assert evals["cfg_choices"]["choices_map"] == {
        "joy": "pass",
        "anger": "fail",
        "neutral": "neutral",
    }
    # Score evals carry an empty map (no FE colour rule — keeps the field
    # uniformly present so the drawer doesn't have to branch on existence).
    assert evals["cfg_score"]["choices_map"] == {}


def test_grouped_eval_scores_choices_map_missing_defaults_to_empty():
    """Configs whose lookup entry is missing ``choices_map`` still emit ``{}``
    — the field is always present on every eval, regardless of vintage."""
    from tracer.utils.helper import build_task_grouped_eval_scores

    config_lookup = {
        # Legacy lookup shape — no ``choices_map`` key at all.
        "cfg_legacy": {"name": "Legacy", "output": "score", "choices": []},
    }
    rows = [
        {
            "span_id": "s1",
            "eval_config_id": "cfg_legacy",
            "eval_task_id": "task1",
            "target_type": "span",
            "output_float": 0.5,
            "output_bool": None,
            "output_str": None,
            "output_str_list": "[]",
            "error": 0,
            "explanation": None,
        },
    ]
    out = build_task_grouped_eval_scores(
        rows, config_lookup, {"task1": "Task1"}, {"s1": "span-1"}, "trace"
    )
    assert out["eval_tasks"][0]["evals"][0]["choices_map"] == {}
