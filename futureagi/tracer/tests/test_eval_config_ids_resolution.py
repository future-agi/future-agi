"""Tests for eval-logger table resolution and the shared eval-config-id selectors.

Covers the CH25 fix that routes eval-logger reads through ``eval_logger_source()``
and consolidates the "distinct eval-config IDs that have data" lookup into the
``AnalyticsQueryService``:

* ``eval_logger_source()`` picks the configured table + its not-deleted predicate.
* ``get_eval_config_ids_with_data_ch`` / ``get_eval_config_ids_for_traces_ch``
  generate that predicate (``is_deleted = 0`` on a ``_v2`` stack) and scope correctly.
* A ClickHouse read failure propagates instead of being masked as "no eval scores".
"""

import re
from pathlib import Path
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
        captured["settings"] = settings
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
        assert "(deleted = 0 OR deleted IS NULL)" in query
        # Project selector uses the rewrite-safe `deleted` marker only — the CDC
        # `_peerdb_is_deleted` guard is no longer injected here.
        assert "_peerdb_is_deleted" not in query

    def test_project_selector_forwards_timeout(self):
        svc, captured = _capturing_service([])
        svc.get_eval_config_ids_with_data_ch("proj-1", timeout_ms=30000)
        assert captured["timeout_ms"] == 30000

    @override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger_v2")
    def test_traces_selector_v2_predicate_and_scope(self):
        svc, captured = _capturing_service([{"config_id": "x"}])
        ids = svc.get_eval_config_ids_for_traces_ch(
            ["t1", "t2"], ["project-config-1", "project-config-2"]
        )

        assert ids == ["x"]
        query = captured["query"]
        assert "tracer_eval_logger_v2" in query
        assert "FINAL" not in query
        assert "is_deleted = 0" in query
        assert "_peerdb_is_deleted" not in query
        assert "trace_id IN %(trace_ids)s" in query
        assert "custom_eval_config_id IN %(candidate_config_ids)s" in query
        assert captured["params"] == {
            "trace_ids": ["t1", "t2"],
            "candidate_config_ids": ("project-config-1", "project-config-2"),
        }

    @pytest.mark.parametrize(
        ("trace_ids", "candidate_config_ids"),
        (([], ["project-config-1"]), (["t1"], [])),
        ids=("no-traces", "no-project-configs"),
    )
    def test_traces_selector_empty_scope_short_circuits(
        self, trace_ids, candidate_config_ids
    ):
        svc, captured = _capturing_service([{"config_id": "x"}])
        ids = svc.get_eval_config_ids_for_traces_ch(trace_ids, candidate_config_ids)
        # No CH round-trip unless both tenant dimensions are non-empty.
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

            def get_eval_config_ids_for_traces_ch(
                self,
                trace_ids,
                candidate_config_ids,
                timeout_ms=3000,
            ):
                assert trace_ids == ["t1"]
                assert candidate_config_ids == ["project-config-1"]
                raise CHError("clickhouse unavailable")

        candidate_qs = mock.Mock()
        candidate_qs.values_list.return_value = ["project-config-1"]
        config_manager = mock.Mock()
        config_manager.filter.return_value = candidate_qs

        with (
            mock.patch(
                "tracer.views.trace_session._resolve_session_ids_to_canonical",
                return_value={"s1": "s1"},
            ),
            mock.patch(
                "tracer.views.trace_session.CustomEvalConfig.objects",
                config_manager,
            ),
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
def test_session_detail_same_trace_across_projects_hydrates_only_owned_eval():
    """A shared customer trace ID cannot import another project's eval config."""
    from tracer.views.trace_session import TraceSessionView

    project_id = "00000000-0000-4000-8000-000000000001"
    trace_id = "customer-supplied-shared-trace"
    own_config_id = "00000000-0000-4000-8000-000000000002"
    foreign_config_id = "00000000-0000-4000-8000-000000000003"

    own_config = mock.Mock()
    own_config.id = own_config_id
    own_config.name = "Owned eval"
    own_config.model = "owned-model"
    own_config.eval_template.output_type_normalized = "score"

    candidate_qs = mock.Mock()
    candidate_qs.values_list.return_value = [own_config_id]
    metadata_qs = mock.Mock()
    metadata_qs.select_related.return_value = [own_config]
    config_manager = mock.Mock()

    def scoped_configs(**filters):
        assert filters["project_id"] == project_id
        assert filters["deleted"] is False
        if "id__in" in filters:
            assert filters["id__in"] == [own_config_id]
            return metadata_qs
        return candidate_qs

    config_manager.filter.side_effect = scoped_configs

    class _FakeAnalytics:
        discovery_args = None
        score_args = None

        def execute_ch_query(self, query, params=None, timeout_ms=None, settings=None):
            if "count(DISTINCT trace_id)" in query:
                return _Result(
                    [
                        {
                            "session_start": None,
                            "session_end": None,
                            "total_cost": 0,
                            "total_tokens": 0,
                            "total_traces": 1,
                            "end_user_id": "",
                        }
                    ]
                )
            if "GROUP BY trace_id" in query:
                return _Result(
                    [
                        {
                            "trace_id": trace_id,
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
            raise AssertionError(f"unexpected ClickHouse query: {query}")

        def get_eval_config_ids_for_traces_ch(
            self,
            trace_ids,
            candidate_config_ids,
            timeout_ms=3000,
        ):
            self.discovery_args = (trace_ids, candidate_config_ids)
            # CH contains both projects' rows under the same trace ID, but the
            # project-owned candidate predicate makes only this ID discoverable.
            return [own_config_id]

        def get_trace_eval_scores_ch(self, trace_ids, config_ids, timeout_ms=5000):
            self.score_args = (trace_ids, config_ids)
            return [
                {
                    "trace_id": trace_id,
                    "config_id": own_config_id,
                    "float_count": 1,
                    "float_score": 90.0,
                },
                # Defense in depth: even a malformed service response cannot
                # create a metadata-backed foreign eval column.
                {
                    "trace_id": trace_id,
                    "config_id": foreign_config_id,
                    "float_count": 1,
                    "float_score": 10.0,
                },
            ]

    analytics = _FakeAnalytics()
    with (
        mock.patch(
            "tracer.views.trace_session._resolve_session_ids_to_canonical",
            return_value={"session-1": "session-1"},
        ),
        mock.patch(
            "tracer.views.trace_session._expand_session_group",
            return_value=("session-1",),
        ),
        mock.patch(
            "tracer.views.trace_session.CustomEvalConfig.objects",
            config_manager,
        ),
        mock.patch(
            "tracer.views.trace_session.get_session_navigation",
            return_value=(None, None),
        ),
    ):
        response = TraceSessionView()._retrieve_clickhouse(
            request=mock.Mock(),
            trace_session_id="session-1",
            project_id=project_id,
            analytics=analytics,
            query_data={"page_number": 0, "page_size": 25},
        )

    assert response.status_code == 200
    assert analytics.discovery_args == ([trace_id], [own_config_id])
    assert analytics.score_args == ([trace_id], [own_config_id])
    metrics = response.data["result"]["response"][0]["evals_metrics"]
    assert set(metrics) == {own_config_id}
    assert metrics[own_config_id]["name"] == "Owned eval"
    assert foreign_config_id not in metrics


@pytest.mark.unit
class TestEvalReadSelectors:
    """The non-discovery eval reads also resolve their table via eval_logger_source()."""

    @override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger_v2")
    def test_children_eval_metrics_v2_predicate_and_scope(self):
        svc, captured = _capturing_service([{"span_id": "s", "config_id": "c"}])
        rows = svc.get_children_eval_metrics_ch(["s1", "s2"])

        assert rows == [{"span_id": "s", "config_id": "c"}]
        query = captured["query"]
        assert "tracer_eval_logger_v2 FINAL" not in query
        assert "FROM tracer_eval_logger_v2 AS eval_scan" in query
        assert "ORDER BY eval_scan._version DESC" in query
        assert "LIMIT 1 BY eval_scan.id" in query
        assert "latest_eval.is_deleted = 0" in query
        assert "_peerdb_is_deleted" not in query
        assert "observation_span_id IN %(span_ids)s" in query
        assert captured["params"] == {"span_ids": ["s1", "s2"]}

    def test_children_eval_metrics_empty_short_circuits(self):
        svc, captured = _capturing_service([{"span_id": "s"}])
        assert svc.get_children_eval_metrics_ch([]) == []
        assert captured == {}

    @override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger_v2")
    def test_eval_detail_v2_predicate_and_returns_first_row(self):
        from tracer.services.clickhouse.query_service import AnalyticsQueryService

        svc = AnalyticsQueryService()
        calls = []

        def recorder(query, params=None, timeout_ms=None, settings=None):
            calls.append((query, params, timeout_ms, settings))
            if "FROM spans" in query:
                return _Result([{"trace_id": "00000000-0000-0000-0000-000000000001"}])
            return _Result([{"output_float": 1.0}])

        svc.execute_ch_query = recorder
        row = svc.get_eval_detail_ch("span-1", "cfg-1", project_id="project-1")

        assert row == {"output_float": 1.0}
        assert len(calls) == 2
        anchor_query, anchor_params, *_ = calls[0]
        assert "FROM spans" in anchor_query
        assert "project_id = toUUID(%(project_id)s)" in anchor_query
        assert "HAVING argMax(is_deleted, _version) = 0" in anchor_query
        assert "LIMIT 2" in anchor_query
        assert anchor_params == {"project_id": "project-1", "span_id": "span-1"}

        query, params, *_ = calls[1]
        assert "tracer_eval_logger_v2 FINAL" not in query
        assert "FROM tracer_eval_logger_v2 AS eval_scan" in query
        assert "ORDER BY eval_scan._version DESC" in query
        assert "LIMIT 1 BY eval_scan.id" in query
        assert "latest_eval.is_deleted = 0" in query
        assert "eval_scan.trace_id = toUUID(%(trace_id)s)" in query
        assert "target_type IN ('span', 'trace')" in query
        assert "LIMIT 1" in query
        assert params == {
            "span_id": "span-1",
            "config_id": "cfg-1",
            "trace_id": "00000000-0000-0000-0000-000000000001",
        }

    def test_eval_detail_returns_none_when_absent(self):
        svc, _ = _capturing_service([])
        assert svc.get_eval_detail_ch("span-1", "cfg-1", project_id="project-1") is None

    def test_eval_detail_fails_closed_on_same_project_span_id_collision(self):
        svc, captured = _capturing_service(
            [
                {"trace_id": "00000000-0000-0000-0000-000000000001"},
                {"trace_id": "00000000-0000-0000-0000-000000000002"},
            ]
        )

        assert svc.get_eval_detail_ch("span-1", "cfg-1", project_id="project-1") is None
        assert "FROM spans" in captured["query"]

    @override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger")
    def test_v2_eval_detail_uses_configured_table_on_ch25_connection(self):
        from tracer.services.clickhouse.query_service import AnalyticsQueryService
        from tracer.services.clickhouse.v2.query_service import (
            V2AnalyticsQueryService,
        )

        service = object.__new__(V2AnalyticsQueryService)
        with mock.patch.object(
            AnalyticsQueryService,
            "get_eval_detail_ch",
            return_value={"output_bool": True},
        ) as base_read:
            row = service.get_eval_detail_ch(
                "span-1",
                "cfg-1",
                project_id="project-1",
            )

        assert row == {"output_bool": True}
        base_read.assert_called_once_with(
            "span-1",
            "cfg-1",
            project_id="project-1",
            timeout_ms=5000,
            eval_logger_table="tracer_eval_logger",
        )

    @override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger_v2")
    def test_trace_eval_scores_v2_predicate_and_scope(self):
        svc, captured = _capturing_service([{"trace_id": "t", "config_id": "c"}])
        rows = svc.get_trace_eval_scores_ch(["t1"], ["c1"])

        assert rows == [{"trace_id": "t", "config_id": "c"}]
        query = captured["query"]
        assert "tracer_eval_logger_v2 FINAL" not in query
        assert "FROM tracer_eval_logger_v2 AS eval_scan" in query
        assert "ORDER BY eval_scan._version DESC" in query
        assert "LIMIT 1 BY eval_scan.id" in query
        assert "latest_eval.is_deleted = 0" in query
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


_V2_EVAL_METHOD_CALLS = (
    (
        "get_eval_config_ids_with_data_ch",
        ("project-1",),
        {"candidate_config_ids": ["00000000-0000-0000-0000-000000000001"]},
    ),
    (
        "get_eval_config_ids_for_candidates_ch",
        (["00000000-0000-0000-0000-000000000001"],),
        {},
    ),
    (
        "get_eval_config_ids_for_traces_ch",
        (
            ["00000000-0000-0000-0000-000000000002"],
            ["00000000-0000-0000-0000-000000000001"],
        ),
        {},
    ),
    ("get_children_eval_metrics_ch", (["span-1"],), {}),
    (
        "get_eval_detail_ch",
        ("span-1", "00000000-0000-0000-0000-000000000001"),
        {"project_id": "00000000-0000-0000-0000-000000000003"},
    ),
    (
        "get_trace_eval_scores_ch",
        (
            ["00000000-0000-0000-0000-000000000002"],
            ["00000000-0000-0000-0000-000000000001"],
        ),
        {},
    ),
)


def _invoke_v2_eval_method(method_name, args, kwargs, *, failure=None):
    """Invoke one inherited v2 eval method and retain every generated query."""
    from tracer.services.clickhouse.v2.query_service import V2AnalyticsQueryService

    service = object.__new__(V2AnalyticsQueryService)
    calls = []

    def recorder(query, params=None, timeout_ms=None, settings=None):
        calls.append((query, params, timeout_ms, settings))
        if failure is not None:
            raise failure
        # Eval detail performs a tenant anchor read before its eval-table read.
        if "FROM spans" in query and "eval_logger" not in query:
            return _Result([{"trace_id": "00000000-0000-0000-0000-000000000002"}])
        return _Result([])

    service.execute_ch_query = recorder
    result = getattr(service, method_name)(*args, **kwargs)
    return result, calls


@pytest.mark.unit
class TestV2InheritedEvalReads:
    """V2 service keeps its CH25 connection and honors eval-table topology."""

    def test_v2_overrides_every_public_eval_read_on_base_service(self):
        from tracer.services.clickhouse.query_service import AnalyticsQueryService
        from tracer.services.clickhouse.v2.query_service import (
            V2AnalyticsQueryService,
        )

        expected = {case[0] for case in _V2_EVAL_METHOD_CALLS}
        base_eval_reads = {
            name
            for name, member in vars(AnalyticsQueryService).items()
            if name.startswith("get_") and "eval" in name and callable(member)
        }

        assert base_eval_reads == expected
        assert expected <= vars(V2AnalyticsQueryService).keys()

    @pytest.mark.parametrize("configured_table", ["legacy", "unset"])
    @pytest.mark.parametrize(
        ("method_name", "args", "kwargs"),
        _V2_EVAL_METHOD_CALLS,
        ids=[case[0] for case in _V2_EVAL_METHOD_CALLS],
    )
    def test_v2_read_uses_default_authoritative_eval_table(
        self,
        settings,
        configured_table,
        method_name,
        args,
        kwargs,
    ):
        if configured_table == "legacy":
            settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger"
        else:
            del settings.CH25_EVAL_LOGGER_TABLE

        _, calls = _invoke_v2_eval_method(method_name, args, kwargs)
        eval_queries = [query for query, *_ in calls if "eval_logger" in query]

        assert eval_queries, f"{method_name} did not execute an eval-table query"
        for query in eval_queries:
            assert re.search(r"FROM\s+tracer_eval_logger(?:\s|$)", query)
            assert "tracer_eval_logger_v2" not in query
            assert "eval_scan.config_hash" not in query
            assert "eval_scan.attempts" not in query

        if method_name in {
            "get_children_eval_metrics_ch",
            "get_trace_eval_scores_ch",
        }:
            query = eval_queries[-1]
            assert "eval_scan.status" in query
            assert "eval_scan.skipped_reason" in query
            assert "eval_scan._peerdb_version" in query
            assert "latest_eval._peerdb_is_deleted = 0" in query

    @pytest.mark.parametrize(
        ("method_name", "args", "kwargs"),
        _V2_EVAL_METHOD_CALLS,
        ids=[case[0] for case in _V2_EVAL_METHOD_CALLS],
    )
    def test_v2_read_propagates_clickhouse_failure(
        self,
        method_name,
        args,
        kwargs,
    ):
        failure = RuntimeError("private ClickHouse eval read failure")

        with pytest.raises(RuntimeError) as raised:
            _invoke_v2_eval_method(
                method_name,
                args,
                kwargs,
                failure=failure,
            )

        assert raised.value is failure

    def test_checked_in_v2_schema_has_no_optional_lifecycle_columns(self):
        schema_path = (
            Path(__file__).parents[1]
            / "services"
            / "clickhouse"
            / "v2"
            / "schema"
            / "011_eval_logger_v2.sql"
        )
        ddl = schema_path.read_text()
        create_body = ddl.split("CREATE TABLE IF NOT EXISTS tracer_eval_logger_v2", 1)[
            1
        ].split("ENGINE =", 1)[0]

        for column in ("status", "skipped_reason", "config_hash", "attempts"):
            assert re.search(rf"(?m)^\s*{column}\s+", create_body) is None


@pytest.mark.unit
class TestWindowDaysCovering:
    """``BaseQueryBuilder.window_days_covering`` sizes the eval-discovery
    look-back to the *requested* time window rather than a fixed 30 days, so a
    config with data anywhere in the viewed range keeps its column."""

    @staticmethod
    def _wd(filters):
        from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder

        return BaseQueryBuilder.window_days_covering(filters)

    def _greater_than(self, days_ago):
        from datetime import datetime, timedelta

        start = (datetime.utcnow() - timedelta(days=days_ago)).isoformat()
        return [
            {
                "column_id": "start_time",
                "filter_config": {
                    "filter_op": "greater_than",
                    "filter_value": start,
                },
            }
        ]

    def test_no_time_filter_defaults_to_about_30_days(self):
        # parse_time_range defaults the start to now-30d, so discovery stays ~30d
        # and the default (unfiltered) view is unchanged from the fixed bound.
        assert 30 <= self._wd([]) <= 31

    def test_explicit_start_extends_window_to_cover_it(self):
        # A 6-month view must look back ~180 days — the whole point of the fix.
        assert 180 <= self._wd(self._greater_than(180)) <= 181

    def test_between_covers_to_range_start_not_range_length(self):
        # [90d ago, 1d ago]: N must reach the *start* (~90d), not span the 89-day
        # length — anchored at now(), a shorter N would miss the range entirely.
        from datetime import datetime, timedelta

        now = datetime.utcnow()
        filters = [
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_op": "between",
                    "filter_value": [
                        (now - timedelta(days=90)).isoformat(),
                        (now - timedelta(days=1)).isoformat(),
                    ],
                },
            }
        ]
        assert 90 <= self._wd(filters) <= 91

    def test_sub_day_window_floors_to_one(self):
        # A last-2-hours view rounds up to a 1-day floor (never 0 / negative).
        from datetime import datetime, timedelta

        start = (datetime.utcnow() - timedelta(hours=2)).isoformat()
        filters = [
            {
                "column_id": "start_time",
                "filter_config": {
                    "filter_op": "greater_than",
                    "filter_value": start,
                },
            }
        ]
        assert self._wd(filters) == 1
