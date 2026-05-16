import json
from pathlib import Path


def _swagger():
    repo_root = Path(__file__).resolve().parents[3]
    with (repo_root / "api_contracts" / "openapi" / "swagger.json").open() as f:
        return json.load(f)


def _assert_filter_item_schema(schema):
    assert schema["type"] == "array"
    item = schema["items"]
    assert item["type"] == "object"
    assert "column_id" in item["properties"]
    assert "filter_config" in item["properties"]
    assert item["additionalProperties"] is False
    assert item["required"] == ["column_id", "filter_config"]
    assert "columnId" not in item["properties"]
    assert "filterConfig" not in item["properties"]

    config = item["properties"]["filter_config"]
    assert config["type"] == "object"
    assert {"filter_type", "filter_op", "filter_value", "col_type"}.issubset(
        config["properties"]
    )
    assert config["additionalProperties"] is False
    assert config["required"] == ["filter_type", "filter_op"]
    assert "filterType" not in config["properties"]
    assert "filterOp" not in config["properties"]
    assert "filterValue" not in config["properties"]
    assert "colType" not in config["properties"]


def test_filter_request_schemas_are_not_generic_json_objects():
    """Filter-bearing API contracts must document the wire shape.

    The generated schema should expose the same strict snake_case contract that
    runtime serializers enforce, instead of a bare ``array<object>`` or a shape
    that silently accepts camelCase drift.
    """

    definitions = _swagger()["definitions"]

    _assert_filter_item_schema(definitions["FetchGraph"]["properties"]["filters"])
    _assert_filter_item_schema(definitions["Selection"]["properties"]["filter"])
