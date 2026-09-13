"""Exact-reference public GET and isolated CH25 six-key RMT regressions.

No ORM rows or server sockets: tenant ownership is mocked; all DDL/inserts are
inside the imported disposable chdb fixture. Bare GET remains independently
covered by test_ch25_trace_detail_v2.py.
"""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from django.http import QueryDict
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from tracer.serializers.observation_span import (
    ObservationSpanDetailResponseSerializer,
    SpanReferenceQuerySerializer,
)
from tracer.services.clickhouse.application_read_policy import (
    UNLIMITED_STATEMENT_SETTINGS,
)
from tracer.services.clickhouse.v2 import physical_span_detail_reads as reads
from tracer.tests.test_span_physical_identity_latest import (
    OTHER_PROJECT,
    PROJECT,
    START,
)
from tracer.tests.test_span_physical_identity_latest import engine as engine
from tracer.views import observation_span as views


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    yield


HOUR = START.replace(tzinfo=UTC)
WINNER = HOUR + timedelta(minutes=20, microseconds=123456)


def selector(**changes):
    return {
        "project_id": PROJECT,
        "trace_id": "trace",
        "start_hour": HOUR.isoformat(),
        "observation_type": "span",
        "service_name": "service-a",
        "expected_start_time": WINNER.isoformat(),
        "expected_version": "2",
        **changes,
    }


def validated(**changes):
    serializer = SpanReferenceQuerySerializer(data=selector(**changes))
    assert serializer.is_valid(), serializer.errors
    return serializer.validated_data


def winner(**changes):
    return {
        "project_id": PROJECT,
        "trace_id": "trace",
        "id": "span",
        "start_hour": HOUR,
        "start_time": WINNER,
        "observation_type": "span",
        "service_name": "service-a",
        "_version": "2",
        "is_deleted": 0,
        "project_version_id": None,
        "input": "plain input",
        "output": '{"answer": false}',
        "span_attributes": '{"key":"extra","nested":{"x":0}}',
        "attrs_string": {"key": "map", "other": "value"},
        "attrs_number": {"zero": 0},
        "attrs_bool": {"false": 0},
        **changes,
    }


def execute(reader, **changes):
    return reads.read_physical_span_detail(
        analytics=reader,
        span_id="span",
        reference=validated(**changes),
        authorized_project_id=PROJECT,
    )


@pytest.mark.parametrize("missing", selector())
def test_selector_is_all_or_none(missing):
    query = selector()
    del query[missing]
    serializer = SpanReferenceQuerySerializer(data=query)
    assert not serializer.is_valid() and missing in serializer.errors


@pytest.mark.parametrize("repeated", selector())
def test_repeated_selector_never_chooses_first_or_last(repeated):
    query = QueryDict(mutable=True)
    query.update(selector())
    query.appendlist(repeated, selector()[repeated])
    serializer = SpanReferenceQuerySerializer(data=query)
    assert not serializer.is_valid() and repeated in serializer.errors


@pytest.mark.parametrize(
    "value",
    [
        2,
        2.0,
        True,
        None,
        "",
        "-1",
        "+1",
        "01",
        "1.0",
        "1e3",
        "18446744073709551616",
        "9" * 30,
    ],
)
def test_version_requires_exact_uint64_string(value):
    serializer = SpanReferenceQuerySerializer(data=selector(expected_version=value))
    assert not serializer.is_valid() and "expected_version" in serializer.errors


@pytest.mark.parametrize("value", ["0", "9007199254740993", "18446744073709551615"])
def test_version_preserves_zero_and_uint64_beyond_javascript_integer(value):
    assert validated(expected_version=value)["expected_version"] == value


@pytest.mark.parametrize(
    "key,value",
    [
        ("start_hour", "2026-08-08T12:00:00"),
        ("start_hour", "2026-08-08T12:00:00.000001Z"),
        ("start_hour", "2026-08-08T12:30:00Z"),
        ("expected_start_time", "2026-08-08T12:20:00.1234567Z"),
        ("expected_start_time", "2026-08-08T13:00:00Z"),
        ("expected_start_time", "invalid"),
        ("project_id", "invalid"),
        ("service_name", None),
        ("observation_type", None),
    ],
)
def test_invalid_timestamp_or_physical_selector_rejected(key, value):
    serializer = SpanReferenceQuerySerializer(data=selector(**{key: value}))
    assert not serializer.is_valid() and key in serializer.errors


def test_timezone_normalization_and_literal_blank_discriminators():
    actual = validated(
        start_hour="2026-08-08T17:30:00+05:30",
        expected_start_time="2026-08-08T17:50:00.123456+05:30",
        service_name="",
        observation_type="",
    )
    assert actual["start_hour"] == HOUR and actual["expected_start_time"] == WINNER
    assert actual["service_name"] == actual["observation_type"] == ""
    assert validated(service_name=" service ")["service_name"] == " service "
    empty = SpanReferenceQuerySerializer(data={})
    assert empty.is_valid() and empty.validated_data == {}


def test_sql_has_only_complete_immutable_identity_and_no_statement_abort_caps():
    reader = MagicMock()
    reader.execute_ch_query.return_value = SimpleNamespace(data=[winner()])
    assert execute(reader)["start_time"] == WINNER
    sql, params = reader.execute_ch_query.call_args.args
    settings = reader.execute_ch_query.call_args.kwargs["settings"]
    assert set(reader.execute_ch_query.call_args.kwargs) == {"settings"}
    assert "FROM spans FINAL" in sql
    assert "toJSONString(resource_attrs) AS resource_attrs" in sql
    assert "GROUP BY" not in sql and "LIMIT" not in sql
    assert "expected" not in sql and all("expected" not in key for key in params)
    raw = sql.split("PREWHERE", 1)[1]
    assert "is_deleted" not in raw and "_version" not in raw
    assert "start_time =" not in raw
    assert set(params) == {
        "physical_project_id",
        "physical_trace_id",
        "physical_span_id",
        "physical_hour_us",
        "physical_observation_type",
        "physical_service_name",
    }
    assert all(settings[key] == 0 for key in UNLIMITED_STATEMENT_SETTINGS)
    assert 0 < settings["max_memory_usage"] <= 36 * 1024**3
    assert settings["max_threads"] == 1
    for key in (
        "optimize_move_to_prewhere",
        "optimize_move_to_prewhere_if_final",
        "enable_optimize_predicate_expression_to_final_subquery",
        "query_plan_merge_expressions",
        "use_skip_indexes_if_final",
        "do_not_merge_across_partitions_select_final",
    ):
        assert settings[key] == 0


@pytest.mark.parametrize(
    "data,complete,code",
    [
        ([], True, "span_reference_not_found"),
        ([winner(is_deleted=1)], True, "span_reference_not_found"),
        ([winner(_version="3")], True, "span_reference_changed"),
        (
            [winner(start_time=WINNER + timedelta(microseconds=1))],
            True,
            "span_reference_changed",
        ),
        ([winner(service_name="wrong")], True, "span_reference_unavailable"),
        ([winner(project_id=OTHER_PROJECT)], True, "span_reference_unavailable"),
        ([winner(_version=2)], True, "span_reference_unavailable"),
        ([winner(_version="18446744073709551616")], True, "span_reference_unavailable"),
        ([winner(), winner()], True, "span_reference_unavailable"),
        (None, True, "span_reference_unavailable"),
        ([winner()], False, "span_reference_unavailable"),
    ],
)
def test_read_failures_never_fall_back_or_publish_partial(data, complete, code):
    reader = MagicMock()
    reader.execute_ch_query.return_value = SimpleNamespace(data=data, complete=complete)
    with pytest.raises(reads.PhysicalSpanDetailError, match=code):
        execute(reader)
    reader.execute_ch_query.assert_called_once()


def public_get(monkeypatch, query, reader, *, allowed=True):
    manager = MagicMock()
    manager.filter.return_value.exists.return_value = allowed
    manager.filter.return_value.values_list.return_value.__getitem__.return_value = [
        PROJECT
    ]
    monkeypatch.setattr(views.Project, "no_workspace_objects", manager)
    service = MagicMock(return_value=reader)
    monkeypatch.setattr(views, "V2AnalyticsQueryService", service)
    monkeypatch.setattr(
        views.CustomEvalConfig.no_workspace_objects,
        "filter",
        MagicMock(side_effect=AssertionError("No bare-ID eval ownership lookup")),
    )
    request = Request(APIRequestFactory().get("/tracer/observation-span/span/", query))
    request.organization = SimpleNamespace(id="org")
    request.workspace = SimpleNamespace(id="workspace", is_default=False)
    request.user = SimpleNamespace(organization=request.organization)
    view = views.ObservationSpanView()
    view.request = request
    response = view.retrieve(request, pk="span")
    return response, manager, service


def test_public_reference_envelope_authority_and_explicit_unverified_evals(monkeypatch):
    reader = MagicMock()
    reader.execute_ch_query.return_value = SimpleNamespace(data=[winner()])
    response, manager, _ = public_get(monkeypatch, selector(), reader)
    assert response.status_code == 200, response.data
    body = json.loads(JSONRenderer().render(response.data))
    result = body["result"]
    span = result["observation_span"]
    assert body["status"] is True
    assert span["project_id"] == span["project"] == PROJECT
    assert span["trace_id"] == span["trace"] == "trace"
    assert span["span_id"] == span["id"] == "span"
    assert span["service_name"] == "service-a" and span["observation_type"] == "span"
    assert span["_version"] == "2"
    assert span["start_time"] == "2026-08-08T12:20:00.123456Z"
    assert span["start_hour"] == "2026-08-08T12:00:00.000000Z"
    assert span["input"] == "plain input" and span["output"] == {"answer": False}
    assert span["span_attributes"] == {
        "key": "extra",
        "nested": {"x": 0},
        "other": "value",
        "zero": 0,
        "false": False,
    }
    assert result["evals_metrics"] is None
    assert result["enrichment"] == {
        "evals": {
            "status": "unverified",
            "complete": False,
            "reason": "full_physical_identity_not_supported",
        }
    }
    response_schema = ObservationSpanDetailResponseSerializer(data=body)
    assert response_schema.is_valid(), response_schema.errors
    args, kwargs = manager.filter.call_args
    assert kwargs["id"] == UUID(PROJECT) and kwargs["deleted"] is False
    assert "organization" in kwargs
    assert {key for key, _ in args[0].children} == {"organization", "workspace"}
    reader.execute_ch_query.assert_called_once()


def test_unauthorized_project_cannot_construct_ch_service(monkeypatch):
    reader = MagicMock()
    response, manager, service = public_get(
        monkeypatch, selector(), reader, allowed=False
    )
    assert response.status_code == 404
    assert "span_reference_not_found" in json.dumps(response.data)
    manager.filter.return_value.exists.assert_called_once()
    service.assert_not_called()
    reader.execute_ch_query.assert_not_called()


def test_incomplete_public_selector_fails_before_any_authority_or_ch_read(monkeypatch):
    reader = MagicMock()
    response, manager, service = public_get(
        monkeypatch, {"project_id": PROJECT}, reader
    )
    assert response.status_code == 400
    manager.filter.assert_not_called()
    service.assert_not_called()


def test_wrapped_bare_get_keeps_legacy_reader_and_enrichment_shape(monkeypatch):
    from tracer.services.clickhouse.v2 import trace_detail_reads

    old_reader = MagicMock(
        return_value=SimpleNamespace(
            spans=[
                winner(
                    cost=0.123456789012,
                    attrs_string={"mixed": "string"},
                    attrs_number={"mixed": 0},
                    attrs_bool={"mixed": 0},
                )
            ],
            evals=[],
        )
    )
    monkeypatch.setattr(trace_detail_reads, "read_span_detail", old_reader)
    exact_reader = MagicMock(
        side_effect=AssertionError("Bare GET must not use reference reader")
    )
    monkeypatch.setattr(views, "read_physical_span_detail", exact_reader)
    response, _, _ = public_get(monkeypatch, {}, MagicMock())
    assert response.status_code == 200, response.data
    assert response.data["result"]["observation_span"]["id"] == "span"
    assert response.data["result"]["evals_metrics"] == {}
    assert "enrichment" not in response.data["result"]
    assert "start_hour" not in response.data["result"]["observation_span"]
    assert "resource_attributes" not in response.data["result"]["observation_span"]
    assert response.data["result"]["observation_span"]["cost"] == 0.123457
    assert (
        response.data["result"]["observation_span"]["span_attributes"]["mixed"]
        == "string"
    )
    old_reader.assert_called_once()
    assert old_reader.call_args.kwargs["project_ids"] == [PROJECT]
    exact_reader.assert_not_called()


@pytest.mark.parametrize(
    "rows,expected_status,code",
    [
        ([], 404, "span_reference_not_found"),
        ([winner(is_deleted=1)], 404, "span_reference_not_found"),
        ([winner(_version="3")], 409, "span_reference_changed"),
        ([winner(service_name="wrong")], 503, "span_reference_unavailable"),
    ],
)
def test_public_reference_failures_are_explicit(
    monkeypatch, rows, expected_status, code
):
    reader = MagicMock()
    reader.execute_ch_query.return_value = SimpleNamespace(data=rows)
    response, _, _ = public_get(monkeypatch, selector(), reader)
    assert response.status_code == expected_status
    assert code in json.dumps(response.data)
    assert "observation_span" not in response.data


def test_arbitrary_query_failure_is_unavailable_not_mislabeled_timeout(monkeypatch):
    reader = MagicMock()
    reader.execute_ch_query.side_effect = RuntimeError("arbitrary database failure")
    response, _, _ = public_get(monkeypatch, selector(), reader)
    assert response.status_code == 503
    assert "span_reference_unavailable" in json.dumps(response.data)
    assert "timeout" not in json.dumps(response.data).lower()


@pytest.mark.parametrize(
    "raw,expected",
    [
        (
            '{"service.name":"voice","nested":{"zero":0,"flag":false}}',
            {"service.name": "voice", "nested": {"zero": 0, "flag": False}},
        ),
        ("{}", {}),
        ("", {}),
        (None, {}),
        ("not json", {}),
    ],
)
def test_exact_resource_attributes_are_parsed_without_extra_reads(
    monkeypatch, raw, expected
):
    reader = MagicMock()
    reader.execute_ch_query.return_value = SimpleNamespace(
        data=[winner(resource_attrs=raw)]
    )
    response, _, _ = public_get(monkeypatch, selector(), reader)
    assert response.status_code == 200, response.data
    span = response.data["result"]["observation_span"]
    assert span["resource_attributes"] == expected
    reader.execute_ch_query.assert_called_once()


@pytest.mark.parametrize(
    "cost", [0.123456789012, 0.000000012345, 0, -0.123456789012, None]
)
def test_exact_context_cost_has_no_display_rounding(monkeypatch, cost):
    reader = MagicMock()
    reader.execute_ch_query.return_value = SimpleNamespace(data=[winner(cost=cost)])
    response, _, _ = public_get(monkeypatch, selector(), reader)
    assert response.status_code == 200, response.data
    span = json.loads(JSONRenderer().render(response.data))["result"][
        "observation_span"
    ]
    assert span["cost"] == cost


def test_exact_attributes_follow_canonical_mixed_map_precedence(monkeypatch):
    reader = MagicMock()
    reader.execute_ch_query.return_value = SimpleNamespace(
        data=[
            winner(
                attrs_string={
                    "bool_wins": "string",
                    "number_wins": "string",
                    "extra_wins": "string",
                },
                attrs_number={"bool_wins": 7, "number_wins": 0, "extra_wins": 8},
                attrs_bool={"bool_wins": 0, "extra_wins": 1},
                span_attributes='{"extra_wins":{"nested":0}}',
            )
        ]
    )
    response, _, _ = public_get(monkeypatch, selector(), reader)
    assert response.status_code == 200, response.data
    attrs = response.data["result"]["observation_span"]["span_attributes"]
    assert attrs == {"bool_wins": False, "number_wins": 0, "extra_wins": {"nested": 0}}
    assert attrs["bool_wins"] is False and type(attrs["number_wins"]) is int
    reader.execute_ch_query.assert_called_once()


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("plain text", "plain text"),
        ("", ""),
        (None, None),
        ("null", None),
        ("false", False),
        ("0", 0),
        ('"hello"', "hello"),
        ('{"a":[null,false,0]}', {"a": [None, False, 0]}),
        ('["x",false,0]', ["x", False, 0]),
    ],
)
def test_exact_context_preserves_typed_or_plain_payloads(monkeypatch, raw, expected):
    reader = MagicMock()
    reader.execute_ch_query.return_value = SimpleNamespace(
        data=[winner(input=raw, output=raw)]
    )
    response, _, _ = public_get(monkeypatch, selector(), reader)
    assert response.status_code == 200, response.data
    span = json.loads(JSONRenderer().render(response.data))["result"][
        "observation_span"
    ]
    for key in ("input", "output"):
        assert span[key] == expected and type(span[key]) is type(expected)


def test_exact_voice_promotions_use_selected_payload_without_losing_identity(
    monkeypatch,
):
    reader = MagicMock()
    transcript = [
        {"role": "user", "content": "hello"},
        {"role": "bot", "content": "first"},
        {"role": "assistant", "content": "last"},
    ]
    attrs = {
        "provider_transcript": transcript,
        "recording_url": "https://example.test/mirrored.wav",
        "vapi.call_id": "selected-call",
        "call.total_turns": 0,
        "call.talk_ratio": 0.5,
        "call.user_wpm": 1,
        "call.bot_wpm": 2,
        "numUserInterrupted": 0,
        "ai_interruption_rate": 0,
        "avg_agent_latency_ms": 1.25,
        "turnLatencyAverage": 2.5,
        "raw_log": {
            "recordingUrl": "https://example.test/stale.wav",
            "stereoRecordingUrl": "https://example.test/stereo.wav",
            "status": "ended",
            "durationSeconds": 3.25,
            "endedReason": "completed",
            "summary": "summary",
        },
    }
    reader.execute_ch_query.return_value = SimpleNamespace(
        data=[
            winner(
                input="null",
                output="",
                span_attributes=json.dumps(attrs),
                resource_attrs='{"service.name":"selected-resource"}',
                cost=0.123456789012,
            )
        ]
    )
    response, _, _ = public_get(monkeypatch, selector(), reader)
    assert response.status_code == 200, response.data
    span = json.loads(JSONRenderer().render(response.data))["result"][
        "observation_span"
    ]
    assert span["is_voice"] is True and span["transcript"] == transcript
    assert span["input"] == "hello" and span["output"] == "last"
    assert span["recording_url"] == "https://example.test/mirrored.wav"
    assert span["stereo_recording_url"] == "https://example.test/stereo.wav"
    assert span["call_status"] == "ended" and span["duration_seconds"] == 3.25
    assert span["ended_reason"] == "completed" and span["provider_summary"] == "summary"
    assert span["provider_call_id"] == "selected-call"
    assert span["metrics"] == {
        "turn_count": 0,
        "talk_ratio": 0.5,
        "user_wpm": 1,
        "bot_wpm": 2,
        "user_interruptions": 0,
        "ai_interruption_rate": 0,
        "avg_agent_latency_ms": 1.25,
        "turn_latency_avg": 2.5,
    }
    assert span["resource_attributes"] == {"service.name": "selected-resource"}
    assert span["cost"] == 0.123456789012
    assert span["project_id"] == span["project"] == PROJECT
    assert span["trace_id"] == span["trace"] == "trace"
    assert span["span_id"] == span["id"] == "span"
    assert span["start_hour"] == "2026-08-08T12:00:00.000000Z"
    assert span["start_time"] == "2026-08-08T12:20:00.123456Z"
    assert span["_version"] == "2"
    reader.execute_ch_query.assert_called_once()


@pytest.mark.parametrize(
    "trigger", ["conversation", "vapi.call_id", "provider_transcript", "call_logs"]
)
@pytest.mark.parametrize("keep_content", [False, True])
def test_exact_voice_detection_and_raw_log_transcript_fallback(
    monkeypatch, trigger, keep_content
):
    reader = MagicMock()
    attrs = {
        "raw_log": json.dumps(
            {
                "id": "raw-call",
                "endedReason": "done",
                "durationSeconds": 4.5,
                "artifact": {
                    "recording": {
                        "mono": {"combinedUrl": "https://example.test/mono.wav"},
                        "stereoUrl": "https://example.test/stereo.wav",
                    }
                },
                "messages": [
                    {"role": "user", "message": "hello", "time": 1},
                    {"role": "tool", "message": "not a turn"},
                    {"role": "bot", "content": "bye"},
                ],
            }
        ),
    }
    if trigger != "conversation":
        attrs[trigger] = None  # Presence, not truthiness, is the legacy detector.
    observation_type = "conversation" if trigger == "conversation" else "span"
    reader.execute_ch_query.return_value = SimpleNamespace(
        data=[
            winner(
                observation_type=observation_type,
                span_attributes=json.dumps(attrs),
                input='{"keep":false}' if keep_content else "null",
                output="[0,false]" if keep_content else "",
            )
        ]
    )
    response, _, _ = public_get(
        monkeypatch, selector(observation_type=observation_type), reader
    )
    assert response.status_code == 200, response.data
    span = response.data["result"]["observation_span"]
    assert span["is_voice"] is True
    assert span["transcript"] == [
        {"role": "user", "content": "hello"},
        {"role": "bot", "content": "bye"},
    ]
    assert span["input"] == ({"keep": False} if keep_content else "hello")
    assert span["output"] == ([0, False] if keep_content else "bye")
    assert span["recording_url"] == "https://example.test/mono.wav"
    assert span["stereo_recording_url"] == "https://example.test/stereo.wav"
    assert span["provider_call_id"] == "raw-call"
    assert span["ended_reason"] == "done" and span["duration_seconds"] == 4.5
    reader.execute_ch_query.assert_called_once()


def test_eval_id_resolution_and_exact_get_share_the_same_context_helper(monkeypatch):
    from model_hub.utils.eval_playground_span_context import build_span_context
    from model_hub.views.separate_evals import _build_span_context
    from tracer.services.clickhouse import v2

    assert _build_span_context is build_span_context is views.build_span_context
    monkeypatch.setattr(
        v2, "get_reader", MagicMock(side_effect=AssertionError("No ID context lookup"))
    )
    reader = MagicMock()
    reader.execute_ch_query.return_value = SimpleNamespace(
        data=[
            winner(
                resource_attrs='{"resource":"selected"}',
                input="0",
                output="false",
                cost=0.000000012345,
                metadata_json='{"a":false}',
                tags=["selected"],
            )
        ]
    )
    response, _, _ = public_get(monkeypatch, selector(), reader)
    assert response.status_code == 200, response.data
    span = response.data["result"]["observation_span"]
    # The full existing ordinary context contract, not only its new fields.
    expected = _build_span_context(SimpleNamespace(**span))
    for key, value in expected.items():
        assert span[key] == value
    assert "is_voice" not in span
    v2.get_reader.assert_not_called()
    reader.execute_ch_query.assert_called_once()


@pytest.fixture
def detail_engine(request):
    run, insert = request.getfixturevalue("engine")
    for definition in (
        "status_message String DEFAULT ''",
        "tags Array(String)",
        "span_events String DEFAULT '[]'",
        "custom_eval_config_id Nullable(UUID)",
        "trace_session_id Nullable(UUID)",
        "metadata Map(String, String)",
        "resource_attrs JSON(max_dynamic_paths=512)",
    ):
        run(f"ALTER TABLE spans ADD COLUMN {definition}")

    class Reader:
        def __init__(self):
            self.calls = []

        def execute_ch_query(self, sql, params, *, settings):
            self.calls.append((sql, params, settings))
            sql += " SETTINGS " + ", ".join(
                f"{key}={value!r}" for key, value in settings.items()
            )
            rows = run(sql, params)
            for row in rows:
                row["start_hour"] = datetime.fromisoformat(row["start_hour"])
            return SimpleNamespace(data=rows)

    return Reader(), insert


@pytest.mark.parametrize("corrected_minute", [0, 10, 59])
def test_rmt_complete_hour_winner_not_old_exact_timestamp(
    detail_engine, corrected_minute
):
    reader, insert = detail_engine
    insert(start_time=WINNER, _version=1, input="old", attrs_number={"key": 1})
    corrected = HOUR + timedelta(minutes=corrected_minute, microseconds=654321)
    insert(start_time=corrected, _version=2, input="new", attrs_number={})
    with pytest.raises(reads.PhysicalSpanDetailError, match="span_reference_changed"):
        execute(reader, expected_version="1")
    latest = execute(reader, expected_start_time=corrected.isoformat())
    assert latest["start_time"] == corrected and latest["_version"] == "2"
    assert latest["input"] == "new" and latest["attrs_number"] == {}


def test_rmt_tombstone_at_different_time_never_resurrects(detail_engine):
    reader, insert = detail_engine
    insert(start_time=WINNER, _version=1)
    insert(start_time=HOUR + timedelta(minutes=1), _version=2, is_deleted=1)
    with pytest.raises(reads.PhysicalSpanDetailError, match="span_reference_not_found"):
        execute(reader, expected_version="1")


@pytest.mark.parametrize(
    "collision",
    [
        {"project_id": OTHER_PROJECT},
        {"trace_id": "other-trace"},
        {"id": "other-span"},
        {"service_name": "other-service"},
        {"observation_type": "other-type"},
        {"start_time": WINNER + timedelta(hours=1)},
    ],
)
@pytest.mark.parametrize("deleted", [0, 1])
def test_rmt_every_immutable_discriminator_survives_collisions(
    detail_engine, collision, deleted
):
    reader, insert = detail_engine
    insert(start_time=WINNER, _version=2, input="selected")
    insert(
        **{
            "start_time": WINNER,
            "_version": 99,
            "input": "collision",
            "is_deleted": deleted,
            **collision,
        }
    )
    result = execute(reader)
    assert result["input"] == "selected" and result["_version"] == "2"
    assert result["start_time"] == WINNER


def test_public_rmt_empty_and_null_payloads_belong_to_one_latest_winner(
    monkeypatch, detail_engine
):
    reader, insert = detail_engine
    insert(
        start_time=WINNER - timedelta(minutes=5),
        _version=1,
        input="old",
        output='{"old": true}',
        attrs_number={"zero": 99},
        attrs_bool={"flag": 1},
        attributes_extra='{"stale":true}',
        tags=["old"],
        status="error",
        cost=99,
    )
    insert(
        start_time=WINNER,
        _version=2,
        input="",
        output="null",
        attrs_number={"zero": 0},
        attrs_bool={"flag": 0},
        attributes_extra="{}",
        tags=[],
        status=None,
        cost=0,
    )
    response, _, _ = public_get(monkeypatch, selector(), reader)
    assert response.status_code == 200, response.data
    span = response.data["result"]["observation_span"]
    assert span["input"] == "" and span["output"] is None
    assert span["span_attributes"] == {"zero": 0, "flag": False}
    assert span["tags"] == [] and span["status"] is None and span["cost"] == 0
    assert span["_version"] == "2"


def test_rmt_maximum_uint64_and_blank_discriminators(detail_engine):
    reader, insert = detail_engine
    insert(
        start_time=WINNER,
        _version=2**64 - 1,
        service_name="",
        observation_type="",
        input="max",
    )
    result = execute(
        reader, expected_version=str(2**64 - 1), service_name="", observation_type=""
    )
    assert result["_version"] == "18446744073709551615" and result["input"] == "max"


def test_public_get_executes_real_rmt_winner_and_serializes_canonical_reference(
    monkeypatch, detail_engine
):
    reader, insert = detail_engine
    insert(start_time=WINNER - timedelta(minutes=5), _version=1, input="old")
    insert(start_time=WINNER, _version=2, input="selected")
    response, _, _ = public_get(monkeypatch, selector(), reader)
    assert response.status_code == 200, response.data
    span = json.loads(JSONRenderer().render(response.data))["result"][
        "observation_span"
    ]
    assert span["input"] == "selected" and span["_version"] == "2"
    assert span["start_time"] == "2026-08-08T12:20:00.123456Z"
    assert len(reader.calls) == 1


def test_public_rmt_resources_cost_and_structured_content_share_latest_winner(
    monkeypatch, detail_engine, request
):
    reader, insert = detail_engine
    insert(
        start_time=WINNER - timedelta(minutes=10),
        _version=1,
        resource_attrs='{"stale":true}',
        input="old",
        output="old",
        cost=99,
    )
    resource = {"service.name": "selected", "nested": {"zero": 0, "flag": False}}
    insert(
        start_time=WINNER,
        _version=2,
        resource_attrs=json.dumps(resource),
        input="false",
        output='{"answer":[0,false,null]}',
        cost=0.123456789012,
    )
    insert(
        start_time=WINNER,
        _version=99,
        service_name="other-service",
        resource_attrs='{"collision":true}',
        input="collision",
        cost=99,
    )
    stored = request.getfixturevalue("engine")[0](
        "SELECT cost FROM spans FINAL WHERE service_name = 'service-a'"
    )[0]
    response, _, _ = public_get(monkeypatch, selector(), reader)
    assert response.status_code == 200, response.data
    span = json.loads(JSONRenderer().render(response.data))["result"][
        "observation_span"
    ]
    # The native JSON column expands dotted paths, just as the existing
    # CHSpanReader's toJSONString(resource_attrs) does for eval ID resolution.
    assert span["resource_attributes"] == {
        "service": {"name": "selected"},
        "nested": {"zero": 0, "flag": False},
    }
    assert span["input"] is False and span["output"] == {"answer": [0, False, None]}
    assert span["cost"] == stored["cost"] and span["cost"] != round(stored["cost"], 6)
    assert span["_version"] == "2"
    assert span["start_time"] == "2026-08-08T12:20:00.123456Z"
    assert len(reader.calls) == 1


def test_public_rmt_voice_promotions_do_not_mix_stale_attributes_or_resources(
    monkeypatch, detail_engine, request
):
    reader, insert = detail_engine
    insert(
        start_time=WINNER - timedelta(minutes=10),
        _version=1,
        resource_attrs='{"stale":true}',
        input="stale",
        output="stale",
        attributes_extra='{"provider_transcript":[{"role":"user","content":"stale"}]}',
    )
    attrs = {
        "vapi.call_id": "new-call",
        "raw_log": {
            "messages": [
                {"role": "user", "message": "new user"},
                {"role": "bot", "message": "new bot"},
            ]
        },
    }
    insert(
        start_time=WINNER,
        _version=2,
        resource_attrs="{}",
        input="null",
        output="",
        attributes_extra=json.dumps(attrs),
        cost=0.000000012345,
    )
    stored = request.getfixturevalue("engine")[0]("SELECT cost FROM spans FINAL")[0]
    response, _, _ = public_get(monkeypatch, selector(), reader)
    assert response.status_code == 200, response.data
    span = response.data["result"]["observation_span"]
    assert span["resource_attributes"] == {} and "stale" not in span["span_attributes"]
    assert span["input"] == "new user" and span["output"] == "new bot"
    assert span["provider_call_id"] == "new-call"
    assert span["cost"] == stored["cost"] and span["cost"] != round(stored["cost"], 6)
    assert span["_version"] == "2"
    assert len(reader.calls) == 1
