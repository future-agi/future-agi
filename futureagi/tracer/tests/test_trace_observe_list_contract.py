"""Contract tests for the list_traces_of_session response serializer.

Guards the two failure modes this contract has already been through:
- typing cells as objects (DictField(child=JSONField)) rejected every scalar
  cell under strict validation;
- a strict scalar union would reject the array/object cells the row builder
  legitimately emits (aggregated span attributes, verbatim metadata values).
"""

import json
from pathlib import Path

from tracer.serializers.trace import TraceObserveListResponseSerializer
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
