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

    config = item["properties"]["filter_config"]
    assert config["type"] == "object"
    assert {"filter_type", "filter_op", "filter_value", "col_type"}.issubset(
        config["properties"]
    )
    assert config["additionalProperties"] is True


def test_filter_request_schemas_are_not_generic_json_objects():
    """Filter-bearing API contracts must document the wire shape.

    Runtime fields stay permissive for saved-view compatibility, but OpenAPI
    should still expose the canonical FE/BE filter contract instead of a bare
    ``array<object>``.
    """

    definitions = _swagger()["definitions"]

    _assert_filter_item_schema(definitions["FetchGraph"]["properties"]["filters"])
    _assert_filter_item_schema(definitions["Selection"]["properties"]["filter"])
