"""Public API-column compatibility and legacy workspace visibility."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.api_key import SecretModel
from model_hub.views import dynamic_columns as columns
from tfc.middleware.workspace_context import clear_workspace_context
from tfc.tests import outbound_fixture

outbound_wire = outbound_fixture.outbound_wire
ERROR = ("destination not allowed or unreachable", {"response_status": 400})


def config(url):
    return {"url": url, "method": "POST", "output_type": "object", "body": {}}


@pytest.mark.parametrize("header", ["User-Agent", "Accept"])
def test_public_request_retains_default_headers(outbound_wire, header):
    result = columns.AddApiColumnView()._make_api_call(None, config(outbound_wire.url))
    assert result[1] == {"response_status": 200}
    received = requests.structures.CaseInsensitiveDict(outbound_wire.received[-1][2])
    assert received.get(header) == requests.utils.default_headers()[header]


def test_public_request_caller_headers_override_defaults(outbound_wire):
    request_config = config(outbound_wire.url)
    request_config["headers"] = {
        "user-agent": {"type": "PlainText", "value": "fixture-client/1"},
        "aCcEpT": {"type": "PlainText", "value": "application/json"},
    }
    assert columns.AddApiColumnView()._make_api_call(None, request_config)[1] == {
        "response_status": 200
    }
    received = requests.structures.CaseInsensitiveDict(outbound_wire.received[-1][2])
    assert received["User-Agent"] == "fixture-client/1"
    assert received["Accept"] == "application/json"


@pytest.mark.django_db
@pytest.mark.parametrize("location", ["headers", "params"])
@pytest.mark.parametrize(
    "secret_scope,dataset_scope,allowed",
    [
        ("default", "default", True),
        ("null", "default", True),
        ("default", "null", True),
        ("null", "null", True),
        ("sibling", "sibling", True),
        ("null", "sibling", False),
        ("default", "sibling", False),
        ("sibling", "null", False),
        ("foreign-null", "default", False),
        ("foreign-null", "null", False),
        ("deleted-null", "default", False),
        ("deleted-default", "null", False),
    ],
)
def test_stored_secret_workspace_visibility(
    organization, workspace, outbound_wire, location, secret_scope, dataset_scope, allowed
):
    assert workspace.is_default
    sibling = Workspace.objects.create(
        name="compat-sibling", organization=organization, created_by=workspace.created_by
    )
    scopes = {"default": workspace.id, "sibling": sibling.id, "null": None}
    secret = SecretModel.no_workspace_objects.create(
        name="compat-secret", organization=organization, workspace=workspace,
        key="fixture-value",
    )
    # update avoids BaseModel's automatic middleware workspace assignment.
    secret_org = (
        Organization.objects.create(name="compat-other-org")
        if secret_scope.startswith("foreign-") else organization
    )
    SecretModel.no_workspace_objects.filter(id=secret.id).update(
        workspace_id=scopes[secret_scope.split("-")[-1]],
        organization=secret_org,
        deleted=secret_scope.startswith("deleted-"),
    )
    clear_workspace_context()
    dataset = SimpleNamespace(
        organization_id=organization.id, workspace_id=scopes[dataset_scope]
    )
    cell = SimpleNamespace(row=SimpleNamespace(dataset=dataset))
    request_config = config(outbound_wire.url)
    request_config[location] = {
        "X-Fixture": {"type": "Secret", "value": str(secret.id)}
    }
    result = columns.AddApiColumnView()._make_api_call(cell, request_config)
    if allowed:
        assert result[1] == {"response_status": 200}
        _, path, headers, _ = outbound_wire.received[-1]
        assert (
            headers["X-Fixture"] if location == "headers" else path.split("=", 1)[1]
        ) == "fixture-value"
    else:
        assert result == ERROR
        assert not outbound_wire.received


@pytest.mark.parametrize("status", [400, 404, 429, 500, 503])
def test_remote_status_has_url_free_error(monkeypatch, status):
    response = requests.Response()
    response.status_code = status
    response.url = "http://public.fixture.test/private-value"
    response._content = b"private response body"
    monkeypatch.setattr(columns.outbound_destination, "request", Mock(return_value=response))
    assert columns.AddApiColumnView()._make_api_call(None, config(response.url)) == (
        "upstream request failed", {"response_status": 400}
    )


def test_invalid_json_has_url_free_error(monkeypatch):
    response = requests.Response()
    response.status_code = 200
    response._content = b"private invalid JSON"
    monkeypatch.setattr(columns.outbound_destination, "request", Mock(return_value=response))
    assert columns.AddApiColumnView()._make_api_call(
        None, config("http://public.fixture.test/private-value")
    ) == ("upstream response could not be decoded", {"response_status": 400})
