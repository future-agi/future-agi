"""API-column destination and stored-secret boundary contracts."""

import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from model_hub.views import dynamic_columns as columns

ERROR = ("destination not allowed or unreachable", {"response_status": 400})


@pytest.fixture
def legacy_transport(monkeypatch):
    """The old transport succeeds without opening any network connection."""
    response = requests.Response()
    response.status_code = 200
    response._content = b'"fixture-result"'
    response.encoding = "utf-8"
    transport = Mock(return_value=response)
    monkeypatch.setattr(requests, "request", transport)
    # These refusals must precede worker startup as well as any network activity.
    worker = Mock(side_effect=AssertionError("unexpected outbound worker"))
    monkeypatch.setattr(subprocess, "Popen", worker)
    yield transport
    worker.assert_not_called()


def config(url="http://public.fixture.test/value"):
    return {"url": url, "method": "GET", "output_type": "string"}


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "169.254.20.30",
        "10.2.3.4",
        "172.16.2.3",
        "192.168.2.3",
        "100.64.2.3",
        "0.0.0.0",
        "224.0.0.1",
        "[::1]",
        "[fe80::1]",
        "[fc00::1]",
        "[fec0::1]",
        "[ff02::1]",
        "[::]",
        "[::ffff:127.0.0.1]",
        "[::127.0.0.1]",
        "[64:ff9b::7f00:1]",
        "[64:ff9b:1::a00:1]",
        "[2002:7f00:1::]",
        "2130706433",
        "0177.0.0.1",
        "0x7f000001",
        "127.1",
    ],
)
def test_nonpublic_destinations_are_refused(host, legacy_transport):
    assert (
        columns.AddApiColumnView()._make_api_call(None, config(f"http://{host}/"))
        == ERROR
    )


@pytest.mark.parametrize(
    "url", ["ftp://public.fixture.test/x", "http://name:pass@public.fixture.test/x"]
)
def test_url_authority_is_validated(url, legacy_transport):
    assert columns.AddApiColumnView()._make_api_call(None, config(url)) == ERROR


def test_row_substitution_is_checked_before_transport(monkeypatch, legacy_transport):
    view = columns.AddApiColumnView()
    monkeypatch.setattr(view, "_resolve_cell_value", lambda variable, row: "127.0.0.1")
    cell = SimpleNamespace(
        row=SimpleNamespace(
            dataset=SimpleNamespace(organization_id="org", workspace_id="workspace")
        )
    )
    assert view._make_api_call(cell, config("http://{{column}}/value")) == ERROR


@pytest.mark.parametrize("location", ["headers", "params"])
def test_foreign_secret_is_not_resolved(location, monkeypatch, legacy_transport):
    record = SimpleNamespace(
        id="foreign",
        organization_id="other-org",
        workspace_id="other-workspace",
        deleted=False,
        actual_key="fixture-value",
    )
    lookups = []
    workspace_filters = []

    class Manager:
        def filter(self, scope):
            workspace_filters.append(scope)
            return self

        def get(self, **scope):
            lookups.append(scope)
            if all(getattr(record, key) == value for key, value in scope.items()):
                return record
            raise columns.SecretModel.DoesNotExist()

    monkeypatch.setattr(columns.SecretModel, "objects", Manager())
    monkeypatch.setattr(columns.SecretModel, "no_workspace_objects", Manager())
    workspaces = Mock()
    workspaces.no_workspace_objects.filter.return_value.exists.return_value = False
    # The original base lacks the explicit workspace model import; this stub is
    # unused there, so its unscoped lookup still returns the foreign fixture.
    monkeypatch.setattr(columns, "Workspace", workspaces, raising=False)
    dataset = SimpleNamespace(organization_id="own-org", workspace_id="own-workspace")
    cell = SimpleNamespace(row=SimpleNamespace(dataset=dataset))
    request_config = config()
    request_config[location] = {"X-Fixture": {"type": "Secret", "value": "foreign"}}
    assert columns.AddApiColumnView()._make_api_call(cell, request_config) == ERROR
    assert lookups == [
        {
            "id": "foreign",
            "organization_id": "own-org",
            "deleted": False,
        }
    ]
    assert workspace_filters == [columns.Q(workspace_id="own-workspace")]
    workspaces.no_workspace_objects.filter.assert_called_once_with(
        id="own-workspace", organization_id="own-org", is_default=True
    )
    legacy_transport.assert_not_called()
