"""Voice replay on real SELECT-only inline fixtures; no configured DB/DDL.

Native tests exercise generated selection and content SQL, not HTTP or deployed
performance. Explicit transport fixtures below only verify failure contracts.
"""

from datetime import timedelta
import json
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import observe_candidate_hydration as hydration
import replay_observe_filters as replay
import replay_observe_queries_readonly as queries


@pytest.fixture(scope="module", autouse=True)
def candidate_runtime():
    from django.apps import apps
    import probe_long_text_index_contract_readonly as setup

    if not apps.ready:
        setup.initialize()


@pytest.fixture
def voice():
    from tracer.services.clickhouse.v2.query_builders.voice_call_list import (
        VoiceCallListQueryBuilderV2,
    )
    from tracer.tests import test_voice_physical_replay as fixture

    return VoiceCallListQueryBuilderV2, fixture


def request_case(fixture, *, days=7, filters=(), page_size=2, **params):
    window = {
        "column_id": "created_at",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [
                (fixture.NOW - timedelta(days=days)).isoformat(),
                fixture.NOW.isoformat(),
            ],
        },
    }
    return {
        "surface": "voice_calls",
        "request": {
            "method": "GET",
            "path": queries.VOICE_LIST_PATH,
            "target_rows": page_size,
            "params": {
                "project_id": fixture.PROJECT,
                "page_size": page_size,
                "page": 1,
                "cursor_mode": True,
                "filters": json.dumps([window, *filters]),
                **params,
            },
        },
    }


def attribute(key, value, kind="text"):
    return {
        "column_id": key,
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": kind,
            "filter_op": "equals",
            "filter_value": value,
        },
    }


def adapter_for(fixture):
    return queries.CandidateQueries(
        SimpleNamespace(relational_metadata=None),
        {"scope": {"project_id": fixture.PROJECT}},
        [fixture.PROJECT],
    )


class NativeReader:
    supports_bounded_speculative_reads = False

    def __init__(self, engine, fixture, rows):
        self.engine, self.fixture, self.rows = engine, fixture, rows
        self.calls = []

    def remaining_read_ms(self):
        return 60_000

    def close(self):
        pass

    def execute_ch_query(self, sql, params, **kwargs):
        from tracer.services.clickhouse.query_service import QueryResult

        queries.validate_select(sql, params, [self.fixture.PROJECT])
        self.calls.append((sql, params, kwargs))
        rows = self.fixture.execute_voice(self.engine, None, self.rows, (sql, params))
        return QueryResult(rows, len(rows), "clickhouse", 1.0)


@pytest.mark.parametrize("days", [7, 30, 365])
@pytest.mark.parametrize("kind", ["string", "number", "long-string"])
@pytest.mark.parametrize("has_match", [True, False])
def test_native_adapter_selects_and_hydrates_typed_voice_roots(
    voice, days, kind, has_match
):
    engine = pytest.importorskip("chdb")
    _, fixture = voice
    long_value = "recording-" + "x" * 1001
    leaves = {
        "string": attribute("current", "value" if has_match else "absent"),
        "number": attribute("duration", 2 if has_match else 999, "number"),
        "long-string": attribute("recording", long_value if has_match else "z" * 1001),
    }

    class ContentFixture:
        def query(self, sql, fmt):
            return engine.query(
                sql.replace(
                    "map('current', 'value')",
                    f"map('current', 'value', 'recording', '{long_value}')",
                ),
                fmt,
            )

    case = request_case(fixture, days=days, filters=[leaves[kind]])
    rows = [
        fixture.physical_row(
            trace_id=f"call-{index}",
            id=f"root-{index}",
            observation_type="conversation",
            start_time=fixture.NOW - timedelta(minutes=index + 1),
        )
        for index in range(3)
    ]
    reader = NativeReader(ContentFixture(), fixture, rows)
    payload = adapter_for(fixture).entity_list(
        reader, case, queries.normalize_filters(case["request"])
    )
    assert payload["query_complete"] and payload["query_exact"]
    assert [row["trace_id"] for row in payload["table"]] == (
        ["call-0", "call-1"] if has_match else []
    )
    content_calls = [
        call for call in reader.calls if "content_root_identities" in call[1]
    ]
    assert len(content_calls) == int(has_match)
    assert payload["query_layer_coverage"]["content"]
    assert not payload["query_layer_coverage"]["full_public_request"]
    for row in payload["table"]:
        assert row["provider"] is None
        assert row["attrs_string"]["recording"] == long_value
        assert row["attrs_number"]["duration"] == 2
        assert row["attrs_bool"]["flag"] == 1
        assert json.loads(row["span_attributes"]) == {"current": True}
        assert "call_logs" not in row["attrs_string"]


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_native_full_run_routes_voice_without_promoting_fixture_timing(voice, method):
    engine = pytest.importorskip("chdb")
    _, fixture = voice
    reader = NativeReader(
        engine, fixture, [fixture.physical_row(observation_type="conversation")]
    )
    reader.deadline = time.monotonic() + 60
    adapter = adapter_for(fixture)
    adapter.args = SimpleNamespace(
        relational_metadata=None, safety_seconds=60, verify_trace_ids=False
    )
    case = {
        **request_case(fixture),
        "id": "voice-native-fixture",
        "blocked": None,
        "period": "7D",
        "target_ms": 5000,
    }
    if method == "POST":
        request = case["request"]
        request["method"] = "POST"
        request["body"] = request.pop("params")
        request["body"]["filters"] = json.loads(request["body"]["filters"])
    with patch.object(queries, "ReadOnlyExecutor", return_value=reader):
        result = adapter.run(case)
    assert result["status"] == "COMPLETE_UNVERIFIED"
    assert result["surface"] == "voice_calls" and result["result_rows"] == 1
    assert (
        result["query_layer_coverage"]["contract"]
        == "CH25_voice_physical_root_content.v1"
    )
    assert not result["http_e2e"] and not result["ui_e2e"]
    assert result["correctness"] == "UNVERIFIED"
    assert any("content_root_identities" in params for _, params, _ in reader.calls)
    # values() fixture timings, including QueryResult's synthetic 1ms, are
    # functional evidence only. No production latency assertion is made.


def test_native_voice_content_batches_and_rejects_latest_version_drift(voice):
    engine = pytest.importorskip("chdb")
    builder_cls, fixture = voice
    builder = fixture.make_builder(builder_cls)
    selected = [
        fixture.voice_root(trace_id=f"call-{index}", root_span_id=f"root-{index}")
        for index in range(3)
    ]
    rows = [
        fixture.physical_row(
            trace_id=row["trace_id"],
            id=row["root_span_id"],
            observation_type="conversation",
        )
        for row in selected
    ]
    reader = NativeReader(engine, fixture, rows)
    with patch("django.conf.settings.VOICE_CONTENT_MAX_BATCH_SIZE", 2):
        payload = hydration.hydrate_voice_page(
            reader, builder, {"query_complete": True, "table": selected}
        )
    assert [phase["rows"] for phase in payload["hydration_phases"]] == [2, 1]
    assert [
        len(params["content_root_identities"]) for _, params, _ in reader.calls
    ] == [2, 1]
    assert [row["trace_id"] for row in payload["table"]] == [
        row["trace_id"] for row in selected
    ]
    reader = NativeReader(engine, fixture, [*rows, {**rows[0], "_version": 3}])
    with pytest.raises(replay.ReplayError, match="IDENTITY_OR_VERSION_DRIFT"):
        hydration.hydrate_voice_page(
            reader, builder, {"query_complete": True, "table": selected}
        )


class TransportFixture:
    """Supplementary failure/dispatch fixture, never native SQL evidence."""

    def __init__(self, response):
        self.response, self.calls = response, []

    def remaining_read_ms(self):
        return 60_000

    def execute_ch_query(self, sql, params, **kwargs):
        self.calls.append((sql, params, kwargs))
        return SimpleNamespace(data=self.response(params))


def test_voice_dispatch_uses_public_normalization_and_metadata(voice):
    builder_cls, fixture = voice
    adapter = adapter_for(fixture)
    metadata = {"annotation_label_ids": ["label"], "eval_filter_metadata": {"eval": {}}}
    adapter.metadata = SimpleNamespace(builder_kwargs=lambda _: metadata)
    for flag, expected in (
        ("false", False),
        ("0", False),
        ("true", True),
        (True, True),
    ):
        case = request_case(fixture, remove_simulation_calls=flag)
        seen = {}

        def collect(read_page, builder, key, target, remaining):
            seen.update(builder=builder, key=key, target=target)
            return {"query_complete": True, "table": []}

        with patch.object(queries, "collect_selector_page", side_effect=collect):
            adapter.entity_list(TransportFixture(None), case, [])
        assert isinstance(seen["builder"], builder_cls)
        assert seen["key"] == "trace_id" and seen["target"] == 2
        assert not seen["builder"]._bounded_internal_scan
        assert seen["builder"].remove_simulation_calls is expected
        assert seen["builder"].annotation_label_ids == ["label"]
        assert seen["builder"].eval_filter_metadata == {"eval": {}}


@pytest.mark.parametrize(
    "change",
    [
        {"page": 2, "cursor_mode": False},
        {"cursor_mode": False},
        {"page_size": 3},
        {"project_id": "22222222-2222-4222-8222-222222222222"},
    ],
)
def test_voice_does_not_silently_replay_different_workload(voice, change):
    _, fixture = voice
    case = request_case(fixture)
    case["request"]["params"].update(change)
    reader = TransportFixture(None)
    with pytest.raises(
        replay.ReplayError, match="REPLAY_VOICE_FIRST_PAGE_WORKLOAD_MISMATCH"
    ):
        adapter_for(fixture).entity_list(reader, case, [])
    assert not reader.calls


@pytest.mark.parametrize(
    "params,error_key",
    [
        ({"remove_simulation_calls": "invalid-boolean"}, "remove_simulation_calls"),
        ({"page": 2}, "cursor_mode"),
    ],
)
def test_invalid_voice_serializer_input_is_rejected_before_queries(
    voice, params, error_key
):
    from rest_framework.exceptions import ValidationError

    _, fixture = voice
    reader = TransportFixture(None)
    case = request_case(fixture, **params)
    with pytest.raises(ValidationError) as error:
        adapter_for(fixture).entity_list(reader, case, [])
    assert set(error.value.detail) == {error_key}
    assert error.value.detail[error_key][0].code == "invalid"
    assert not reader.calls


def test_missing_duplicate_foreign_content_and_duplicate_selection_fail_closed(voice):
    builder_cls, fixture = voice
    builder, row = fixture.make_builder(builder_cls), fixture.voice_root()
    for content in (
        [],
        [row, row],
        [{**row, "_root_version": 3}],
        [{**row, "_root_service_name": "foreign"}],
        [{**row, "project_id": "22222222-2222-4222-8222-222222222222"}],
    ):
        with pytest.raises(replay.ReplayError, match="IDENTITY_OR_VERSION_DRIFT"):
            hydration.hydrate_voice_page(
                TransportFixture(lambda _: content),
                builder,
                {"query_complete": True, "table": [row]},
            )
    reader = TransportFixture(None)
    with pytest.raises(replay.ReplayError, match="DUPLICATE_VOICE_ROOT"):
        hydration.hydrate_voice_page(
            reader, builder, {"query_complete": True, "table": [row, row]}
        )
    assert not reader.calls
    with pytest.raises(replay.ReplayError, match="VOICE_CONTENT_FIELDS_MISSING"):
        hydration.hydrate_voice_page(
            TransportFixture(lambda _: [row]),
            builder,
            {"query_complete": True, "table": [row]},
        )


def test_memory_split_requires_every_identity_and_does_not_retry_other_failures(voice):
    from clickhouse_driver.errors import ServerException
    from tracer.tests.test_trace_root_physical_replay import content_identity_row

    builder_cls, fixture = voice
    builder = fixture.make_builder(builder_cls)
    rows = [
        fixture.voice_root(trace_id=f"call-{i}", root_span_id=f"root-{i}")
        for i in range(2)
    ]

    def response(params):
        identities = params["content_root_identities"]
        if len(identities) > 1:
            raise ServerException("synthetic memory failure", code=241)
        return [
            {
                **content_identity_row(identity),
                "provider": None,
                "span_attributes": "{}",
                "attrs_string": {},
                "attrs_number": {},
                "attrs_bool": {},
            }
            for identity in identities
        ]

    reader = TransportFixture(response)
    result = hydration.hydrate_voice_page(
        reader, builder, {"query_complete": True, "table": rows}
    )
    assert len(reader.calls) == 3 and len(result["table"]) == 2
    from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded

    reader = TransportFixture(response)
    with patch("django.conf.settings.VOICE_CONTENT_MAX_QUERY_ATTEMPTS", 1):
        with pytest.raises(ReadDeadlineExceeded, match="query budget exceeded"):
            hydration.hydrate_voice_page(
                reader, builder, {"query_complete": True, "table": rows}
            )
    assert len(reader.calls) == 1
    for code in (159, 386):

        def fail(_):
            raise ServerException("synthetic failure", code=code)

        reader = TransportFixture(fail)
        with pytest.raises(ServerException):
            hydration.hydrate_voice_page(
                reader, builder, {"query_complete": True, "table": rows}
            )
        assert len(reader.calls) == 1


def test_incomplete_and_empty_voice_pages_do_not_read_content(voice):
    builder_cls, fixture = voice
    for complete, rows in ((False, [fixture.voice_root()]), (True, [])):
        reader = TransportFixture(None)
        result = hydration.hydrate_voice_page(
            reader,
            fixture.make_builder(builder_cls),
            {"query_complete": complete, "table": rows},
        )
        assert result["query_complete"] is complete
        assert not reader.calls


def test_unknown_list_surface_cannot_fall_back_to_trace(voice):
    _, fixture = voice
    with pytest.raises(replay.ReplayError, match="SURFACE_UNSUPPORTED"):
        adapter_for(fixture).entity_list(
            TransportFixture(None), {"surface": "voices"}, []
        )
