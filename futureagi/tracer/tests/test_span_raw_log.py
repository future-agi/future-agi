"""``span_raw_log`` is the one reader of a voice span's ``raw_log`` payload.

The payload rides as a JSON string in ClickHouse ``attrs_string`` and as an
object on PG-era rows. The eval mapper and the voice call list/detail each kept
their own copy of the parse; the view's copy passed a JSON array or scalar
through, and every reader behind it then called ``.get`` on it.
"""

import json
import uuid

import pytest
from rest_framework import status
from structlog.testing import capture_logs

from tracer.tests.test_bounded_chunk_completeness import (
    CHECKPOINT,
    PROJECT_ID,
    _checkpointed_page,
    _read_voice_page,
)
from tracer.tests.test_voice_call_detail_org_scope import (
    VOICE_CALL_DETAIL_URL,
    _detail,
    _make_org_project,
)
from tracer.utils.attribute_accessor import span_raw_log

_PAYLOAD = {"id": "call-1", "customer": {"number": "+15551234567"}}
# Cut mid-object, as a truncated collector attribute would be.
_UNPARSEABLE = '{"id": "call-1", "customer": {"number": "+15551234567"'
_NOT_AN_OBJECT = [[_PAYLOAD], json.dumps([_PAYLOAD]), json.dumps("call-1"), "5"]


def _unparseable_warnings(logs):
    return [log for log in logs if log["event"] == "raw_log_unparseable"]


@pytest.mark.parametrize("raw_log", [_PAYLOAD, json.dumps(_PAYLOAD)])
def test_reads_an_object_or_its_json_string(raw_log):
    assert span_raw_log({"raw_log": raw_log}, span_id="span-1") == _PAYLOAD


@pytest.mark.parametrize(
    "attrs", [{}, {"raw_log": None}, {"raw_log": ""}, {"raw_log": {}}]
)
def test_an_absent_payload_is_empty_and_not_logged(attrs):
    with capture_logs() as logs:
        assert span_raw_log(attrs, span_id="span-1") == {}
    assert logs == []


@pytest.mark.parametrize("raw_log", _NOT_AN_OBJECT)
def test_a_payload_that_is_not_an_object_reads_as_absent(raw_log):
    assert span_raw_log({"raw_log": raw_log}, span_id="span-1") == {}


def test_an_unparseable_payload_is_logged_with_the_span_not_the_payload():
    with capture_logs() as logs:
        assert span_raw_log({"raw_log": _UNPARSEABLE}, span_id="span-1") == {}

    [warning] = _unparseable_warnings(logs)
    assert warning["log_level"] == "warning"
    assert warning["span_id"] == "span-1"
    assert warning["exc_info"] is True
    assert "+15551234567" not in json.dumps(warning, default=str)


# ── Voice call list and detail ─────────────────────────────────────────────


def _voice_root_row():
    from datetime import timedelta

    from tracer.tests.test_trace_root_physical_replay import complete_root_row

    return complete_root_row(
        {
            "project_id": PROJECT_ID,
            "trace_id": "trace-1",
            "root_span_id": "root-1",
            "span_id": "root-1",
            "_root_observation_type": "conversation",
            "start_time": CHECKPOINT,
            "end_time": CHECKPOINT + timedelta(seconds=9),
            "provider": "vapi",
        },
        project_id=PROJECT_ID,
    )


def _voice_list_page(raw_log):
    return _read_voice_page(
        _checkpointed_page(rows=[_voice_root_row()], before_id="trace-1"),
        raw_log=raw_log,
    )


@pytest.mark.parametrize("raw_log", _NOT_AN_OBJECT)
def test_voice_list_renders_a_call_whose_payload_is_not_an_object(raw_log):
    # The simulator filter is on, so the page reads each row's payload twice:
    # once to drop simulator calls, once to build the row.
    response = _voice_list_page(raw_log)

    assert getattr(response, "status_code", response) == status.HTTP_200_OK
    assert [row["trace_id"] for row in response.data["results"]] == ["trace-1"]


def test_voice_list_parses_each_call_payload_once():
    with capture_logs() as logs:
        response = _voice_list_page(_UNPARSEABLE)

    assert getattr(response, "status_code", response) == status.HTTP_200_OK
    [warning] = _unparseable_warnings(logs)
    assert warning["span_id"] == "root-1"


def _voice_detail(auth_client, user, monkeypatch, raw_log):
    _, workspace, project = _make_org_project(user, "Raw log")
    auth_client.set_workspace(workspace)
    trace_id = str(uuid.uuid4())
    detail = _detail(project.id, trace_id)
    detail.spans[0]["attrs_string"] = {"raw_log": raw_log}
    monkeypatch.setattr("tracer.views.trace.V2AnalyticsQueryService", object)
    monkeypatch.setattr(
        "tracer.views.trace.read_trace_detail", lambda **_kwargs: detail
    )
    return trace_id, auth_client.get(VOICE_CALL_DETAIL_URL, {"trace_id": trace_id})


@pytest.mark.django_db
@pytest.mark.parametrize("raw_log", [*_NOT_AN_OBJECT[1:], _UNPARSEABLE])
def test_voice_detail_reads_a_payload_it_cannot_use_as_absent(
    auth_client, user, monkeypatch, raw_log
):
    with capture_logs() as logs:
        trace_id, response = _voice_detail(auth_client, user, monkeypatch, raw_log)

    assert response.status_code == status.HTTP_200_OK, response.data
    assert response.data["result"]["trace_id"] == trace_id
    warnings = _unparseable_warnings(logs)
    if raw_log == _UNPARSEABLE:
        assert [w["span_id"] for w in warnings] == ["root"]
    else:
        assert warnings == []


@pytest.mark.django_db
@pytest.mark.parametrize("customer", ["+15551234567", ["+15551234567"], 7])
def test_voice_detail_reads_a_customer_that_is_not_an_object_as_absent(
    auth_client, user, monkeypatch, customer
):
    raw_log = json.dumps({**_PAYLOAD, "customer": customer})

    trace_id, response = _voice_detail(auth_client, user, monkeypatch, raw_log)

    assert response.status_code == status.HTTP_200_OK, response.data
    assert response.data["result"]["trace_id"] == trace_id
