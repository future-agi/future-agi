"""Tests for eval-logger table resolution and the shared eval-config-id selectors.

Covers the CH25 fix that routes eval-logger reads through ``eval_logger_source()``
and consolidates the "distinct eval-config IDs that have data" lookup into the
``AnalyticsQueryService``:

* ``eval_logger_source()`` picks the configured table + its not-deleted predicate.
* ``get_eval_config_ids_with_data_ch`` / ``get_eval_config_ids_for_traces_ch``
  generate that predicate (``is_deleted = 0`` on a ``_v2`` stack) and scope correctly.
* A ClickHouse read failure propagates instead of being masked as "no eval scores".
"""

from unittest import mock

import pytest
from django.test import override_settings


class _Result:
    """Minimal stand-in for ``QueryResult`` — selectors only read ``.data``."""

    def __init__(self, data):
        self.data = data


def _capturing_service(rows):
    """An ``AnalyticsQueryService`` whose ``execute_ch_query`` records its args.

    ``__init__`` is lazy (no CH connection), so we can construct it directly and
    shadow ``execute_ch_query`` with a recorder that returns canned rows.
    """
    from tracer.services.clickhouse.query_service import AnalyticsQueryService

    svc = AnalyticsQueryService()
    captured = {}

    def _recorder(query, params=None, timeout_ms=None, settings=None):
        captured["query"] = query
        captured["params"] = params
        captured["timeout_ms"] = timeout_ms
        return _Result(rows)

    svc.execute_ch_query = _recorder
    return svc, captured


@pytest.mark.unit
class TestEvalLoggerSource:
    """``eval_logger_source()`` resolves the table + not-deleted predicate."""

    @override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger")
    def test_legacy_table_uses_deleted_predicate(self):
        # Legacy table filters on `deleted`, not `_peerdb_is_deleted`: the v2
        # rewriter renames `_peerdb_is_deleted` → `is_deleted` (which this table
        # lacks), so `deleted` is the rewrite-safe soft-delete marker.
        from tracer.services.clickhouse.eval_logger_table import eval_logger_source

        table, not_deleted = eval_logger_source()
        assert table == "tracer_eval_logger"
        assert not_deleted == "(deleted = 0 OR deleted IS NULL)"
        assert "_peerdb_is_deleted" not in not_deleted

    @override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger_v2")
    def test_v2_table_uses_is_deleted_predicate(self):
        from tracer.services.clickhouse.eval_logger_table import eval_logger_source

        table, not_deleted = eval_logger_source()
        assert table == "tracer_eval_logger_v2"
        assert not_deleted == "is_deleted = 0"

    @override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger_v2")
    def test_alias_prefixes_v2_predicate(self):
        from tracer.services.clickhouse.eval_logger_table import eval_logger_source

        _, not_deleted = eval_logger_source("e")
        assert not_deleted == "e.is_deleted = 0"

    @override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger")
    def test_alias_prefixes_legacy_predicate(self):
        from tracer.services.clickhouse.eval_logger_table import eval_logger_source

        _, not_deleted = eval_logger_source("e")
        assert not_deleted == "(e.deleted = 0 OR e.deleted IS NULL)"


@pytest.mark.unit
class TestEvalConfigIdSelectors:
    """The two service selectors generate the resolved table + predicate + scope."""

    @override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger_v2")
    def test_project_selector_v2_predicate_and_scope(self):
        svc, captured = _capturing_service([{"config_id": "a"}, {"config_id": "b"}])
        ids = svc.get_eval_config_ids_with_data_ch("proj-1")

        assert ids == ["a", "b"]
        query = captured["query"]
        assert "tracer_eval_logger_v2" in query
        # PERF: FINAL dropped — it forced a full-table merge and was a prime
        # OOM source; DISTINCT config_id needs no row-collapsing.
        assert "FINAL" not in query
        assert "is_deleted = 0" in query
        assert "_peerdb_is_deleted" not in query
        # project scope is the spans subquery, not dictGet
        assert "project_id = %(project_id)s" in query
        assert "dictGet" not in query
        # Default 30-day window bound prunes span + eval partitions.
        assert captured["params"] == {"project_id": "proj-1", "window_days": 30}

    @override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger_v2")
    def test_project_selector_candidate_config_ids_fast_path(self):
        # The hot path: caller pre-resolves the project's configs from PG, so
        # discovery scopes by custom_eval_config_id (the eval table's leading
        # sort key) — no trace join, no spans scan.
        svc, captured = _capturing_service([{"config_id": "a"}])
        ids = svc.get_eval_config_ids_with_data_ch(
            "proj-1", candidate_config_ids=["a", "b"]
        )

        assert ids == ["a"]
        query = captured["query"]
        assert "FINAL" not in query
        assert "custom_eval_config_id IN %(config_ids)s" in query
        assert "trace_id IN" not in query
        assert "FROM spans" not in query
        assert captured["params"]["config_ids"] == ("a", "b")

    def test_project_selector_candidate_empty_short_circuits(self):
        svc, captured = _capturing_service([{"config_id": "a"}])
        # An empty candidate set means "this project has no configs" — no CH read.
        assert (
            svc.get_eval_config_ids_with_data_ch("proj-1", candidate_config_ids=[])
            == []
        )
        assert captured == {}

    @override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger")
    def test_project_selector_legacy_predicate(self):
        svc, captured = _capturing_service([])
        svc.get_eval_config_ids_with_data_ch("proj-1")

        query = captured["query"]
        assert "tracer_eval_logger" in query
        assert "FINAL" not in query
        assert "_peerdb_is_deleted = 0" in query

    def test_project_selector_forwards_timeout(self):
        svc, captured = _capturing_service([])
        svc.get_eval_config_ids_with_data_ch("proj-1", timeout_ms=30000)
        assert captured["timeout_ms"] == 30000

    @override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger_v2")
    def test_traces_selector_v2_predicate_and_scope(self):
        svc, captured = _capturing_service([{"config_id": "x"}])
        ids = svc.get_eval_config_ids_for_traces_ch(["t1", "t2"])

        assert ids == ["x"]
        query = captured["query"]
        assert "tracer_eval_logger_v2" in query
        assert "FINAL" not in query
        assert "is_deleted = 0" in query
        assert "_peerdb_is_deleted" not in query
        assert "trace_id IN %(trace_ids)s" in query
        assert captured["params"] == {"trace_ids": ["t1", "t2"]}

    def test_traces_selector_empty_short_circuits(self):
        svc, captured = _capturing_service([{"config_id": "x"}])
        ids = svc.get_eval_config_ids_for_traces_ch([])
        # No CH round-trip for an empty trace set.
        assert ids == []
        assert captured == {}


@pytest.mark.unit
class TestEvalReadFailurePropagates:
    """A CH read failure must surface, not be masked as an empty session result."""

    def test_trace_session_retrieve_propagates_ch_error(self):
        from tracer.services.clickhouse.client import CHError
        from tracer.views.trace_session import TraceSessionView

        view = TraceSessionView()

        class _FakeAnalytics:
            def execute_ch_query(
                self, query, params=None, timeout_ms=None, settings=None
            ):
                # Paginated trace list — return one trace so we reach eval discovery.
                if "root_latency_ms" in query:
                    return _Result(
                        [
                            {
                                "trace_id": "t1",
                                "input": None,
                                "output": None,
                                "root_latency_ms": 0,
                                "total_cost": 0,
                                "trace_min_start_time": None,
                                "total_tokens": 0,
                                "input_tokens": 0,
                                "output_tokens": 0,
                            }
                        ]
                    )
                # Session aggregate (and anything else) — empty is fine.
                return _Result([])

            def get_eval_config_ids_for_traces_ch(self, trace_ids, timeout_ms=3000):
                raise CHError("clickhouse unavailable")

        with mock.patch(
            "tracer.views.trace_session._resolve_session_ids_to_canonical",
            return_value={"s1": "s1"},
        ):
            with pytest.raises(CHError):
                view._retrieve_clickhouse(
                    request=mock.Mock(),
                    trace_session_id="s1",
                    project_id="p1",
                    analytics=_FakeAnalytics(),
                    query_data={"page_number": 0, "page_size": 30},
                )


@pytest.mark.unit
class TestEvalReadSelectors:
    """The non-discovery eval reads also resolve their table via eval_logger_source()."""

    @override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger_v2")
    def test_children_eval_metrics_v2_predicate_and_scope(self):
        svc, captured = _capturing_service([{"span_id": "s", "config_id": "c"}])
        rows = svc.get_children_eval_metrics_ch(["s1", "s2"])

        assert rows == [{"span_id": "s", "config_id": "c"}]
        query = captured["query"]
        assert "tracer_eval_logger_v2 FINAL" in query
        assert "is_deleted = 0" in query
        assert "_peerdb_is_deleted" not in query
        assert "observation_span_id IN %(span_ids)s" in query
        assert captured["params"] == {"span_ids": ["s1", "s2"]}

    def test_children_eval_metrics_empty_short_circuits(self):
        svc, captured = _capturing_service([{"span_id": "s"}])
        assert svc.get_children_eval_metrics_ch([]) == []
        assert captured == {}

    @override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger_v2")
    def test_eval_detail_v2_predicate_and_returns_first_row(self):
        svc, captured = _capturing_service([{"output_float": 1.0}])
        row = svc.get_eval_detail_ch("span-1", "cfg-1")

        assert row == {"output_float": 1.0}
        query = captured["query"]
        assert "tracer_eval_logger_v2 FINAL" in query
        assert "is_deleted = 0" in query
        assert "target_type IN ('span', 'trace')" in query
        assert "LIMIT 1" in query
        assert captured["params"] == {"span_id": "span-1", "config_id": "cfg-1"}

    def test_eval_detail_returns_none_when_absent(self):
        svc, _ = _capturing_service([])
        assert svc.get_eval_detail_ch("span-1", "cfg-1") is None

    @override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger_v2")
    def test_trace_eval_scores_v2_predicate_and_scope(self):
        svc, captured = _capturing_service([{"trace_id": "t", "config_id": "c"}])
        rows = svc.get_trace_eval_scores_ch(["t1"], ["c1"])

        assert rows == [{"trace_id": "t", "config_id": "c"}]
        query = captured["query"]
        assert "tracer_eval_logger_v2 FINAL" in query
        assert "is_deleted = 0" in query
        assert "_peerdb_is_deleted" not in query
        assert "trace_id IN %(trace_ids)s" in query
        assert "custom_eval_config_id IN %(config_ids)s" in query
        assert captured["params"] == {"trace_ids": ["t1"], "config_ids": ["c1"]}

    @override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger_v2")
    def test_trace_eval_scores_null_safe_output_str(self):
        """A NULL output_str (successful bool/float eval) must not be filtered out."""
        svc, captured = _capturing_service([{"trace_id": "t", "config_id": "c"}])
        svc.get_trace_eval_scores_ch(["t1"], ["c1"])
        query = captured["query"]
        assert "ifNull(output_str, '') != 'ERROR'" in query
        assert "output_str != 'ERROR'" not in query.replace(
            "ifNull(output_str, '') != 'ERROR'", ""
        )

    def test_trace_eval_scores_empty_short_circuits(self):
        svc, captured = _capturing_service([{"trace_id": "t"}])
        assert svc.get_trace_eval_scores_ch([], ["c1"]) == []
        assert svc.get_trace_eval_scores_ch(["t1"], []) == []
        assert captured == {}
