"""Runtime and Swagger contracts for dynamic Observe list/graph payloads."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tracer.serializers.filters import ObserveGraphDataResponseSerializer
from tracer.serializers.observation_span import (
    SpanListMetadataSerializer,
    SpanObserveListResponseSerializer,
    SpanPrototypeListResponseSerializer,
)
from tracer.serializers.trace import (
    TraceAgentGraphResponseSerializer,
    TraceObserveListMetadataSerializer,
    TracePrototypeListResponseSerializer,
    TraceVoiceCallListResponseSerializer,
)
from tracer.utils.helper import (
    ensure_project_session_property_identities,
    get_default_project_session_config,
    get_default_span_config,
    get_default_trace_config,
    update_column_config_based_on_eval_config,
)
from tracer.utils.property_registry import canonical_system_attribute_name


def _repo_root():
    return Path(__file__).resolve().parents[3]


def _swagger():
    with (_repo_root() / "api_contracts" / "openapi" / "swagger.json").open() as f:
        return json.load(f)


def _wire_format(value):
    return json.loads(json.dumps(value, default=str))


def _filter_evidence():
    return {
        "query_applied_filter_version": "canonical-json-sha256-v1",
        "query_applied_filter_sha256": "a" * 64,
        "query_applied_filter_count": 1,
    }


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
                **_filter_evidence(),
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


def test_default_trace_and_span_columns_have_stable_registry_identities():
    trace_config = get_default_trace_config()
    span_config = get_default_span_config()
    session_config = get_default_project_session_config()

    assert all(
        item["property_id"]
        == "system_attribute:traces:"
        + canonical_system_attribute_name("traces", item["id"])
        and item["property_kind"] == "system_attribute"
        and item["property_source"] == "traces"
        for item in trace_config
    )
    assert all(
        item["property_id"]
        == "system_attribute:spans:"
        + canonical_system_attribute_name("spans", item["id"])
        and item["property_kind"] == "system_attribute"
        and item["property_source"] == "traces"
        for item in span_config
    )
    assert all(
        item["property_id"]
        == "system_attribute:sessions:"
        + canonical_system_attribute_name("sessions", item["id"])
        and item["property_kind"] == "system_attribute"
        and item["property_source"] == "sessions"
        for item in session_config
    )


def test_observe_span_columns_include_user_fields_once_with_stable_identities():
    span_config = get_default_span_config(include_user_fields=True)
    field_ids = [item["id"] for item in span_config]

    assert {"user_id", "user_id_type", "user_id_hash"}.issubset(field_ids)
    assert len(field_ids) == len(set(field_ids))
    assert all(
        item["property_id"]
        == "system_attribute:spans:"
        + canonical_system_attribute_name("spans", item["id"])
        and item["property_kind"] == "system_attribute"
        and item["property_source"] == "traces"
        for item in span_config
    )


def test_legacy_saved_session_columns_gain_identities_without_mutating_storage():
    saved_config = [
        {"id": "session_id", "name": "Session Id", "is_visible": True},
        {
            "id": "annotation-label-1",
            "property_id": "annotation:annotation-label-1",
            "property_kind": "annotation",
            "property_source": "sessions",
        },
    ]

    config = ensure_project_session_property_identities(saved_config)

    assert "property_id" not in saved_config[0]
    assert config[0]["property_id"] == "system_attribute:sessions:session"
    assert config[0]["property_source"] == "sessions"
    assert config[1]["property_id"] == "annotation:annotation-label-1"


def test_voice_eval_columns_keep_trace_transport_without_average_prefix():
    eval_config = SimpleNamespace(
        id="eval-config-1",
        name="Quality",
        eval_template=SimpleNamespace(
            id="eval-template-1",
            config={"output": "score"},
            choices=None,
        ),
    )

    config = update_column_config_based_on_eval_config(
        [],
        [eval_config],
        is_simulator=True,
        property_source="traces",
    )

    assert config[0]["name"] == "Quality"
    assert config[0]["property_id"] == "eval_config:eval-config-1"
    assert config[0]["property_kind"] == "eval_config"
    assert config[0]["property_source"] == "traces"


def test_session_annotation_columns_have_stable_registry_identities():
    from tracer.views.trace_session import TraceSessionView

    label = SimpleNamespace(
        id="annotation-label-1",
        name="Correctness",
        type="numeric",
        settings={},
    )
    with patch("tracer.views.trace_session.Score.objects.filter") as filter_scores:
        filter_scores.return_value.values.return_value.distinct.return_value = []
        config = TraceSessionView._build_score_column_config([label])

    assert config[0]["property_id"] == "annotation:annotation-label-1"
    assert config[0]["property_kind"] == "annotation"
    assert config[0]["property_source"] == "sessions"


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
            **_filter_evidence(),
        }
    )

    assert serializer.is_valid(), serializer.errors


def test_list_and_graph_serializers_publish_typed_filter_evidence_and_window():
    for serializer_class in (
        TraceObserveListMetadataSerializer,
        SpanListMetadataSerializer,
    ):
        serializer = serializer_class(
            data={
                "total_rows": 1,
                **_filter_evidence(),
            }
        )
        assert serializer.is_valid(), serializer.errors

    graph = ObserveGraphDataResponseSerializer(
        data={
            "status": True,
            "result": {
                "metric_name": "latency",
                "data": [],
                "query_complete": True,
                "query_exact": True,
                "query_status": "complete",
                "query_sampled": False,
                "query_window_start": "2026-01-01T00:00:00Z",
                "query_window_end": "2026-02-01T00:00:00Z",
                **_filter_evidence(),
            },
        }
    )
    assert graph.is_valid(), graph.errors


def test_filter_evidence_serializers_reject_malformed_digest():
    serializer = TraceObserveListMetadataSerializer(
        data={
            "total_rows": 1,
            **{**_filter_evidence(), "query_applied_filter_sha256": "not-a-digest"},
        }
    )

    assert not serializer.is_valid()
    assert "query_applied_filter_sha256" in serializer.errors


def test_public_list_and_graph_views_attach_evidence_at_the_response_boundary():
    root = _repo_root() / "futureagi" / "tracer" / "views"
    trace_source = (root / "trace.py").read_text()
    span_source = (root / "observation_span.py").read_text()
    session_source = (root / "trace_session.py").read_text()

    assert 'observe_type="trace"' in trace_source
    assert 'observe_type="voice"' in trace_source
    assert '"project_id": str(row.get("project_id") or project_id)' in trace_source
    assert 'observe_type="span"' in span_source
    assert 'observe_type="session"' in session_source
    assert 'entry["project_id"] = entry_project_id' in session_source


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


@pytest.mark.parametrize(
    "definition",
    [
        "ObserveGraphDataResult",
        "TraceObserveListMetadata",
        "TraceVoiceCallListResponse",
        "SpanListMetadata",
    ],
)
def test_swagger_publishes_typed_filter_attestation(definition):
    properties = _swagger()["definitions"][definition]["properties"]

    assert properties["query_applied_filter_version"]["enum"] == [
        "canonical-json-sha256-v1"
    ]
    assert properties["query_applied_filter_sha256"]["pattern"] == r"^[0-9a-f]{64}$"
    assert properties["query_applied_filter_count"]["minimum"] == 0
