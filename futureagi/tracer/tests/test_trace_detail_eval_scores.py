"""Unit tests for the grouped trace-detail ``eval_scores``.

Covers the flat ``{scope, evals: [{aggregate, spans[]}]}`` structure the
trace-detail endpoint attaches per span-tree entry:

  * ``build_grouped_eval_scores`` — pure grouping/aggregation semantics.
  * ``attach_grouped_eval_scores`` — root gets the trace scope (all spans),
    children get their own scope.
  * ``fetch_grouped_eval_rows`` — the CH read + batched PG config lookup,
    incl. the fail-loud ``EvalFetchError`` contract.
  * ``TraceView._attach_detail_eval_scores`` — the span-tree walk in the view.

Task grouping (``eval_tasks`` nesting) is scoped out: a config that
ran under several eval tasks folds into ONE eval entry.
"""

from datetime import datetime, timedelta

import pytest

from tracer.utils.helper import (
    EvalFetchError,
    attach_grouped_eval_scores,
    build_grouped_eval_scores,
    fetch_grouped_eval_rows,
)

NOW = datetime(2026, 8, 20, 12, 0, 0)

SCORE_LOOKUP = {"c-score": {"name": "groundedness", "output": "score"}}
PASSFAIL_LOOKUP = {"c-pf": {"name": "is_polite", "output": "Pass/Fail"}}
CHOICES_LOOKUP = {
    "c-ch": {
        "name": "sentiment",
        "output": "choices",
        "choices": ["Positive", "Negative", "Neutral"],
        "choices_map": {"Positive": "pass", "Negative": "fail"},
    }
}

SPAN_NAMES = {"s1": "retrieve_docs", "s2": "generate", "s3": "rerank"}


def _row(span_id, config_id, **overrides):
    row = {
        "span_id": span_id,
        "eval_config_id": config_id,
        "target_type": "span",
        "output_float": None,
        "output_bool": None,
        "output_str": None,
        "output_str_list": [],
        "error": 0,
        "status": "completed",
        "explanation": None,
        "created_at": NOW,
    }
    row.update(overrides)
    return row


class TestBuildGroupedEvalScores:
    def test_flat_evals_no_task_nesting(self):
        result = build_grouped_eval_scores(
            [_row("s1", "c-score", output_float=0.8)],
            SCORE_LOOKUP,
            SPAN_NAMES,
            "trace",
        )
        assert result["scope"] == "trace"
        assert "eval_tasks" not in result
        assert len(result["evals"]) == 1
        assert result["evals"][0]["eval_name"] == "groundedness"

    def test_score_aggregate_is_mean_of_spans(self):
        rows = [
            _row("s1", "c-score", output_float=0.8),
            _row("s2", "c-score", output_float=0.4),
        ]
        result = build_grouped_eval_scores(rows, SCORE_LOOKUP, SPAN_NAMES, "trace")
        assert result["evals"][0]["aggregate"] == 60.0

    def test_passfail_aggregate_is_exact_counts(self):
        rows = [
            _row("s1", "c-pf", output_bool=True),
            _row("s2", "c-pf", output_bool=True),
            _row("s3", "c-pf", output_bool=False),
        ]
        result = build_grouped_eval_scores(rows, PASSFAIL_LOOKUP, SPAN_NAMES, "trace")
        assert result["evals"][0]["aggregate"] == {"pass": 2, "fail": 1}

    def test_choices_aggregate_zero_fills_declared_labels(self):
        rows = [
            _row("s1", "c-ch", output_str_list=["Positive"]),
            _row("s2", "c-ch", output_str_list='["Positive"]'),
            _row("s3", "c-ch", output_str_list=["Negative"]),
        ]
        result = build_grouped_eval_scores(rows, CHOICES_LOOKUP, SPAN_NAMES, "trace")
        assert result["evals"][0]["aggregate"] == {
            "Positive": 2,
            "Negative": 1,
            "Neutral": 0,
        }
        assert result["evals"][0]["choices_map"] == {
            "Positive": "pass",
            "Negative": "fail",
        }

    def test_per_span_raw_values(self):
        rows = [
            _row("s1", "c-score", output_float=0.8),
            _row("s1", "c-pf", output_bool=False),
            _row("s2", "c-ch", output_str_list=["Neutral"]),
        ]
        lookup = {**SCORE_LOOKUP, **PASSFAIL_LOOKUP, **CHOICES_LOOKUP}
        result = build_grouped_eval_scores(rows, lookup, SPAN_NAMES, "trace")
        by_name = {e["eval_name"]: e for e in result["evals"]}
        assert by_name["groundedness"]["spans"][0]["value"] == 80.0
        assert by_name["groundedness"]["spans"][0]["span_name"] == "retrieve_docs"
        assert by_name["is_polite"]["spans"][0]["value"] == "fail"
        assert by_name["sentiment"]["spans"][0]["value"] == ["Neutral"]

    def test_latest_rerun_wins_per_span(self):
        rows = [
            _row("s1", "c-score", output_float=0.2, created_at=NOW),
            _row(
                "s1",
                "c-score",
                output_float=0.9,
                created_at=NOW - timedelta(hours=1),
            ),
        ]
        result = build_grouped_eval_scores(rows, SCORE_LOOKUP, SPAN_NAMES, "span")
        ev = result["evals"][0]
        assert ev["spans"][0]["value"] == 20.0
        # The aggregate must reflect the SAME latest-per-span row, not the mean
        # of all reruns (which would be (20+90)/2 = 55.0).
        assert ev["aggregate"] == 20.0

    def test_score_aggregate_ignores_stale_reruns_across_spans(self):
        """Aggregate mean is over latest-per-span, matching the chips beneath."""
        rows = [
            _row("s1", "c-score", output_float=0.9, created_at=NOW),
            _row(
                "s1", "c-score", output_float=0.1, created_at=NOW - timedelta(hours=1)
            ),
            _row("s2", "c-score", output_float=0.7, created_at=NOW),
        ]
        result = build_grouped_eval_scores(rows, SCORE_LOOKUP, SPAN_NAMES, "trace")
        ev = result["evals"][0]
        # Latest per span: s1=90, s2=70 -> mean 80. Stale s1=10 excluded.
        assert ev["aggregate"] == 80.0
        assert {s["span_id"]: s["value"] for s in ev["spans"]} == {
            "s1": 90.0,
            "s2": 70.0,
        }

    def test_passfail_aggregate_counts_latest_per_span_only(self):
        """Pass/Fail counts collapse reruns/multi-task rows to latest-per-span,
        so the aggregate can never exceed the span count."""
        rows = [
            # s1: latest is a fail (an older pass rerun must not be counted)
            _row("s1", "c-pf", output_bool=False, created_at=NOW),
            _row("s1", "c-pf", output_bool=True, created_at=NOW - timedelta(hours=1)),
            # s2: single pass
            _row("s2", "c-pf", output_bool=True, created_at=NOW),
        ]
        result = build_grouped_eval_scores(rows, PASSFAIL_LOOKUP, SPAN_NAMES, "trace")
        ev = result["evals"][0]
        assert ev["aggregate"] == {"pass": 1, "fail": 1}
        # pass + fail == number of evaluated spans (2), matching the chips.
        assert ev["aggregate"]["pass"] + ev["aggregate"]["fail"] == len(ev["spans"])

    def test_configs_from_different_tasks_fold_into_one_eval(self):
        """Task grouping is scoped out: same config under two tasks = ONE
        eval entry aggregating both rows."""
        rows = [
            _row("s1", "c-pf", output_bool=True),
            _row("s2", "c-pf", output_bool=False),
        ]
        result = build_grouped_eval_scores(rows, PASSFAIL_LOOKUP, SPAN_NAMES, "trace")
        assert len(result["evals"]) == 1
        assert result["evals"][0]["aggregate"] == {"pass": 1, "fail": 1}

    def test_errored_rows_excluded_from_aggregate(self):
        rows = [
            _row("s1", "c-score", output_float=0.8),
            _row("s2", "c-score", output_float=0.1, error=1),
            _row("s3", "c-score", output_float=0.1, output_str="ERROR"),
        ]
        result = build_grouped_eval_scores(rows, SCORE_LOOKUP, SPAN_NAMES, "trace")
        ev = result["evals"][0]
        assert ev["aggregate"] == 80.0
        by_span = {s["span_id"]: s for s in ev["spans"]}
        assert by_span["s1"]["error"] is False
        assert by_span["s2"]["error"] is True
        assert by_span["s2"]["value"] is None
        assert by_span["s3"]["error"] is True

    def test_status_errored_counts_as_error(self):
        """``status='errored'`` marks an error even without the legacy flag."""
        rows = [_row("s1", "c-score", output_float=0.5, status="errored")]
        result = build_grouped_eval_scores(rows, SCORE_LOOKUP, SPAN_NAMES, "span")
        span = result["evals"][0]["spans"][0]
        assert span["error"] is True
        assert span["value"] is None

    def test_target_type_carried_per_eval(self):
        rows = [_row("s1", "c-score", output_float=0.8, target_type="trace")]
        result = build_grouped_eval_scores(rows, SCORE_LOOKUP, SPAN_NAMES, "trace")
        assert result["evals"][0]["target_type"] == "trace"

    def test_unknown_config_and_missing_span_rows_skipped(self):
        rows = [
            _row("s1", "c-unknown", output_float=0.8),
            _row("", "c-score", output_float=0.8),
            _row("s1", "", output_float=0.8),
        ]
        result = build_grouped_eval_scores(rows, SCORE_LOOKUP, SPAN_NAMES, "trace")
        assert result["evals"] == []

    def test_explanation_surfaces_on_span(self):
        rows = [_row("s1", "c-score", output_float=0.3, explanation="too vague")]
        result = build_grouped_eval_scores(rows, SCORE_LOOKUP, SPAN_NAMES, "span")
        assert result["evals"][0]["spans"][0]["explanation"] == "too vague"


class TestAttachGroupedEvalScores:
    def _targets(self):
        root = {"observation_span": {"id": "s1"}}
        child_a = {"observation_span": {"id": "s2"}}
        child_b = {"observation_span": {"id": "s3"}}
        targets = [
            ("s1", "root_span", True, root),
            ("s2", "generate", False, child_a),
            ("s3", "rerank", False, child_b),
        ]
        return targets, root, child_a, child_b

    def test_root_gets_trace_scope_children_get_own_scope(self):
        targets, root, child_a, child_b = self._targets()
        rows = [
            _row("s1", "c-pf", output_bool=True),
            _row("s2", "c-pf", output_bool=False),
        ]
        rows_by_span = {"s1": [rows[0]], "s2": [rows[1]]}

        trace_level = attach_grouped_eval_scores(
            targets, rows, rows_by_span, PASSFAIL_LOOKUP
        )

        assert root["eval_scores"] is trace_level
        assert root["eval_scores"]["scope"] == "trace"
        assert root["eval_scores"]["evals"][0]["aggregate"] == {"pass": 1, "fail": 1}
        # Root carries every span's rows; children only their own.
        assert len(root["eval_scores"]["evals"][0]["spans"]) == 2
        assert child_a["eval_scores"]["scope"] == "span"
        assert child_a["eval_scores"]["evals"][0]["spans"][0]["span_id"] == "s2"
        assert child_a["eval_scores"]["evals"][0]["aggregate"] == {"pass": 0, "fail": 1}

    def test_span_with_no_rows_is_empty_but_scoped(self):
        targets, _root, _child_a, child_b = self._targets()
        rows = [_row("s1", "c-pf", output_bool=True)]

        attach_grouped_eval_scores(targets, rows, {"s1": rows}, PASSFAIL_LOOKUP)

        assert child_b["eval_scores"] == {"scope": "span", "evals": []}

    def test_every_target_has_eval_scores(self):
        targets, *_ = self._targets()
        attach_grouped_eval_scores(targets, [], {}, PASSFAIL_LOOKUP)
        for _sid, _name, _is_root, target in targets:
            assert "eval_scores" in target


class _FakeResult:
    def __init__(self, data):
        self.data = data


class _FakeAnalytics:
    def __init__(self, data=None, error=None):
        self._data = data or []
        self._error = error
        self.queries = []

    def execute_ch_query(self, query, params, timeout_ms=None):
        self.queries.append((query, params))
        if self._error:
            raise self._error
        return _FakeResult(self._data)


def _ch_row(span_id, config_id, **overrides):
    row = {
        "span_id": span_id,
        "eval_config_id": config_id,
        "target_type": "span",
        "output_float": None,
        "output_bool": None,
        "output_str": None,
        "output_str_list": "[]",
        "error": 0,
        "status": "completed",
        "eval_explanation": "",
        "created_at": NOW,
    }
    row.update(overrides)
    return row


@pytest.mark.django_db
class TestFetchGroupedEvalRows:
    def test_fetch_builds_rows_and_config_lookup(self, custom_eval_config):
        cid = str(custom_eval_config.id)
        analytics = _FakeAnalytics(
            data=[
                _ch_row("s1", cid, output_float=0.7),
                # session-level row: no span anchor -> skipped
                _ch_row("", cid, output_float=0.9),
                # unknown config -> skipped
                _ch_row("s1", "00000000-0000-0000-0000-000000000000"),
            ]
        )

        eval_rows, rows_by_span, config_lookup = fetch_grouped_eval_rows(
            analytics, "trace-1"
        )

        assert len(eval_rows) == 1
        assert eval_rows[0]["span_id"] == "s1"
        assert rows_by_span == {"s1": eval_rows}
        assert config_lookup[cid]["name"] == custom_eval_config.name
        assert config_lookup[cid]["output"] == "score"

    def test_fetch_skips_non_terminal_rows(self, custom_eval_config):
        """pending/running/skipped rows carry no result — excluded so a
        rerun-in-flight can't blank a span's latest completed value."""
        cid = str(custom_eval_config.id)
        analytics = _FakeAnalytics(
            data=[
                _ch_row("s1", cid, output_float=0.7),
                _ch_row("s1", cid, status="running"),
                _ch_row("s1", cid, status="pending"),
                _ch_row("s1", cid, status="skipped"),
            ]
        )

        eval_rows, _rows_by_span, _lookup = fetch_grouped_eval_rows(
            analytics, "trace-1"
        )

        assert len(eval_rows) == 1
        assert eval_rows[0]["status"] == "completed"

    def test_fetch_keeps_errored_rows(self, custom_eval_config):
        cid = str(custom_eval_config.id)
        analytics = _FakeAnalytics(data=[_ch_row("s1", cid, error=1)])

        eval_rows, _rows_by_span, _lookup = fetch_grouped_eval_rows(
            analytics, "trace-1"
        )

        assert len(eval_rows) == 1
        assert eval_rows[0]["error"] == 1

    def test_fetch_raises_eval_fetch_error_on_ch_failure(self):
        analytics = _FakeAnalytics(error=RuntimeError("CH down"))

        with pytest.raises(EvalFetchError):
            fetch_grouped_eval_rows(analytics, "trace-1")

    def test_fetch_drops_soft_deleted_config(self, custom_eval_config):
        custom_eval_config.deleted = True
        custom_eval_config.save(update_fields=["deleted"])
        cid = str(custom_eval_config.id)
        analytics = _FakeAnalytics(data=[_ch_row("s1", cid, output_float=0.7)])

        eval_rows, rows_by_span, config_lookup = fetch_grouped_eval_rows(
            analytics, "trace-1"
        )

        assert eval_rows == []
        assert config_lookup == {}


class TestViewAttachWalk:
    """`TraceView._attach_detail_eval_scores` walks the assembled span tree —
    root entries get the trace scope, nested children their own scope —
    independent of which detail handler (v1 PG / v2 CH) built the tree."""

    def _detail(self):
        child = {"observation_span": {"id": "s2", "name": "generate"}, "children": []}
        root = {
            "observation_span": {"id": "s1", "name": "root"},
            "children": [child],
        }
        return {"observation_spans": [root]}, root, child

    def test_walk_attaches_root_and_children(self, monkeypatch):
        from tracer.views import trace as trace_views

        detail, root, child = self._detail()
        rows = [
            _row("s1", "c-pf", output_bool=True),
            _row("s2", "c-pf", output_bool=False),
        ]
        monkeypatch.setattr(
            trace_views,
            "fetch_grouped_eval_rows",
            lambda analytics, trace_id: (
                rows,
                {"s1": [rows[0]], "s2": [rows[1]]},
                PASSFAIL_LOOKUP,
            ),
        )

        trace_views.TraceView._attach_detail_eval_scores(detail, "trace-1", None)

        assert root["eval_scores"]["scope"] == "trace"
        assert len(root["eval_scores"]["evals"][0]["spans"]) == 2
        assert child["eval_scores"]["scope"] == "span"
        assert child["eval_scores"]["evals"][0]["spans"][0]["span_id"] == "s2"

    def test_walk_no_spans_skips_fetch(self, monkeypatch):
        from tracer.views import trace as trace_views

        called = []
        monkeypatch.setattr(
            trace_views,
            "fetch_grouped_eval_rows",
            lambda *a, **k: called.append(True),
        )

        trace_views.TraceView._attach_detail_eval_scores(
            {"observation_spans": []}, "trace-1", None
        )

        assert called == []
