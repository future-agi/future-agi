"""V2 (ClickHouse) trace-detail handler — unit tests for the CH-only-trace fix.

These cover the behaviour added when routing ``GET /tracer/trace/{id}/`` through
the v1/v2 dispatch (``TRACE_DETAIL``): the ClickHouse tenant gate, the metadata
synthesis for collector-ingested traces that have no Postgres ``Trace`` row, and
the v1/v2 response-envelope parity that keeps the two paths interchangeable for
the frontend.

They are pure unit tests — the ClickHouse ``analytics`` client is faked and the
Postgres managers are patched — so they need no database or CH test stack (the
PG-enrichment blocks in the handler are fail-open, so any un-patched lookup is
caught and skipped).
"""

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.db.models import Q

from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.services.clickhouse.query_builders.trace_detail import TraceDetailHandler
from tracer.services.clickhouse.v2.query_builders.trace_detail import (
    retrieve_trace_detail_ch,
)

try:
    from model_hub.models.score import Score as ScoreModel
except Exception:  # pragma: no cover - import shape guard
    ScoreModel = None


# --------------------------------------------------------------------------- #
# Fakes / helpers
# --------------------------------------------------------------------------- #
class _FakeResult:
    def __init__(self, data):
        self.data = data


class _FakeAnalytics:
    """Stands in for AnalyticsQueryService; routes by the SQL it is handed."""

    def __init__(self, *, project_rows, span_rows=None, eval_rows=None):
        self.project_rows = project_rows
        self.span_rows = span_rows or []
        self.eval_rows = eval_rows or []
        self.queries = []

    def execute_ch_query(self, query, params=None, timeout_ms=None, **_):
        self.queries.append(query)
        if "AS project_id FROM spans" in query and "LIMIT 1" in query:
            return _FakeResult(list(self.project_rows))
        if "ORDER BY start_time" in query:
            return _FakeResult(list(self.span_rows))
        if "FINAL" in query:
            return _FakeResult(list(self.eval_rows))
        return _FakeResult([])


def _root_span_row(**overrides):
    row = {
        "id": "S1",
        "trace_id": "T1",
        "parent_span_id": None,
        "name": "root-span",
        "observation_type": "CHAIN",
        "start_time": "2026-01-01T00:00:00Z",
        "end_time": "2026-01-01T00:00:01Z",
        "input": '{"q": "hi"}',
        "output": '{"a": "yo"}',
        "model": "gpt-4",
        "latency_ms": 1200,
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
        "cost": 0.001,
        "status": "OK",
        "status_message": None,
        "tags": "[]",
        "span_events": "[]",
        "provider": "openai",
        # non-empty so the per-span PG attribute fallback is skipped
        "span_attributes": '{"k": "v"}',
        "project_version_id": None,
        "custom_eval_config_id": None,
        "trace_session_id": "SESS1",
        "metadata_json": '{"foo": "bar"}',
        "attrs_string": {},
        "attrs_number": {},
        "attrs_bool": {},
    }
    row.update(overrides)
    return row


def _patch_v2_pg(stack, *, project_accessible, pg_trace=None):
    """Patch the Postgres surfaces the v2 handler touches and return nothing.

    - Project tenant gate -> ``project_accessible``
    - ``Trace.objects.filter().first()`` -> ``pg_trace`` (None == CH-only trace)
    - scope-Q builder, eval-logger source, and the best-effort enrichment
      managers (Score / ObservationSpan) -> empty, so no DB is touched.
    """
    proj_mgr = MagicMock()
    proj_mgr.filter.return_value.exists.return_value = project_accessible
    stack.enter_context(patch.object(Project, "no_workspace_objects", proj_mgr))

    trace_mgr = MagicMock()
    trace_mgr.filter.return_value.first.return_value = pg_trace
    stack.enter_context(patch.object(Trace, "objects", trace_mgr))

    obs_mgr = MagicMock()
    obs_mgr.filter.return_value.exclude.return_value.values_list.return_value = []
    stack.enter_context(patch.object(ObservationSpan, "objects", obs_mgr))

    if ScoreModel is not None:
        score_mgr = MagicMock()
        score_mgr.filter.return_value.select_related.return_value.values.return_value = []
        stack.enter_context(patch.object(ScoreModel, "objects", score_mgr))

    stack.enter_context(
        patch(
            "tracer.views.trace._project_workspace_scope_q",
            lambda request, project_prefix="": Q(),
        )
    )
    stack.enter_context(
        patch(
            "tracer.services.clickhouse.eval_logger_table.eval_logger_source",
            lambda: ("tracer_eval_logger_v2", "is_deleted = 0"),
        )
    )


# --------------------------------------------------------------------------- #
# 1) ClickHouse tenant gate
# --------------------------------------------------------------------------- #
class TestV2TenantGate:
    """The v2 handler denies cross-tenant / unknown traces by raising
    ``Trace.DoesNotExist`` (fail-closed) — before reading any span data."""

    def test_denies_when_project_not_accessible(self):
        analytics = _FakeAnalytics(project_rows=[{"project_id": "P1"}])
        with ExitStack() as stack:
            _patch_v2_pg(stack, project_accessible=False)
            with pytest.raises(Trace.DoesNotExist):
                retrieve_trace_detail_ch(MagicMock(), MagicMock(), "T1", analytics)
        # gate fails closed before the spans query runs
        assert not any("ORDER BY start_time" in q for q in analytics.queries)

    def test_denies_when_trace_has_no_spans_in_ch(self):
        analytics = _FakeAnalytics(project_rows=[])  # no project resolved
        with ExitStack() as stack:
            _patch_v2_pg(stack, project_accessible=True)
            with pytest.raises(Trace.DoesNotExist):
                retrieve_trace_detail_ch(MagicMock(), MagicMock(), "T1", analytics)


# --------------------------------------------------------------------------- #
# 2) Metadata synthesis for a CH-only trace (no PG Trace row)
# --------------------------------------------------------------------------- #
class TestV2SynthesisFromRootSpan:
    """When there is no Postgres ``Trace`` row (collector ingest), the handler
    synthesizes the trace envelope from the root span instead of 404-ing."""

    def test_synthesizes_trace_envelope_from_root_span(self):
        analytics = _FakeAnalytics(
            project_rows=[{"project_id": "P1"}],
            span_rows=[_root_span_row()],
        )
        with ExitStack() as stack:
            _patch_v2_pg(stack, project_accessible=True, pg_trace=None)
            result = retrieve_trace_detail_ch(MagicMock(), MagicMock(), "T1", analytics)

        trace = result["trace"]
        assert trace["id"] == "T1"
        assert trace["project"] == "P1"
        assert trace["name"] == "root-span"  # taken from the root span
        assert trace["session"] == "SESS1"  # from trace_session_id
        assert trace["metadata"] == {"foo": "bar"}
        assert trace["error"] is False  # status OK -> no error
        # spans + computed rollups still present
        assert len(result["observation_spans"]) == 1
        assert result["summary"]["total_spans"] == 1
        assert result["summary"]["total_tokens"] == 15

    def test_serializer_used_when_pg_trace_present(self):
        """With a PG row, the trace metadata comes from the serializer, not
        synthesis — proving synthesis is the no-row fallback, not the default."""
        analytics = _FakeAnalytics(
            project_rows=[{"project_id": "P1"}],
            span_rows=[_root_span_row()],
        )
        view = MagicMock()
        view.get_serializer.return_value.data = {"id": "T1", "name": "from-serializer"}
        with ExitStack() as stack:
            _patch_v2_pg(
                stack, project_accessible=True, pg_trace=SimpleNamespace(id="T1")
            )
            result = retrieve_trace_detail_ch(view, MagicMock(), "T1", analytics)

        assert result["trace"]["name"] == "from-serializer"
        view.get_serializer.assert_called_once()


# --------------------------------------------------------------------------- #
# 2b) span_attributes = typed maps ∪ attributes_extra
# --------------------------------------------------------------------------- #
class TestV2SpanAttributesMerge:
    """span_attributes = typed maps ∪ attributes_extra. Regression: a non-empty
    attributes_extra used to suppress the maps entirely."""

    def _span_attrs(self, **overrides):
        row = _root_span_row(
            # aliased to `span_attributes` in the SQL — this is attributes_extra
            span_attributes='{"input.value": "hi", "output.value": "yo"}',
            attrs_string={"test_string": "beta", "user.id": "dave"},
            attrs_number={"test_number": 100},
            attrs_bool={"streaming": 1},
        )
        row.update(overrides)
        analytics = _FakeAnalytics(project_rows=[{"project_id": "P1"}], span_rows=[row])
        with ExitStack() as stack:
            _patch_v2_pg(stack, project_accessible=True, pg_trace=None)
            result = retrieve_trace_detail_ch(MagicMock(), MagicMock(), "T1", analytics)
        return result["observation_spans"][0]["observation_span"]["span_attributes"]

    def test_merges_all_four_sources(self):
        attrs = self._span_attrs()
        # attributes_extra overflow
        assert attrs["input.value"] == "hi"
        assert attrs["output.value"] == "yo"
        # typed maps — previously dropped when attributes_extra was non-empty
        assert attrs["test_string"] == "beta"
        assert attrs["user.id"] == "dave"
        assert attrs["test_number"] == 100

    def test_bool_map_coerced_to_bool(self):
        assert self._span_attrs()["streaming"] is True

    def test_attributes_extra_overrides_maps_on_collision(self):
        attrs = self._span_attrs(
            attrs_string={"dupe": "from_map"},
            span_attributes='{"dupe": "from_extra"}',
        )
        assert attrs["dupe"] == "from_extra"


# --------------------------------------------------------------------------- #
# 3) v1 (PG) <-> v2 (CH) response-envelope parity
# --------------------------------------------------------------------------- #
class TestV1V2EnvelopeParity:
    """Both handlers must return the identical response envelope so the FE can
    consume either path interchangeably as the routing mode flips."""

    _ENVELOPE = {"trace", "observation_spans", "summary", "graph"}

    def _v1_result(self):
        view = MagicMock()
        fake_trace = SimpleNamespace(id="T1", project_id="P1", project_version_id=None)
        view.get_queryset.return_value.filter.return_value.first.return_value = (
            fake_trace
        )
        view.get_serializer.return_value.data = {"id": "T1", "name": "root"}

        span_tree = [
            {
                "observation_span": {
                    "id": "S1",
                    "name": "root",
                    "observation_type": "CHAIN",
                    "latency_ms": 1200,
                    "total_tokens": 15,
                    "status": "OK",
                },
                "children": [],
            }
        ]
        with patch(
            "tracer.views.observation_span.get_observation_spans",
            return_value=span_tree,
        ):
            return TraceDetailHandler(view=view, request=MagicMock(), pk="T1").fetch()

    def _v2_result(self):
        analytics = _FakeAnalytics(
            project_rows=[{"project_id": "P1"}],
            span_rows=[_root_span_row()],
        )
        with ExitStack() as stack:
            _patch_v2_pg(stack, project_accessible=True, pg_trace=None)
            return retrieve_trace_detail_ch(MagicMock(), MagicMock(), "T1", analytics)

    def test_top_level_keys_match(self):
        v1, v2 = self._v1_result(), self._v2_result()
        assert set(v1) == set(v2) == self._ENVELOPE

    def test_summary_and_graph_shape_match(self):
        v1, v2 = self._v1_result(), self._v2_result()
        assert set(v1["summary"]) == set(v2["summary"])
        assert set(v1["graph"]) == set(v2["graph"]) == {"nodes", "edges"}

    # ----- value parity over a richer trace ----------------------------------
    # One logical multi-span trace (two roots, a parent->child edge, a span with
    # cost=None, a root with latency_ms=None, and one ERROR span) fed through BOTH
    # handlers. Both call the same `compute_trace_summary_and_graph`, so the
    # summary VALUES — not just the keys — must be identical; this is the drift
    # the duplicated compute used to risk.
    def _v1_rich(self):
        view = MagicMock()
        fake_trace = SimpleNamespace(id="T1", project_id="P1", project_version_id=None)
        view.get_queryset.return_value.filter.return_value.first.return_value = (
            fake_trace
        )
        view.get_serializer.return_value.data = {"id": "T1", "name": "root"}

        def _obs(sid, otype, tt, pt, ct, cost, status, latency):
            return {
                "id": sid,
                "name": sid.lower(),
                "observation_type": otype,
                "total_tokens": tt,
                "prompt_tokens": pt,
                "completion_tokens": ct,
                "cost": cost,
                "status": status,
                "latency_ms": latency,
            }

        span_tree = [
            {
                "observation_span": _obs("R1", "LLM", 10, 6, 4, 0.005, "OK", 1000),
                "children": [
                    {
                        "observation_span": _obs(
                            "C1", "TOOL", 5, 2, 3, None, "ERROR", 250
                        ),
                        "children": [],
                    }
                ],
            },
            {
                "observation_span": _obs("R2", "CHAIN", 0, 0, 0, 0, "OK", None),
                "children": [],
            },
        ]
        with patch(
            "tracer.views.observation_span.get_observation_spans",
            return_value=span_tree,
        ):
            return TraceDetailHandler(view=view, request=MagicMock(), pk="T1").fetch()

    def _v2_rich(self):
        rows = [
            _root_span_row(
                id="R1",
                parent_span_id=None,
                observation_type="LLM",
                total_tokens=10,
                prompt_tokens=6,
                completion_tokens=4,
                cost=0.005,
                status="OK",
                latency_ms=1000,
            ),
            _root_span_row(
                id="C1",
                parent_span_id="R1",
                observation_type="TOOL",
                total_tokens=5,
                prompt_tokens=2,
                completion_tokens=3,
                cost=None,
                status="ERROR",
                latency_ms=250,
            ),
            _root_span_row(
                id="R2",
                parent_span_id=None,
                observation_type="CHAIN",
                total_tokens=0,
                prompt_tokens=0,
                completion_tokens=0,
                cost=0,
                status="OK",
                latency_ms=None,
            ),
        ]
        analytics = _FakeAnalytics(project_rows=[{"project_id": "P1"}], span_rows=rows)
        with ExitStack() as stack:
            _patch_v2_pg(stack, project_accessible=True, pg_trace=None)
            return retrieve_trace_detail_ch(MagicMock(), MagicMock(), "T1", analytics)

    def test_summary_values_match_for_rich_trace(self):
        v1, v2 = self._v1_rich(), self._v2_rich()
        assert v1["summary"] == v2["summary"]
        # spot-check the values the FE renders + the edge cases
        assert v1["summary"]["total_spans"] == 3
        assert v1["summary"]["total_tokens"] == 15
        assert v1["summary"]["total_cost"] == 0.005  # cost=None counts as 0
        assert v1["summary"]["total_duration_ms"] == 1000  # latency=None -> 0
        assert v1["summary"]["error_count"] == 1

    def test_graph_values_match_for_rich_trace(self):
        v1, v2 = self._v1_rich(), self._v2_rich()
        assert (
            {n["id"] for n in v1["graph"]["nodes"]}
            == {n["id"] for n in v2["graph"]["nodes"]}
            == {"R1", "C1", "R2"}
        )
        v1_edges = {(e["from"], e["to"]) for e in v1["graph"]["edges"]}
        v2_edges = {(e["from"], e["to"]) for e in v2["graph"]["edges"]}
        assert v1_edges == v2_edges == {("R1", "C1")}


# --------------------------------------------------------------------------- #
# 4) Eval score mapping
# --------------------------------------------------------------------------- #
class TestV2EvalScoreRendering:
    """Nullable output_float/output_bool -> numeric score; a real 0.0 (0%) float
    score must survive (`is not None`, not truthiness)."""

    @staticmethod
    def _eval_row(**overrides):
        # empty eval_config_id -> skips the CustomEvalConfig lookup (no DB hit)
        row = {
            "span_id": "S1",
            "eval_config_id": "",
            "output_float": None,
            "output_bool": None,
            "output_str": None,
            "eval_explanation": "",
        }
        row.update(overrides)
        return row

    def _scores_for(self, eval_row):
        analytics = _FakeAnalytics(
            project_rows=[{"project_id": "P1"}],
            span_rows=[_root_span_row()],
            eval_rows=[eval_row],
        )
        with ExitStack() as stack:
            _patch_v2_pg(stack, project_accessible=True, pg_trace=None)
            result = retrieve_trace_detail_ch(MagicMock(), MagicMock(), "T1", analytics)
        return result["observation_spans"][0]["eval_scores"]

    def test_zero_float_score_is_kept(self):
        # regression: truthiness check previously dropped this to None
        scores = self._scores_for(self._eval_row(output_float=0.0))
        assert len(scores) == 1 and scores[0]["score"] == 0.0

    def test_nonzero_float_score(self):
        assert self._scores_for(self._eval_row(output_float=0.75))[0]["score"] == 75.0

    def test_bool_false_score(self):
        assert self._scores_for(self._eval_row(output_bool=False))[0]["score"] == 0

    def test_no_score_when_both_null(self):
        assert self._scores_for(self._eval_row())[0]["score"] is None


# --------------------------------------------------------------------------- #
# 5) Enrichment fault logging (loud on genuine faults, silent on dropped table)
# --------------------------------------------------------------------------- #
class TestV2EnrichmentFaultLogging:
    """A genuine PG fault on the Trace lookup surfaces (logged) while the handler
    degrades to root-span synthesis; the expected dropped-table case stays silent."""

    def _run_with_trace_objects(self, trace_objects):
        import tracer.services.clickhouse.v2.query_builders.trace_detail as td

        analytics = _FakeAnalytics(
            project_rows=[{"project_id": "P1"}],
            span_rows=[_root_span_row()],
        )
        logger_mock = MagicMock()
        with ExitStack() as stack:
            _patch_v2_pg(stack, project_accessible=True, pg_trace=None)
            stack.enter_context(patch.object(Trace, "objects", trace_objects))
            stack.enter_context(patch.object(td, "logger", logger_mock))
            result = retrieve_trace_detail_ch(MagicMock(), MagicMock(), "T1", analytics)
        return result, logger_mock

    def test_genuine_fault_is_logged_and_degrades(self):
        objs = MagicMock()
        objs.filter.side_effect = RuntimeError("boom")
        result, logger_mock = self._run_with_trace_objects(objs)
        assert result["trace"]["id"] == "T1"  # synthesized from the root span
        assert logger_mock.exception.called

    def test_dropped_table_is_silent(self):
        from django.db.utils import ProgrammingError

        objs = MagicMock()
        objs.filter.side_effect = ProgrammingError("relation does not exist")
        result, logger_mock = self._run_with_trace_objects(objs)
        assert result["trace"]["id"] == "T1"
        assert not logger_mock.exception.called
