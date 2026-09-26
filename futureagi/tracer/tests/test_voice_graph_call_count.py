"""The Voice chart counts calls, under the Voice list's simulator toggle.

Dev QA follow-up to the voice graph scope (40a21c8ae/6371884d0). A black-box run
on the local e2e stack (api-tester D5x-call2) posted one voice call whose
``conversation`` root has one ``llm`` child: ``get_graph_methods`` with
``observe_type="voice"`` summed traffic 2 while ``list_voice_calls`` returned 1.
The trace graph's traffic counts every contributing span of a matched trace, so
the Voice chart equalled the call count only while each call was one span.

The Voice list also honours ``remove_simulation_calls`` (simulator phone
numbers on VAPI/Retell roots), but the Voice chart had no such parameter.

These tests seed the real CH25 ``spans`` table of the test database and ask
both endpoints for the same window and toggle.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.test import override_settings
from rest_framework import status

from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    compile_exact_graph_filter_predicates,
)
from tracer.services.clickhouse.query_builders.voice_call_list import (
    VAPI_PHONE_NUMBERS,
    VOICE_CALL_ROOT_FILTER,
    VOICE_CALL_SIMULATOR_EXCLUSION_FILTER,
)
from tracer.services.clickhouse.v2.query_service import V2AnalyticsQueryService
from tracer.services.exact_aggregation_cache import normalize_exact_observe_identity
from tracer.tests._ch_seed import seed_ch_spans

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

# (trace, span, parent, observation_type, provider, latency_ms, raw_log)
# * the voice call is a conversation root with one llm child (the F2 shape);
# * the simulator call is a VAPI conversation root dialled by a simulator;
# * the child-conversation trace is not a call and must never be counted.
_CUSTOMER = {"customer": {"number": "+15550000001"}}
_SIMULATOR = {"customer": {"number": VAPI_PHONE_NUMBERS[0]}}
_FIXTURE = (
    ("voice-call", "voice-root", None, "conversation", "vapi", 60_000, _CUSTOMER),
    ("voice-call", "voice-llm", "voice-root", "llm", "openai", 800, None),
    ("simulator-call", "sim-root", None, "conversation", "vapi", 30_000, _SIMULATOR),
    ("child-conversation", "chain-root", None, "chain", "", 500, None),
    ("child-conversation", "conv-child", "chain-root", "conversation", "", 400, None),
)
# The list's population per toggle, and each call's root latency.
CALLS = {False: {"voice-call": 60_000, "simulator-call": 30_000}}
CALLS[True] = {"voice-call": 60_000}


@pytest.fixture()
def voice_fixture(observe_project):
    """Seed the fixture into a fresh project and return its window."""

    start = (datetime.now(UTC) - timedelta(days=1)).replace(
        minute=0, second=0, microsecond=0
    )
    trace_ids = {trace: str(uuid.uuid4()) for trace, *_ in _FIXTURE}
    span_ids = {span: uuid.uuid4().hex[:16] for _, span, *_ in _FIXTURE}
    rows = []
    for index, (trace, span, parent, kind, provider, latency, raw_log) in enumerate(
        _FIXTURE
    ):
        started = start + timedelta(minutes=index)
        rows.append(
            {
                "id": span_ids[span],
                "trace_id": trace_ids[trace],
                "project_id": str(observe_project.id),
                "org_id": str(observe_project.organization_id),
                "parent_span_id": span_ids[parent] if parent else None,
                "name": span,
                "observation_type": kind,
                "status": "OK",
                "start_time": started,
                "end_time": started + timedelta(milliseconds=latency),
                "latency_ms": latency,
                "provider": provider,
                "cost": 0.01,
                "span_attributes": {"raw_log": raw_log} if raw_log else {},
                "created_at": started,
                "updated_at": started,
            }
        )
    seed_ch_spans(rows)
    cache.clear()
    yield {
        "project_id": str(observe_project.id),
        "trace_ids": trace_ids,
        "window": (start - timedelta(days=2), start + timedelta(days=1)),
    }
    cache.clear()


def _window_filter(window):
    return [
        {
            "column_id": "created_at",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [bound.isoformat() for bound in window],
            },
        }
    ]


def _voice_list(client, fixture, remove_simulation_calls):
    response = client.post(
        "/tracer/trace/list_voice_calls/",
        {
            "project_id": fixture["project_id"],
            "page_size": 25,
            "cursor_mode": True,
            "remove_simulation_calls": remove_simulation_calls,
            "filters": _window_filter(fixture["window"]),
        },
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK, response.content
    body = response.json()
    result = body.get("result", body)
    return {row["trace_id"] for row in result["results"]}


def _voice_scope(remove_simulation_calls):
    # The toggle is sent only when on, exactly as the Voice screen sends it.
    scope = {"observe_type": "voice"}
    if remove_simulation_calls:
        scope["remove_simulation_calls"] = True
    return scope


def _graph(client, fixture, metric_id, **scope):
    response = client.post(
        "/tracer/trace/get_graph_methods/",
        {
            "project_id": fixture["project_id"],
            "interval": "day",
            "property": "average",
            "req_data_config": {
                "id": metric_id,
                "type": "SYSTEM_METRIC",
                "property_id": f"system_attribute:traces:{metric_id}",
                "source": "traces",
            },
            "filters": _window_filter(fixture["window"]),
            **scope,
        },
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK, response.content
    body = response.json()
    result = body.get("result", body)
    assert result["query_status"] == "complete", result
    return [point for point in result["data"] if point["primary_traffic"]]


@pytest.mark.parametrize("remove_simulation_calls", [False, True])
def test_voice_chart_counts_the_voice_list_calls(
    auth_client, voice_fixture, remove_simulation_calls
):
    listed = _voice_list(auth_client, voice_fixture, remove_simulation_calls)
    expected = {
        voice_fixture["trace_ids"][trace] for trace in CALLS[remove_simulation_calls]
    }
    assert listed == expected

    traffic = _graph(
        auth_client,
        voice_fixture,
        "traffic",
        **_voice_scope(remove_simulation_calls),
    )

    # One per voice call, whatever spans the call carries: chart == list.
    assert sum(point["value"] for point in traffic) == len(listed)


@pytest.mark.parametrize("remove_simulation_calls", [False, True])
def test_voice_chart_latency_is_the_call_latency(
    auth_client, voice_fixture, remove_simulation_calls
):
    latency = _graph(
        auth_client,
        voice_fixture,
        "latency",
        **_voice_scope(remove_simulation_calls),
    )

    # Each call contributes its conversation root - the call itself - so the
    # child llm span's 800 ms never averages into call latency.
    calls = CALLS[remove_simulation_calls]
    assert [point["value"] for point in latency] == [
        round(sum(calls.values()) / len(calls), 2)
    ]


def test_trace_chart_still_counts_every_span(auth_client, voice_fixture):
    traffic = _graph(auth_client, voice_fixture, "traffic")

    assert sum(point["value"] for point in traffic) == len(_FIXTURE)


def test_simulator_toggle_is_a_voice_graph_parameter(auth_client, voice_fixture):
    response = auth_client.post(
        "/tracer/trace/get_graph_methods/",
        {
            "project_id": voice_fixture["project_id"],
            "interval": "day",
            "req_data_config": {"id": "traffic", "type": "SYSTEM_METRIC"},
            "filters": _window_filter(voice_fixture["window"]),
            "remove_simulation_calls": True,
        },
        format="json",
    )

    # The toggle exists but narrows only the voice population.
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "Requires observe_type 'voice'" in response.content.decode()


@pytest.mark.parametrize(
    "private_leaf", [VOICE_CALL_ROOT_FILTER, VOICE_CALL_SIMULATOR_EXCLUSION_FILTER]
)
def test_public_graph_request_cannot_carry_a_voice_population_leaf(
    auth_client, observe_project, private_leaf
):
    # The exact identity keeps these leaves only because no request can.
    response = auth_client.post(
        "/tracer/trace/get_graph_methods/",
        {
            "project_id": str(observe_project.id),
            "interval": "day",
            "req_data_config": {"id": "traffic", "type": "SYSTEM_METRIC"},
            "filters": [private_leaf],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "_eval_task_trace_root" in response.content.decode()


def test_exact_graph_identity_keeps_the_simulator_exclusion():
    end = datetime(2026, 9, 25, tzinfo=UTC)
    identity = normalize_exact_observe_identity(
        {
            "project_id": str(uuid.uuid4()),
            "filters": [
                *_window_filter((end - timedelta(days=7), end)),
                VOICE_CALL_ROOT_FILTER,
                VOICE_CALL_SIMULATOR_EXCLUSION_FILTER,
            ],
            "interval": "day",
            "metric_id": "traffic",
            "observe_type": "trace",
        }
    )

    # The worker and the cache key see the toggle, so a refreshed or
    # background read cannot serve the unfiltered population.
    assert VOICE_CALL_SIMULATOR_EXCLUSION_FILTER in identity["filters"]
    assert VOICE_CALL_ROOT_FILTER in identity["filters"]


@override_settings(EXACT_AGGREGATION_TASK_QUEUE="exact_aggregation")
def test_refreshed_voice_graph_worker_counts_the_listed_calls(
    auth_client, voice_fixture
):
    # The chart's refresh button hands the read to the exact-aggregation
    # worker, whose payload every later page load is served from.
    from tracer.tasks import exact_aggregation

    with patch(
        "tracer.tasks.exact_aggregation.refresh_exact_aggregation_snapshot.apply_async"
    ) as enqueue:
        response = auth_client.post(
            "/tracer/trace/get_graph_methods/?refresh=true",
            {
                "project_id": voice_fixture["project_id"],
                "interval": "day",
                "req_data_config": {"id": "traffic", "type": "SYSTEM_METRIC"},
                "filters": _window_filter(voice_fixture["window"]),
                **_voice_scope(True),
            },
            format="json",
        )

    assert response.status_code == status.HTTP_200_OK, response.content
    assert enqueue.call_count == 1
    task = enqueue.call_args.kwargs["kwargs"]
    payload = exact_aggregation._observe_payload(task["namespace"], task["identity"])

    assert payload["query_status"] == "complete"
    assert sum(point["value"] for point in payload["data"]) == len(CALLS[True])


@pytest.mark.parametrize("remove_simulation_calls", [False, True])
def test_eval_and_annotation_membership_selects_the_listed_calls(
    voice_fixture, remove_simulation_calls
):
    # Eval and annotation graphs compile the same leaves as membership.
    leaves = [VOICE_CALL_ROOT_FILTER]
    if remove_simulation_calls:
        leaves.append(VOICE_CALL_SIMULATOR_EXCLUSION_FILTER)
    sql, params = compile_exact_graph_filter_predicates(
        leaves,
        project_id=voice_fixture["project_id"],
        observe_type="trace",
        annotation_label_ids=(),
    )
    start, end = voice_fixture["window"]

    result = V2AnalyticsQueryService().execute_ch_query(
        f"""
        SELECT DISTINCT trace_id FROM spans FINAL
        WHERE project_id = toUUID(%(project_id)s) AND is_deleted = 0
          AND start_time >= %(snapshot_start_date)s
          AND start_time < %(snapshot_end_date)s
          AND {sql}
        """,
        {
            **params,
            "project_id": voice_fixture["project_id"],
            "snapshot_start_date": start,
            "snapshot_end_date": end,
        },
    )

    assert {row["trace_id"] for row in result.data} == {
        voice_fixture["trace_ids"][trace] for trace in CALLS[remove_simulation_calls]
    }
