"""The voice destination validates source membership in the snapshot it renders."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from tracer.services.clickhouse.v2.trace_detail_reads import TraceDetailRead
from tracer.views.trace import TraceView

PROJECT, TRACE = str(uuid4()), str(uuid4())
ROOT, CHILD = "0123456789abcdef", "abcdef0123456789"


def span(span_id, parent=None, kind="llm"):
    return {
        "id": span_id,
        "project_id": PROJECT,
        "trace_id": TRACE,
        "parent_span_id": parent,
        "observation_type": kind,
    }


def render_call(monkeypatch, rows, expected=CHILD):
    monkeypatch.setattr(
        "tracer.views.trace._simulation_context_for_voice_call", lambda **_: {}
    )
    request = SimpleNamespace(
        validated_query_data={
            "project_id": PROJECT,
            **({"expected_span_id": expected} if expected is not None else {}),
        },
        organization=None,
        user=None,
    )
    detail = TraceDetailRead(PROJECT, tuple(rows), (), (), (), 2, 1.0)
    return TraceView()._voice_call_detail_clickhouse(request, TRACE, detail, [])


@pytest.mark.parametrize(
    "change", ["deleted-child", "extra-root", "detached-child", "replacement-root"]
)
def test_changed_source_never_returns_voice_content(monkeypatch, change):
    rows = [span(ROOT, kind="conversation"), span(CHILD, ROOT)]
    if change == "deleted-child":
        rows.pop()
    elif change == "extra-root":
        rows.append(span("fedcba9876543210", kind="conversation"))
    elif change == "detached-child":
        rows[1]["parent_span_id"] = None
    else:
        rows[0]["id"] = "fedcba9876543210"
    response = render_call(monkeypatch, rows)
    assert response.status_code == 409
    assert "observation_span" not in response.data.get("result", {})


def test_same_snapshot_proves_collector_child_membership(monkeypatch):
    response = render_call(
        monkeypatch, [span(ROOT, kind="conversation"), span(CHILD, ROOT)]
    )
    assert response.status_code == 200
    assert [row["id"] for row in response.data["result"]["observation_span"]] == [
        ROOT,
        CHILD,
    ]


def test_ordinary_voice_callers_keep_existing_behavior(monkeypatch):
    response = render_call(
        monkeypatch, [span(ROOT, kind="conversation")], expected=None
    )
    assert response.status_code == 200
