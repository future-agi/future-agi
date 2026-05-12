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

    def test_includes_trace_public_fields(
        self, auth_client, populated_observe_project
    ):
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
            "should_use_clickhouse",
            lambda self, qt: True,
        )
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


@pytest.mark.integration
@pytest.mark.api
class TestGetEvalAttributesListUnknownRowType:
    def test_unknown_row_type_returns_400(
        self, auth_client, populated_observe_project
    ):
        project = populated_observe_project["project"]
        response = auth_client.get(
            "/tracer/observation-span/get_eval_attributes_list/",
            {
                "filters": json.dumps({"project_id": str(project.id)}),
                "row_type": "made_up",
            },
        )
        assert response.status_code == 400


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestTraceEvalResolvesDottedSpanPath:
    """End-to-end: a trace task with mapping ``output -> spans.0.output``
    actually resolves through ``_process_trace_mapping`` to the first span's
    ``span_attributes.output.value`` and writes a non-error EvalLogger row.
    """

    def test_trace_eval_resolves_indexed_span_path(
        self,
        populated_observe_project,
        eval_template,
        stub_run_eval,
        stub_cost_log,
        inline_temporal,
    ):
        from tracer.models.custom_eval_config import CustomEvalConfig
        from tracer.models.eval_task import (
            EvalTask,
            EvalTaskStatus,
            RowType,
            RunType,
        )
        from tracer.models.observation_span import EvalLogger
        from tracer.utils.eval_tasks import process_eval_task

        project = populated_observe_project["project"]
        config = CustomEvalConfig.objects.create(
            project=project,
            eval_template=eval_template,
            name="Trace eval w/ dotted span path",
            config={"output": "Pass/Fail"},
            mapping={
                "input": "spans.0.input",
                "output": "spans.0.output",
            },
            model="turing_large",
        )
        task = EvalTask.objects.create(
            project=project,
            name="Dotted path trace task",
            filters={"project_id": str(project.id)},
            sampling_rate=100.0,
            run_type=RunType.HISTORICAL,
            spans_limit=1000,
            status=EvalTaskStatus.PENDING,
            row_type=RowType.TRACES,
        )
        task.evals.add(config)

        process_eval_task._original_func(str(task.id))

        rows = list(
            EvalLogger.objects.filter(
                eval_task_id=str(task.id), deleted=False
            ).select_related("trace")
        )
        # 4 traces × 1 eval = 4 rows. None should be error rows — the dotted
        # path resolves successfully because every trace has spans whose
        # span_attributes carry ``input`` and ``output`` keys (set by
        # populated_observe_project).
        assert len(rows) == 4
        assert all(not r.error for r in rows), [
            (r.id, r.error_message) for r in rows if r.error
        ]
