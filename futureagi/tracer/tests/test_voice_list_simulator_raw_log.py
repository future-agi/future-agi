"""The Voice list's simulator toggle reads raw_log in each shape it is stored in.

``list_voice_calls`` with ``remove_simulation_calls`` drops calls dialled from
a simulator phone number: a VAPI call's ``customer.number`` or a Retell call's
``from_number``. The bounded classifier drops them in ClickHouse
(``simulator_call_sql``) before the page is hydrated; the view then parses
each hydrated row's payload with ``span_raw_log`` and re-checks it with
``is_simulator_call`` as a defensive parity check.

The collector stores a call's ``raw_log`` as a JSON string in
``attrs_string``; PG-era rows keep it as an object in ``attributes_extra``.
origin/dev's Python check read ``span_attrs.get("raw_log")`` and called
``.get`` on the string, so with the toggle on, the first VAPI or Retell call
the classifier kept failed the whole page with ``AttributeError`` (500). Once
``is_simulator_call`` took the parsed payload, no test sent a JSON-string
payload through the simulator check any more.

These tests seed the real CH25 ``spans`` table of the test database, one row
per storage shape, and ask the endpoint for the list with the toggle off and
on. Because ClickHouse drops simulator calls first, the view's check only
decides a row the predicate missed, so a second test turns the predicate off
and requires the Python check alone to drop them from the hydrated rows.
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from django.core.cache import cache
from rest_framework import status
from structlog.testing import capture_logs

from tracer.services.clickhouse.query_builders import voice_call_list
from tracer.services.clickhouse.query_builders.voice_call_list import (
    VAPI_PHONE_NUMBERS,
)
from tracer.services.clickhouse.v2.adapter import (
    CH_INSERT_COLUMNS,
    adapt,
    row_to_tuple,
)
from tracer.tests._ch_seed import _get_ch_client, seed_ch_spans

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

_SIMULATOR = VAPI_PHONE_NUMBERS[0]
_CALLER = "+15550000001"
# A collector attribute cut mid-object. Its number marks the payload so the
# log assertion can prove the payload never reaches the log.
_MALFORMED = '{"id": "call-9", "customer": {"number": "+15550009999"'
_MALFORMED_MARKER = "+15550009999"

# (call, provider, raw_log, storage). "collector" is a JSON string in
# attrs_string; "pg" is an object in attributes_extra; "json-text" is a JSON
# string inside attributes_extra, read by simulator_call_sql's raw_log_text arm.
_CALLS = (
    ("vapi-simulator", "vapi", {"customer": {"number": _SIMULATOR}}, "collector"),
    ("retell-simulator", "retell", {"from_number": _SIMULATOR}, "collector"),
    ("pg-simulator", "vapi", {"customer": {"number": _SIMULATOR}}, "pg"),
    ("json-text-simulator", "vapi", {"customer": {"number": _SIMULATOR}}, "json-text"),
    ("vapi-caller", "vapi", {"customer": {"number": _CALLER}}, "collector"),
    ("retell-caller", "retell", {"from_number": _CALLER}, "collector"),
    ("pg-caller", "vapi", {"customer": {"number": _CALLER}}, "pg"),
    ("malformed", "vapi", _MALFORMED, "collector"),
    # A customer that is not an object has no number, even the simulator's.
    ("string-customer", "vapi", {"customer": _SIMULATOR}, "collector"),
    ("list-customer", "vapi", {"customer": [_SIMULATOR]}, "collector"),
    ("number-customer", "vapi", {"customer": 7}, "collector"),
)
_SIMULATOR_CALLS = {call for call, *_ in _CALLS if call.endswith("-simulator")}
_ALL_CALLS = {call for call, *_ in _CALLS}

# Ingest writes the provider's lowercase choice value; this row only pins that
# the list's ClickHouse predicate compares it case-insensitively.
_CAPITALIZED_PROVIDER_CALL = (
    "capitalized-provider-simulator",
    "Vapi",
    {"customer": {"number": _SIMULATOR}},
    "collector",
)


def _span_row(project, start, index, trace_id, span_id, provider, raw_log, storage):
    started = start + timedelta(minutes=index)
    if storage == "collector" and not isinstance(raw_log, str):
        raw_log = json.dumps(raw_log)
    return {
        "id": span_id,
        "trace_id": trace_id,
        "project_id": str(project.id),
        "org_id": str(project.organization_id),
        "parent_span_id": None,
        "name": f"{provider} Call Log",
        "observation_type": "conversation",
        "status": "OK",
        "start_time": started,
        "end_time": started + timedelta(seconds=30),
        "latency_ms": 30_000,
        "provider": provider,
        "cost": 0.01,
        "span_attributes": {} if storage == "json-text" else {"raw_log": raw_log},
        "created_at": started,
        "updated_at": started,
    }


def _seed_raw_log_as_json_text(row, raw_log):
    # The adapter routes every string attribute to attrs_string, so place the
    # JSON-string payload in attributes_extra after adapting the row.
    ch_row = dataclasses.replace(
        adapt(row), attributes_extra=json.dumps({"raw_log": json.dumps(raw_log)})
    )
    client = _get_ch_client()
    try:
        client.insert(
            "spans", [row_to_tuple(ch_row)], column_names=list(CH_INSERT_COLUMNS)
        )
    finally:
        client.close()


def _seed(project, start, calls, fixture):
    rows = []
    for index, (call, provider, raw_log, storage) in enumerate(
        calls, start=len(fixture["trace_ids"])
    ):
        fixture["trace_ids"][call] = str(uuid.uuid4())
        fixture["span_ids"][call] = uuid.uuid4().hex[:16]
        row = _span_row(
            project,
            start,
            index,
            fixture["trace_ids"][call],
            fixture["span_ids"][call],
            provider,
            raw_log,
            storage,
        )
        if storage == "json-text":
            _seed_raw_log_as_json_text(row, raw_log)
        else:
            rows.append(row)
    seed_ch_spans(rows)
    cache.clear()


@pytest.fixture()
def voice_calls(observe_project):
    start = (datetime.now(UTC) - timedelta(days=1)).replace(
        minute=0, second=0, microsecond=0
    )
    fixture = {
        "project": observe_project,
        "start": start,
        "trace_ids": {},
        "span_ids": {},
        "window": (start - timedelta(days=2), start + timedelta(days=1)),
    }
    _seed(observe_project, start, _CALLS, fixture)
    yield fixture
    cache.clear()


@pytest.fixture()
def capitalized_provider_call(voice_calls):
    _seed(
        voice_calls["project"],
        voice_calls["start"],
        [_CAPITALIZED_PROVIDER_CALL],
        voice_calls,
    )
    return _CAPITALIZED_PROVIDER_CALL[0]


def _voice_list(client, fixture, remove_simulation_calls):
    window = fixture["window"]
    response = client.post(
        "/tracer/trace/list_voice_calls/",
        {
            "project_id": str(fixture["project"].id),
            "page_size": 25,
            "cursor_mode": True,
            "remove_simulation_calls": remove_simulation_calls,
            "filters": [
                {
                    "column_id": "created_at",
                    "filter_config": {
                        "col_type": "SYSTEM_METRIC",
                        "filter_type": "datetime",
                        "filter_op": "between",
                        "filter_value": [bound.isoformat() for bound in window],
                    },
                }
            ],
        },
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK, response.content
    body = response.json()
    result = body.get("result", body)
    call_by_trace = {trace: call for call, trace in fixture["trace_ids"].items()}
    return {call_by_trace.get(row["trace_id"]) for row in result["results"]}


@pytest.mark.parametrize("remove_simulation_calls", [False, True])
def test_toggle_drops_exactly_the_simulator_calls(
    auth_client, voice_calls, capitalized_provider_call, remove_simulation_calls
):
    with capture_logs() as logs:
        listed = _voice_list(auth_client, voice_calls, remove_simulation_calls)

    if remove_simulation_calls:
        assert listed == _ALL_CALLS - _SIMULATOR_CALLS
    else:
        assert listed == _ALL_CALLS | {capitalized_provider_call}

    # The malformed call is listed, and its payload is logged once, by span id.
    warnings = [log for log in logs if log["event"] == "raw_log_unparseable"]
    assert [warning["span_id"] for warning in warnings] == [
        voice_calls["span_ids"]["malformed"]
    ]
    assert _MALFORMED_MARKER not in json.dumps(warnings, default=str)


def test_python_check_alone_drops_the_simulator_calls(
    auth_client, voice_calls, monkeypatch
):
    # With the ClickHouse predicate matching nothing, every simulator call
    # reaches the view's hydrated rows and only is_simulator_call can drop it;
    # passing it the span attributes instead of the parsed raw_log drops none.
    monkeypatch.setattr(voice_call_list, "simulator_call_sql", lambda **_: "0")

    listed = _voice_list(auth_client, voice_calls, True)

    assert listed == _ALL_CALLS - _SIMULATOR_CALLS
