"""SRC-HTTP-02 regression cases; transport is controllable, never a canned API.

The URL-resolution cases deliberately stay red until the shared URL owner
registers HttpEndpointTestView. No live network is needed by these tests.
"""

import copy
import json
from unittest.mock import patch

import httpx
import pytest
import requests
from django.urls import resolve
from rest_framework.test import APIClient

from accounts.models.workspace import Workspace
from model_hub.models.api_key import SecretModel
from model_hub.views import http_endpoint_test as endpoint
from tfc.utils import outbound_destination
from model_hub.tests import test_experiment_v2_results_tenancy as tenancy

tenants = tenancy.tenants

URL = "/model-hub/r4/endpoints/test/"
TRACEPARENT = "00-11111111111111111111111111111111-2222222222222222-01"
pytestmark = pytest.mark.django_db


@pytest.fixture
def payload():
    return {
        "url": "https://fixture.example/answer/{{topic}}",
        "method": "POST",
        "headers": [{"name": "Authorization", "secret": "ENDPOINT_TOKEN"}],
        "body_template": '{"message": "{{question}}"}',
        "output_path": "$.data.answer",
        "transform": {"output": "$.data.answer"},
        "mapping": {"question": "question-column"},
        "dataset_id": None,
        "row_id": None,
        "trace_context": {"traceId": "1" * 32, "spanId": "2" * 16},
        # These are buildRequest's bytes, not a second server-side template.
        "prepared_request": {
            "url": "https://fixture.example/answer/a%2Fb",
            "method": "POST",
            "headers": [
                {"name": "Authorization", "secret_ref": "ENDPOINT_TOKEN"},
                {"name": "traceparent", "value": TRACEPARENT},
            ],
            "body": ' {"message":"a \\"quoted\\" question","count":0} ',
        },
    }


@pytest.fixture
def token(organization, workspace):
    return SecretModel.no_workspace_objects.create(
        name="ENDPOINT_TOKEN",
        key="fixture-only-header-value",
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def https_fixture(monkeypatch):
    """Replace only the wire transport; request/response and view stay real."""
    seen = []
    state = {"status": 200, "reply": {"data": {"answer": "from HTTPS fixture"}}}

    def send(*, method, url, headers, data, timeout, max_bytes):
        assert timeout == 15.0
        assert max_bytes == endpoint.MAX_RESPONSE_BYTES
        request = httpx.Request(method, url, headers=headers, content=(data or "").encode("utf-8"))
        seen.append(request)
        if "exception" in state:
            raise state["exception"]
        # json=None on HTTPX means no body, not the JSON literal null.
        content = (state["text"].encode("utf-8") if "text" in state
                   else json.dumps(state["reply"]).encode("utf-8"))
        if len(content) > max_bytes:
            raise outbound_destination.DestinationError("body_limit")
        response = requests.Response()
        response.status_code = state["status"]
        response._content = content
        response.headers.update({
            "Content-Type": "text/plain" if "text" in state else "application/json",
            "traceparent": request.headers.get("traceparent", ""),
        })
        return response

    monkeypatch.setattr(outbound_destination, "request", send)
    return seen, state


def test_core_route_resolves_to_non_ee_view():
    match = resolve(URL)
    assert match.func.view_class is endpoint.HttpEndpointTestView
    assert match.func.view_class.__module__ == "model_hub.views.http_endpoint_test"


def test_anonymous_cannot_send_endpoint_requests(payload, https_fixture):
    response = APIClient().post(URL, payload, format="json")
    assert response.status_code in (401, 403)
    assert https_fixture[0] == []


def test_selected_row_bytes_resolved_headers_and_measured_reply(
    auth_client, payload, token, https_fixture
):
    before = copy.deepcopy(payload)
    with patch.object(endpoint, "perf_counter", side_effect=[10.0, 10.125]):
        response = auth_client.post(URL, payload, format="json")
    assert response.status_code == 200, response.content
    result = response.json()["result"]
    sent = https_fixture[0][0]
    assert len(https_fixture[0]) == 1
    assert str(sent.url) == payload["prepared_request"]["url"]
    assert sent.method == "POST"
    assert sent.content == payload["prepared_request"]["body"].encode("utf-8")
    assert sent.headers["authorization"] == token.actual_key
    assert sent.headers["content-type"] == "application/json"
    assert sent.headers["traceparent"] == TRACEPARENT
    assert result["status"] == 200
    assert result["latency_ms"] == pytest.approx(125.0)
    assert result["simulated"] is False
    assert result["provenance"] == "upstream"
    assert result["response"] == https_fixture[1]["reply"]
    assert result["response_is_json"] is True
    assert result["error"] is None
    assert result["echoed"] == TRACEPARENT
    assert result["request"]["body"] == payload["prepared_request"]["body"]
    assert "ENDPOINT_TOKEN" in result["request"]["headers"]["Authorization"]
    assert token.actual_key not in json.dumps(result)
    assert payload == before


def test_get_does_not_send_a_body(auth_client, payload, token, https_fixture):
    payload["prepared_request"].update(method="GET", body="")
    response = auth_client.post(URL, payload, format="json")
    assert response.status_code == 200, response.content
    assert https_fixture[0][0].method == "GET"
    assert https_fixture[0][0].content == b""


def test_non_2xx_is_actionable_and_same_configuration_can_retry(
    auth_client, payload, token, https_fixture
):
    https_fixture[1]["status"] = 503
    failed = auth_client.post(URL, payload, format="json").json()["result"]
    assert failed["status"] == 503
    assert "HTTP 503" in failed["error"]
    assert failed["response"] == https_fixture[1]["reply"]
    https_fixture[1]["status"] = 200
    recovered = auth_client.post(URL, payload, format="json").json()["result"]
    assert recovered["error"] is None
    assert recovered["response"]["data"]["answer"] == "from HTTPS fixture"
    assert https_fixture[0][0].content == https_fixture[0][1].content


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (outbound_destination.DestinationError("ConnectionError"), "Couldn't reach"),
        (outbound_destination.DestinationError("deadline"), "15 s"),
        (outbound_destination.DestinationError("gaierror"), "Couldn't resolve"),
        (outbound_destination.DestinationError("address_class"), "public"),
        (outbound_destination.DestinationError("redirect"), "redirect"),
    ],
)
def test_transport_failure_is_not_a_simulated_response(
    auth_client, payload, token, https_fixture, failure, expected
):
    https_fixture[1]["exception"] = failure
    response = auth_client.post(URL, payload, format="json")
    assert response.status_code == 200, response.content
    result = response.json()["result"]
    assert expected in result["error"]
    assert result["simulated"] is False
    assert result["response"] is None
    assert "do not expose" not in json.dumps(result)


@pytest.mark.parametrize("reply", [False, 0, None, [], {}])
def test_json_scalars_and_empty_values_are_real_json(
    auth_client, payload, token, https_fixture, reply
):
    https_fixture[1]["reply"] = reply
    result = auth_client.post(URL, payload, format="json").json()["result"]
    assert result["response_is_json"] is True
    assert result["response"] == reply
    assert result["error"] is None


def test_non_json_preserves_actual_body_for_diagnosis(
    auth_client, payload, token, https_fixture
):
    https_fixture[1]["text"] = "upstream is warming up"
    result = auth_client.post(URL, payload, format="json").json()["result"]
    assert result["response_is_json"] is False
    assert result["response"] == "upstream is warming up"
    assert "not JSON" in result["error"]


def test_missing_secret_blocks_then_recovers_after_settings_fix(
    auth_client, payload, https_fixture, organization, workspace
):
    response = auth_client.post(URL, payload, format="json")
    assert response.status_code == 400, response.content
    assert "No workspace secret named ENDPOINT_TOKEN" in response.json()["result"]
    assert https_fixture[0] == []
    SecretModel.no_workspace_objects.create(
        name="ENDPOINT_TOKEN", key="new-fixture-value",
        organization=organization, workspace=workspace,
    )
    assert auth_client.post(URL, payload, format="json").status_code == 200
    assert https_fixture[0][0].headers["authorization"] == "new-fixture-value"


def test_another_workspace_secret_is_not_resolved(
    auth_client, payload, https_fixture, organization, user
):
    other = Workspace.objects.create(
        name="Endpoint other workspace", organization=organization, created_by=user
    )
    SecretModel.no_workspace_objects.create(
        name="ENDPOINT_TOKEN", key="other-workspace-fixture-value",
        organization=organization, workspace=other,
    )
    response = auth_client.post(URL, payload, format="json")
    assert response.status_code == 400, response.content
    assert "No workspace secret named ENDPOINT_TOKEN" in response.json()["result"]
    assert https_fixture[0] == []


def test_echoed_secret_value_is_not_returned(
    auth_client, payload, token, https_fixture
):
    https_fixture[1]["reply"] = {"headers": {"Authorization": token.actual_key}}
    result = auth_client.post(URL, payload, format="json").json()["result"]
    assert result["response"]["headers"]["Authorization"] == "[redacted]"
    assert token.actual_key not in json.dumps(result)


def test_prepared_request_is_required_no_template_or_fixture_fallback(
    auth_client, payload, https_fixture
):
    payload.pop("prepared_request")
    response = auth_client.post(URL, payload, format="json")
    assert response.status_code == 400, response.content
    assert https_fixture[0] == []


def test_response_size_is_bounded(auth_client, payload, token, https_fixture, monkeypatch):
    monkeypatch.setattr(endpoint, "MAX_RESPONSE_BYTES", 16)
    https_fixture[1]["text"] = "x" * 40
    result = auth_client.post(URL, payload, format="json").json()["result"]
    assert "too large" in result["error"]
    assert result["response"] is None


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "https://[::1]/", "http://10.0.0.1/",
    "http://169.254.169.254/latest/meta-data/", "http://168.63.129.16/",
    "file:///etc/hosts",
])
def test_ssrf_refusal_happens_before_worker_or_socket(url):
    prepared = {"url": url, "method": "GET", "headers": [], "body": ""}
    # Exercise the actual production transport's admission, not a mocked refusal.
    with patch.object(outbound_destination.subprocess, "Popen") as spawn:
        result = endpoint.execute_endpoint_request(prepared, resolve_secret=lambda ref: None)
    spawn.assert_not_called()
    assert result["status"] is None
    assert result["response"] is None
    assert "public" in result["error"]


def test_cross_org_secret_reference_never_dispatches(tenants, https_fixture):
    a, b = tenants["a"], tenants["b"]
    SecretModel.no_workspace_objects.create(
        name="OTHER_ORG_TOKEN", key="other-org-fixture-value",
        organization=b["org"], workspace=b["workspace"],
    )
    response = a["client"].post(URL, {"prepared_request": {
        "url": "https://fixture.example/answer", "method": "GET", "body": "",
        "headers": [{"name": "Authorization", "secret_ref": "OTHER_ORG_TOKEN"}],
    }}, format="json")
    assert response.status_code == 400, response.content
    assert "No workspace secret named" in response.json()["result"]
    assert "other-org-fixture-value" not in response.content.decode()
    assert https_fixture[0] == []


def test_dns_private_answer_is_refused_before_connect(monkeypatch):
    monkeypatch.setattr(outbound_destination.socket, "getaddrinfo", lambda *a, **k: [
        (2, 1, 6, "", ("93.184.216.34", 443)),
        (2, 1, 6, "", ("169.254.169.254", 443)),
    ])
    with patch.object(outbound_destination, "_connect_checked") as connect:
        with pytest.raises(outbound_destination.DestinationError, match="address_class"):
            outbound_destination._exchange({
                "url": "https://fixture.example/answer", "method": "GET",
                "timeout": 15.0, "max_bytes": 1024,
            })
    connect.assert_not_called()


@pytest.mark.parametrize(
    ("credential", "reply", "expected"),
    [
        pytest.param("8675309", 8675309, "[redacted]", id="top-level-integer"),
        pytest.param(
            "8675309",
            {"data": {"credential": 8675309, "count": 7, "ratio": 1.5,
                      "active": True, "missing": None}},
            {"data": {"credential": "[redacted]", "count": 7, "ratio": 1.5,
                      "active": True, "missing": None}},
            id="nested-object-number",
        ),
        pytest.param(
            "8675309", [7, [8675309, 1.5, False, None]],
            [7, ["[redacted]", 1.5, False, None]], id="nested-list-number",
        ),
        pytest.param("8675309", 186753090, "[redacted]", id="integer-substring"),
        pytest.param("8675309", 8675309.25, "[redacted]", id="float-substring"),
        pytest.param("true", True, "[redacted]", id="boolean-true"),
        pytest.param("false", False, "[redacted]", id="boolean-false"),
        pytest.param("null", None, "[redacted]", id="null"),
        pytest.param("200", {"credential": 200, "count": 7},
                     {"credential": "[redacted]", "count": 7}, id="status-stays-numeric"),
        pytest.param("125", [125, 7], ["[redacted]", 7], id="latency-stays-numeric"),
    ],
)
def test_endpoint_test_redacts_credential_json_scalars_only_in_payload(
    auth_client, payload, https_fixture, organization, workspace,
    credential, reply, expected,
):
    """Real Test route, synthetic header, transport only mocked."""
    SecretModel.no_workspace_objects.create(
        name="ENDPOINT_TOKEN", key=credential,
        organization=organization, workspace=workspace,
    )
    https_fixture[1]["reply"] = reply
    with patch.object(endpoint, "perf_counter", side_effect=[10.0, 10.125]):
        response = auth_client.post(URL, payload, format="json")
    assert response.status_code == 200, response.content
    result = response.json()["result"]
    assert len(https_fixture[0]) == 1
    assert https_fixture[0][0].headers["authorization"] == credential
    assert result["response"] == expected
    # JSON equality also distinguishes preserved booleans from 0/1.
    assert json.dumps(result["response"], sort_keys=True) == json.dumps(expected, sort_keys=True)
    assert credential not in json.dumps(result["response"])
    if credential == "8675309":
        assert credential not in response.content.decode("utf-8")
    assert type(result["status"]) is int
    assert result["status"] == 200
    assert type(result["latency_ms"]) is float
    assert result["latency_ms"] == pytest.approx(125.0)
    assert result["simulated"] is False
    assert result["response_is_json"] is True
    assert result["error"] is None


def test_transport_failure_keeps_absent_response_with_null_credential(
    auth_client, payload, https_fixture, organization, workspace,
):
    """A credential equal to "null" must not turn a missing reply into a redacted body."""
    SecretModel.no_workspace_objects.create(
        name="ENDPOINT_TOKEN", key="null",
        organization=organization, workspace=workspace,
    )
    https_fixture[1]["exception"] = outbound_destination.DestinationError("deadline")
    response = auth_client.post(URL, payload, format="json")
    assert response.status_code == 200, response.content
    result = response.json()["result"]
    assert result["response"] is None
    assert result["response_is_json"] is False
    assert "15 s" in result["error"]
