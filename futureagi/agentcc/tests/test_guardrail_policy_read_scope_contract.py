"""Guardrail policy scope: the write and read contracts are declared apart.

A policy an earlier release left at key or project scope still reads back as
stored (docs-agentcc-711, item 2; test_guardrail_policy_scope_refused.py), so
the read response contract must declare global, key and project. Every write
(create, PUT, PATCH) still accepts global only and refuses key and project
with ``scoped_policy_not_supported``.

- ``AgentccGuardrailPolicy`` (the write definition) keeps ``scope`` enum
  ["global"]; it is the request body of create, PUT and PATCH.
- ``AgentccGuardrailPolicyRead`` (the read definition) declares every stored
  scope; it is the response of list, retrieve, create, PUT and PATCH.

The round trip stores a legacy-scoped policy directly (as an earlier release
did), reads it through the API and checks each read response against the
committed OpenAPI response schema. ``checks``, ``applied_keys`` and
``applied_projects`` are JSON fields that the generator declares as objects
while they hold lists; that pre-existing typing is outside this check, which
covers every other declared property, the scope enum included.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from jsonschema import Draft4Validator
from rest_framework import status

from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.serializers import guardrail_policy as policy_serializers
from agentcc.services import config_push
from agentcc.views import gateway as gateway_views

POLICIES = "/agentcc/guardrail-policies/"
COLLECTION = "/agentcc/guardrail-policies/"
ITEM = "/agentcc/guardrail-policies/{id}/"
WRITE = "AgentccGuardrailPolicy"
READ = "AgentccGuardrailPolicyRead"
CODE = "scoped_policy_not_supported"
JSON_FIELDS = {"checks", "applied_keys", "applied_projects"}
CHECK = {
    "name": "keyword-blocklist",
    "action": "block",
    "enabled": True,
    "config": {"words": ["policyword"]},
}


@pytest.fixture
def gateway():
    """Every push goes to a mock; nothing leaves the test process."""
    client = MagicMock()
    with (
        patch.object(config_push, "get_gateway_client", return_value=client),
        patch.object(gateway_views, "get_gateway_client", return_value=client),
    ):
        yield client


def _swagger():
    root = Path(__file__).resolve().parents[3]
    with (root / "api_contracts" / "openapi" / "swagger.json").open() as f:
        return json.load(f)


def _ref(schema):
    return (schema or {}).get("$ref", "").rsplit("/", 1)[-1]


def _resolved(swagger, schema):
    while "$ref" in schema:
        schema = swagger["definitions"][_ref(schema)]
    return schema


def _payload(schema):
    """The payload schema: ``result`` inside an inline success envelope.

    GeneralMethods.success_response() answers ``{"status": true, "result":
    ...}``, as the round trip below reads it, and the generator documents that
    envelope inline around the inferred schema.
    """
    properties = schema.get("properties") or {}
    if (properties.get("status") or {}).get(
        "type"
    ) == "boolean" and "result" in properties:
        return properties["result"]
    return schema


def _response_schemas(swagger):
    """The committed item schema of each read response, by operation."""
    collection, item = swagger["paths"][COLLECTION], swagger["paths"][ITEM]

    def success(operation, status_code="200"):
        return _payload(operation["responses"][status_code]["schema"])

    return {
        "list": success(collection["get"])["properties"]["results"]["items"],
        "retrieve": success(item["get"]),
        "create": success(collection["post"], "201"),
        "update": success(item["put"]),
        "partial_update": success(item["patch"]),
    }


def _contract_errors(swagger, schema, row):
    """Every way ``row`` violates the committed response schema, the
    generator's object typing of the JSON list fields aside."""
    definition = _resolved(swagger, schema)
    checked = {
        "type": "object",
        "required": [
            name for name in definition.get("required", []) if name not in JSON_FIELDS
        ],
        "properties": {
            name: value
            for name, value in definition["properties"].items()
            if name not in JSON_FIELDS
        },
    }
    missing = sorted(set(checked["properties"]) - set(row))
    errors = [f"missing declared property {name}" for name in missing]
    errors += sorted(
        error.message for error in Draft4Validator(checked).iter_errors(row)
    )
    return errors


class TestReadAndWriteScopeDeclarations:
    def test_the_read_serializer_declares_every_stored_scope(self):
        read = getattr(policy_serializers, "AgentccGuardrailPolicyReadSerializer", None)
        assert read is not None, "no read serializer separate from the write one"
        fields = read().fields
        assert sorted(fields["scope"].choices) == ["global", "key", "project"]
        assert fields["scope"].read_only
        write = policy_serializers.AgentccGuardrailPolicySerializer().fields
        assert list(write["scope"].choices) == ["global"]
        assert not write["scope"].read_only

    def test_the_committed_contract_separates_request_and_response(self):
        swagger = _swagger()
        definitions = swagger["definitions"]
        assert definitions[WRITE]["properties"]["scope"]["enum"] == ["global"]
        assert READ in definitions, "the contract has no read definition"
        read_scope = definitions[READ]["properties"]["scope"]
        assert sorted(read_scope["enum"]) == ["global", "key", "project"]
        assert read_scope.get("readOnly") is True
        collection, item = swagger["paths"][COLLECTION], swagger["paths"][ITEM]
        for operation in (collection["post"], item["put"], item["patch"]):
            bodies = [p for p in operation["parameters"] if p["in"] == "body"]
            assert [_ref(p["schema"]) for p in bodies] == [WRITE]
        for name, schema in _response_schemas(swagger).items():
            assert _ref(schema) == READ, name


@pytest.mark.integration
@pytest.mark.api
class TestALegacyScopedPolicyRoundTripsThroughTheContract:
    @pytest.mark.parametrize(
        "scope",
        [AgentccGuardrailPolicy.SCOPE_KEY, AgentccGuardrailPolicy.SCOPE_PROJECT],
    )
    def test_reads_match_the_response_schema_and_writes_still_refuse_it(
        self, auth_client, organization, gateway, scope
    ):
        targets = {"applied_keys": ["k1"]}
        if scope == AgentccGuardrailPolicy.SCOPE_PROJECT:
            targets = {"applied_projects": ["p1"]}
        policy = AgentccGuardrailPolicy.no_workspace_objects.create(
            organization=organization,
            name=f"left-{scope}",
            scope=scope,
            checks=[CHECK],
            **targets,
        )
        swagger = _swagger()
        schemas = _response_schemas(swagger)

        read = auth_client.get(f"{POLICIES}{policy.id}/")
        listed = auth_client.get(POLICIES)
        assert read.status_code == listed.status_code == status.HTTP_200_OK
        rows = [("retrieve", read.json()["result"])]
        rows += [("list", row) for row in listed.json()["result"]]
        assert [row["scope"] for _, row in rows] == [scope, scope]
        for operation, row in rows:
            errors = _contract_errors(swagger, schemas[operation], row)
            assert not errors, (operation, errors)

        # The write contract still offers global only, and a write of the
        # stored legacy scope is still refused and leaves the policy as stored.
        assert scope not in swagger["definitions"][WRITE]["properties"]["scope"]["enum"]
        refused = auth_client.patch(
            f"{POLICIES}{policy.id}/", {"scope": scope}, format="json"
        )
        assert refused.status_code == status.HTTP_400_BAD_REQUEST, refused.json()
        assert refused.json()["code"] == CODE, refused.json()
        policy.refresh_from_db()
        assert policy.scope == scope
