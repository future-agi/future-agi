"""Request bodies in the platform's API contract come from openapi.yaml (omega_hub/api/request_schema.py)."""

from drf_yasg import openapi

from omega_hub.api.request_schema import FORM, JSON, request_body, to_schema


def test_json_multipart_and_bodiless_operations_are_told_apart():
    media, schema = request_body("/omega-hub/v1/review/submissions/{submission_id}/decision", "POST")
    assert media == JSON and schema == {"$ref": "#/components/schemas/Decision"}
    media, schema = request_body("/omega-hub/v1/submissions", "POST")
    assert media == FORM and schema["required"] == ["bundle"]
    assert request_body("/omega-hub/v1/entries/{publisher}/{name}/like", "POST") == (JSON, None)
    assert request_body("/omega-hub/v1/no-such-route", "POST") is None


def test_the_decision_body_keeps_its_rules_and_resolves_the_disclosure():
    schema = to_schema({"$ref": "#/components/schemas/Decision"})
    assert schema["type"] == openapi.TYPE_OBJECT and schema["required"] == ["decision", "rulesVersion"]
    props = schema["properties"]
    assert props["decision"]["enum"] == ["approve", "reject", "request_changes"]
    assert props["reason"]["minLength"] == 20 and props["reason"]["maxLength"] == 2000
    assert props["checklist"]["additionalProperties"]["type"] == openapi.TYPE_BOOLEAN
    assert set(props["disclosure"]["required"]) == {"summary", "network", "files", "shell", "background", "credentials"}
    lengths = {field: spec["maxLength"] for field, spec in props["disclosure"]["properties"].items()}
    assert lengths == {"summary": 200, "network": 1200, "files": 1200, "shell": 300,
                       "background": 300, "credentials": 1200}
    assert props["notes"]["items"]["required"] == ["file", "note"]
    flat = repr(schema)
    assert "oneOf" not in flat and "anyOf" not in flat and "nullable" not in flat
