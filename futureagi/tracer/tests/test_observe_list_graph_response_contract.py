"""Runtime and Swagger contracts for dynamic Observe list/graph payloads."""

import json
from pathlib import Path

import pytest

from tracer.serializers.observation_span import (
    SpanObserveListResponseSerializer,
    SpanPrototypeListResponseSerializer,
)
from tracer.serializers.trace import (
    TraceAgentGraphResponseSerializer,
    TracePrototypeListResponseSerializer,
    TraceVoiceCallListResponseSerializer,
)
from tracer.utils.helper import get_default_span_config, get_default_trace_config


def _repo_root():
    return Path(__file__).resolve().parents[3]


def _swagger():
    with (_repo_root() / "api_contracts" / "openapi" / "swagger.json").open() as f:
        return json.load(f)


def _wire_format(value):
    return json.loads(json.dumps(value, default=str))


def _list_payload(*, config_key, config):
    return {
        "status": True,
        "result": {
            config_key: _wire_format(config),
            "metadata": {
                "total_rows": 1,
                "total_rows_is_lower_bound": False,
                "has_more": False,
                "query_complete": True,
                "query_status": "complete",
            },
            "table": [
                {
                    "id": "row-1",
                    "name": "checkout",
                    "latency": 12.5,
                    "tokens": 42,
                    "is_error": False,
                    "cost": None,
                    "models": ["gpt-4o", "gpt-4o-mini"],
                    "context": {"region": "us", "retries": [0, 1]},
                }
            ],
        },
    }


@pytest.mark.parametrize(
    ("serializer_class", "config_key", "config"),
    [
        (
            TracePrototypeListResponseSerializer,
            "column_config",
            get_default_trace_config(),
        ),
        (
            SpanPrototypeListResponseSerializer,
            "column_config",
            get_default_span_config(),
        ),
        (SpanObserveListResponseSerializer, "config", get_default_span_config()),
    ],
)
def test_list_response_contract_accepts_every_json_cell_shape(
    serializer_class, config_key, config
):
    serializer = serializer_class(
        data=_list_payload(config_key=config_key, config=config)
    )

    assert serializer.is_valid(), serializer.errors


@pytest.mark.parametrize(
    ("serializer_class", "config_key", "config"),
    [
        (
            TracePrototypeListResponseSerializer,
            "column_config",
            get_default_trace_config(),
        ),
        (
            SpanPrototypeListResponseSerializer,
            "column_config",
            get_default_span_config(),
        ),
        (SpanObserveListResponseSerializer, "config", get_default_span_config()),
    ],
)
def test_list_response_contract_rejects_non_json_cells(
    serializer_class, config_key, config
):
    payload = _list_payload(config_key=config_key, config=config)
    payload["result"]["table"][0]["not_json"] = {"set-members"}
    serializer = serializer_class(data=payload)

    assert not serializer.is_valid()
    assert "table" in serializer.errors["result"]


def _agent_graph_payload(*, pending=False):
    result = {
        "nodes": [],
        "edges": [],
        "path_edges": [],
        "query_complete": not pending,
        "query_status": "pending" if pending else "complete",
        "query_sampled": False,
        "query_refreshing": pending,
    }
    if not pending:
        result.update(
            {
                "nodes": [
                    {
                        "id": "agent:checkout",
                        "name": "checkout",
                        "type": "agent",
                        "span_count": 3,
                        "avg_latency_ms": 14.5,
                        "total_tokens": 87,
                        "total_cost": 0.003,
                        "error_count": 0,
                        "trace_count": 2,
                        "trace_count_exact": True,
                    }
                ],
                "graph_collapsed": False,
                "graph_node_limit": 80,
                "omitted_node_count": 0,
                "query_count": 1,
                "query_rows_returned": 3,
                "query_elapsed_ms": 125.5,
                "query_completed_at": "2026-08-09T12:00:00Z",
                "query_cached": False,
            }
        )
    return {"status": True, "result": result}


@pytest.mark.parametrize("pending", [False, True])
def test_agent_graph_response_contract_accepts_complete_and_pending(pending):
    serializer = TraceAgentGraphResponseSerializer(
        data=_agent_graph_payload(pending=pending)
    )

    assert serializer.is_valid(), serializer.errors


def test_agent_graph_response_contract_rejects_incomplete_nodes():
    payload = _agent_graph_payload(pending=False)
    del payload["result"]["nodes"][0]["span_count"]
    serializer = TraceAgentGraphResponseSerializer(data=payload)

    assert not serializer.is_valid()
    assert "nodes" in serializer.errors["result"]


def test_voice_list_response_accepts_mixed_json_rows_and_typed_config():
    serializer = TraceVoiceCallListResponseSerializer(
        data={
            "count": 1,
            "count_is_lower_bound": False,
            "total_pages": 1,
            "current_page": 1,
            "next": None,
            "previous": None,
            "results": _list_payload(
                config_key="column_config", config=get_default_trace_config()
            )["result"]["table"],
            "config": _wire_format(get_default_trace_config()),
            "has_more": False,
            "query_complete": True,
            "query_status": "complete",
        }
    )

    assert serializer.is_valid(), serializer.errors


@pytest.mark.parametrize(
    ("path", "definition"),
    [
        ("/tracer/trace/list_traces/", "TracePrototypeListResponse"),
        ("/tracer/trace/list_traces_of_session/", "TraceObserveListResponse"),
        ("/tracer/observation-span/list_spans/", "SpanPrototypeListResponse"),
        ("/tracer/observation-span/list_spans_observe/", "SpanObserveListResponse"),
        ("/tracer/trace/agent_graph/", "TraceAgentGraphResponse"),
        ("/tracer/trace/list_voice_calls/", "TraceVoiceCallListResponse"),
    ],
)
def test_swagger_wires_explicit_response_contracts(path, definition):
    operation = _swagger()["paths"][path]["get"]

    assert operation["responses"]["200"]["schema"]["$ref"] == (
        f"#/definitions/{definition}"
    )
    assert operation["x-runtime-response-validation"] is True


@pytest.mark.parametrize(
    ("response_definition", "config_key"),
    [
        ("TracePrototypeListResponse", "column_config"),
        ("TraceObserveListResponse", "config"),
        ("SpanPrototypeListResponse", "column_config"),
        ("SpanObserveListResponse", "config"),
    ],
)
def test_swagger_list_rows_are_recursive_json_values(response_definition, config_key):
    definitions = _swagger()["definitions"]
    result_ref = definitions[response_definition]["properties"]["result"]["$ref"]
    result = definitions[result_ref.rsplit("/", 1)[-1]]
    cell = result["properties"]["table"]["items"]["additionalProperties"]

    assert cell["x-json-value"] is True
    assert cell["x-nullable"] is True
    assert result["properties"][config_key]["items"]["$ref"].startswith(
        "#/definitions/"
    )
