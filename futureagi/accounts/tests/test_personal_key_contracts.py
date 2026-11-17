"""The committed API contract must describe personal-key-only behavior.

These checks read the canonical artifact, not just the serializers that will
produce it, so forgetting schema/client regeneration cannot pass unnoticed.
"""

import copy
import json
import subprocess
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def swagger():
    root = Path(__file__).resolve().parents[3]
    return json.loads((root / "api_contracts/openapi/swagger.json").read_text())


def test_bootstrap_contract_is_retired_and_never_advertises_a_secret_pair(swagger):
    responses = swagger["paths"]["/accounts/keys/"]["get"]["responses"]
    assert not any(status.startswith("2") for status in responses)
    assert responses["410"]["schema"] == {
        "$ref": "#/definitions/SecretKeysRetiredResponse"
    }
    schema = swagger["definitions"]["SecretKeysRetiredResponse"]
    assert set(schema["properties"]) == {"code", "create_url"}
    assert schema["properties"]["code"]["default"] == "org_keys_retired"
    assert "create_url" in schema["required"]
    assert "SecretKeysResponse" not in swagger["definitions"]
    assert "SecretKeyDataResponse" not in swagger["definitions"]


def test_live_bootstrap_schema_suppresses_the_automatic_success_response():
    from drf_yasg import openapi

    from accounts.views.keys import GetKeysView
    from tfc.utils.api_contracts import ManagementAPIAutoSchema

    inspector = ManagementAPIAutoSchema(
        view=GetKeysView(),
        path="/accounts/keys/",
        method="GET",
        components=openapi.ReferenceResolver(
            openapi.SCHEMA_DEFINITIONS, force_init=True
        ),
        request=None,
        overrides=GetKeysView.get._swagger_auto_schema,
    )
    responses = inspector.get_responses()
    assert not any(status.startswith("2") for status in responses)
    assert responses["410"]["schema"] == {
        "$ref": "#/definitions/SecretKeysRetiredResponse"
    }


def test_personal_key_creation_contract_has_optional_nullable_workspace(swagger):
    schema = swagger["definitions"]["CreateSecretKey"]
    assert schema["required"] == ["key_name"]
    workspace = schema["properties"]["workspace_id"]
    assert workspace["type"] == "string"
    assert workspace["format"] == "uuid"
    assert workspace["x-nullable"] is True


def test_mcp_connection_contract_exposes_read_only_reconnect_state(swagger):
    field = swagger["definitions"]["MCPConnectionResult"]["properties"][
        "reconnect_required"
    ]
    assert field["type"] == "boolean"
    assert field["readOnly"] is True


@pytest.mark.parametrize("name", ["AccountsErrorResponse", "MCPErrorResponse"])
def test_error_contract_includes_optional_personal_issuer_guidance(swagger, name):
    schema = swagger["definitions"][name]
    field = schema["properties"]["create_url"]
    assert field["type"] == "string"
    assert "verified retired credential" in field["description"]
    assert "create_url" not in schema.get("required", [])


@pytest.mark.parametrize(
    "unexpected_removal", [None, "path", "operation", "definition", "retirement"]
)
def test_generation_guard_allows_only_the_retired_pair_definitions(
    tmp_path, unexpected_removal
):
    """Regeneration must not require the global surface-shrink escape hatch."""
    previous = {
        "paths": {
            "/accounts/keys/": {"get": {"responses": {"200": {}}}},
            "/unrelated/": {"get": {}, "post": {}},
        },
        "definitions": {
            "SecretKeysResponse": {}, "SecretKeyDataResponse": {}, "Unrelated": {}
        },
    }
    generated = copy.deepcopy(previous)
    generated["paths"]["/accounts/keys/"]["get"]["responses"] = {
        "410": {"schema": {"$ref": "#/definitions/SecretKeysRetiredResponse"}}
    }
    generated["definitions"] = {"SecretKeysRetiredResponse": {}, "Unrelated": {}}
    if unexpected_removal == "path":
        del generated["paths"]["/unrelated/"]
    elif unexpected_removal == "operation":
        del generated["paths"]["/unrelated/"]["post"]
    elif unexpected_removal == "definition":
        del generated["definitions"]["Unrelated"]
    elif unexpected_removal == "retirement":
        generated["paths"]["/accounts/keys/"]["get"]["responses"] = {"200": {}}
    before, after = tmp_path / "before.json", tmp_path / "after.json"
    before.write_text(json.dumps(previous))
    after.write_text(json.dumps(generated))
    root = Path(__file__).resolve().parents[3]
    script = (root / "scripts/generate-openapi-schema.sh").read_text()
    guard = script.split("<<'NODE'\n", 1)[1].split("\nNODE", 1)[0]
    result = subprocess.run(
        ["node", "-", str(before), str(after)],
        input=guard, text=True, capture_output=True, timeout=15, check=False,
    )
    assert result.returncode == (0 if unexpected_removal is None else 1)
    if unexpected_removal is not None:
        assert "Refusing to replace" in result.stderr
