"""``activity_by_trace_ids``: what a trace did between its input and output,
for an Insights transcript. Tool calls and failed steps come back as of their
latest version (a call that failed in a later version reads as failed, one
deleted is gone), cut to the size asked for with their full lengths; a step
whose input holds an image comes back for its media count only, and nothing
of another project leaks in.

Runs against the configured ClickHouse (``CH25_*``); data lives under fresh
ids and is deleted afterwards.
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest

from tracer.tests.test_session_trace_ids_prefilter import START, _client, _ids


def _span(client, project_id, trace_id, **extra):
    """One span version; each call is its own insert, so its own part."""
    cols = {
        "project_id": project_id,
        "observation_type": "tool",
        "start_time": START,
        "trace_id": trace_id,
        "id": str(uuid.uuid4()),
        "parent_span_id": "root",
        "name": "step",
        "status": "OK",
        "_version": 1,
        "is_deleted": 0,
        **extra,
    }
    client.command(
        f"INSERT INTO spans ({', '.join(cols)}) "
        f"VALUES ({', '.join(f'%({c})s' for c in cols)})",
        parameters=cols,
    )
    return cols["id"]


@pytest.fixture()
def world():
    client = _client()
    project, other = _ids(2)
    trace, other_trace = _ids(2)
    at = lambda seconds: START + timedelta(seconds=seconds)  # noqa: E731
    try:
        _span(
            client,
            project,
            trace,
            id="root",
            parent_span_id="",
            observation_type="agent",
            name="support_agent",
        )
        _span(
            client,
            project,
            trace,
            name="lookup_order",
            start_time=at(1),
            input='{"order_id": "HB-1"}',
            output='{"status": "shipped"}',
        )
        # Failed in its latest version.
        refund = str(uuid.uuid4())
        for version, status in ((1, "OK"), (2, "ERROR")):
            _span(
                client,
                project,
                trace,
                id=refund,
                name="issue_refund",
                start_time=at(2),
                status=status,
                status_message="refund_declined" if status == "ERROR" else "",
                input='{"amount": 61.2}',
                output='{"error": "refund_declined"}',
                _version=version,
            )
        _span(
            client,
            project,
            trace,
            name="fetch_logs",
            start_time=at(3),
            output="x" * 5000,
        )
        image = json.dumps(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "screenshot attached"},
                            {
                                "type": "image_url",
                                "image_url": {"url": "data:image/png;base64,AAAA"},
                            },
                        ],
                    }
                ]
            }
        )
        _span(
            client,
            project,
            trace,
            name="chat",
            observation_type="llm",
            start_time=at(4),
            input=image,
        )
        # A step with nothing to show, and a tool call deleted since.
        _span(
            client,
            project,
            trace,
            name="plan",
            observation_type="llm",
            start_time=at(5),
        )
        gone = str(uuid.uuid4())
        for version, deleted in ((1, 0), (2, 1)):
            _span(
                client,
                project,
                trace,
                id=gone,
                name="gone_tool",
                start_time=at(6),
                _version=version,
                is_deleted=deleted,
            )
        _span(client, other, other_trace, name="other_tool")
        _span(client, other, trace, name="same_trace_other_project")
        yield {"project": project, "trace": trace, "other_trace": other_trace}
    finally:
        client.command(
            "ALTER TABLE spans DELETE WHERE project_id IN (%(p)s, %(o)s) "
            "SETTINGS mutations_sync=2",
            parameters={"p": project, "o": other},
        )


@pytest.mark.integration
def test_a_traces_tool_calls_failures_and_media_steps_as_they_are_now(world):
    from ee.agenthub.insights.units import AUDIO_PART, IMAGE_PART
    from tracer.services.clickhouse.v2 import get_reader

    with get_reader() as reader:
        rows = reader.activity_by_trace_ids(
            [world["trace"], world["other_trace"]],
            project_id=world["project"],
            media_patterns=(IMAGE_PART, AUDIO_PART),
            field_chars=100,
        )
    by_name = {row["name"]: row for row in rows}
    assert list(by_name) == ["lookup_order", "issue_refund", "fetch_logs", "chat"]
    assert by_name["lookup_order"]["output"] == '{"status": "shipped"}'
    assert by_name["issue_refund"]["status"] == "ERROR"
    assert by_name["issue_refund"]["status_message"] == "refund_declined"
    assert by_name["fetch_logs"]["output"] == "x" * 100
    assert by_name["fetch_logs"]["output_chars"] == 5000
    assert by_name["chat"]["images"] == 1 and by_name["chat"]["audio"] == 0
    # Three tool calls are shown; the llm step only carried media.
    assert {row["in_trace"] for row in rows} == {3}


@pytest.mark.integration
def test_failed_steps_come_first_when_a_trace_holds_more_than_asked(world):
    from ee.agenthub.insights.units import AUDIO_PART, IMAGE_PART
    from tracer.services.clickhouse.v2 import get_reader

    with get_reader() as reader:
        rows = reader.activity_by_trace_ids(
            [world["trace"]],
            project_id=world["project"],
            media_patterns=(IMAGE_PART, AUDIO_PART),
            per_trace=1,
        )
    assert [row["name"] for row in rows] == ["issue_refund"]
    assert rows[0]["in_trace"] == 3
