"""DB-backed action regressions; DNS is local and no delivery is requested."""

import socket
from types import SimpleNamespace

import pytest
from rest_framework.response import Response

from agentcc.hosted import deployment_profile
from agentcc.models.webhook import AgentccWebhook
from agentcc.services import url_safety
from agentcc.services.webhook_credential_updates import update_webhook
from agentcc.tests.test_webhook_encrypted_fixtures import make_webhook
from agentcc.tests.test_webhook_encrypted_fixtures import (
    webhook_encryption_key as webhook_encryption_key,
)
from agentcc.views.webhook_outbound import AgentccWebhookViewSet

pytestmark = pytest.mark.django_db
HTTP_URL = "http://hooks.example.com/events"
HTTPS_URL = "https://hooks.example.com/events"
WARNING_CODE = "webhook_url_unencrypted"
REFUSAL_CODE = "WEBHOOK_URL_NOT_HTTPS"


@pytest.fixture(autouse=True)
def local_resolution(monkeypatch, settings):
    settings.CLOUD_DEPLOYMENT = "test"
    monkeypatch.setenv("AGENTCC_DEPLOYMENT_PROFILE", "hosted")
    monkeypatch.setattr(
        url_safety, "is_cloud_env", lambda _deployment: deployment_profile() == "hosted"
    )

    def resolve(host, *_args, **_kwargs):
        addresses = {
            "hooks.example.com": "93.184.216.34",
            "other.example.com": "93.184.216.34",
            "private.example.com": "10.0.0.1",
            "127.0.0.1": "127.0.0.1",
        }
        assert host in addresses, "Unexpected DNS lookup"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (addresses[host], 0))]

    # Replace this module's binding, not the process-wide socket resolver used
    # by the database connection.
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
def organization():
    model = AgentccWebhook._meta.get_field("organization").remote_field.model
    return model._base_manager.create(name="webhook-https-policy")


@pytest.fixture
def action_path(monkeypatch, organization):
    """Real actions, serializers and persistence; not auth/routing coverage."""
    view = AgentccWebhookViewSet()
    view._gm = SimpleNamespace(
        success_response=lambda data: Response(data),
        bad_request=lambda data: Response(data, status=400),
    )
    monkeypatch.setattr(
        "tfc.ee_gating.check_ee_can_create", lambda *_args, **_kwargs: None
    )

    def invoke(action, payload, endpoint=None):
        if endpoint is not None:
            view.get_object = lambda: AgentccWebhook._base_manager.get(pk=endpoint.pk)
        return getattr(view, action)(
            SimpleNamespace(organization=organization, data=payload)
        )

    return invoke


@pytest.fixture
def endpoint(organization):
    # Encrypted insertion represents a migrated, previously saved HTTP endpoint.
    return make_webhook(
        manager=AgentccWebhook._base_manager,
        organization=organization,
        name="receiver",
        url=HTTP_URL,
        events=["error.occurred"],
    )


def payload(url):
    return {"name": "receiver", "url": url, "events": ["error.occurred"]}


def assert_refusal(response):
    assert response.status_code == 400
    error = response.data["url"][0]
    assert error.code == REFUSAL_CODE
    assert str(error) == "Use an HTTPS URL when creating a webhook or changing its URL."
    assert "warnings" not in response.data


def assert_warning(response):
    assert response.status_code == 200
    warning = response.data["warnings"]
    assert len(warning) == 1
    assert warning[0]["code"] == WARNING_CODE
    assert warning[0]["message"]


@pytest.mark.parametrize("scheme", ["http", "HTTP", "HtTp"])
def test_hosted_create_http_is_refused_without_inserting(action_path, scheme):
    before = AgentccWebhook._base_manager.count()
    assert_refusal(
        action_path("create", payload(f"{scheme}://hooks.example.com/events"))
    )
    assert AgentccWebhook._base_manager.count() == before


@pytest.mark.parametrize("action", ["update", "partial_update"])
@pytest.mark.parametrize(
    "new_url",
    [
        "http://other.example.com/events",
        "http://hooks.example.com/different",
        "http://hooks.example.com/events?changed=1",
        "HTTP://hooks.example.com/events",
    ],
)
def test_hosted_changed_http_is_refused(action_path, endpoint, action, new_url):
    assert_refusal(action_path(action, payload(new_url), endpoint))
    endpoint.refresh_from_db()
    assert endpoint.url == HTTP_URL
    assert endpoint.name == "receiver"


@pytest.mark.parametrize("action", ["update", "partial_update"])
def test_hosted_https_cannot_be_changed_to_http(action_path, endpoint, action):
    endpoint = update_webhook(endpoint, {"url": HTTPS_URL})
    assert_refusal(action_path(action, payload(HTTP_URL), endpoint))
    endpoint.refresh_from_db()
    assert endpoint.url == HTTPS_URL


@pytest.mark.parametrize("action", ["update", "partial_update"])
@pytest.mark.parametrize("stored_url", [HTTP_URL, "HTTP://hooks.example.com/events"])
def test_hosted_unchanged_http_saves_with_warning(
    action_path, endpoint, action, stored_url
):
    endpoint = update_webhook(endpoint, {"url": stored_url})
    data = {**payload(stored_url), "description": "changed description"}
    assert_warning(action_path(action, data, endpoint))
    endpoint.refresh_from_db()
    assert endpoint.url == stored_url
    assert endpoint.description == "changed description"


def test_hosted_partial_update_without_url_saves_with_warning(action_path, endpoint):
    response = action_path("partial_update", {"description": "changed"}, endpoint)
    assert_warning(response)
    endpoint.refresh_from_db()
    assert endpoint.url == HTTP_URL
    assert endpoint.description == "changed"


@pytest.mark.parametrize("profile", ["", "self-hosted"])
@pytest.mark.parametrize("scheme", ["http", "HTTP"])
def test_self_host_create_http_saves_with_warning(
    action_path, monkeypatch, profile, scheme
):
    monkeypatch.setenv("AGENTCC_DEPLOYMENT_PROFILE", profile)
    url = f"{scheme}://hooks.example.com/events"
    response = action_path("create", payload(url))
    assert_warning(response)
    saved = AgentccWebhook._base_manager.get(pk=response.data["id"])
    assert saved.url == url


@pytest.mark.parametrize("action", ["update", "partial_update"])
def test_self_host_changed_http_still_saves(action_path, endpoint, monkeypatch, action):
    monkeypatch.setenv("AGENTCC_DEPLOYMENT_PROFILE", "self-hosted")
    new_url = "http://other.example.com/events"
    assert_warning(action_path(action, payload(new_url), endpoint))
    endpoint.refresh_from_db()
    assert endpoint.url == new_url


@pytest.mark.parametrize("profile", ["hosted", "self-hosted"])
def test_https_create_has_no_warning(action_path, monkeypatch, profile):
    monkeypatch.setenv("AGENTCC_DEPLOYMENT_PROFILE", profile)
    response = action_path("create", payload(HTTPS_URL))
    assert response.status_code == 200
    assert "warnings" not in response.data
    assert AgentccWebhook._base_manager.get(pk=response.data["id"]).url == HTTPS_URL


@pytest.mark.parametrize("profile", ["hosted", "self-hosted"])
@pytest.mark.parametrize("action", ["update", "partial_update"])
def test_https_update_has_no_warning(
    action_path, endpoint, monkeypatch, profile, action
):
    monkeypatch.setenv("AGENTCC_DEPLOYMENT_PROFILE", profile)
    response = action_path(action, payload(HTTPS_URL), endpoint)
    assert response.status_code == 200
    assert "warnings" not in response.data
    endpoint.refresh_from_db()
    assert endpoint.url == HTTPS_URL


@pytest.mark.parametrize("profile", ["hosted", "self-hosted"])
@pytest.mark.parametrize("action", ["create", "update", "partial_update"])
@pytest.mark.parametrize("host", ["127.0.0.1", "private.example.com"])
def test_private_address_refusal_still_precedes_https_policy(
    action_path, endpoint, monkeypatch, profile, action, host
):
    monkeypatch.setenv("AGENTCC_DEPLOYMENT_PROFILE", profile)
    before = AgentccWebhook._base_manager.count()
    response = action_path(action, payload(f"http://{host}/events"), endpoint)
    assert response.status_code == 400
    error = response.data["url"][0]
    assert str(error) == url_safety.WEBHOOK_PRIVATE_URL_ERROR
    assert error.code == "invalid"
    assert "warnings" not in response.data
    endpoint.refresh_from_db()
    assert endpoint.url == HTTP_URL
    assert AgentccWebhook._base_manager.count() == before
