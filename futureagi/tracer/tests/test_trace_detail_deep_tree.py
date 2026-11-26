"""Trace detail for span trees deeper than the interpreter recursion limit.

A parent chain within the 1,000-span cap used to fail in three recursive
walks: the V2 ``_parent_id`` cleanup, ``compute_trace_summary_and_graph`` and
the stock JSON renderer. These tests drive deep chains through the handlers
and both endpoints that return the tree.

Deep results are checked with iterative walks and compared as sha256 and
length only; ``==``, ``json.loads`` and ``deepcopy`` recurse per level.
"""

import random
import uuid
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status
from rest_framework.renderers import JSONRenderer

from model_hub.models.ai_model import AIModel
from tfc.utils.general_methods import GeneralMethods
from tracer.models.project import Project
from tracer.renderers import TraceDetailJSONRenderer
from tracer.services.clickhouse.query_builders.trace_detail import (
    TraceDetailHandler,
    compute_trace_summary_and_graph,
)
from tracer.services.clickhouse.v2.query_builders.trace_detail import (
    TraceDetailHandlerV2,
    retrieve_trace_detail_ch,
)
from tracer.services.clickhouse.v2.trace_detail_reads import (
    TraceDetailReadUnavailable,
)
from tracer.tests.test_ch25_trace_detail_v2 import (
    _FakeResult,
    _patch_v2_pg,
    _root_span_row,
)
from tracer.tests.test_shared_link_resolve import (
    _make_error_issue,
    _member_trace_id,
    _share_error_issue,
)
from tracer.tests.test_trace_detail_renderer import _chain_oracle, _digest
from tracer.views.trace import TraceView

_START = datetime(2026, 1, 1, tzinfo=UTC)


def _span_id(index):
    return f"S{index:05d}"


def _chain_rows(depth):
    return [
        _root_span_row(
            id=_span_id(index),
            parent_span_id=_span_id(index - 1) if index > 1 else None,
            name=f"step-{index}",
            start_time=_START + timedelta(microseconds=index),
        )
        for index in range(1, depth + 1)
    ]


class _ChainAnalytics:
    """Serves one trace's identity read and its content replay, batch by batch.

    Routed by bound parameters, not SQL text: the eval and annotation reads
    return no rows.
    """

    def __init__(self, rows):
        self.rows = {row["id"]: row for row in rows}

    def execute_ch_query(self, query, params=None, timeout_ms=None, **_):
        params = params or {}
        if "detail_span_identities" in params:
            return _FakeResult(
                [self.rows[span_id] for span_id in params["detail_span_ids"]]
            )
        if "detail_trace_id" in params:
            return _FakeResult(
                [
                    {
                        "project_id": "P1",
                        "trace_id": row["trace_id"],
                        "span_id": row["id"],
                        "start_time": row["start_time"],
                        "latest_is_deleted": 0,
                        "latest_version": 1,
                    }
                    for row in self.rows.values()
                ]
            )
        return _FakeResult([])


def _v2_chain_result(depth):
    analytics = _ChainAnalytics(_chain_rows(depth))
    with ExitStack() as stack:
        _patch_v2_pg(stack, project_accessible=True, pg_trace=None)
        return retrieve_trace_detail_ch(MagicMock(), MagicMock(), "T1", analytics)


def _entries(observation_spans):
    pending = list(observation_spans)
    while pending:
        entry = pending.pop()
        yield entry
        pending.extend(entry["children"])


def _envelope(result):
    return GeneralMethods().success_response(result).data


def _assert_chain(result, depth):
    entries = list(_entries(result["observation_spans"]))
    assert len(entries) == depth
    assert all("_parent_id" not in entry for entry in entries)
    assert all(len(entry["children"]) <= 1 for entry in entries)
    assert result["summary"]["total_spans"] == depth
    nodes = result["graph"]["nodes"]
    edges = result["graph"]["edges"]
    assert [node["id"] for node in nodes] == [
        _span_id(index) for index in range(1, depth + 1)
    ]
    assert edges == [
        {"from": _span_id(index), "to": _span_id(index + 1)}
        for index in range(1, depth)
    ]


# --------------------------------------------------------------------------- #
# compute_trace_summary_and_graph
# --------------------------------------------------------------------------- #
def _reference_walk(spans_tree):
    """Dev's recursive walk, kept verbatim as the order and edge reference."""
    graph_nodes = []
    graph_edges = []

    def walk(entries, parent_id=None):
        for entry in entries:
            span = entry.get("observation_span") or entry.get("observationSpan") or {}
            span_id = span.get("id", "")
            graph_nodes.append(
                {
                    "id": span_id,
                    "name": span.get("name", ""),
                    "type": span.get("observation_type", "unknown"),
                    "latency_ms": span.get("latency_ms") or span.get("latency") or 0,
                    "tokens": span.get("total_tokens") or 0,
                    "status": span.get("status"),
                }
            )
            if parent_id:
                graph_edges.append({"from": parent_id, "to": span_id})
            children = entry.get("children", [])
            if children:
                walk(children, parent_id=span_id)

    walk(spans_tree)
    return {"nodes": graph_nodes, "edges": graph_edges}


def _random_forest(rng, counter, depth=0):
    entries = []
    for _ in range(rng.randint(0, 4 if depth else 3)):
        counter[0] += 1
        span = {
            "id": "" if rng.random() < 0.1 else f"N{counter[0]}",
            "name": f"n{counter[0]}",
            "observation_type": rng.choice(["llm", "chain", "tool", "agent"]),
            "latency_ms": rng.choice([None, 0, 5, 12]),
            "total_tokens": rng.choice([None, 0, 3]),
            "status": rng.choice(["OK", "ERROR", None]),
        }
        entry = {"observationSpan" if rng.random() < 0.1 else "observation_span": span}
        shape = rng.random()
        if depth < 7 and shape < 0.6:
            entry["children"] = _random_forest(rng, counter, depth + 1)
        elif shape < 0.75:
            entry["children"] = None
        elif shape < 0.9:
            entry["children"] = []
        entries.append(entry)
    return entries


@pytest.mark.parametrize("seed", range(25))
def test_summary_graph_walk_matches_recursive_reference(seed):
    forest = _random_forest(random.Random(seed), [0])
    summary, graph = compute_trace_summary_and_graph(forest)
    reference = _reference_walk(forest)
    assert graph == reference
    assert summary["total_spans"] == len(reference["nodes"])
    assert list(summary["span_type_counts"]) == list(
        dict.fromkeys(node["type"] for node in reference["nodes"])
    )


def test_summary_graph_walks_shared_entry_like_reference():
    shared = {"observation_span": {"id": "shared"}, "children": []}
    leaf = {"observation_span": {"id": "leaf"}, "children": [shared]}
    forest = [
        {"observation_span": {"id": "a"}, "children": [shared, leaf]},
        {"observation_span": {"id": "b"}, "children": [shared]},
    ]
    _, graph = compute_trace_summary_and_graph(forest)
    assert graph == _reference_walk(forest)
    assert [node["id"] for node in graph["nodes"]] == [
        "a",
        "shared",
        "leaf",
        "shared",
        "b",
        "shared",
    ]


def test_summary_graph_cycle_raises_recursion_error_like_reference():
    root = {"observation_span": {"id": "root"}, "children": []}
    child = {"observation_span": {"id": "child"}, "children": [root]}
    root["children"].append(child)
    with pytest.raises(RecursionError):
        _reference_walk([root])
    with pytest.raises(RecursionError):
        compute_trace_summary_and_graph([root])


def test_summary_graph_handles_chain_past_recursion_limit():
    depth = 5000
    node = {"observation_span": {"id": _span_id(depth)}, "children": []}
    for index in range(depth - 1, 0, -1):
        node = {"observation_span": {"id": _span_id(index)}, "children": [node]}
    summary, graph = compute_trace_summary_and_graph([node])
    assert summary["total_spans"] == depth
    assert [n["id"] for n in graph["nodes"]] == [
        _span_id(index) for index in range(1, depth + 1)
    ]
    assert len(graph["edges"]) == depth - 1
    assert graph["edges"][-1] == {"from": _span_id(depth - 1), "to": _span_id(depth)}


# --------------------------------------------------------------------------- #
# Handlers
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("depth", [999, 1000])
def test_v2_handler_returns_deep_chain(depth):
    result = _v2_chain_result(depth)
    _assert_chain(result, depth)
    body = TraceDetailJSONRenderer().render(_envelope(result))
    assert _digest(body) == _digest(_chain_oracle(_envelope(result)))


def test_v2_handler_keeps_span_cap_for_deep_chain():
    with pytest.raises(TraceDetailReadUnavailable) as raised:
        _v2_chain_result(1001)
    assert raised.value.code == "span_limit_exceeded"


def test_v1_handler_returns_deep_chain():
    depth = 999
    node = {"observation_span": {"id": _span_id(depth)}, "children": []}
    for index in range(depth - 1, 0, -1):
        node = {"observation_span": {"id": _span_id(index)}, "children": [node]}
    view = MagicMock()
    view.get_queryset.return_value.filter.return_value.first.return_value = (
        SimpleNamespace(id="T1", project_id="P1", project_version_id=None)
    )
    view.get_serializer.return_value.data = {"id": "T1", "name": "root"}
    with patch(
        "tracer.views.observation_span.get_observation_spans", return_value=[node]
    ):
        result = TraceDetailHandler(view=view, request=MagicMock(), pk="T1").fetch()
    _assert_chain(result, depth)
    body = TraceDetailJSONRenderer().render(_envelope(result))
    assert _digest(body) == _digest(_chain_oracle(_envelope(result)))


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("action", "renderer_class"),
    [
        ("retrieve", TraceDetailJSONRenderer),
        ("list", JSONRenderer),
        ("metadata", JSONRenderer),
        (None, JSONRenderer),
    ],
)
def test_trace_view_swaps_renderer_only_for_retrieve(action, renderer_class):
    view = TraceView()
    view.action = action
    assert [type(renderer) for renderer in view.get_renderers()] == [renderer_class]


@contextmanager
def _patched_fetch(fetch):
    with (
        patch("tracer.views.trace.V2AnalyticsQueryService", MagicMock()),
        patch.object(TraceDetailHandlerV2, "fetch", fetch),
    ):
        yield


def _stock_bytes(response):
    return JSONRenderer().render(
        response.data, response.accepted_media_type, response.renderer_context
    )


@pytest.mark.django_db
@pytest.mark.parametrize("accept", ["application/json", "*/*"])
def test_trace_detail_endpoint_renders_deep_chain(auth_client, accept):
    result = _v2_chain_result(999)
    expected = _chain_oracle(_envelope(result))
    with _patched_fetch(lambda handler: result):
        response = auth_client.get(f"/tracer/trace/{uuid.uuid4()}/", HTTP_ACCEPT=accept)
    assert response.status_code == status.HTTP_200_OK
    assert type(response.accepted_renderer) is TraceDetailJSONRenderer
    assert response["Content-Type"] == "application/json"
    assert _digest(response.content) == _digest(expected)


@pytest.mark.django_db
def test_trace_detail_endpoint_shallow_bytes_match_stock_renderer(auth_client):
    result = _v2_chain_result(3)
    with _patched_fetch(lambda handler: result):
        response = auth_client.get(f"/tracer/trace/{uuid.uuid4()}/")
        head = auth_client.head(f"/tracer/trace/{uuid.uuid4()}/")
    assert response.status_code == status.HTTP_200_OK
    assert type(response.accepted_renderer) is TraceDetailJSONRenderer
    assert response.content == _stock_bytes(response)
    assert head.status_code == status.HTTP_200_OK
    assert type(head.accepted_renderer) is TraceDetailJSONRenderer
    assert head["Content-Type"] == response["Content-Type"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (RuntimeError("boom"), status.HTTP_400_BAD_REQUEST),
        (
            TraceDetailReadUnavailable("span_limit_exceeded"),
            status.HTTP_503_SERVICE_UNAVAILABLE,
        ),
    ],
)
def test_trace_detail_endpoint_error_bytes_match_stock_renderer(
    auth_client, error, status_code
):
    with _patched_fetch(MagicMock(side_effect=error)):
        response = auth_client.get(f"/tracer/trace/{uuid.uuid4()}/")
    assert response.status_code == status_code
    assert response.content == _stock_bytes(response)


@pytest.fixture
def licensed_error_issue(db, organization, workspace):
    with patch("tfc.ee_gating.check_ee_feature", return_value=None):
        project = Project.objects.create(
            name=f"Deep Trace Share Project {uuid.uuid4().hex[:8]}",
            organization=organization,
            workspace=workspace,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
        )
        yield _make_error_issue(project)


def _get_shared_trace(api_client, auth_client, issue, result):
    token = _share_error_issue(auth_client, issue.cluster_id).json()["result"]["token"]
    with patch(
        "tracer.services.clickhouse.v2.query_builders.trace_detail."
        "retrieve_trace_detail_ch",
        return_value=result,
    ):
        return api_client.get(
            f"/tracer/shared/{token}/traces/{_member_trace_id(issue)}/"
        )


@pytest.mark.django_db
def test_shared_issue_trace_renders_deep_chain(
    api_client, auth_client, licensed_error_issue
):
    result = _v2_chain_result(999)
    expected = _chain_oracle(_envelope(result))
    response = _get_shared_trace(api_client, auth_client, licensed_error_issue, result)
    assert response.status_code == status.HTTP_200_OK
    assert type(response.accepted_renderer) is TraceDetailJSONRenderer
    assert response["Content-Type"] == "application/json"
    assert _digest(response.content) == _digest(expected)


@pytest.mark.django_db
def test_shared_issue_trace_shallow_bytes_match_stock_renderer(
    api_client, auth_client, licensed_error_issue
):
    result = _v2_chain_result(3)
    response = _get_shared_trace(api_client, auth_client, licensed_error_issue, result)
    assert response.status_code == status.HTTP_200_OK
    assert response.content == _stock_bytes(response)
