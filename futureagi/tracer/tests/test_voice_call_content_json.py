"""Read-only CH25 projection checks; opt in with an isolated local test endpoint.

Only SELECTs over literal values are issued. No source table, DDL or insert is
needed to exercise the actual voice content expression on String and JSON.
"""

import json
import os

import pytest

from tracer.services.clickhouse.v2.query_builders.voice_call_list import (
    _VoiceTraceReplayBuilder,
)


@pytest.fixture(scope="module")
def local_clickhouse():
    import clickhouse_connect

    host = os.environ.get("OBSERVED_CATALOG_TEST_CH_HOST")
    if not host:
        pytest.skip("requires an explicitly selected local ClickHouse")
    assert host in {"localhost", "127.0.0.1"}, "local-only projection test"
    client = clickhouse_connect.get_client(
        host=host,
        port=int(os.environ.get("OBSERVED_CATALOG_TEST_CH_PORT", "8123")),
        username=os.environ.get("OBSERVED_CATALOG_TEST_CH_USER", "test"),
        password=os.environ.get("OBSERVED_CATALOG_TEST_CH_PASSWORD", "test"),
        connect_timeout=2,
        send_receive_timeout=10,
        settings={"readonly": 1, "max_threads": 1, "max_execution_time": 5},
    )
    try:
        yield client
    finally:
        client.close()


@pytest.mark.parametrize("storage_type", ["String", "JSON"])
@pytest.mark.parametrize(
    "attributes",
    [
        {},
        {"call_logs": [{"message": "must not appear in the list"}]},
        {
            "metadata": {
                "call_execution_id": 'call-"quoted-雪',
                "nested": {"call_logs": "keep nested data"},
            },
            "conversation": {"messages": ["hello", "bye"]},
            "measurements": [7, 0.25],
            "call_logs": [{"message": "must not appear in the list"}],
        },
    ],
    ids=["empty", "logs-only", "nested-call"],
)
def test_voice_content_preserves_nested_json_and_removes_only_top_level_logs(
    local_clickhouse, storage_type, attributes
):
    fields = {
        alias: expression
        for expression, alias in _VoiceTraceReplayBuilder(
            project_id="local-projection-test"
        )._root_replay_content_fields()
    }
    result = local_clickhouse.query(
        f"SELECT {fields['span_attributes']} AS content, "
        f"{fields['attrs_string']} AS strings "
        f"FROM (SELECT CAST({{payload:String}}, '{storage_type}') AS attributes_extra, "
        "map('call_logs', 'discard', 'call.status', 'completed') AS attrs_string)",
        parameters={"payload": json.dumps(attributes, ensure_ascii=False)},
    )
    content, strings = result.result_rows[0]
    assert json.loads(content) == {
        key: value for key, value in attributes.items() if key != "call_logs"
    }
    assert strings == {"call.status": "completed"}
    from tracer.services.observability_providers import ObservabilityService

    hydrated = {**strings, **json.loads(content)}
    call = ObservabilityService.process_raw_logs({}, "openai", hydrated)
    assert call["call_id"] == attributes.get("metadata", {}).get("call_execution_id")
    assert call["status"] == "completed"
