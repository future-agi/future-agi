"""CustomModelHandler calls one checked endpoint through the guarded POST.

The handler used to POST to ``key_config["endpoint_url"]`` with
``requests`` (redirects followed, body unbounded, ``str(exception)`` back to
the tenant) although the views validated only ``api_base``. It now reads the
one stored endpoint (``api_base``; ``endpoint_url`` for rows saved before
the fix), and ``tfc.utils.ssrf_guard.guarded_post`` re-checks it on every
call with the deployment's address policy, so legacy rows are covered
without a data migration.
"""

import json

import pytest
from django.test import override_settings

from agentic_eval.core_evals.run_prompt.runprompt_handlers.base_handler import (
    ModelHandlerContext,
)
from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.custom_model_handler import (
    CustomModelHandler,
)
from model_hub.models.custom_models import CustomAIModel
from tfc.utils import ssrf_guard
from tfc.utils.tests.guarded_post_fakes import (
    COMPLETION_BODY,
    JSON_HEADERS,
    FakeResolver,
    FakeResponse,
    FakeTransport,
    UnguardedHttp,
)
from tfc.utils.tests.loopback_endpoints import clear_proxy_env

PUBLIC_IP = "93.184.216.34"
PUBLIC_URL = "https://api.example.com/v1/chat/completions"
METADATA_TOKEN_URL = (
    "http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token"
)
DENY_ENV = "CUSTOM_MODEL_DENY_PRIVATE_ENDPOINTS"
MODEL = "handler-custom"


@pytest.fixture
def resolver(monkeypatch):
    resolver = FakeResolver().install(monkeypatch)
    resolver.add("api.example.com", PUBLIC_IP)
    return resolver


@pytest.fixture
def transport(monkeypatch):
    return FakeTransport().install(monkeypatch)


@pytest.fixture(autouse=True)
def _no_env_proxy(monkeypatch):
    # guarded_post honours HTTP(S)_PROXY; these tests use the fake transport.
    clear_proxy_env(monkeypatch)


@pytest.fixture(autouse=True)
def unguarded(monkeypatch):
    return UnguardedHttp().install(monkeypatch)


@pytest.fixture
def hosted(monkeypatch):
    monkeypatch.delenv(DENY_ENV, raising=False)
    with override_settings(CLOUD_DEPLOYMENT="US"):
        yield


@pytest.fixture
def self_hosted(monkeypatch):
    monkeypatch.delenv(DENY_ENV, raising=False)
    with override_settings(CLOUD_DEPLOYMENT=""):
        yield


def _handler(user, workspace, key_config):
    CustomAIModel.objects.create(
        user_model_id=MODEL,
        provider="custom",
        input_token_cost=0,
        output_token_cost=0,
        organization=user.organization,
        workspace=workspace,
        key_config=key_config,
        user=user,
        deleted=False,
    )
    context = ModelHandlerContext(
        model=MODEL,
        messages=[{"role": "user", "content": "hello"}],
        organization_id=str(user.organization.id),
        workspace_id=str(workspace.id),
        provider="custom",
    )
    return CustomModelHandler(context)


@pytest.mark.django_db
def test_public_endpoint_works_end_to_end(
    hosted, user, workspace, resolver, transport, unguarded
):
    handler = _handler(
        user,
        workspace,
        {"api_base": PUBLIC_URL, "headers": {"Authorization": "Bearer tenant"}},
    )
    result = handler.execute_sync()
    assert result.response == COMPLETION_BODY.decode()
    assert [p["host"] for p in transport.pools] == [PUBLIC_IP]
    assert transport.pools[0]["server_hostname"] == "api.example.com"
    sent = transport.requests[0]
    assert sent["url"] == "/v1/chat/completions"
    body = json.loads(sent["body"])
    assert body["messages"] == [{"role": "user", "content": "hello"}]
    assert body["stream"] is False
    assert transport.sent_headers["authorization"] == "Bearer tenant"
    assert unguarded.calls == []


@pytest.mark.django_db
def test_legacy_endpoint_url_row_still_works(
    hosted, user, workspace, resolver, transport, unguarded
):
    handler = _handler(user, workspace, {"endpoint_url": PUBLIC_URL, "headers": {}})
    assert handler.execute_sync().response == COMPLETION_BODY.decode()
    assert [p["host"] for p in transport.pools] == [PUBLIC_IP]


@pytest.mark.django_db
def test_legacy_internal_endpoint_url_is_refused_at_call_time(
    hosted, user, workspace, resolver, transport, unguarded
):
    handler = _handler(
        user,
        workspace,
        {
            "api_base": PUBLIC_URL,
            "endpoint_url": METADATA_TOKEN_URL,
            "headers": {"Metadata-Flavor": "Google"},
        },
    )
    with pytest.raises(RuntimeError) as excinfo:
        handler.execute_sync()
    assert ssrf_guard.ENDPOINT_ERROR_MESSAGES["blocked"] in str(excinfo.value)
    assert "169.254" not in str(excinfo.value)
    assert transport.pools == []
    assert unguarded.calls == []


@pytest.mark.django_db
@pytest.mark.parametrize(
    "address", ["127.0.0.1", "10.0.0.3", "::ffff:169.254.169.254", "64:ff9b::a9fe:a9fe"]
)
def test_hosted_internal_answers_are_refused_before_connecting(
    hosted, user, workspace, resolver, transport, unguarded, address
):
    resolver.add("alias.example.com", address)
    handler = _handler(
        user, workspace, {"api_base": "http://alias.example.com/v1", "headers": {}}
    )
    with pytest.raises(RuntimeError):
        handler.execute_sync()
    assert transport.pools == []
    assert unguarded.calls == []


@pytest.mark.django_db
def test_redirect_to_internal_is_not_followed(
    hosted, user, workspace, resolver, transport, unguarded
):
    transport.respond(FakeResponse(302, {"Location": METADATA_TOKEN_URL}))
    handler = _handler(user, workspace, {"api_base": PUBLIC_URL, "headers": {}})
    with pytest.raises(RuntimeError) as excinfo:
        handler.execute_sync()
    assert ssrf_guard.ENDPOINT_ERROR_MESSAGES["redirect"] in str(excinfo.value)
    assert len(transport.requests) == 1


@pytest.mark.django_db
def test_metadata_headers_are_stripped_at_call_time(
    hosted, user, workspace, resolver, transport, unguarded
):
    handler = _handler(
        user,
        workspace,
        {
            "endpoint_url": PUBLIC_URL,
            "headers": {
                "X-Api-Key": "k",
                "Metadata-Flavor": "Google",
                "X-Google-Metadata-Request": "True",
                "X-aws-ec2-metadata-token": "t",
                "X-Forwarded-Host": "internal",
                "Host": "metadata.google.internal",
            },
        },
    )
    handler.execute_sync()
    sent = transport.sent_headers
    assert sent["x-api-key"] == "k"
    assert sent["host"] == "api.example.com"
    for dropped in (
        "metadata-flavor",
        "x-google-metadata-request",
        "x-aws-ec2-metadata-token",
        "x-forwarded-host",
    ):
        assert dropped not in sent


@pytest.mark.django_db
def test_response_body_is_capped(
    hosted, user, workspace, resolver, transport, unguarded
):
    transport.respond(
        FakeResponse(
            200, JSON_HEADERS, b"x" * (ssrf_guard.MODEL_ENDPOINT_MAX_RESPONSE_BYTES + 1)
        )
    )
    handler = _handler(user, workspace, {"api_base": PUBLIC_URL, "headers": {}})
    with pytest.raises(RuntimeError) as excinfo:
        handler.execute_sync()
    assert ssrf_guard.ENDPOINT_ERROR_MESSAGES["too_large"] in str(excinfo.value)


@pytest.mark.django_db
def test_error_status_is_generic(
    hosted, user, workspace, resolver, transport, unguarded
):
    transport.respond(FakeResponse(401, JSON_HEADERS, b"internal-body-marker"))
    handler = _handler(user, workspace, {"api_base": PUBLIC_URL, "headers": {}})
    with pytest.raises(RuntimeError) as excinfo:
        handler.execute_sync()
    assert "The endpoint answered HTTP 401 Unauthorized." in str(excinfo.value)
    assert "internal-body-marker" not in str(excinfo.value)
    assert "api.example.com" not in str(excinfo.value)


@pytest.mark.django_db
def test_self_hosted_localhost_model_works_without_a_setting(
    self_hosted, user, workspace, resolver, transport, unguarded
):
    resolver.add("localhost", "127.0.0.1")
    handler = _handler(
        user,
        workspace,
        {"api_base": "http://localhost:11434/v1/chat/completions", "headers": {}},
    )
    assert handler.execute_sync().response == COMPLETION_BODY.decode()
    assert transport.pools[0]["host"] == "127.0.0.1"


@pytest.mark.django_db
def test_self_hosted_metadata_is_refused_by_default(
    self_hosted, user, workspace, resolver, transport, unguarded
):
    handler = _handler(user, workspace, {"endpoint_url": METADATA_TOKEN_URL})
    with pytest.raises(RuntimeError):
        handler.execute_sync()
    assert transport.pools == []
    assert unguarded.calls == []
