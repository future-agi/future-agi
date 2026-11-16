"""Model-free native cost context regressions; integration cases use owned CH25."""

import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import create_autospec
from uuid import uuid4

import pytest

import model_hub.tasks  # noqa: F401 -- break the existing eval/task import cycle
from tracer.models.trace import Trace
from tracer.serializers.usage_coverage import CostCoverageSerializer
from tracer.services.clickhouse.query_builders.trace_detail import (
    compute_trace_summary_and_graph,
)
from tracer.services.clickhouse.v2.span_reader import (
    _DATA_KEYS,
    CHSpanReader,
    _row_to_chspan,
)
from tracer.tests._copilot_usage_capture import captured_usage_rows, usage_tree
from tracer.utils.eval import (
    build_session_context,
    build_span_context,
    build_trace_context,
)
from tracer.utils.normalization import normalize_attributes

START = datetime(2026, 10, 20, 12, tzinfo=UTC)
SCOPE = {"name": "github.copilot", "version": "1.0.98"}
NATIVE = normalize_attributes({"gen_ai.operation.name": "chat"}, SCOPE)
# Stored compact flags after native pricing qualification; normalize_attributes
# alone only identifies the producer/operation, it does not assign cost kinds.
UNKNOWN = {**NATIVE, "fi.cost.kind": "unknown"}
KNOWN = {**NATIVE, "fi.cost.kind": "provider_reported"}
ZERO = {**KNOWN}  # genuine USD zero uses the same kind, never "unknown"


def _span(**overrides):
    # Use the real row decoder: native unknown starts as numeric zero in CH,
    # then becomes None at this exact boundary. No fake monetary coercion.
    row = dict.fromkeys(_DATA_KEYS, "")
    row.update(
        id=str(uuid4()),
        project_id="project-1",
        trace_id="trace-1",
        trace_session_id="session-1",
        name="chat",
        observation_type="llm",
        start_time=START,
        end_time=START + timedelta(seconds=1),
        latency_ms=1000,
        total_tokens=120,
        prompt_tokens=100,
        completion_tokens=20,
        cost=0.0,
        attrs_string={},
        attrs_number={},
        attrs_bool={},
        is_deleted=0,
        streaming=None,
        temperature=None,
        top_p=None,
        max_tokens=None,
        metadata="{}",
        resource_attrs="{}",
        attributes_extra="{}",
        tags="[]",
        span_events="[]",
    )
    row.update(overrides)
    return _row_to_chspan(tuple(row[key] for key in _DATA_KEYS))


class _TraceQuery:
    def __init__(self, traces=()):
        self.traces = list(traces)

    def order_by(self, *_args):
        return self

    def __getitem__(self, key):
        return self.traces[key]

    def count(self):
        return len(self.traces)


def _context(monkeypatch, spans):
    trace_ids = list(dict.fromkeys(span.trace_id for span in spans))
    # Spec the REAL backend so a renamed/nonexistent reader method fails tests.
    reader = create_autospec(CHSpanReader, instance=True)
    reader.__enter__.return_value = reader
    reader.session_trace_ids.return_value = trace_ids
    reader.list_by_session.return_value = spans
    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", lambda: reader)

    def scoped_traces(**kwargs):
        assert kwargs == {
            "project_id": "project-1",
            "id__in": trace_ids,
            "deleted": False,
        }
        return _TraceQuery(
            SimpleNamespace(id=tid, name=tid, created_at=START, error=False)
            for tid in trace_ids
        )

    monkeypatch.setattr(Trace.objects, "filter", scoped_traces)
    result = build_session_context(
        SimpleNamespace(
            id="session-1",
            project_id="project-1",
            name="session",
            bookmarked=False,
            created_at=START,
        )
    )
    assert result is not None
    reader.session_trace_ids.assert_called_once_with("project-1", "session-1")
    reader.list_by_session.assert_called_once_with("session-1", project_id="project-1")
    return result


def _assert_coverage(context, status, subtotal, known, unknown):
    assert context["cost_coverage"] == {
        "status": status,
        "known_subtotal": subtotal,
        "known_fact_count": known,
        "unknown_fact_count": unknown,
    }
    # Pin the shared typed contract, including numeric/non-null fields.
    serializer = CostCoverageSerializer(data=context["cost_coverage"])
    assert serializer.is_valid(), serializer.errors
    json.dumps(context, allow_nan=False)


@pytest.mark.unit
def test_mixed_native_costs_across_traces_are_not_a_complete_total(monkeypatch):
    unknown = _span(trace_id="trace-2", attrs_string=UNKNOWN)
    assert unknown.cost is None
    context = _context(
        monkeypatch,
        [
            _span(cost=1.25, attrs_string=KNOWN),
            unknown,
            _span(trace_id="trace-2", attrs_string=UNKNOWN),
        ],
    )
    assert context["total_cost"] == 1.25  # numeric compatibility amount
    _assert_coverage(context, "partial", 1.25, 1, 2)
    _assert_coverage(context["traces"][0], "complete", 1.25, 1, 0)
    _assert_coverage(context["traces"][1], "unknown", 0.0, 0, 2)
    assert "NOT a complete total" in context["cost_note"]
    assert "Unknown price is not free" in json.dumps(context)


@pytest.mark.unit
def test_session_explorer_summary_keeps_incomplete_price_warning(monkeypatch):
    from ai_tools.base import ToolContext
    from ai_tools.tools.web.trace_explorer import (
        TraceExploreInput,
        TraceExplorerTool,
        clear_context_data,
        load_context_data,
    )

    context = _context(
        monkeypatch,
        [
            _span(cost=1.25, attrs_string=KNOWN),
            _span(attrs_string=UNKNOWN),
        ],
    )
    eval_id = str(uuid4())
    load_context_data(eval_id, "session", context)
    try:
        result = TraceExplorerTool().execute(
            TraceExploreInput(eval_id=eval_id, root="session", action="summary"),
            ToolContext(user=None, organization=None, workspace=None),
        )
        assert not result.is_error
        assert "NOT a complete total" in result.content
        assert "cost_coverage" in result.content
    finally:
        clear_context_data(eval_id)


@pytest.mark.unit
@pytest.mark.parametrize(
    "attrs,cost,status,known,unknown",
    [
        (UNKNOWN, 0.0, "unknown", 0, 1),
        (ZERO, 0.0, "complete", 1, 0),
        ({}, 0.0, "complete", 1, 0),
        ({}, None, "complete", 1, 0),  # legacy None retains its old numeric meaning
        ({}, 1.25, "complete", 1, 0),
    ],
)
def test_native_and_plain_legacy_prices(
    monkeypatch, attrs, cost, status, known, unknown
):
    context = _context(monkeypatch, [_span(attrs_string=attrs, cost=cost)])
    assert context["total_cost"] == (cost or 0.0)
    _assert_coverage(context, status, cost or 0.0, known, unknown)


@pytest.mark.unit
@pytest.mark.parametrize("attrs", [ZERO, {}], ids=["explicit-zero", "legacy-zero"])
def test_zero_plus_unknown_is_partial_not_free(monkeypatch, attrs):
    context = _context(
        monkeypatch, [_span(attrs_string=attrs), _span(attrs_string=UNKNOWN)]
    )
    assert context["total_cost"] == 0
    _assert_coverage(context, "partial", 0.0, 1, 1)


@pytest.mark.unit
@pytest.mark.parametrize(
    "overrides,known,unknown",
    [
        ({"observation_type": "tool"}, 0, 0),
        ({"observation_type": "chain"}, 0, 0),
        ({"observation_type": "agent"}, 0, 0),
        ({"observation_type": "llm"}, 0, 1),
        ({"observation_type": "tool", "prompt_tokens": 100}, 0, 1),
        ({"observation_type": "chain", "completion_tokens": 20}, 0, 1),
        ({"observation_type": "agent", "total_tokens": 120}, 0, 1),
        (
            {
                "observation_type": "tool",
                "attrs_string": {**UNKNOWN, "fi.usage.scope": "turn"},
            },
            0,
            1,
        ),
        ({"observation_type": "tool", "attrs_string": ZERO}, 1, 0),
        ({"observation_type": "tool", "attrs_string": {}, "cost": 0.25}, 1, 0),
    ],
)
def test_only_additive_monetary_activity_counts(monkeypatch, overrides, known, unknown):
    span = _span(
        **{
            "total_tokens": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "attrs_string": UNKNOWN,
            **overrides,
        }
    )
    context = _context(monkeypatch, [span])
    _assert_coverage(
        context, "unknown" if unknown else "complete", span.cost or 0.0, known, unknown
    )


@pytest.mark.unit
@pytest.mark.parametrize("with_child", [False, True])
@pytest.mark.parametrize("cost_kind", ["provider_reported", "unknown"])
def test_parent_summary_never_becomes_observed_usage(
    monkeypatch, with_child, cost_kind
):
    spans = [
        _span(
            id="parent",
            name="invoke_agent",
            observation_type="agent",
            cost=17,
            total_tokens=43071,
            attrs_string={
                "fi.usage.contribution": "none",
                "fi.cost.kind": cost_kind,
                "fi.usage.scope": "turn",
                "fi.source.summary.gen_ai.cost.total": "17",
            },
        )
    ]
    if with_child:
        spans.append(_span(parent_span_id="parent", cost=1.25, attrs_string=KNOWN))
    context = _context(monkeypatch, spans)
    _assert_coverage(
        context, "complete", 1.25 if with_child else 0.0, int(with_child), 0
    )
    assert context["total_tokens"] == (120 if with_child else 0)
    assert context["total_spans"] == len(spans)
    assert (
        "monetary coverage does not prove capture completeness" in context["cost_note"]
    )


@pytest.mark.unit
def test_repeated_pydantic_parent_is_excluded_not_equal_valued_siblings(monkeypatch):
    context = _context(
        monkeypatch,
        [
            _span(
                id="parent",
                name="pydantic_ai.agent.run",
                observation_type="agent",
                cost=2.5,
                total_tokens=240,
                prompt_tokens=200,
                completion_tokens=40,
            ),
            _span(parent_span_id="parent", cost=1.25),
            _span(parent_span_id="parent", cost=1.25),
            # Same parent id in another trace must NOT affect summary detection.
            _span(trace_id="trace-2", parent_span_id="parent", attrs_string=UNKNOWN),
        ],
    )
    assert context["total_cost"] == 2.5
    assert context["total_tokens"] == 360
    _assert_coverage(context, "partial", 2.5, 2, 1)
    _assert_coverage(context["traces"][0], "complete", 2.5, 2, 0)


@pytest.mark.unit
@pytest.mark.parametrize("parent_only", [False, True])
def test_genai_context_proves_descendants_within_each_trace(monkeypatch, parent_only):
    spans = [
        _span(
            id="parent",
            observation_type="agent",
            cost=1.25,
            attrs_string={"gen_ai.operation.name": "invoke_agent"},
        ),
        _span(
            id="loop",
            parent_span_id="parent",
            observation_type="chain",
            total_tokens=0,
            prompt_tokens=0,
            completion_tokens=0,
            cost=0,
        ),
        # Matching IDs in another trace must not prove this summary.
        _span(trace_id="trace-2", parent_span_id="loop", cost=1.25),
    ]
    if not parent_only:
        spans.append(_span(parent_span_id="loop", cost=1.25))
    context = _context(monkeypatch, spans)
    assert context["total_tokens"] == 240
    assert context["total_cost"] == 2.5
    _assert_coverage(context, "complete", 2.5, 2, 0)


@pytest.mark.unit
@pytest.mark.parametrize(
    "shape,expected",
    [
        ("legacy-capture", 88073),
        ("normalized-capture", 88073),
        ("retried-capture", 88073),
        ("parent-only", 120),
        ("unequal", 241),
        ("independent", 240),
        ("pydantic", 120),
        ("explicit-nonadditive", 120),
    ],
)
def test_trace_session_and_detail_token_parity(monkeypatch, shape, expected):
    if shape.endswith("capture"):
        legacy = captured_usage_rows()
        normalized = captured_usage_rows(normalized=True)
        if shape == "retried-capture":
            # FINAL retains both kinds, while unchanged physical keys replace.
            live = {(row["id"], row["observation_type"]): row for row in legacy}
            live.update(
                {(row["id"], row["observation_type"]): row for row in normalized}
            )
            rows = list(live.values())
            assert len(rows) == 164
        else:
            rows = legacy if shape == "legacy-capture" else normalized
            assert len(rows) == 91
        spans = [_span(**row) for row in rows]
    else:
        attrs = {"gen_ai.operation.name": "invoke_agent"}
        if shape == "explicit-nonadditive":
            attrs["fi.usage.contribution"] = "none"
        spans = [
            _span(
                id="parent",
                name="pydantic_ai.agent.run" if shape == "pydantic" else "invoke_agent",
                observation_type="agent",
                attrs_string=attrs,
                total_tokens=121 if shape == "unequal" else 120,
            )
        ]
        if shape != "parent-only":
            spans.append(
                _span(parent_span_id="" if shape == "independent" else "parent")
            )
    session = _context(monkeypatch, spans)
    from tracer.services.clickhouse.v2 import get_reader

    reader = get_reader()
    reader.list_by_trace.return_value = spans
    trace = build_trace_context(
        SimpleNamespace(id="trace-1", project_id="project-1"), anchor_span_id="anchor"
    )
    detail, graph = compute_trace_summary_and_graph(
        usage_tree([vars(span) for span in spans])
    )
    assert (
        trace["total_tokens"]
        == session["total_tokens"]
        == detail["total_tokens"]
        == expected
    )
    assert session["traces"][0]["total_tokens"] == expected
    assert (
        trace["span_count"]
        == session["total_spans"]
        == detail["total_spans"]
        == len(spans)
    )
    assert len(trace["spans"]) == len(graph["nodes"]) == len(spans)
    assert [span["id"] for span in trace["spans"]] == [span.id for span in spans]
    assert trace["span_id"] == "anchor"
    reader.list_by_trace.assert_called_once_with("trace-1", project_id="project-1")


@pytest.mark.unit
def test_context_page_limits_do_not_limit_cost_coverage(monkeypatch):
    spans = [_span(trace_id=f"trace-{i}", cost=1.25) for i in range(100)]
    spans.extend(_span(trace_id="trace-100", attrs_string=UNKNOWN) for _ in range(51))
    context = _context(monkeypatch, spans)
    assert len(context["traces"]) == 100
    assert context["trace_count"] == 101
    assert context["total_spans"] == 151
    _assert_coverage(context, "partial", 125.0, 100, 51)


@pytest.mark.unit
def test_inline_span_cap_does_not_limit_unknown_fact_count(monkeypatch):
    context = _context(monkeypatch, [_span(attrs_string=UNKNOWN) for _ in range(51)])
    assert len(context["traces"][0]["spans"]) == 50
    _assert_coverage(context, "unknown", 0.0, 0, 51)
    _assert_coverage(context["traces"][0], "unknown", 0.0, 0, 51)


@pytest.mark.unit
def test_failed_span_read_omits_context_not_complete_zero(monkeypatch):
    reader = create_autospec(CHSpanReader, instance=True)
    reader.__enter__.return_value = reader
    reader.session_trace_ids.return_value = []
    reader.list_by_session.side_effect = TimeoutError("synthetic timeout")
    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", lambda: reader)
    monkeypatch.setattr(Trace.objects, "filter", lambda **_kwargs: _TraceQuery())
    assert (
        build_session_context(SimpleNamespace(id="session-1", project_id="project-1"))
        is None
    )
    assert build_session_context(None) is None


@pytest.mark.unit
def test_empty_context_and_compatibility_rounding(monkeypatch):
    _assert_coverage(_context(monkeypatch, []), "complete", 0.0, 0, 0)
    context = _context(monkeypatch, [_span(cost=1.23456789)])
    assert context["total_cost"] == 1.234568
    _assert_coverage(context, "complete", 1.23456789, 1, 0)


@pytest.mark.unit
@pytest.mark.parametrize(
    "cost,expected",
    [(0.0, 0.0), (Decimal("0"), 0.0), (None, None), (Decimal("1.25"), 1.25)],
)
def test_span_context_preserves_explicit_zero_baseline_bug(cost, expected):
    assert build_span_context(SimpleNamespace(cost=cost))["cost"] == expected


@pytest.mark.django_db
def test_active_session_pipeline_forwards_coverage_without_a_model_call(
    monkeypatch,
    trace_session,
    session_trace,
    custom_eval_config,
    stub_run_eval,
    stub_cost_log,
):
    from tracer.utils.eval import _execute_evaluation_for_session

    reader = create_autospec(CHSpanReader, instance=True)
    reader.__enter__.return_value = reader
    reader.session_trace_ids.return_value = [str(session_trace.id)]
    reader.list_by_session.return_value = [
        _span(trace_id=str(session_trace.id), cost=1.25, attrs_string=KNOWN),
        _span(trace_id=str(session_trace.id), attrs_string=UNKNOWN),
    ]
    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", lambda: reader)
    monkeypatch.setattr("tracer.utils.eval._emit_eval_billing", lambda **_kwargs: None)
    custom_eval_config.config = {
        "run_config": {"data_injection": {"session_context": True}}
    }
    _execute_evaluation_for_session(
        trace_session=trace_session,
        custom_eval_config=custom_eval_config,
        eval_task_id=None,
        run_params={"input": "synthetic input", "output": "synthetic output"},
    )
    assert len(stub_run_eval.requests) == 1
    request = stub_run_eval.requests[0]
    assert request.organization_id == str(trace_session.project.organization_id)
    context = request.inputs["session_context"]
    _assert_coverage(context, "partial", 1.25, 1, 1)
    assert "NOT a complete total" in context["cost_note"]


# Match the existing owned-database CH25 regression harness; do not touch the
# shared spans table or the ch_rehearsal baseline used by the Slice D suite.
@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    yield


@pytest.fixture
def eval_cost_client():
    from conftest import _ch_test_owned_database, _open_ch_test_http_client
    from tracer.tests.test_openrouter_broadcast_list_rollups_ch25 import (
        _REMAP_DDL,
        _SPANS_DDL,
    )

    with _ch_test_owned_database("test_eval_cost_coverage_") as database:
        client = _open_ch_test_http_client(database=database)
        try:
            client.command(_SPANS_DDL)
            client.command(_REMAP_DDL)
            # The existing reduced DDL lacks fields projected by list_by_session.
            # Add actual column types, not a patched SELECT/row decoder.
            columns = {
                "created_at": "DateTime64(6, 'UTC') DEFAULT now64(6, 'UTC')",
                "operation_name": "String",
                "status_message": "String",
                "org_id": "Nullable(UUID)",
                "prompt_version_id": "Nullable(UUID)",
                "prompt_label_id": "Nullable(UUID)",
                "custom_eval_config_id": "Nullable(UUID)",
                "tags": "String",
                "span_events": "String",
                "resource_attrs": "JSON",
                "llm_request_model": "String",
                "llm_response_model": "String",
                "embedding_model": "String",
                "streaming": "Nullable(UInt8)",
                "temperature": "Nullable(Float64)",
                "top_p": "Nullable(Float64)",
                "max_tokens": "Nullable(Int32)",
                "eval_status": "String",
                "semconv_source": "String",
            }
            for column, kind in columns.items():
                client.command(f"ALTER TABLE spans ADD COLUMN {column} {kind}")
            yield client
        finally:
            client.close()


@pytest.mark.integration
@pytest.mark.django_db
def test_real_session_reader_remap_scope_and_latest_cost_coverage(
    monkeypatch,
    eval_cost_client,
    observe_project,
):
    from tracer.models.trace_session import TraceSession

    client = eval_cost_client
    old, new = uuid4(), uuid4()
    traces = [
        Trace.objects.create(project=observe_project, name=f"cost-{i}")
        for i in range(2)
    ]
    session = TraceSession(id=old, project=observe_project, name="synthetic session")
    client.insert(
        "trace_session_id_remap",
        [[old, new, START]],
        column_names=["old_id", "new_id", "version"],
    )

    def insert(*, index, cost, attrs, version=1, deleted=0, project=None):
        row = {
            "project_id": project or observe_project.id,
            "trace_session_id": old if index == 0 else new,
            "trace_id": str(traces[index].id),
            "id": f"request-{index}",
            "observation_type": "llm",
            "name": "chat",
            "start_time": START + timedelta(days=index),
            "end_time": START + timedelta(days=index, seconds=1),
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
            "cost": cost,
            "attrs_string": {str(k): str(v) for k, v in attrs.items()},
            "_version": version,
            "is_deleted": deleted,
        }
        client.insert("spans", [list(row.values())], column_names=list(row))

    @contextmanager
    def owned_reader():
        reader = CHSpanReader.__new__(CHSpanReader)
        reader._client = client
        yield reader  # fixture owns the client lifetime, methods are unmodified

    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", owned_reader)
    insert(index=0, cost=1.25, attrs=KNOWN)
    insert(index=1, cost=0, attrs=UNKNOWN)
    insert(index=1, cost=99, attrs=KNOWN, project=uuid4())  # same IDs, different tenant
    context = build_session_context(session)
    _assert_coverage(context, "partial", 1.25, 1, 1)
    assert context["total_spans"] == 2
    assert context["total_tokens"] == 240
    assert {trace["id"] for trace in context["traces"]} == {
        str(trace.id) for trace in traces
    }

    insert(index=1, cost=0, attrs=ZERO, version=2)
    _assert_coverage(build_session_context(session), "complete", 1.25, 2, 0)
    insert(index=1, cost=0, attrs=UNKNOWN, version=3)
    _assert_coverage(build_session_context(session), "partial", 1.25, 1, 1)
    insert(index=1, cost=0, attrs=UNKNOWN, version=4, deleted=1)
    context = build_session_context(session)
    _assert_coverage(context, "complete", 1.25, 1, 0)
    assert context["total_spans"] == 1
