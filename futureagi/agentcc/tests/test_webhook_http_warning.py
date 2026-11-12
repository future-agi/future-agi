"""DB-backed response regressions with DNS isolated; no receiver calls."""

import socket
from types import SimpleNamespace

import pytest
from rest_framework.response import Response

from agentcc.models.webhook import AgentccWebhook
from agentcc.serializers.webhook import AgentccWebhookSerializer
from agentcc.services import url_safety
from agentcc.services.webhook_credential_updates import update_webhook
from agentcc.tests.test_webhook_encrypted_fixtures import make_webhook
from agentcc.tests.test_webhook_encrypted_fixtures import (
    webhook_encryption_key as webhook_encryption_key,
)
from agentcc.views.webhook_outbound import AgentccWebhookViewSet

pytestmark = pytest.mark.django_db

WARNING = "This URL uses HTTP, so deliveries are not encrypted. Webhook payloads can include event data from your organization. Use an HTTPS URL to protect payloads in transit."
WARNING_CODE = "webhook_url_unencrypted"
HTTP_URL = "http://hooks.example.com/events"
HTTPS_URL = "https://hooks.example.com/events"


@pytest.fixture(autouse=True)
def isolated_resolution(monkeypatch, settings):
    settings.CLOUD_DEPLOYMENT = "test"
    monkeypatch.setenv("AGENTCC_DEPLOYMENT_PROFILE", "self-hosted")
    monkeypatch.setattr(url_safety, "is_cloud_env", lambda _deployment: False)

    def resolve(host, *_args, **_kwargs):
        address = "127.0.0.1" if host == "127.0.0.1" else "93.184.216.34"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))]

    monkeypatch.setattr(
        url_safety,
        "socket",
        SimpleNamespace(
            getaddrinfo=resolve,
            AF_UNSPEC=socket.AF_UNSPEC,
            SOCK_STREAM=socket.SOCK_STREAM,
            gaierror=socket.gaierror,
        ),
    )


@pytest.fixture
def response_path(monkeypatch):
    """Real actions, serializers and encrypted writes; not auth/routing coverage."""
    org_model = AgentccWebhook._meta.get_field("organization").remote_field.model
    org = org_model._base_manager.create(name="webhook-http-warning")
    endpoint = make_webhook(
        organization=org,
        name="previously-saved",
        url=HTTP_URL,
        events=["error.occurred"],
        headers={},
        secret="",
    )
    view = AgentccWebhookViewSet()
    view.get_object = lambda: AgentccWebhook._base_manager.get(pk=endpoint.pk)
    view._gm = SimpleNamespace(
        success_response=lambda data: Response(data),
        bad_request=lambda data: Response(data, status=400),
    )
    monkeypatch.setattr(
        "tfc.ee_gating.check_ee_can_create", lambda *_args, **_kwargs: None
    )
    return view, org, endpoint


@pytest.mark.parametrize("action", ["create", "update", "partial_update"])
@pytest.mark.parametrize("url", [HTTP_URL, "HTTP://hooks.example.com/events"])
def test_http_warning_on_saved_response(response_path, action, url):
    view, org, _endpoint = response_path
    request = SimpleNamespace(
        organization=org,
        data={"name": "receiver", "url": url, "events": ["error.occurred"]},
    )
    response = getattr(view, action)(request)
    assert response.status_code == 200
    assert response.data["warnings"] == [{"code": WARNING_CODE, "message": WARNING}]


@pytest.mark.parametrize("action", ["create", "update", "partial_update"])
def test_https_responses_do_not_gain_warnings(response_path, action):
    view, org, _endpoint = response_path
    request = SimpleNamespace(
        organization=org,
        data={"name": "receiver", "url": HTTPS_URL, "events": ["error.occurred"]},
    )
    response = getattr(view, action)(request)
    assert response.status_code == 200
    assert "warnings" not in response.data


@pytest.mark.parametrize("url", [HTTP_URL, HTTPS_URL])
def test_partial_update_uses_saved_url_when_url_is_omitted(response_path, url):
    view, org, endpoint = response_path
    update_webhook(endpoint, {"url": url})
    response = view.partial_update(
        SimpleNamespace(organization=org, data={"name": "renamed"})
    )
    assert response.status_code == 200
    assert response.data["url"] == url
    if url == HTTP_URL:
        assert response.data["warnings"] == [{"code": WARNING_CODE, "message": WARNING}]
    else:
        assert "warnings" not in response.data


@pytest.mark.parametrize("action", ["create", "update", "partial_update"])
def test_hosted_private_http_retains_existing_refusal(
    response_path, monkeypatch, action
):
    monkeypatch.setenv("AGENTCC_DEPLOYMENT_PROFILE", "hosted")
    monkeypatch.setattr(url_safety, "is_cloud_env", lambda _deployment: True)
    view, org, _endpoint = response_path
    response = getattr(view, action)(
        SimpleNamespace(
            organization=org,
            data={
                "name": "receiver",
                "url": "http://127.0.0.1/events",
                "events": ["error.occurred"],
            },
        )
    )
    assert response.status_code == 400
    assert str(response.data["url"][0]) == url_safety.WEBHOOK_PRIVATE_URL_ERROR
    assert "warnings" not in response.data


def test_self_host_public_http_preserves_the_base_validation_policy(monkeypatch):
    # Self-hosted warning policy permits public HTTP even when cloud address
    # checks are enabled.
    monkeypatch.setattr(url_safety, "is_cloud_env", lambda _deployment: True)
    serializer = AgentccWebhookSerializer(
        data={"name": "receiver", "url": HTTP_URL, "events": ["error.occurred"]}
    )
    assert serializer.is_valid(), serializer.errors
