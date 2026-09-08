"""Contract tests for the list_traces_of_session response serializer.

Guards the two failure modes this contract has already been through:
- typing cells as objects (DictField(child=JSONField)) rejected every scalar
  cell under strict validation;
- a strict scalar union would reject the array/object cells the row builder
  legitimately emits (aggregated span attributes, verbatim metadata values).
"""

import json
from pathlib import Path

from tracer.serializers.trace import (
    TraceObserveListResponseSerializer,
    TraceSessionListResponseSerializer,
)
from tracer.utils.helper import get_default_trace_config


def _repo_root():
    return Path(__file__).resolve().parents[3]


def _swagger():
    with (_repo_root() / "api_contracts" / "openapi" / "swagger.json").open() as f:
        return json.load(f)


def _wire_format(value):
    """Simulate JSON rendering — tuples become arrays, like on the wire."""
    return json.loads(json.dumps(value, default=str))


class TestTraceObserveListResponseContract:
    def _payload(self, table):
        return {
            "status": True,
            "result": {
                "metadata": {"total_rows": len(table)},
                "table": table,
                "config": _wire_format(get_default_trace_config()),
            },
        }

    def test_accepts_row_of_scalars(self):
        """The regression from review: real cells are scalars, not objects."""
        row = {
            "trace_id": "a2f1c9d0-0000-4000-8000-000000000001",
            "trace_name": "checkout-flow",
            "latency": 1.42,
            "total_tokens": 812,
            "status": "SUCCESS",
            "is_error": False,
            "cost": None,
        }
        serializer = TraceObserveListResponseSerializer(data=self._payload([row]))
        assert serializer.is_valid(), serializer.errors

    def test_accepts_array_and_object_cells(self):
        """Aggregated span attributes produce arrays; metadata values are
        copied through verbatim and can be objects. A scalar-union contract
        would wrongly reject both."""
        row = {
            "trace_id": "a2f1c9d0-0000-4000-8000-000000000002",
            "llm.model": ["gpt-4o", "gpt-4o-mini"],  # multi-value span attr
            "user_context": {"plan": "pro", "region": "us"},  # metadata value
        }
        serializer = TraceObserveListResponseSerializer(data=self._payload([row]))
        assert serializer.is_valid(), serializer.errors

    def test_accepts_real_default_column_config(self):
        """config rows must match the asdict(FieldConfig) shape exactly —
        including choices defaulting to (None,) → [null] on the wire."""
        serializer = TraceObserveListResponseSerializer(data=self._payload([]))
        assert serializer.is_valid(), serializer.errors

    def test_accepts_additive_bounded_page_metadata(self):
        payload = self._payload([])
        payload["result"]["metadata"].update(
            {
                "total_rows_is_lower_bound": True,
                "has_more": False,
                "query_complete": False,
                "query_status": "degraded",
                "query_error_code": "read_budget_exceeded",
                "query_elapsed_ms": 749.25,
                "query_count": 2,
                "query_rows_returned": 125,
                "query_result_payload_bytes": 8192,
            }
        )

        serializer = TraceObserveListResponseSerializer(data=payload)

        assert serializer.is_valid(), serializer.errors

    def test_session_list_accepts_candidate_order_provenance(self):
        payload = self._payload([])
        payload["result"]["metadata"].update(
            {
                "query_exact": False,
                "query_provenance": "spans_per_session_candidate",
                "ordering_exact": False,
            }
        )

        serializer = TraceSessionListResponseSerializer(data=payload)

        assert serializer.is_valid(), serializer.errors

    def test_session_list_allows_nullable_core_cells_and_dynamic_only_rows(self):
        payload = self._payload(
            [
                {
                    "session_id": None,
                    "session_name": None,
                    "duration": None,
                    "dynamic.flag": True,
                },
                {"dynamic.only": {"nested": [1, None, "ok"]}},
            ]
        )

        serializer = TraceSessionListResponseSerializer(data=payload)

        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["result"]["table"][0]["dynamic.flag"]
        assert serializer.validated_data["result"]["table"][1]["dynamic.only"] == {
            "nested": [1, None, "ok"]
        }

    def test_session_list_types_stable_cells_and_allows_dynamic_cells(self):
        payload = self._payload(
            [
                {
                    "session_id": "session-1",
                    "session_name": "checkout",
                    "project_id": "a2f1c9d0-0000-4000-8000-000000000003",
                    "start_time": "2026-08-29T12:00:00Z",
                    "end_time": "2026-08-29T12:01:00Z",
                    "duration": 60.0,
                    "total_cost": 0.012,
                    "total_tokens": 42,
                    "total_traces_count": 2,
                    "first_message": {"role": "user", "content": "hello"},
                    "custom.plan": "pro",
                    "a2f1c9d0-0000-4000-8000-000000000004": 0.9,
                }
            ]
        )

        serializer = TraceSessionListResponseSerializer(data=payload)

        assert serializer.is_valid(), serializer.errors
        row = serializer.validated_data["result"]["table"][0]
        assert row["custom.plan"] == "pro"
        assert row["a2f1c9d0-0000-4000-8000-000000000004"] == 0.9

    def test_session_list_rejects_malformed_stable_cell(self):
        payload = self._payload(
            [{"session_id": "session-1", "total_tokens": {"wrong": "shape"}}]
        )

        serializer = TraceSessionListResponseSerializer(data=payload)

        assert not serializer.is_valid()
        assert "total_tokens" in serializer.errors["result"]["table"][0]

    def test_rejects_missing_metadata(self):
        payload = {
            "status": True,
            "result": {"table": [], "config": []},
        }
        serializer = TraceObserveListResponseSerializer(data=payload)
        assert not serializer.is_valid()
        assert "metadata" in serializer.errors["result"]

    def test_rejects_malformed_config_row(self):
        payload = self._payload([])
        payload["result"]["config"] = [{"name": "Missing Id"}]
        serializer = TraceObserveListResponseSerializer(data=payload)
        assert not serializer.is_valid()

    def test_swagger_wires_response_serializer_to_endpoint(self):
        operation = _swagger()["paths"]["/tracer/trace/list_traces_of_session/"]["get"]
        ref = operation["responses"]["200"]["schema"]["$ref"]
        assert ref.rsplit("/", 1)[-1] == "TraceObserveListResponse"

    def test_swagger_scopes_candidate_order_provenance_to_session_list(self):
        swagger = _swagger()
        operation = swagger["paths"]["/tracer/trace-session/list_sessions/"]["get"]
        ref = operation["responses"]["200"]["schema"]["$ref"]
        assert ref.rsplit("/", 1)[-1] == "TraceSessionListResponse"

        definitions = swagger["definitions"]
        session_metadata = definitions["TraceSessionListMetadata"]["properties"]
        trace_metadata = definitions["TraceObserveListMetadata"]["properties"]
        assert {
            "query_exact",
            "query_provenance",
            "ordering_exact",
        } <= session_metadata.keys()
        assert session_metadata["query_provenance"]["enum"] == [
            "spans_per_session_candidate"
        ]
        assert "query_provenance" not in trace_metadata
        assert "ordering_exact" not in trace_metadata

        session_table = definitions["TraceSessionListResult"]["properties"]["table"]
        assert session_table["items"]["$ref"].rsplit("/", 1)[-1] == (
            "TraceSessionTableRow"
        )
        session_row = definitions["TraceSessionTableRow"]
        assert "session_id" not in session_row.get("required", [])
        assert session_row["properties"]["session_id"]["x-nullable"] is True
        assert session_row["additionalProperties"]["x-json-value"] is True
        assert session_row["additionalProperties"]["x-nullable"] is True

    def test_swagger_table_cells_are_json_values(self):
        """The cell schema must carry x-json-value (and nullability). drf-yasg
        still emits type:object for JSONField subclasses, but the FE runtime
        mapper checks x-json-value BEFORE type, so scalars validate — losing
        the extension would regress to object-only cells, the original bug."""
        definitions = _swagger()["definitions"]
        result_ref = definitions["TraceObserveListResponse"]["properties"]["result"][
            "$ref"
        ].rsplit("/", 1)[-1]
        table_items = definitions[result_ref]["properties"]["table"]["items"]
        cell_schema = table_items["additionalProperties"]
        assert cell_schema.get("x-json-value") is True
        assert cell_schema.get("x-nullable") is True

    def test_swagger_config_items_are_typed(self):
        """config rows must reference the typed column-config definition,
        not an untyped JSON blob."""
        definitions = _swagger()["definitions"]
        result_ref = definitions["TraceObserveListResponse"]["properties"]["result"][
            "$ref"
        ].rsplit("/", 1)[-1]
        config_items = definitions[result_ref]["properties"]["config"]["items"]
        assert config_items == {"$ref": "#/definitions/TraceObserveColumnConfig"}

    def test_swagger_exposes_additive_bounded_page_metadata(self):
        definitions = _swagger()["definitions"]
        result_ref = definitions["TraceObserveListResponse"]["properties"]["result"][
            "$ref"
        ].rsplit("/", 1)[-1]
        metadata_ref = definitions[result_ref]["properties"]["metadata"]["$ref"].rsplit(
            "/", 1
        )[-1]
        metadata = definitions[metadata_ref]["properties"]

        assert {
            "total_rows_is_lower_bound",
            "has_more",
            "query_complete",
            "query_status",
            "query_error_code",
            "query_elapsed_ms",
            "query_count",
            "query_rows_returned",
            "query_result_payload_bytes",
        } <= metadata.keys()
