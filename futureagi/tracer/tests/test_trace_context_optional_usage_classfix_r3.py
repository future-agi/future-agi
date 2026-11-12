"""Optional usage metadata must not erase an authorized trace's context."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize(
    "attributes", ["absent", None, {}], ids=["absent", "null", "empty"]
)
def test_authorized_context_survives_optional_usage_attributes(monkeypatch, attributes):
    from tracer.utils.eval import build_trace_context

    owned = SimpleNamespace(
        id="span",
        name="owned-content",
        project_id="owned-project",
        trace_id="coincident-trace",
        observation_type="llm",
        status="ERROR",
        parent_span_id=None,
        total_tokens=7,
        latency_ms=3,
    )
    if attributes != "absent":
        owned.attrs_string = attributes
    foreign = SimpleNamespace(**{**vars(owned), "name": "foreign-content"})
    foreign.project_id = "foreign-project"
    reader = Mock()
    reader.__enter__ = Mock(return_value=reader)
    reader.__exit__ = Mock(return_value=False)
    reader.list_by_trace.side_effect = lambda trace_id, *, project_id: (
        [owned] if project_id == "owned-project" else [foreign]
    )
    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", lambda: reader)

    context = build_trace_context(
        SimpleNamespace(id="coincident-trace", project_id="owned-project"),
        anchor_span_id="span",
    )

    assert [span["name"] for span in context["spans"]] == ["owned-content"]
    assert context["span_count"] == context["error_count"] == 1
    assert context["total_tokens"] == 7
    assert context["total_latency_ms"] == 3
    assert context["span_id"] == "span"
    reader.list_by_trace.assert_called_once_with(
        "coincident-trace", project_id="owned-project"
    )


def test_nonadditive_usage_still_keeps_the_span_but_not_its_tokens(monkeypatch):
    from tracer.utils.eval import build_trace_context

    span = SimpleNamespace(
        id="span",
        name="owned-content",
        observation_type="llm",
        status="OK",
        parent_span_id=None,
        total_tokens=7,
        latency_ms=3,
        attrs_string={"fi.usage.contribution": "none"},
    )
    reader = Mock()
    reader.__enter__ = Mock(return_value=reader)
    reader.__exit__ = Mock(return_value=False)
    reader.list_by_trace.return_value = [span]
    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", lambda: reader)

    context = build_trace_context(SimpleNamespace(id="trace", project_id="project"))

    assert context["span_count"] == 1
    assert context["total_tokens"] == 0
    assert context["spans"][0]["name"] == "owned-content"
