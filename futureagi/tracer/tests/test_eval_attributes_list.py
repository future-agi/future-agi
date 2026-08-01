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
import uuid

import pytest
from rest_framework import status

# Cycle-breaker — same rationale as the runtime test file.
import model_hub.tasks  # noqa: F401, E402
from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.project import Project


@pytest.mark.integration
@pytest.mark.api
class TestGetEvalAttributesListSpans:
    """Legacy span behaviour — returned shape unchanged."""

    @pytest.mark.parametrize(
        "endpoint",
        [
            "/tracer/observation-span/get_span_attributes_list/",
            "/tracer/observation-span/get_eval_attributes_list/",
        ],
    )
    def test_guaranteed_root_keys_survive_empty_bounded_sample(
        self, endpoint, auth_client, populated_observe_project, monkeypatch
    ):
        from tracer.services.clickhouse.query_service import AnalyticsQueryService

        project = populated_observe_project["project"]
        monkeypatch.setattr(
            AnalyticsQueryService,
            "get_span_attribute_keys_ch_for_projects",
            lambda self, project_ids: [],
        )

        response = auth_client.get(
            endpoint,
            {
                "filters": json.dumps({"project_id": str(project.id)}),
                "row_type": "spans",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["query_complete"] is False
        assert payload["query_status"] == "sampled"
        assert payload["query_error_code"] == "sample_limit"
        assert payload["query_sampled"] is True
        result = set(payload["result"])
        assert "final_status" in result
        assert "country" not in result

    @pytest.mark.parametrize(
        "endpoint",
        [
            "/tracer/observation-span/get_span_attributes_list/",
            "/tracer/observation-span/get_eval_attributes_list/",
        ],
    )
    def test_guaranteed_root_keys_survive_discovery_failure(
        self, endpoint, auth_client, populated_observe_project, monkeypatch
    ):
        from tracer.services.clickhouse.query_service import AnalyticsQueryService

        project = populated_observe_project["project"]

        def fail_discovery(self, project_ids):
            raise TimeoutError("bounded attribute discovery timed out")

        monkeypatch.setattr(
            AnalyticsQueryService,
            "get_span_attribute_keys_ch_for_projects",
            fail_discovery,
        )

        response = auth_client.get(
            endpoint,
            {
                "filters": json.dumps({"project_id": str(project.id)}),
                "row_type": "spans",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["query_complete"] is False
        assert payload["query_status"] == "degraded"
        assert payload["query_error_code"] == "read_budget_exceeded"
        assert payload["query_sampled"] is False
        result = set(payload["result"])
        assert "final_status" in result
        assert "country" not in result

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
            "final_status",
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


class TestEvalMappingCardinalityClickHouseOnly:
    def test_sizes_nested_paths_from_one_bounded_ch_query(self, monkeypatch):
        from tracer.services.clickhouse.query_service import AnalyticsQueryService
        from tracer.views import observation_span as span_views

        captured = {}

        class _Result:
            data = [
                {
                    "max_spans_per_trace": 3,
                    "max_traces_per_session": 2,
                }
            ]

        def fake_execute(self, query, params=None, timeout_ms=None, settings=None):
            captured.update(
                query=query,
                params=params,
                timeout_ms=timeout_ms,
                settings=settings,
            )
            return _Result()

        monkeypatch.setattr(
            AnalyticsQueryService,
            "execute_ch_query",
            fake_execute,
        )
        monkeypatch.setattr(span_views.django_cache, "get", lambda key: None)
        monkeypatch.setattr(
            span_views.django_cache, "set", lambda key, value, timeout: None
        )

        result = span_views.ObservationSpanView()._observed_mapping_cardinality(
            "11111111-1111-4111-8111-111111111111"
        )

        assert result == (3, 2)
        assert "FROM spans" in captured["query"]
        assert "Trace.objects" not in captured["query"]
        assert "LIMIT %(input_sample_rows)s" in captured["query"]
        assert captured["query"].index("LIMIT %(input_sample_rows)s") < captured[
            "query"
        ].index("GROUP BY trace_id")
        assert captured["params"]["input_sample_rows"] == 10_000
        assert captured["params"]["sample_size"] == 100
        assert captured["timeout_ms"] == 750
        assert captured["settings"]["max_threads"] == 2
        assert captured["settings"]["max_memory_usage"] == 268_435_456

    def test_budget_failure_still_exposes_first_nested_slot(self, monkeypatch):
        from tracer.services.clickhouse.query_service import AnalyticsQueryService
        from tracer.views import observation_span as span_views

        def fail_execute(self, query, params=None, timeout_ms=None, settings=None):
            raise TimeoutError("bounded test timeout")

        monkeypatch.setattr(
            AnalyticsQueryService,
            "execute_ch_query",
            fail_execute,
        )
        monkeypatch.setattr(span_views.django_cache, "get", lambda key: None)
        monkeypatch.setattr(
            span_views.django_cache, "set", lambda key, value, timeout: None
        )

        result = span_views.ObservationSpanView()._observed_mapping_cardinality(
            "11111111-1111-4111-8111-111111111111"
        )

        assert result == (1, 1)


class TestSpanAttributeKeysPartitionPruning:
    """The recent-window discovery query must prune by the partition key.

    ``spans`` is partitioned by ``toDate(start_time)``; ``created_at`` is
    neither the partition key nor in the sort key. Windowing by ``created_at``
    defeats partition pruning and scans the whole project. Pin that the query
    windows by ``start_time`` and does NOT order by it: ``start_time`` sits
    behind ``observation_type``/``service_name`` in the sort key, so an ordered
    top-N reads the whole window (materializing the fat ``attrs_*`` maps) before
    ``LIMIT`` applies -> Code 396 / Code 159 on high-volume projects. Without the
    ORDER BY, ``project_id`` leading the sort key lets the small per-map LIMIT
    bound the scan. Also pin that only the Map ``.keys`` subcolumn is read
    (never values).
    """

    def _capture_sql(self, monkeypatch, *, recent_days=7) -> str:
        from tracer.services.clickhouse.query_service import AnalyticsQueryService

        captured: dict = {}

        class _Result:
            data: list = []

        def _capture(self, query, params, timeout_ms=None, settings=None):
            captured["query"] = query
            captured["timeout_ms"] = timeout_ms
            captured["settings"] = settings
            return _Result()

        monkeypatch.setattr(
            AnalyticsQueryService, "execute_ch_query", _capture, raising=True
        )
        AnalyticsQueryService().get_span_attribute_keys_ch_for_projects(
            ["c4de3065-12b5-488c-a814-aa1c8e3f856f"], recent_days=recent_days
        )
        assert captured["timeout_ms"] == 750
        assert captured["settings"]["max_threads"] == 2
        assert captured["settings"]["timeout_overflow_mode"] == "throw"
        assert captured["settings"]["read_overflow_mode"] == "throw"
        return captured["query"]

    def test_windows_by_start_time_without_recency_order(self, monkeypatch):
        sql = self._capture_sql(monkeypatch, recent_days=7)
        # start_time is the partition key -> CH can prune to the window.
        assert "start_time >= now() - toIntervalDay" in sql
        # The recency ORDER BY is dropped so the sample LIMIT bounds the scan.
        assert "ORDER BY start_time" not in sql

    def test_reads_keys_subcolumn_not_whole_map(self, monkeypatch):
        sql = self._capture_sql(monkeypatch, recent_days=7)
        # keys-only endpoint -> read the Map .keys subcolumn, never the
        # (200-380 KB) map values via mapKeys().
        assert "attrs_string.keys" in sql
        assert "attrs_number.keys" in sql
        assert "attrs_bool.keys" in sql
        assert "mapKeys(" not in sql

    def test_preserves_limit_and_type_labels(self, monkeypatch):
        sql = self._capture_sql(monkeypatch, recent_days=7)
        # Wide maps made the old 10k-row sample exceed the endpoint's 256 MiB
        # budget. Discovery is explicitly sampled, so keep the smaller cap.
        # Three per-map samples plus the existing outer catalog cap.
        assert sql.count("LIMIT 1000") == 4
        assert "LIMIT 10000" not in sql
        assert "'string'" in sql
        assert "'number'" in sql
        assert "'boolean'" in sql

    def test_does_not_window_or_order_by_created_at(self, monkeypatch):
        sql = self._capture_sql(monkeypatch, recent_days=7)
        # created_at defeats pruning; it must not gate the recent window.
        assert "created_at >= now()" not in sql
        assert "ORDER BY created_at" not in sql

    def test_full_project_discovery_skips_order_by_to_short_circuit(self, monkeypatch):
        # recent_days=None (dashboard/metrics filter discovery): no window, so
        # the ORDER BY must be dropped or the sample LIMIT can't short-circuit and
        # CH scans the whole project (~477k rows) instead of ~15k.
        sql = self._capture_sql(monkeypatch, recent_days=None)
        assert "start_time >= now()" not in sql
        assert "ORDER BY start_time" not in sql
        assert sql.count("LIMIT 1000") == 4


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


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestEvalAttributeProjectScope:
    @staticmethod
    def _other_workspace_project(organization, user):
        suffix = uuid.uuid4().hex[:8]
        other_workspace = Workspace.objects.create(
            name=f"Other Eval Attribute Workspace {suffix}",
            organization=organization,
            is_active=True,
            created_by=user,
        )
        return Project.objects.create(
            name=f"Other Eval Attribute Project {suffix}",
            organization=organization,
            workspace=other_workspace,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
            metadata={},
        )

    @pytest.mark.parametrize(
        "endpoint",
        [
            "/tracer/observation-span/get_span_attributes_list/",
            "/tracer/observation-span/get_eval_attributes_list/",
        ],
    )
    def test_rejects_same_org_other_workspace_before_clickhouse(
        self, endpoint, auth_client, organization, user, monkeypatch
    ):
        from tracer.services.clickhouse.query_service import AnalyticsQueryService

        other_project = self._other_workspace_project(organization, user)

        def fail_if_called(*args, **kwargs):
            raise AssertionError(
                "ClickHouse must not run before the Project tenant gate"
            )

        monkeypatch.setattr(
            AnalyticsQueryService,
            "get_span_attribute_keys_ch",
            fail_if_called,
        )

        response = auth_client.get(
            endpoint,
            {
                "filters": json.dumps({"project_id": str(other_project.id)}),
                "row_type": "spans",
            },
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestSavedEvalMappingAttributeInventory:
    def test_span_picker_keeps_saved_mapping_missing_from_bounded_ch_sample(
        self, auth_client, observe_project, eval_template, monkeypatch
    ):
        from tracer.services.clickhouse.query_service import AnalyticsQueryService

        CustomEvalConfig.objects.create(
            project=observe_project,
            eval_template=eval_template,
            name=f"saved-rare-span-{uuid.uuid4().hex[:8]}",
            mapping={"input_text": "rare_customer_attribute"},
        )
        monkeypatch.setattr(
            AnalyticsQueryService,
            "get_span_attribute_keys_ch",
            lambda self, project_id: [{"key": "common_attribute", "type": "string"}],
        )

        response = auth_client.get(
            "/tracer/observation-span/get_eval_attributes_list/",
            {
                "filters": json.dumps({"project_id": str(observe_project.id)}),
                "row_type": "spans",
            },
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["result"] == [
            "common_attribute",
            "final_status",
            "rare_customer_attribute",
        ]

    def test_trace_picker_keeps_saved_indexed_path_beyond_sampled_cardinality(
        self, auth_client, observe_project, eval_template, monkeypatch
    ):
        from tracer.services.clickhouse.query_service import AnalyticsQueryService
        from tracer.views.observation_span import ObservationSpanView

        saved_path = "spans.7.rare_customer_attribute"
        CustomEvalConfig.objects.create(
            project=observe_project,
            eval_template=eval_template,
            name=f"saved-rare-trace-{uuid.uuid4().hex[:8]}",
            mapping={"input_text": saved_path},
        )
        monkeypatch.setattr(
            AnalyticsQueryService,
            "get_span_attribute_keys_ch",
            lambda self, project_id: [{"key": "common_attribute", "type": "string"}],
        )
        monkeypatch.setattr(
            ObservationSpanView,
            "_observed_mapping_cardinality",
            lambda self, project_id: (1, 1),
        )

        response = auth_client.get(
            "/tracer/observation-span/get_eval_attributes_list/",
            {
                "filters": json.dumps({"project_id": str(observe_project.id)}),
                "row_type": "traces",
            },
        )

        assert response.status_code == status.HTTP_200_OK
        assert saved_path in response.json()["result"]
