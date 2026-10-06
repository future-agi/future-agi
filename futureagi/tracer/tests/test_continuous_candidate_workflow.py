"""Dense continuous windows are fully proved with bounded workflow pages."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from clickhouse_driver import Client

from tracer.models.eval_task import RowType, RunType
from tracer.selectors.eval_tasks import continuous_candidates as candidates
from tracer.selectors.eval_tasks import row_resolver
from tracer.services.eval_tasks import reconciler

pytestmark = pytest.mark.unit
START = datetime(2026, 9, 15, tzinfo=UTC)
END = START + timedelta(minutes=40)
PROJECT_ID = "00000000-0000-4000-8000-000000000001"


def _budget(row_type=RowType.TRACES):
    return candidates.ContinuousWorkflowReadBudget(
        row_type=row_type, deadline_seconds=120
    )


class PagedAnalytics:
    def __init__(self, rows, keys, *, fail_on=None, repeat=False):
        self.rows = sorted(rows, key=lambda row: tuple(row[key] for key in keys))
        self.keys = keys
        self.fail_on = fail_on
        self.repeat = repeat
        self.calls = []

    def execute_ch_query(self, query, params, **kwargs):
        self.calls.append((query, params))
        if len(self.calls) == self.fail_on:
            raise TimeoutError("late page unavailable")
        after = params.get("candidate_after")
        if after is not None and len(self.keys) == 1:
            # Single-column keys must bind a scalar, not a one-element tuple.
            assert not isinstance(after, tuple)
            after = (after,)
        rows = self.rows
        if after is not None and not self.repeat:
            rows = [row for row in rows if tuple(row[key] for key in self.keys) > after]
        return SimpleNamespace(data=rows[: params["candidate_limit"]])


@pytest.mark.parametrize("count", [2_000, 2_001])
@pytest.mark.parametrize("keys", [("id",), ("trace_id", "id", "start_us")])
def test_keyset_pages_exhaust_exact_and_partial_final_pages(count, keys):
    rows = [
        {"trace_id": "one-trace", "id": f"span-{index:05d}", "start_us": 42}
        for index in range(count)
    ]
    analytics = PagedAnalytics(rows, keys)
    budget = _budget()
    result = candidates._read_candidate_rows(
        analytics,
        "SELECT DISTINCT trace_id, id, start_us FROM fixture",
        {},
        budget,
        keys=keys,
    )
    assert result == rows
    assert budget.attempts == len(analytics.calls) == 3
    assert all(params["candidate_limit"] == 1_000 for _, params in analytics.calls)
    client = Client("localhost")
    for query, params in analytics.calls:
        sql = client.substitute_params(query, params, client.connection.context)
        assert "OFFSET" not in sql
        if keys == ("id",) and "candidate_after" in params:
            assert "WHERE id > 'span-" in sql


@pytest.mark.parametrize(
    "failure",
    ["late_page", "query_budget", "result_budget", "deadline", "nonadvancing"],
)
def test_workflow_page_failure_never_returns_a_partial_result(monkeypatch, failure):
    rows = [{"id": f"row-{index:05d}"} for index in range(2_001)]
    analytics = PagedAnalytics(
        rows,
        ("id",),
        fail_on=2 if failure == "late_page" else None,
        repeat=failure == "nonadvancing",
    )
    budget = _budget()
    if failure == "query_budget":
        monkeypatch.setattr(candidates, "_WORKFLOW_MAX_QUERY_ATTEMPTS", 2)
    elif failure == "result_budget":
        monkeypatch.setattr(candidates, "_WORKFLOW_MAX_RESULT_BYTES", 1_000)
    elif failure == "deadline":
        budget.deadline = candidates.time.monotonic() - 1
    with pytest.raises(candidates.ContinuousCandidateReadError) as error:
        candidates._read_candidate_rows(
            analytics, "SELECT DISTINCT id FROM fixture", {}, budget, keys=("id",)
        )
    assert type(error.value) is candidates.ContinuousCandidateReadError


def _task(*, full_rescan=False):
    return SimpleNamespace(
        id="00000000-0000-4000-8000-000000000002",
        project_id=PROJECT_ID,
        row_type=RowType.TRACES,
        run_type=RunType.CONTINUOUS,
        start_time=START,
        created_at=START,
        continuous_cursor=None if full_rescan else START,
        sampling_rate=100,
        spans_limit=10,
        filters={
            "filters": [
                {
                    "column_id": "gen_ai.release-tag",
                    "filter_config": {
                        "col_type": "SPAN_ATTRIBUTE",
                        "filter_type": "text",
                        "filter_op": "in",
                        "filter_value": ["selected-release"],
                    },
                }
            ]
        },
    )


class DenseTraceAnalytics:
    """Synthetic results with real discovery SQL and trace filter builders."""

    def __init__(self, trace_count, *, matches=True, fail_classifier=None):
        self.ids = [f"trace-{index:05d}" for index in range(trace_count)]
        self.matches = (
            set(self.ids[:: max(1, trace_count // 5)][:5]) if matches else set()
        )
        self.physical_rows = [
            {
                "trace_id": self.ids[index % trace_count],
                "id": f"span-{index:05d}",
                "start_us": 42,
            }
            for index in range(max(10_001, trace_count))
        ]
        self.fast_reads = 0
        self.page_reads = 0
        self.classifier_reads = 0
        self.fail_classifier = fail_classifier

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        if "arrival_floor_ns" in params:
            assert params["arrival_floor_ns"] == candidates._epoch_nanoseconds(START)
            if "AS candidate_page" not in query:
                self.fast_reads += 1
                return SimpleNamespace(
                    data=self.physical_rows[: params["candidate_limit"]]
                )
            self.page_reads += 1
            # Fallback restarts the ORIGINAL window, not the smallest failed slice.
            assert params["arrival_ceiling_ns"] == candidates._epoch_nanoseconds(END)
            assert "'' AS id" in query
            rows = [
                {"trace_id": trace_id, "id": "", "session_id": ""}
                for trace_id in self.ids
            ]
            after = params.get("candidate_after")
            if after is not None:
                rows = [row for row in rows if (row["trace_id"], "", "") > after]
            return SimpleNamespace(data=rows[: params["candidate_limit"]])
        self.classifier_reads += 1
        assert timeout_ms == 3_000
        assert settings["max_execution_time"] == 3
        assert settings["max_threads"] == 1
        if self.classifier_reads == self.fail_classifier:
            raise TimeoutError("late classifier unavailable")
        ids = params["candidate_trace_ids"]
        assert len(ids) <= 10
        return SimpleNamespace(
            data=[
                {
                    "project_id": PROJECT_ID,
                    "trace_id": trace_id,
                    "root_span_id": "root-" + trace_id,
                    "start_time": START,
                    "filter_witness_0": ("child-" + trace_id, START),
                }
                for trace_id in ids
                if trace_id in self.matches
            ]
        )


def _install_analytics(monkeypatch, analytics):
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.query_service.V2AnalyticsQueryService",
        lambda: analytics,
    )


@pytest.mark.parametrize("full_rescan", [False, True])
@pytest.mark.parametrize("trace_count", [1, 5, 10_005])
def test_dense_continuous_window_proves_complete_sparse_matches(
    monkeypatch, full_rescan, trace_count
):
    analytics = DenseTraceAnalytics(trace_count)
    _install_analytics(monkeypatch, analytics)
    task = _task(full_rescan=full_rescan)
    result = row_resolver._resolve_continuous_rows(task, ceiling=END, sampling_rate=100)
    assert set(result.candidate_ids) == set(analytics.ids)
    assert set(result.matched_ids) == analytics.matches
    assert {w.trace_id for w in result.trace_filter_witnesses} == analytics.matches
    assert result.full_state is full_rescan
    assert result.covered_through == END
    assert analytics.fast_reads == (1 if full_rescan else 4)
    assert analytics.classifier_reads == (trace_count + 9) // 10
    assert task.continuous_cursor == (None if full_rescan else START)


def test_dense_window_with_zero_matches_still_proves_all_changed_candidates(
    monkeypatch,
):
    analytics = DenseTraceAnalytics(10_005, matches=False)
    _install_analytics(monkeypatch, analytics)
    result = row_resolver._resolve_continuous_rows(
        _task(), ceiling=END, sampling_rate=100
    )
    assert len(result.candidate_ids) == 10_005
    assert result.matched_ids == ()
    assert result.covered_through == END


def test_fast_query_cap_escalates_to_complete_workflow_read(monkeypatch):
    monkeypatch.setattr(candidates, "_MAX_QUERY_ATTEMPTS", 0)
    analytics = DenseTraceAnalytics(5)
    _install_analytics(monkeypatch, analytics)
    result = row_resolver._resolve_continuous_rows(
        _task(), ceiling=END, sampling_rate=100
    )
    assert set(result.matched_ids) == analytics.matches
    assert analytics.fast_reads == 0
    assert analytics.page_reads == analytics.classifier_reads == 1


@pytest.mark.parametrize("failure", ["late_classifier", "shared_query_budget"])
def test_failed_dense_reconcile_never_writes_or_advances_cursor(monkeypatch, failure):
    analytics = DenseTraceAnalytics(
        10_005, fail_classifier=2 if failure == "late_classifier" else None
    )
    _install_analytics(monkeypatch, analytics)
    if failure == "shared_query_budget":
        # Eleven discovery pages leave room for only ONE classifier.
        monkeypatch.setattr(candidates, "_WORKFLOW_MAX_QUERY_ATTEMPTS", 12)
    writes = []
    monkeypatch.setattr(reconciler, "_apply_resolved", lambda *a, **k: writes.append(k))
    monkeypatch.setattr(reconciler.timezone, "now", lambda: END)
    task = _task()
    with pytest.raises(row_resolver.EvalTaskReadBudgetExceeded) as error:
        reconciler.reconcile(task)
    assert type(error.value) is row_resolver.EvalTaskReadBudgetExceeded
    assert writes == []
    assert task.continuous_cursor == START
    assert analytics.classifier_reads == (2 if failure == "late_classifier" else 1)
