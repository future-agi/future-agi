"""The public cap identity is typed and cannot grow monetary fields."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from agentcc.contracts.gateway_admin import AdmissionCapRefusalMetadata
from agentcc.serializers.cap_contract import AdmissionCapsPatchField

ROOT = Path(__file__).resolve().parents[3]


def identity(**overrides):
    return {
        "cap_scope": "agent",
        "cap_ref": "synthetic-worker",
        "cap_kind": "daily",
        "policy_revision": 7,
        **overrides,
    }


@pytest.mark.parametrize("scope", ["agent", "key"])
@pytest.mark.parametrize("kind", ["daily", "run"])
def test_cap_identity_matches_source_and_generated_contract(scope, kind):
    source = json.loads(
        (ROOT / "api_contracts/gateway/agentcc-admin.schema.json").read_text()
    )
    schema = {
        "$defs": source["$defs"],
        "$ref": "#/$defs/AdmissionCapRefusalMetadata",
    }
    value = identity(cap_scope=scope, cap_kind=kind)
    Draft202012Validator(schema).validate(value)
    assert (
        AdmissionCapRefusalMetadata.model_validate(value, strict=True).model_dump()
        == value
    )


@pytest.mark.parametrize(
    "change",
    [
        {"available_micros": 100},
        {"required_micros": 200},
        {"spend": 3.5},
        {"limit": 4},
        {"balance": 5},
        {"cap_scope": "org"},
        {"cap_kind": "monthly"},
        {"policy_revision": "7"},
        {"policy_revision": True},
        {"cap_ref": ""},
    ],
)
def test_cap_identity_rejects_values_and_wrong_types(change):
    with pytest.raises(ValidationError):
        AdmissionCapRefusalMetadata.model_validate(identity(**change), strict=True)


@pytest.mark.parametrize("field", list(identity()))
def test_cap_identity_requires_every_identity_field(field):
    value = identity()
    del value[field]
    with pytest.raises(ValidationError):
        AdmissionCapRefusalMetadata.model_validate(value, strict=True)


def test_swagger_contains_generated_value_free_gateway_refusal_shape():
    swagger = json.loads((ROOT / "api_contracts/openapi/swagger.json").read_text())
    documented = swagger["definitions"]["GatewayConfigPatchRequest"]["properties"][
        "budgets"
    ]["x-admission-cap-refusal"]
    assert (
        documented
        == AdmissionCapsPatchField.Meta.swagger_schema_fields["x-admission-cap-refusal"]
    )
    metadata = documented["properties"]["error"]["properties"]["metadata"]
    assert metadata == AdmissionCapRefusalMetadata.model_json_schema()
    assert metadata["additionalProperties"] is False
    assert set(metadata["properties"]) == set(identity())


def test_cap_refusal_descriptions_scope_identity_to_resolved_admission():
    source = json.loads(
        (ROOT / "api_contracts/gateway/agentcc-admin.schema.json").read_text()
    )
    descriptions = [
        source["$defs"]["AdmissionCapRefusalMetadata"]["description"],
        AdmissionCapsPatchField.Meta.swagger_schema_fields["x-admission-cap-refusal"][
            "description"
        ],
    ]
    for description in descriptions:
        assert "after a server-owned admission snapshot resolves" in description
        assert "may omit identity" in description
        assert "per-call caps" in description
        assert "stop reason" in description
