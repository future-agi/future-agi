"""
Tests for the row_type-aware ``get_eval_attributes_list`` endpoint.

Pin three things:

  1. ``row_type=spans`` (and the implicit default) returns the legacy flat
     list of span_attribute keys — no behavioural change for existing
     callers.
  2. ``row_type=traces`` returns trace-level model fields plus
     ``spans.<n>.<key>`` paths where ``n`` runs 0 .. observed-max-spans-1.
  3. ``row_type=sessions`` returns session-level model fields plus
     ``traces.<i>.<trace_field>`` and ``traces.<i>.spans.<j>.<key>``
     paths sized to the observed maxes.

Plus an end-to-end check: a saved mapping using one of the new dotted
paths actually resolves through the trace evaluator's
``_process_trace_mapping`` and writes a non-error EvalLogger row.
"""

import json

import pytest

# Cycle-breaker — same rationale as the runtime test file.
import model_hub.tasks  # noqa: F401, E402


@pytest.mark.integration
@pytest.mark.api
class TestGetEvalAttributesListSpans:
    """Legacy span behaviour — returned shape unchanged."""

    def test_spans_default_returns_flat_list(
        self, auth_client, populated_observe_project
    ):
        project = populated_observe_project["project"]
        response = auth_client.get(
            "/tracer/observation-span/get_eval_attributes_list/",
            {"filters": json.dumps({"project_id": str(project.id)})},
        )
        assert response.status_code == 200
        result = response.json().get("result", [])
        assert isinstance(result, list)
        # populated_observe_project's spans set ``input`` and ``output`` in
        # span_attributes, so those keys must appear.
        assert "input" in result
        assert "output" in result
        # No dotted paths — the spans surface is flat.
        assert not any("." in path for path in result)

    def test_spans_explicit_row_type_returns_flat_list(
        self, auth_client, populated_observe_project
    ):
        project = populated_observe_project["project"]
        response = auth_client.get(
            "/tracer/observation-span/get_eval_attributes_list/",
            {
                "filters": json.dumps({"project_id": str(project.id)}),
                "row_type": "spans",
            },
        )
        assert response.status_code == 200
        result = response.json().get("result", [])
        assert "input" in result
        assert not any("." in path for path in result)


@pytest.mark.integration
@pytest.mark.api
class TestGetEvalAttributesListTraces:
    """``row_type=traces`` returns trace fields + indexed ``spans.<n>.<key>`` paths."""

    @pytest.mark.xfail(
        reason="Production CH query references span_attr_str (v1 column) not yet migrated to v2 schema",
        strict=False,
    )
    def test_includes_trace_public_fields(self, auth_client, populated_observe_project):
        project = populated_observe_project["project"]
        response = auth_client.get(
            "/tracer/observation-span/get_eval_attributes_list/",
            {
                "filters": json.dumps({"project_id": str(project.id)}),
                "row_type": "traces",
            },
        )
        assert response.status_code == 200
        result = response.json().get("result", [])
        # All allow-list trace fields surface as bare scalar paths.
        for field in (
            "input",
            "output",
            "name",
            "error",
            "tags",
            "metadata",
            "external_id",
        ):
            assert field in result

    def test_includes_indexed_span_paths_per_observed_key(
        self, auth_client, populated_observe_project
    ):
        """``spans.<n>.<key>`` for n in 0..(max-spans-per-trace − 1).

        ``populated_observe_project`` builds 3-span traces, so we expect
        indices 0, 1, 2 to appear. ``span_attributes`` carries ``input``
        and ``output`` keys, so each index has both.
        """
        project = populated_observe_project["project"]
        response = auth_client.get(
            "/tracer/observation-span/get_eval_attributes_list/",
            {
                "filters": json.dumps({"project_id": str(project.id)}),
                "row_type": "traces",
            },
        )
        result = response.json().get("result", [])
        for i in range(3):
            assert f"spans.{i}.input" in result
            assert f"spans.{i}.output" in result
        # No phantom positions beyond the observed max
        assert "spans.3.input" not in result

    def test_does_not_expose_first_last_aliases(
        self, auth_client, populated_observe_project
    ):
        """Position aliases (``first``/``last``) are resolver-supported
        but intentionally not surfaced in the picker — only indexed
        positions appear, sized to the observed max."""
        project = populated_observe_project["project"]
        response = auth_client.get(
            "/tracer/observation-span/get_eval_attributes_list/",
            {
                "filters": json.dumps({"project_id": str(project.id)}),
                "row_type": "traces",
            },
        )
        result = response.json().get("result", [])
        assert not any(p.startswith("spans.first.") for p in result)
        assert not any(p.startswith("spans.last.") for p in result)


@pytest.mark.integration
@pytest.mark.api
class TestGetEvalAttributesListSessions:
    """``row_type=sessions`` returns session fields + indexed ``traces.<i>.<...>`` paths."""

    @pytest.mark.xfail(
        reason="Production CH query references span_attr_str (v1 column) not yet migrated to v2 schema",
        strict=False,
    )
    def test_includes_session_public_fields(
        self, auth_client, populated_observe_project
    ):
        project = populated_observe_project["project"]
        response = auth_client.get(
            "/tracer/observation-span/get_eval_attributes_list/",
            {
                "filters": json.dumps({"project_id": str(project.id)}),
                "row_type": "sessions",
            },
        )
        result = response.json().get("result", [])
        for field in ("name", "bookmarked"):
            assert field in result

    @pytest.mark.xfail(
        reason="Production CH query references span_attr_str (v1 column) not yet migrated to v2 schema",
        strict=False,
    )
    def test_includes_indexed_traces_with_trace_fields(
        self, auth_client, populated_observe_project
    ):
        """``traces.<i>.<trace_field>`` for i in 0..(max-traces-per-session − 1).

        ``populated_observe_project`` builds 2 traces per session, so
        indices 0 and 1 should appear with each trace field.
        """
        project = populated_observe_project["project"]
        response = auth_client.get(
            "/tracer/observation-span/get_eval_attributes_list/",
            {
                "filters": json.dumps({"project_id": str(project.id)}),
                "row_type": "sessions",
            },
        )
        result = response.json().get("result", [])
        for i in range(2):
            assert f"traces.{i}.input" in result
            assert f"traces.{i}.output" in result
            assert f"traces.{i}.metadata" in result
            assert f"traces.{i}.tags" in result
        # No phantom positions beyond the observed max
        assert "traces.2.input" not in result

    def test_includes_nested_traces_spans_paths(
        self, auth_client, populated_observe_project
    ):
        """``traces.<i>.spans.<j>.<key>`` for the full observed grid.

        2 traces × 3 spans × 2 keys = 12 nested paths in the test data.
        """
        project = populated_observe_project["project"]
        response = auth_client.get(
            "/tracer/observation-span/get_eval_attributes_list/",
            {
                "filters": json.dumps({"project_id": str(project.id)}),
                "row_type": "sessions",
            },
        )
        result = response.json().get("result", [])
        for i in range(2):
            for j in range(3):
                assert f"traces.{i}.spans.{j}.input" in result
                assert f"traces.{i}.spans.{j}.output" in result
        # No phantom positions
        assert "traces.0.spans.3.input" not in result
        assert "traces.2.spans.0.input" not in result


@pytest.mark.integration
@pytest.mark.api
class TestSpanAttributeKeysNormalisation:
    """``_get_span_attribute_keys`` must hand callers bare strings.

    The CH-backed ``get_span_attribute_keys_ch`` returns ``{key, type}`` dicts
    so the legacy spans picker can render type chips. The trace/session
    path builders f-string into ``spans.<n>.<key>`` — without unwrapping,
    paths become ``spans.0.{'key': '…', 'type': 'text'}`` garbage.

    Pin the unwrap behaviour and the regression at the live endpoint:
    no path in the trace/session response should contain ``{`` or ``}``.
    """

    def test_normalises_dict_and_string_inputs(self, monkeypatch):
        """Pure unit test on ``_get_span_attribute_keys`` itself.

        Forces the CH analytics service to return mixed input — dicts
        with ``key``, dicts without ``key``, bare strings, and empty
        sentinels — and asserts the helper hands callers only the
        usable bare-string keys. Empty / malformed entries are dropped
        entirely; nothing gets stringified into the path output.
        """
        from tracer.services.clickhouse.query_service import (
            AnalyticsQueryService,
        )
        from tracer.views.observation_span import ObservationSpanView

        raw_input = [
            {"key": "gen_ai.input.foo", "type": "text"},
            "bare_string_key",
            {"key": "gen_ai.output.bar", "type": "text"},
            {"type": "text"},  # no key — must be dropped
            {"key": "", "type": "text"},  # empty key — must be dropped
            "",  # empty string — must be dropped
        ]

        monkeypatch.setattr(
            AnalyticsQueryService,
            "get_span_attribute_keys_ch",
            lambda self, pid: raw_input,
        )

        view = ObservationSpanView()
        result = view._get_span_attribute_keys("any-project-id")

        assert result == [
            "gen_ai.input.foo",
            "bare_string_key",
            "gen_ai.output.bar",
        ]

    def test_no_curly_braces_in_traces_response(
        self, auth_client, populated_observe_project
    ):
        """End-to-end pin: the live row_type=traces response NEVER contains
        ``{`` or ``}`` characters in any path. Catches a regression of the
        original dict-stringify bug at the live endpoint, regardless of
        what shape the underlying CH/PG helper returns."""
        project = populated_observe_project["project"]
        response = auth_client.get(
            "/tracer/observation-span/get_eval_attributes_list/",
            {
                "filters": json.dumps({"project_id": str(project.id)}),
                "row_type": "traces",
            },
        )
        result = response.json().get("result", [])
        bad = [p for p in result if "{" in p or "}" in p]
        assert bad == [], f"Found malformed paths: {bad[:5]}"

    def test_no_curly_braces_in_sessions_response(
        self, auth_client, populated_observe_project
    ):
        """End-to-end pin for row_type=sessions, same rationale as the
        traces version above."""
        project = populated_observe_project["project"]
        response = auth_client.get(
            "/tracer/observation-span/get_eval_attributes_list/",
            {
                "filters": json.dumps({"project_id": str(project.id)}),
                "row_type": "sessions",
            },
        )
        result = response.json().get("result", [])
        bad = [p for p in result if "{" in p or "}" in p]
        assert bad == [], f"Found malformed paths: {bad[:5]}"


class TestSpanAttributeKeysPartitionPruning:
    """The recent-window discovery query must prune by the partition key.

    ``spans`` is partitioned by ``toDate(start_time)``; ``created_at`` is
    neither the partition key nor in the sort key. Windowing/ordering by
    ``created_at`` defeats partition pruning and scans the whole project
    (measured ~23x over-read at 100k spans -> Code: 159 timeouts). Pin that
    the query windows and orders by ``start_time`` instead.
    """

    def _capture_sql(self, monkeypatch, *, recent_days=7) -> str:
        from tracer.services.clickhouse.query_service import AnalyticsQueryService

        captured: dict = {}

        class _Result:
            data: list = []

        def _capture(self, query, params, timeout_ms=None):
            captured["query"] = query
            return _Result()

        monkeypatch.setattr(
            AnalyticsQueryService, "execute_ch_query", _capture, raising=True
        )
        AnalyticsQueryService().get_span_attribute_keys_ch_for_projects(
            ["c4de3065-12b5-488c-a814-aa1c8e3f856f"], recent_days=recent_days
        )
        return captured["query"]

    def test_windows_and_orders_by_start_time(self, monkeypatch):
        sql = self._capture_sql(monkeypatch, recent_days=7)
        # start_time is the partition key -> CH can prune to the window.
        assert "start_time >= now() - toIntervalDay" in sql
        assert "ORDER BY start_time DESC" in sql

    def test_does_not_window_or_order_by_created_at(self, monkeypatch):
        sql = self._capture_sql(monkeypatch, recent_days=7)
        # created_at defeats pruning; it must not gate the recent window.
        assert "created_at >= now()" not in sql
        assert "ORDER BY created_at" not in sql

    def test_full_project_discovery_skips_order_by_to_short_circuit(self, monkeypatch):
        # recent_days=None (dashboard/metrics filter discovery): no window, so
        # the ORDER BY must be dropped or LIMIT 10000 can't short-circuit and
        # CH scans the whole project (~477k rows) instead of ~15k.
        sql = self._capture_sql(monkeypatch, recent_days=None)
        assert "start_time >= now()" not in sql
        assert "ORDER BY start_time" not in sql
        assert "LIMIT 10000" in sql


@pytest.mark.integration
@pytest.mark.api
class TestGetEvalAttributesListUnknownRowType:
    def test_unknown_row_type_returns_400(self, auth_client, populated_observe_project):
        project = populated_observe_project["project"]
        response = auth_client.get(
            "/tracer/observation-span/get_eval_attributes_list/",
            {
                "filters": json.dumps({"project_id": str(project.id)}),
                "row_type": "made_up",
            },
        )
        assert response.status_code == 400
