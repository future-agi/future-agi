"""Partner mutations must publish bounded request shapes, not untyped bodies."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture(scope="module")
def swagger():
    path = Path(__file__).resolve().parents[3] / "api_contracts/openapi/swagger.json"
    return json.loads(path.read_text())


def resolve(swagger, schema):
    while "$ref" in schema:
        schema = swagger["definitions"][schema["$ref"].split("/")[-1]]
    return schema


@pytest.mark.parametrize(
    "path,method,status,required,response_fields",
    [
        (
            "/agentcc/mcp/vendor-bindings/",
            "post",
            "201",
            {"server_id", "vendor", "credential_id"},
            {"id", "server_id", "vendor", "enabled"},
        ),
        (
            "/agentcc/mcp/vendor-bindings/{binding_id}/",
            "patch",
            "200",
            {"enabled"},
            {"id", "enabled"},
        ),
        (
            "/agentcc/mcp/vendor-connections/{server_id}/connect/",
            "post",
            "200",
            set(),
            {"connect_url"},
        ),
    ],
)
def test_vendor_mutation_contract(
    swagger, path, method, status, required, response_fields
):
    operation = swagger["paths"][path][method]
    assert operation["x-runtime-request-validation"] is True
    assert operation["x-runtime-response-validation"] is True
    bodies = [p for p in operation["parameters"] if p["in"] == "body"]
    assert len(bodies) == 1
    request = resolve(swagger, bodies[0]["schema"])
    assert request["type"] == "object"
    assert set(request.get("required", [])) == required
    response = resolve(swagger, operation["responses"][status]["schema"])
    assert set(response["properties"]) == response_fields
    if method == "patch":
        assert request["properties"]["enabled"]["enum"] == [False]
        assert response["properties"]["enabled"]["enum"] == [False]
    elif "vendor-connections" in path:
        assert request["properties"]["binding"]["maxLength"] == 4096
        assert request["properties"]["asserted_id"]["maxLength"] == 128
    else:
        assert request["properties"]["toolkits"]["maxItems"] == 1
        assert request["properties"]["credential_id"]["format"] == "uuid"


@pytest.mark.parametrize(
    "view_name,method,data,kwargs",
    [
        ("VendorBindingsView", "post", {}, {}),
        (
            "VendorBindingDetailView",
            "patch",
            {"enabled": True},
            {"binding_id": "not-looked-up"},
        ),
        (
            "VendorBindingDetailView",
            "patch",
            {"enabled": False, "extra": True},
            {"binding_id": "not-looked-up"},
        ),
        (
            "VendorConnectView",
            "post",
            {"asserted_id": "x" * 129},
            {"server_id": "not-looked-up"},
        ),
        (
            "VendorConnectView",
            "post",
            {"unexpected": True},
            {"server_id": "not-looked-up"},
        ),
    ],
)
def test_vendor_contract_validates_before_side_effects(view_name, method, data, kwargs):
    from agentcc.views import mcp_vendor_connections as views

    # Calling the wrapped handler directly isolates its validator. No DB access,
    # credential lookup or vendor request is permitted in this unmarked test.
    request = SimpleNamespace(method=method.upper(), data=data)
    response = getattr(getattr(views, view_name)(), method)(request, **kwargs)
    assert response.status_code == 400
    if method == "patch":
        assert response.data == {"error": "disable_only"}
