"""The retired scheme is a publication-only, exact-name exclusion."""

from copy import deepcopy
from unittest.mock import patch

from drf_yasg import openapi
from drf_yasg.generators import OpenAPISchemaGenerator

from agentcc.managed_credit_schema import ManagedCreditSchemaGenerator

LEGACY = "ManagedCreditStaffBearer"


def generator():
    return ManagedCreditSchemaGenerator(
        info=openapi.Info(title="Schema policy", default_version="v1")
    )


def test_only_the_legacy_definition_is_removed():
    definitions = {
        LEGACY: {"type": "apiKey", "name": "Authorization", "in": "header"},
        "StaffBearer": {"type": "apiKey", "name": "Authorization", "in": "header"},
        "UnrelatedScheme": {"type": "apiKey", "name": "X-Test", "in": "header"},
    }
    before = deepcopy(definitions)
    with patch.object(
        OpenAPISchemaGenerator, "get_security_definitions", return_value=definitions
    ):
        assert generator().get_security_definitions() == {
            name: value for name, value in before.items() if name != LEGACY
        }
    assert definitions == before


def test_only_legacy_operation_references_are_removed():
    def operation(name, security=None):
        return openapi.Operation(
            operation_id=name,
            responses=openapi.Responses({"200": openapi.Response("OK")}),
            security=security,
        )

    paths = openapi.Paths({
        "/staff/": openapi.PathItem(post=operation(
            "staff", [{"GatewayAdminBearer": []}, {"StaffBearer": []}]
        )),
        "/mixed/": openapi.PathItem(post=operation(
            "mixed", [
                {LEGACY: []},
                {LEGACY: [], "UnrelatedScheme": ["read"]},
                {"StaffBearer": []},
            ]
        )),
        "/legacy/": openapi.PathItem(post=operation("legacy", [{LEGACY: []}])),
        "/public/": openapi.PathItem(get=operation("public", [])),
        "/inherited/": openapi.PathItem(get=operation("inherited")),
        "/optional/": openapi.PathItem(get=operation(
            "optional", [{}, {"StaffBearer": []}]
        )),
    })
    schema = openapi.Swagger(
        info=openapi.Info(title="Schema policy", default_version="v1"),
        _prefix="/",
        paths=paths,
        security=[{"BearerToken": []}],
    )
    expected = deepcopy(schema)
    expected.paths["/mixed/"]["post"]["security"] = [
        {"UnrelatedScheme": ["read"]}, {"StaffBearer": []}
    ]
    del expected.paths["/legacy/"]["post"]["security"]
    with patch.object(OpenAPISchemaGenerator, "get_schema", return_value=schema):
        assert generator().get_schema(public=True) == expected


def test_no_schema_is_preserved():
    with patch.object(OpenAPISchemaGenerator, "get_schema", return_value=None):
        assert generator().get_schema(public=True) is None
