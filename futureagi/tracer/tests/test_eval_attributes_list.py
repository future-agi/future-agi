"""
Tests for the row_type-aware ``get_eval_attributes_list`` endpoint.

Three contracts the endpoint must honor:

  1. **Alignment.** For ``row_type=traces`` / ``row_type=sessions`` only
     ``(idx, key)`` / ``(trace_idx, span_idx, key)`` slots realized by at
     least one trace in the project's recent sample are advertised. No
     cartesian filler that resolves to ``_MISSING`` everywhere — that
     bug caused production eval-task failures (every trace flagged
     "Required attribute ... not found").
  2. **Shape.** Response envelope is
     ``{items, metadata.total_rows, page_number, page_size}`` under
     ``result`` — the same shape regardless of row_type.
  3. **Pagination + search.** ``page_number`` / ``page_size`` slice the
     materialised path list; ``search`` filters case-insensitively on
     the path string.

Plus an end-to-end check that a saved mapping using one of the new dotted
paths actually resolves through ``_process_trace_mapping`` and writes a
non-error EvalLogger row.
"""

import json
from datetime import timedelta

import pytest
from django.utils import timezone

# Cycle-breaker — same rationale as the runtime test file.
import model_hub.tasks  # noqa: F401, E402


def _items(response):
    """Helper: pull the paginated items list out of the standard envelope."""
    payload = response.json().get("result") or {}
    return payload.get("items", []) if isinstance(payload, dict) else []


def _metadata(response):
    payload = response.json().get("result") or {}
    return payload.get("metadata", {}) if isinstance(payload, dict) else {}


@pytest.mark.integration
@pytest.mark.api
class TestGetEvalAttributesListSpans:
    """``row_type=spans`` returns the flat key list inside the paginated envelope."""

    def test_spans_default_returns_flat_list(
        self, auth_client, populated_observe_project
    ):
        project = populated_observe_project["project"]
        response = auth_client.get(
            "/tracer/observation-span/get_eval_attributes_list/",
            {
                "filters": json.dumps({"project_id": str(project.id)}),
                "page_size": 200,
            },
        )
        assert response.status_code == 200
        items = _items(response)
        assert isinstance(items, list)
        # populated_observe_project's spans set ``input`` and ``output`` in
        # span_attributes, so those keys must appear.
        assert "input" in items
        assert "output" in items
        # No dotted paths — the spans surface is flat.
        assert not any("." in path for path in items)

    def test_spans_explicit_row_type_returns_flat_list(
        self, auth_client, populated_observe_project
    ):
        project = populated_observe_project["project"]
        response = auth_client.get(
            "/tracer/observation-span/get_eval_attributes_list/",
            {
                "filters": json.dumps({"project_id": str(project.id)}),
                "row_type": "spans",
                "page_size": 200,
            },
        )
        assert response.status_code == 200
        items = _items(response)
        assert "input" in items
        assert not any("." in path for path in items)


@pytest.mark.integration
@pytest.mark.api
class TestGetEvalAttributesListTraces:
    """``row_type=traces`` returns trace fields + realized ``spans.<n>.<key>`` paths."""

    def test_includes_trace_public_fields(
        self, auth_client, populated_observe_project
    ):
        project = populated_observe_project["project"]
        response = auth_client.get(
            "/tracer/observation-span/get_eval_attributes_list/",
            {
                "filters": json.dumps({"project_id": str(project.id)}),
                "row_type": "traces",
                "page_size": 200,
            },
        )
        assert response.status_code == 200
        items = _items(response)
        for field in (
            "input",
            "output",
            "name",
            "error",
            "tags",
            "metadata",
            "external_id",
        ):
            assert field in items

    def test_includes_indexed_span_paths_per_observed_pair(
        self, auth_client, populated_observe_project
    ):
        """``spans.<n>.<key>`` for every realized (idx, key) pair.

        ``populated_observe_project`` builds 3-span traces where every
        span carries ``input`` and ``output`` in ``span_attributes`` —
        so the realized set is exactly ``{0,1,2} × {input, output}``.
        """
        project = populated_observe_project["project"]
        response = auth_client.get(
            "/tracer/observation-span/get_eval_attributes_list/",
            {
                "filters": json.dumps({"project_id": str(project.id)}),
                "row_type": "traces",
                "page_size": 200,
            },
        )
        items = _items(response)
        for i in range(3):
            assert f"spans.{i}.input" in items
            assert f"spans.{i}.output" in items
        # No phantom positions beyond the observed max.
        assert "spans.3.input" not in items

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
                "page_size": 200,
            },
        )
        items = _items(response)
        assert not any(p.startswith("spans.first.") for p in items)
        assert not any(p.startswith("spans.last.") for p in items)


@pytest.mark.integration
@pytest.mark.api
class TestGetEvalAttributesListAlignment:
    """The bug that motivated the rewrite: keys at varied positions across
    traces caused the picker to advertise (idx, key) cells that no trace
    realized. Seed data where a given key appears at one specific index in
    one trace and a different index in another, and assert only the
    realized cells are emitted."""

    @pytest.fixture
    def project_with_misaligned_keys(self, db, observe_project):
        """Two traces; ``timeline`` lives at idx 1 in trace A and idx 3
        in trace B. Pre-alignment, the picker would emit
        ``spans.{0,1,2,3}.timeline`` (cartesian); after, only
        ``spans.1.timeline`` and ``spans.3.timeline`` should appear."""
        from tracer.models.observation_span import ObservationSpan
        from tracer.models.trace import Trace

        trace_a = Trace.objects.create(
            project=observe_project, name="trace_a",
            input={}, output={},
        )
        # 2 spans on trace A: idx 0 = unrelated, idx 1 = timeline
        for sp_idx, attrs in enumerate(
            [{"other_key": "x"}, {"timeline": [1, 2, 3]}]
        ):
            ObservationSpan.objects.create(
                id=f"a_{sp_idx}",
                project=observe_project,
                trace=trace_a,
                parent_span_id=None if sp_idx == 0 else "a_0",
                name=f"a_{sp_idx}",
                observation_type="llm",
                start_time=timezone.now() - timedelta(seconds=10 - sp_idx),
                end_time=timezone.now() - timedelta(seconds=9 - sp_idx),
                span_attributes=attrs,
                status="OK",
            )
        trace_b = Trace.objects.create(
            project=observe_project, name="trace_b",
            input={}, output={},
        )
        # 4 spans on trace B: idx 0..2 = unrelated, idx 3 = timeline
        for sp_idx, attrs in enumerate(
            [
                {"other_key": "x"},
                {"another": "y"},
                {"third": "z"},
                {"timeline": [4, 5, 6]},
            ]
        ):
            ObservationSpan.objects.create(
                id=f"b_{sp_idx}",
                project=observe_project,
                trace=trace_b,
                parent_span_id=None if sp_idx == 0 else "b_0",
                name=f"b_{sp_idx}",
                observation_type="llm",
                start_time=timezone.now() - timedelta(seconds=20 - sp_idx),
                end_time=timezone.now() - timedelta(seconds=19 - sp_idx),
                span_attributes=attrs,
                status="OK",
            )
        return observe_project

    def test_excludes_unrealized_index_key_pairs(
        self, auth_client, project_with_misaligned_keys
    ):
        project = project_with_misaligned_keys
        response = auth_client.get(
            "/tracer/observation-span/get_eval_attributes_list/",
            {
                "filters": json.dumps({"project_id": str(project.id)}),
                "row_type": "traces",
                "page_size": 200,
            },
        )
        items = _items(response)
        # Realized: ``timeline`` lives at idx 1 and idx 3.
        assert "spans.1.timeline" in items
        assert "spans.3.timeline" in items
        # Pre-alignment would have advertised these too. Post-alignment,
        # they must NOT appear — those cells resolve to _MISSING.
        assert "spans.0.timeline" not in items
        assert "spans.2.timeline" not in items
        # ``other_key`` lives at idx 0 in BOTH traces.
        assert "spans.0.other_key" in items
        # Not at idx 1 or 3 in any trace.
        assert "spans.1.other_key" not in items
        assert "spans.3.other_key" not in items


@pytest.mark.integration
@pytest.mark.api
class TestGetEvalAttributesListSessions:
    """``row_type=sessions`` returns session fields + realized
    ``traces.<i>.<...>`` paths."""

    def test_includes_session_public_fields(
        self, auth_client, populated_observe_project
    ):
        project = populated_observe_project["project"]
        response = auth_client.get(
            "/tracer/observation-span/get_eval_attributes_list/",
            {
                "filters": json.dumps({"project_id": str(project.id)}),
                "row_type": "sessions",
                "page_size": 200,
            },
        )
        items = _items(response)
        for field in ("name", "bookmarked"):
            assert field in items

    def test_includes_indexed_traces_with_trace_fields(
        self, auth_client, populated_observe_project
    ):
        """``traces.<i>.<trace_field>`` for every observed trace index.

        ``populated_observe_project`` builds 2 traces per session, so
        indices 0 and 1 should appear with each trace field.
        """
        project = populated_observe_project["project"]
        response = auth_client.get(
            "/tracer/observation-span/get_eval_attributes_list/",
            {
                "filters": json.dumps({"project_id": str(project.id)}),
                "row_type": "sessions",
                "page_size": 200,
            },
        )
        items = _items(response)
        for i in range(2):
            assert f"traces.{i}.input" in items
            assert f"traces.{i}.output" in items
            assert f"traces.{i}.metadata" in items
            assert f"traces.{i}.tags" in items
        # No phantom positions beyond the observed max.
        assert "traces.2.input" not in items

    def test_includes_nested_traces_spans_paths(
        self, auth_client, populated_observe_project
    ):
        """``traces.<i>.spans.<j>.<key>`` for each realized triple.

        2 traces × 3 spans × 2 keys = 12 nested paths in the test data.
        """
        project = populated_observe_project["project"]
        response = auth_client.get(
            "/tracer/observation-span/get_eval_attributes_list/",
            {
                "filters": json.dumps({"project_id": str(project.id)}),
                "row_type": "sessions",
                "page_size": 200,
            },
        )
        items = _items(response)
        for i in range(2):
            for j in range(3):
                assert f"traces.{i}.spans.{j}.input" in items
                assert f"traces.{i}.spans.{j}.output" in items
        # No phantom positions.
        assert "traces.0.spans.3.input" not in items
        assert "traces.2.spans.0.input" not in items


@pytest.mark.integration
@pytest.mark.api
class TestPickerPagination:
    """``page_number`` + ``page_size`` slice the materialised path list."""

    def test_pagination_traces_disjoint_windows(
        self, auth_client, populated_observe_project
    ):
        project = populated_observe_project["project"]

        def fetch(page):
            return auth_client.get(
                "/tracer/observation-span/get_eval_attributes_list/",
                {
                    "filters": json.dumps({"project_id": str(project.id)}),
                    "row_type": "traces",
                    "page_size": 5,
                    "page_number": page,
                },
            )

        page0 = fetch(0)
        page1 = fetch(1)
        items0, items1 = _items(page0), _items(page1)
        # Page size honored.
        assert len(items0) == 5
        # Disjoint slices over the same sorted list.
        assert set(items0).isdisjoint(set(items1))
        # ``total_rows`` is stable across pages.
        assert _metadata(page0)["total_rows"] == _metadata(page1)["total_rows"]

    def test_search_substring_case_insensitive(
        self, auth_client, populated_observe_project
    ):
        project = populated_observe_project["project"]
        response = auth_client.get(
            "/tracer/observation-span/get_eval_attributes_list/",
            {
                "filters": json.dumps({"project_id": str(project.id)}),
                "row_type": "traces",
                "search": "INPUT",
                "page_size": 200,
            },
        )
        items = _items(response)
        # Every returned path contains ``input`` (case-insensitive).
        assert items, "search should still return matches"
        assert all("input" in p.lower() for p in items)
        # And nothing without ``input`` slipped in.
        assert "output" not in items
        assert "spans.0.output" not in items

    def test_spans_row_type_paginated_and_searchable(
        self, auth_client, populated_observe_project
    ):
        project = populated_observe_project["project"]
        response = auth_client.get(
            "/tracer/observation-span/get_eval_attributes_list/",
            {
                "filters": json.dumps({"project_id": str(project.id)}),
                "row_type": "spans",
                "search": "put",
                "page_size": 1,
                "page_number": 0,
            },
        )
        items = _items(response)
        meta = _metadata(response)
        # Both ``input`` and ``output`` match the substring ``put``.
        assert meta["total_rows"] >= 2
        assert len(items) == 1


@pytest.mark.integration
@pytest.mark.api
class TestSpanAttributeKeysNormalisation:
    """``_get_span_attribute_keys`` must hand callers bare strings.

    The CH-backed ``get_span_attribute_keys_ch`` returns ``{key, type}``
    dicts so the legacy spans picker can render type chips. The trace
    + session path builders f-string into ``spans.<n>.<key>`` — without
    unwrapping, paths become ``spans.0.{'key': '…', 'type': 'text'}``
    garbage.

    Pin the unwrap behaviour and the regression at the live endpoint:
    no path in the trace/session response should contain ``{`` or ``}``.
    """

    def test_normalises_dict_and_string_inputs(self, monkeypatch):
        from tracer.services.clickhouse.query_service import (
            AnalyticsQueryService,
        )
        from tracer.views.observation_span import ObservationSpanView

        raw_input = [
            {"key": "gen_ai.input.foo", "type": "text"},
            "bare_string_key",
            {"key": "gen_ai.output.bar", "type": "text"},
            {"type": "text"},
            {"key": "", "type": "text"},
            "",
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
        project = populated_observe_project["project"]
        response = auth_client.get(
            "/tracer/observation-span/get_eval_attributes_list/",
            {
                "filters": json.dumps({"project_id": str(project.id)}),
                "row_type": "traces",
                "page_size": 200,
            },
        )
        items = _items(response)
        bad = [p for p in items if "{" in p or "}" in p]
        assert bad == [], f"Found malformed paths: {bad[:5]}"

    def test_no_curly_braces_in_sessions_response(
        self, auth_client, populated_observe_project
    ):
        project = populated_observe_project["project"]
        response = auth_client.get(
            "/tracer/observation-span/get_eval_attributes_list/",
            {
                "filters": json.dumps({"project_id": str(project.id)}),
                "row_type": "sessions",
                "page_size": 200,
            },
        )
        items = _items(response)
        bad = [p for p in items if "{" in p or "}" in p]
        assert bad == [], f"Found malformed paths: {bad[:5]}"


@pytest.mark.integration
@pytest.mark.api
class TestClickhouseFirstRouting:
    """The aligned aggregates are CH-first with PG fallback. Pin both
    paths: CH used when it returns non-empty, PG used when CH returns
    empty (early-adoption window where ``spans`` isn't backfilled) or
    raises.
    """

    def test_traces_uses_clickhouse_when_routed_and_nonempty(
        self, auth_client, populated_observe_project, monkeypatch
    ):
        from tracer.services.clickhouse.query_service import (
            AnalyticsQueryService,
        )

        project = populated_observe_project["project"]
        ch_pairs = [(0, "ch_only_key"), (1, "ch_only_key")]

        monkeypatch.setattr(
            AnalyticsQueryService,
            "should_use_clickhouse",
            lambda self, qt: True,
        )
        monkeypatch.setattr(
            AnalyticsQueryService,
            "get_observed_trace_attribute_pairs_ch",
            lambda self, pid, n: ch_pairs,
        )

        response = auth_client.get(
            "/tracer/observation-span/get_eval_attributes_list/",
            {
                "filters": json.dumps({"project_id": str(project.id)}),
                "row_type": "traces",
                "page_size": 200,
            },
        )
        items = _items(response)
        # CH-returned pairs surface verbatim; PG aggregate is bypassed.
        assert "spans.0.ch_only_key" in items
        assert "spans.1.ch_only_key" in items
        # PG fixture's ``input``/``output`` keys are NOT present at any
        # ``spans.<n>.<key>`` slot — PG was not consulted.
        assert "spans.0.input" not in items

    def test_traces_falls_back_to_pg_on_ch_error(
        self, auth_client, populated_observe_project, monkeypatch
    ):
        from tracer.services.clickhouse.query_service import (
            AnalyticsQueryService,
        )

        project = populated_observe_project["project"]

        def boom(self, pid, n):
            raise RuntimeError("CH simulated outage")

        monkeypatch.setattr(
            AnalyticsQueryService,
            "should_use_clickhouse",
            lambda self, qt: True,
        )
        monkeypatch.setattr(
            AnalyticsQueryService,
            "get_observed_trace_attribute_pairs_ch",
            boom,
        )

        response = auth_client.get(
            "/tracer/observation-span/get_eval_attributes_list/",
            {
                "filters": json.dumps({"project_id": str(project.id)}),
                "row_type": "traces",
                "page_size": 200,
            },
        )
        items = _items(response)
        # PG fallback ran — fixture's ``input``/``output`` keys at the
        # 3-span positions all surface.
        for i in range(3):
            assert f"spans.{i}.input" in items
            assert f"spans.{i}.output" in items


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
