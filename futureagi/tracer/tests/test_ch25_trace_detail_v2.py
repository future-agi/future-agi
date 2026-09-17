"""V2 (ClickHouse) trace-detail handler — unit tests for the CH-only-trace fix.

These cover the behaviour added when routing ``GET /tracer/trace/{id}/`` through
the v1/v2 dispatch (``TRACE_DETAIL``): the ClickHouse tenant gate, the metadata
synthesis for collector-ingested traces that have no Postgres ``Trace`` row, and
the v1/v2 response-envelope parity that keeps the two paths interchangeable for
the frontend.

They are pure unit tests — the ClickHouse ``analytics`` client is faked and the
Postgres managers are patched — so they need no database or CH test stack.
"""

from contextlib import ExitStack
from datetime import UTC, datetime
from inspect import unwrap
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.db.models import Q
from django.test import override_settings

from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.services.clickhouse.query_builders.trace_detail import TraceDetailHandler
from tracer.services.clickhouse.v2.query_builders.trace_detail import (
    TraceDetailHandlerV2,
    retrieve_trace_detail_ch,
)
from tracer.services.clickhouse.v2.trace_detail_reads import (
    PhysicalSpanIdentity,
    TraceDetailNotFound,
    TraceDetailRead,
    TraceDetailReadBuilder,
    TraceDetailReadUnavailable,
    read_span_detail,
    read_trace_detail,
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


@pytest.mark.unit
def test_trace_detail_content_does_not_double_encode_attributes_extra():
    builder = TraceDetailReadBuilder(project_ids=["P1"], trace_id="T1")

    query, _params = builder.build_content_query(
        [
            PhysicalSpanIdentity(
                project_id="P1",
                trace_id="T1",
                span_id="S1",
                start_time=datetime(2026, 1, 1, tzinfo=UTC),
            )
        ]
    )

    assert "latest_attributes_extra AS span_attributes" in query
    assert "toJSONString(latest_attributes_extra)" not in query
    assert "toJSONString(latest_metadata) AS metadata_json" in query


@pytest.mark.unit
@override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger")
def test_trace_detail_eval_query_uses_authoritative_legacy_named_table():
    builder = TraceDetailReadBuilder(project_ids=["P1"], trace_id="T1")

    query, _params = builder.build_eval_query(
        project_id="P1",
        span_ids=["S1"],
        eval_config_ids=["11111111-1111-4111-8111-111111111111"],
    )

    assert "FROM tracer_eval_logger" in query
    assert "FROM tracer_eval_logger_v2" not in query
    assert "argMax(status, _peerdb_version)" in query
    assert "argMax(tuple(skipped_reason), _peerdb_version)" in query
    assert "argMax(_peerdb_is_deleted, _peerdb_version)" in query


class _FakeAnalytics:
    """Stands in for AnalyticsQueryService; routes by the SQL it is handed."""

    def __init__(self, *, project_rows, span_rows=None, eval_rows=None):
        self.project_rows = project_rows
        self.span_rows = span_rows or []
        self.eval_rows = eval_rows or []
        self.queries = []
        self.query_calls = []

    def execute_ch_query(self, query, params=None, timeout_ms=None, **_):
        self.queries.append(query)
        self.query_calls.append((query, params or {}))
        if "latest_trace_evals" in query:
            allowed = {
                str(value) for value in (params or {}).get("detail_eval_config_ids", ())
            }
            return _FakeResult(
                [
                    row
                    for row in self.eval_rows
                    if str(row.get("eval_config_id") or "") in allowed
                ]
            )
        if "AS latest_is_deleted" in query and "AS span_id" in query:
            return _FakeResult(
                [
                    {
                        "project_id": self.project_rows[0]["project_id"],
                        "trace_id": row["trace_id"],
                        "span_id": row["id"],
                        "start_time": row["start_time"],
                        "latest_is_deleted": 0,
                    }
                    for row in self.span_rows
                ]
                if self.project_rows
                else []
            )
        if "latest_physical_spans" in query:
            return _FakeResult(list(self.span_rows))
        return _FakeResult([])


class _SequenceAnalytics:
    """Return explicit per-query rows so the span-anchor phases stay visible."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.query_calls = []

    def execute_ch_query(self, query, params=None, timeout_ms=None, **kwargs):
        self.query_calls.append((query, params or {}, timeout_ms, kwargs))
        if not self.responses:
            raise AssertionError("unexpected ClickHouse query")
        return _FakeResult(list(self.responses.pop(0)))


def _root_span_row(**overrides):
    row = {
        "id": "S1",
        "trace_id": "T1",
        "parent_span_id": None,
        "name": "root-span",
        "observation_type": "CHAIN",
        "start_time": datetime(2026, 1, 1, tzinfo=UTC),
        "end_time": datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
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


def _patch_v2_pg(
    stack,
    *,
    project_accessible,
    pg_trace=None,
    eval_configs=(),
    eval_config_error=None,
):
    """Patch the Postgres surfaces the v2 handler touches and return nothing.

    - Project tenant gate -> ``project_accessible``
    - ``Trace.objects.filter().first()`` -> ``pg_trace`` (None == CH-only trace)
    - scope-Q builder, eval-logger source, and the best-effort enrichment
      managers (Score / ObservationSpan) -> empty, so no DB is touched.
    """
    proj_mgr = MagicMock()
    project_qs = proj_mgr.filter.return_value
    project_qs.values_list.return_value.__getitem__.return_value = (
        ["P1"] if project_accessible else []
    )
    stack.enter_context(patch.object(Project, "no_workspace_objects", proj_mgr))

    trace_mgr = MagicMock()
    trace_mgr.filter.return_value.first.return_value = pg_trace
    stack.enter_context(patch.object(Trace, "objects", trace_mgr))

    config_mgr = MagicMock()
    if eval_config_error is not None:
        config_mgr.filter.side_effect = eval_config_error
    else:
        config_qs = config_mgr.filter.return_value.select_related.return_value
        config_qs.__getitem__.return_value = list(eval_configs)
    stack.enter_context(patch.object(CustomEvalConfig, "objects", config_mgr))

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
            "tracer.services.clickhouse.v2.trace_detail_reads.eval_logger_source",
            lambda *args, **kwargs: ("tracer_eval_logger_v2", "is_deleted = 0"),
        )
    )
    return config_mgr


# --------------------------------------------------------------------------- #
# 1) Trace-detail dispatch uses the direct-write CH25 client
# --------------------------------------------------------------------------- #
@override_settings(
    CLICKHOUSE={
        "CH_HOST": "legacy-clickhouse.invalid",
        "CH_PORT": 9000,
        "CH_USERNAME": "default",
        "CH_PASSWORD": "",
        "CH_DATABASE": "legacy",
    },
    CLICKHOUSE_V2={
        "CH25_HOST": "direct-write-clickhouse.invalid",
        "CH25_TCP_PORT": 19000,
        "CH25_USER": "default",
        "CH25_PASSWORD": "",
        "CH25_DATABASE": "direct_write",
        "QUERY_TYPES_V2_ONLY": "TRACE_DETAIL",
    },
)
def test_trace_view_retrieve_routes_v2_detail_to_split_ch25_host():
    from tracer.services.clickhouse.v2.query_service import (
        V2AnalyticsQueryService,
        reset_v2_query_client,
    )
    from tracer.views.trace import TraceView

    captured = {}

    def _fetch(handler):
        captured["analytics"] = handler.analytics
        return {"trace": {"id": "T1"}}

    reset_v2_query_client()
    try:
        with patch.object(TraceDetailHandlerV2, "fetch", _fetch):
            response = TraceView().retrieve(MagicMock(), pk="T1")
        analytics = captured["analytics"]
        assert response.status_code == 200
        assert isinstance(analytics, V2AnalyticsQueryService)
        assert analytics.ch_client.host == "direct-write-clickhouse.invalid"
        assert analytics.ch_client.database == "direct_write"
        assert analytics.ch_client.host != "legacy-clickhouse.invalid"
    finally:
        reset_v2_query_client()


# --------------------------------------------------------------------------- #
# 2) ClickHouse tenant gate
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

    def test_span_sentinel_fails_closed_before_content_hydration(self):
        started = datetime(2026, 1, 1, tzinfo=UTC)
        span_rows = [
            _root_span_row(id=f"S{index}", start_time=started) for index in range(1001)
        ]
        analytics = _FakeAnalytics(
            project_rows=[{"project_id": "P1"}], span_rows=span_rows
        )

        with pytest.raises(TraceDetailReadUnavailable, match="span_limit_exceeded"):
            read_trace_detail(
                analytics=analytics,
                project_ids=["P1"],
                trace_id="T1",
                deadline_ms=1000,
            )

        assert len(analytics.queries) == 1
        assert "LIMIT 1001" in analytics.queries[0]

    def test_cross_project_trace_collision_fails_before_content_hydration(self):
        started = datetime(2026, 1, 1, tzinfo=UTC)
        identity_rows = [
            {
                "project_id": project_id,
                "trace_id": "T1",
                "span_id": f"root-{project_id}",
                "start_time": started,
                "latest_is_deleted": 0,
            }
            for project_id in ("P1", "P2")
        ]
        analytics = _SequenceAnalytics([identity_rows])

        with pytest.raises(
            TraceDetailReadUnavailable, match="ambiguous_trace_identity"
        ):
            read_trace_detail(
                analytics=analytics,
                project_ids=["P1", "P2"],
                trace_id="T1",
                deadline_ms=1000,
            )

        assert len(analytics.query_calls) == 1

    def test_trace_latest_tombstone_is_not_resurrected(self):
        analytics = _SequenceAnalytics(
            [
                [
                    {
                        "project_id": "P1",
                        "trace_id": "T1",
                        "span_id": "S1",
                        "start_time": datetime(2026, 1, 1, tzinfo=UTC),
                        "latest_is_deleted": 1,
                    }
                ]
            ]
        )

        with pytest.raises(TraceDetailNotFound):
            read_trace_detail(
                analytics=analytics,
                project_ids=["P1"],
                trace_id="T1",
                deadline_ms=1000,
            )

        assert len(analytics.query_calls) == 1
        assert "argMax(is_deleted, _version)" in analytics.query_calls[0][0]

    def test_all_trace_detail_queries_share_one_wall_deadline(self):
        started = datetime(2026, 1, 1, tzinfo=UTC)
        identity = {
            "project_id": "P1",
            "trace_id": "T1",
            "span_id": "S1",
            "start_time": started,
            "latest_is_deleted": 0,
        }
        analytics = _SequenceAnalytics(
            [
                [identity],
                [_root_span_row(project_id="P1", start_time=started)],
                [],
            ]
        )
        ticks = iter((0.0, 0.1, 0.5, 0.9, 1.0))

        with patch(
            "tracer.services.clickhouse.v2.trace_detail_reads.monotonic",
            side_effect=lambda: next(ticks),
        ):
            detail = read_trace_detail(
                analytics=analytics,
                project_ids=["P1"],
                trace_id="T1",
                eval_config_ids_resolver=lambda _project_id: [],
                deadline_ms=1000,
            )

        timeouts = [call[2] for call in analytics.query_calls]
        assert detail.query_count == 3
        assert timeouts == sorted(timeouts, reverse=True)
        assert 890 <= timeouts[0] <= 900
        assert 490 <= timeouts[1] <= 500
        assert 90 <= timeouts[2] <= 100


class TestDirectSpanDetailAnchor:
    @staticmethod
    def _anchor_row(*, project_id="P1", deleted=0):
        return {
            "project_id": project_id,
            "trace_id": f"T-{project_id}",
            "span_id": "S1",
            "start_time": datetime(2026, 1, 1, tzinfo=UTC),
            "latest_is_deleted": deleted,
        }

    def test_latest_tombstone_is_not_resurrected(self):
        analytics = _SequenceAnalytics([[self._anchor_row(deleted=1)]])

        with pytest.raises(TraceDetailNotFound):
            read_span_detail(
                analytics=analytics,
                project_ids=["P1"],
                span_id="S1",
                deadline_ms=1000,
            )

        assert len(analytics.query_calls) == 1
        query, params, *_ = analytics.query_calls[0]
        assert "argMax(is_deleted, _version)" in query
        assert params["detail_project_ids"] == ("P1",)

    def test_cross_project_live_collision_fails_before_hydration(self):
        analytics = _SequenceAnalytics(
            [[self._anchor_row(project_id="P1"), self._anchor_row(project_id="P2")]]
        )

        with pytest.raises(TraceDetailReadUnavailable, match="ambiguous_span_identity"):
            read_span_detail(
                analytics=analytics,
                project_ids=["P1", "P2"],
                span_id="S1",
                deadline_ms=1000,
            )

        assert len(analytics.query_calls) == 1

    def test_one_live_anchor_replays_only_its_project_and_trace(self):
        anchor = self._anchor_row(project_id="P1")
        identity = dict(anchor)
        content = _root_span_row(project_id="P1", trace_id="T-P1")
        analytics = _SequenceAnalytics(
            [
                [anchor, self._anchor_row(project_id="P2", deleted=1)],
                [identity],
                [content],
                [],
            ]
        )

        detail = read_span_detail(
            analytics=analytics,
            project_ids=["P1", "P2"],
            span_id="S1",
            deadline_ms=1000,
        )

        assert detail.project_id == "P1"
        assert [row["id"] for row in detail.spans] == ["S1"]
        identity_params = analytics.query_calls[1][1]
        assert identity_params["detail_project_ids"] == ("P1",)
        assert identity_params["detail_trace_id"] == "T-P1"

    def test_annotation_read_can_be_omitted_for_span_only_response(self):
        anchor = self._anchor_row(project_id="P1")
        identity = dict(anchor)
        content = _root_span_row(project_id="P1", trace_id="T-P1")
        analytics = _SequenceAnalytics([[anchor], [identity], [content]])

        detail = read_span_detail(
            analytics=analytics,
            project_ids=["P1"],
            span_id="S1",
            include_annotations=False,
            deadline_ms=1000,
        )

        assert detail.annotations == ()
        assert detail.query_count == 2
        assert len(analytics.query_calls) == 3  # anchor + identity + content
        assert all("model_hub_score" not in call[0] for call in analytics.query_calls)


@pytest.mark.unit
def test_span_retrieve_does_not_require_a_postgres_span_row():
    from tracer.views.observation_span import ObservationSpanView

    row = _root_span_row(project_id="P1")
    detail = TraceDetailRead(
        project_id="P1",
        spans=(row,),
        eval_config_ids=(),
        evals=(),
        annotations=(),
        query_count=3,
        elapsed_ms=0.0,
    )
    project_manager = MagicMock()
    project_manager.filter.return_value.values_list.return_value.__getitem__.return_value = [
        "P1"
    ]
    request = SimpleNamespace(
        organization=SimpleNamespace(id="ORG1"),
        workspace=None,
        user=SimpleNamespace(organization=SimpleNamespace(id="ORG1")),
    )

    with (
        patch.object(Project, "no_workspace_objects", project_manager),
        patch("tracer.views.observation_span.V2AnalyticsQueryService"),
        patch(
            "tracer.services.clickhouse.v2.trace_detail_reads.read_span_detail",
            return_value=detail,
        ) as selector,
        patch.object(
            ObservationSpan.objects,
            "get",
            side_effect=AssertionError("Postgres span lookup is forbidden"),
        ),
    ):
        response = unwrap(ObservationSpanView.retrieve)(
            ObservationSpanView(), request, pk="S1"
        )

    assert response.status_code == 200
    assert response.data["result"]["observation_span"]["id"] == "S1"
    assert selector.call_args.kwargs["project_ids"] == ["P1"]


@pytest.mark.unit
def test_span_retrieve_unions_typed_maps_with_nonempty_structured_extra():
    from tracer.views.observation_span import ObservationSpanView

    row = _root_span_row(project_id="P1")
    row.update(
        span_attributes='{"dupe":"from-extra","structured":{"attempt":2}}',
        attrs_string={"final_status": "Rechazado", "dupe": "from-map"},
        attrs_number={"score": 12.0},
        attrs_bool={"approved": 1},
    )
    detail = TraceDetailRead(
        project_id="P1",
        spans=(row,),
        eval_config_ids=(),
        evals=(),
        annotations=(),
        query_count=3,
        elapsed_ms=0.0,
    )
    project_manager = MagicMock()
    project_manager.filter.return_value.values_list.return_value.__getitem__.return_value = [
        "P1"
    ]
    request = SimpleNamespace(
        organization=SimpleNamespace(id="ORG1"),
        workspace=None,
        user=SimpleNamespace(organization=SimpleNamespace(id="ORG1")),
    )

    with (
        patch.object(Project, "no_workspace_objects", project_manager),
        patch("tracer.views.observation_span.V2AnalyticsQueryService"),
        patch(
            "tracer.services.clickhouse.v2.trace_detail_reads.read_span_detail",
            return_value=detail,
        ),
    ):
        response = unwrap(ObservationSpanView.retrieve)(
            ObservationSpanView(), request, pk="S1"
        )

    attrs = response.data["result"]["observation_span"]["span_attributes"]
    assert attrs["structured"] == {"attempt": 2}
    assert attrs["final_status"] == "Rechazado"
    assert attrs["score"] == 12.0
    assert attrs["approved"] is True
    assert attrs["dupe"] == "from-extra"


@pytest.mark.unit
def test_eval_detail_keeps_organization_gate_without_workspace_context():
    from tracer.views.observation_span import ObservationSpanView

    organization = SimpleNamespace(id="ORG1")
    request = SimpleNamespace(
        query_params={
            "observation_span_id": "S1",
            "custom_eval_config_id": "C1",
        },
        organization=organization,
        workspace=None,
        user=SimpleNamespace(organization=organization),
    )
    config_manager = MagicMock()
    config_manager.filter.return_value.values.return_value.first.return_value = None
    view = ObservationSpanView()
    view.request = request

    with patch(
        "tracer.views.observation_span.CustomEvalConfig.no_workspace_objects",
        config_manager,
    ):
        response = unwrap(ObservationSpanView.get_evaluation_details)(view, request)

    assert response.status_code == 400
    _, filter_kwargs = config_manager.filter.call_args
    assert filter_kwargs["project__organization"] is organization


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
# 2a) input/output parsing: JSON parses to objects; plaintext is preserved
# --------------------------------------------------------------------------- #
class TestV2InputOutputParsing:
    """Regression: bare plaintext input/output (e.g. voice transcripts) used to
    be dropped to {} by json.loads; it must be preserved as the raw string."""

    def _span(self, **row_overrides):
        analytics = _FakeAnalytics(
            project_rows=[{"project_id": "P1"}],
            span_rows=[_root_span_row(**row_overrides)],
        )
        with ExitStack() as stack:
            _patch_v2_pg(stack, project_accessible=True, pg_trace=None)
            result = retrieve_trace_detail_ch(MagicMock(), MagicMock(), "T1", analytics)
        return result

    def test_json_input_output_parsed_to_objects(self):
        result = self._span(input='{"q": "hi"}', output='{"a": "yo"}')
        span = result["observation_spans"][0]["observation_span"]
        assert span["input"] == {"q": "hi"}
        assert span["output"] == {"a": "yo"}

    def test_plaintext_input_output_preserved(self):
        text_in = "What's on my calendar this afternoon?"
        text_out = "You have a 3pm meeting."
        result = self._span(input=text_in, output=text_out)
        span = result["observation_spans"][0]["observation_span"]
        assert span["input"] == text_in  # not {}
        assert span["output"] == text_out
        # synthesized trace envelope inherits the same raw text
        assert result["trace"]["input"] == text_in
        assert result["trace"]["output"] == text_out


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
        row = {
            "span_id": "S1",
            "eval_config_id": "C1",
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
        config = SimpleNamespace(
            id="C1",
            name="quality",
            eval_template=None,
        )
        with ExitStack() as stack:
            _patch_v2_pg(
                stack,
                project_accessible=True,
                pg_trace=None,
                eval_configs=[config],
            )
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

    def test_project_scopes_configs_and_omits_foreign_collision(self):
        own_config = SimpleNamespace(
            id="C1",
            name="own-quality",
            eval_template=None,
        )
        analytics = _FakeAnalytics(
            project_rows=[{"project_id": "P1"}],
            span_rows=[_root_span_row(custom_eval_config_id="C2")],
            eval_rows=[
                self._eval_row(eval_config_id="C1", output_float=0.8),
                self._eval_row(eval_config_id="C2", output_float=0.1),
            ],
        )
        with ExitStack() as stack:
            config_mgr = _patch_v2_pg(
                stack,
                project_accessible=True,
                pg_trace=None,
                eval_configs=[own_config],
            )
            result = retrieve_trace_detail_ch(MagicMock(), MagicMock(), "T1", analytics)

        span = result["observation_spans"][0]
        assert [score["eval_config_id"] for score in span["eval_scores"]] == ["C1"]
        assert span["observation_span"]["custom_eval_config"] is None
        config_mgr.filter.assert_called_once_with(project_id="P1", deleted=False)
        eval_query, eval_call = next(
            (query, params)
            for query, params in analytics.query_calls
            if "latest_trace_evals" in query
        )
        assert eval_call["detail_eval_config_ids"] == ("C1",)
        assert "toString(custom_eval_config_id)" in eval_query.split("GROUP BY id")[0]

    def test_no_project_configs_skips_eval_query_and_returns_empty(self):
        analytics = _FakeAnalytics(
            project_rows=[{"project_id": "P1"}],
            span_rows=[_root_span_row(custom_eval_config_id="C2")],
            eval_rows=[self._eval_row(eval_config_id="C2", output_float=0.1)],
        )
        with ExitStack() as stack:
            _patch_v2_pg(stack, project_accessible=True, pg_trace=None)
            result = retrieve_trace_detail_ch(MagicMock(), MagicMock(), "T1", analytics)

        span = result["observation_spans"][0]
        assert span["eval_scores"] == []
        assert span["observation_span"]["custom_eval_config"] is None
        assert not any("latest_trace_evals" in query for query in analytics.queries)

    def _scores_for_type(self, output_type, eval_row, config=None):
        """Config resolves to an eval of ``output_type`` (via derive_output_type).
        Pass ``config`` (e.g. {"output": "Pass/Fail"}) with output_type=None to
        exercise the config["output"] fallback when output_type_normalized is unset."""
        analytics = _FakeAnalytics(
            project_rows=[{"project_id": "P1"}],
            span_rows=[_root_span_row()],
            eval_rows=[eval_row],
        )
        cfg = SimpleNamespace(
            id="C1",
            name="e",
            eval_template=SimpleNamespace(
                output_type_normalized=output_type,
                name="e",
                template_type="single",
                config=config or {},
            ),
        )
        with ExitStack() as stack:
            _patch_v2_pg(
                stack,
                project_accessible=True,
                pg_trace=None,
                eval_configs=[cfg],
            )
            result = retrieve_trace_detail_ch(MagicMock(), MagicMock(), "T1", analytics)
        return result["observation_spans"][0]["eval_scores"]

    def test_pass_fail_pass_with_coerced_zero_float(self):
        # Verdict in output_bool, output_float coerced to 0 → 100 (Pass), not 0.
        row = self._eval_row(eval_config_id="C1", output_bool=True, output_float=0.0)
        assert self._scores_for_type("pass_fail", row)[0]["score"] == 100

    def test_pass_fail_fail_with_coerced_zero_float(self):
        row = self._eval_row(eval_config_id="C1", output_bool=False, output_float=0.0)
        assert self._scores_for_type("pass_fail", row)[0]["score"] == 0

    def test_percentage_uses_float_not_coerced_bool(self):
        # percentage → output_float; the coerced output_bool must be ignored.
        row = self._eval_row(eval_config_id="C1", output_float=0.6, output_bool=True)
        assert self._scores_for_type("percentage", row)[0]["score"] == 60.0

    def test_choices_single_surfaces_label_not_zero(self):
        # Choices: value in output_str_list; float/bool coerced to 0. Must show
        # the option as score_label with score None (not 0% / a fake Fail).
        row = self._eval_row(
            output_str_list='["neutral"]', output_bool=False, output_float=0.0
        )
        e = self._scores_for(row)[0]
        assert e["score"] is None
        assert e["score_items"] == ["neutral"]
        assert e["score_label"] == "neutral"

    def test_choices_multiselect_lists_each_option(self):
        row = self._eval_row(
            output_str_list='["neutral","formal"]', output_bool=False, output_float=0.0
        )
        e = self._scores_for(row)[0]
        assert e["score"] is None
        assert e["score_items"] == ["neutral", "formal"]
        assert e["score_label"] == "neutral, formal"

    def test_pass_fail_with_real_float_uses_bool_not_float(self):
        # Deterministic evaluator writes a bool verdict AND a float; Pass/Fail
        # must score from the bool (100), not the float (85).
        row = self._eval_row(eval_config_id="C1", output_bool=True, output_float=0.85)
        assert self._scores_for_type("pass_fail", row)[0]["score"] == 100

    def test_pass_fail_routed_via_config_output_when_normalized_null(self):
        # output_type_normalized unset → derive_output_type falls back to
        # config["output"], so a passing Pass/Fail still scores 100, not 0.
        row = self._eval_row(eval_config_id="C1", output_bool=True, output_float=0.0)
        e = self._scores_for_type(None, row, config={"output": "Pass/Fail"})[0]
        assert e["score"] == 100

    def test_errored_row_nulls_all_derived_fields(self):
        row = self._eval_row(
            output_str_list='["neutral"]', output_bool=True, error=True
        )
        e = self._scores_for(row)[0]
        assert e["score"] is None
        assert e["score_items"] is None
        assert e["score_label"] is None
        assert e["result"] is None

    def test_skipped_row_nulls_all_derived_fields(self):
        row = self._eval_row(
            output_str_list='["neutral"]', output_bool=True, status="skipped"
        )
        e = self._scores_for(row)[0]
        assert e["score"] is None
        assert e["score_items"] is None
        assert e["result"] is None


# --------------------------------------------------------------------------- #
# 5) Enrichment fault logging (loud on genuine faults, silent on dropped table)
# --------------------------------------------------------------------------- #
class TestV2EnrichmentFaultLogging:
    """Only the documented dropped-table transition may synthesize metadata."""

    def _run_with_trace_objects(self, trace_objects, logger_mock=None):
        import tracer.services.clickhouse.v2.query_builders.trace_detail as td

        analytics = _FakeAnalytics(
            project_rows=[{"project_id": "P1"}],
            span_rows=[_root_span_row()],
        )
        logger_mock = logger_mock or MagicMock()
        with ExitStack() as stack:
            _patch_v2_pg(stack, project_accessible=True, pg_trace=None)
            stack.enter_context(patch.object(Trace, "objects", trace_objects))
            stack.enter_context(patch.object(td, "logger", logger_mock))
            result = retrieve_trace_detail_ch(MagicMock(), MagicMock(), "T1", analytics)
        return result, logger_mock

    def test_genuine_fault_is_logged_and_propagates(self):
        objs = MagicMock()
        objs.filter.side_effect = RuntimeError("boom")
        logger_mock = MagicMock()
        with pytest.raises(RuntimeError, match="boom"):
            self._run_with_trace_objects(objs, logger_mock)
        assert logger_mock.exception.called

    def test_eval_config_lookup_fault_propagates(self):
        analytics = _FakeAnalytics(
            project_rows=[{"project_id": "P1"}],
            span_rows=[_root_span_row()],
        )
        with ExitStack() as stack:
            _patch_v2_pg(
                stack,
                project_accessible=True,
                pg_trace=None,
                eval_config_error=RuntimeError("eval config lookup failed"),
            )
            with pytest.raises(RuntimeError, match="eval config lookup failed"):
                retrieve_trace_detail_ch(MagicMock(), MagicMock(), "T1", analytics)

    def test_dropped_table_is_silent(self):
        from django.db.utils import ProgrammingError

        objs = MagicMock()
        objs.filter.side_effect = ProgrammingError("relation does not exist")
        result, logger_mock = self._run_with_trace_objects(objs)
        assert result["trace"]["id"] == "T1"
        assert not logger_mock.exception.called
