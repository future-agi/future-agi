"""openai/azure custom models: the tenant ``api_base`` goes through the guard.

For openai and azure custom models litellm makes the call. Checking only the
stored ``api_base`` before the call left a hosted deployment open: the OpenAI
SDK and litellm's own HTTP handler follow redirects, so a public endpoint
that redirected to 169.254.169.254 got the metadata server's answer back to
the tenant, through the validation error and through custom-prompt evals.
The stored config also passed every key to litellm (``extra_headers`` with
``Metadata-Flavor``).

Now every litellm call with a tenant base URL gets a client built on the
guarded httpx transport (checked and pinned connection, no redirects,
metadata headers dropped); the stored openai/azure config is whitelisted at
save and at call time; and the tenant sees categorised generic errors.

Real litellm and the real OpenAI SDK, against loopback servers only:
``public-a.test`` -> 127.0.0.1 treated as public; ``[::1]`` stands in for the
metadata server and keeps its real class, so hosted refuses it.
"""

import pytest
from django.test import override_settings

from agentic_eval.core.llm import llm as llm_module
from agentic_eval.core_evals.run_prompt.litellm_models import LiteLLMModelManager
from model_hub.models.custom_models import CustomAIModel
from model_hub.utils import model_endpoints
from model_hub.utils.utils import validate_model_working
from tfc.utils import ssrf_guard
from tfc.utils.tests.guarded_post_fakes import FakeResolver
from tfc.utils.tests.loopback_endpoints import (
    MODEL_ANSWER,
    PUBLIC_NAME,
    SECRET_MARKER,
    LoopbackServer,
    clear_proxy_env,
    metadata_stand_in,
    model_server,
    recording_proxy,
    redirector,
    treat_as_public,
)

CREATE_URL = "/model-hub/custom_models/create/"
EDIT_URL = "/model-hub/custom_models/edit/"
DENY_ENV = "CUSTOM_MODEL_DENY_PRIVATE_ENDPOINTS"
MESSAGES = ssrf_guard.ENDPOINT_ERROR_MESSAGES
PLATFORM_KEY = "sk-platform-test"


@pytest.fixture(autouse=True)
def _environment(monkeypatch):
    clear_proxy_env(monkeypatch)
    monkeypatch.delenv(DENY_ENV, raising=False)
    monkeypatch.setattr(llm_module, "LITELLM_NUM_RETRIES", 0)
    # LLM() builds a platform OpenAI client at init; a tenant endpoint must
    # never receive this key.
    monkeypatch.setenv("OPENAI_API_KEY", PLATFORM_KEY)


@pytest.fixture
def resolver(monkeypatch):
    resolver = FakeResolver().install(monkeypatch)
    resolver.add(PUBLIC_NAME, "127.0.0.1")
    resolver.add("localhost", "127.0.0.1")
    resolver.add("api.example.com", "93.184.216.34")
    resolver.add("internal-model.example", "10.0.0.5")
    return resolver


@pytest.fixture
def hosted(monkeypatch):
    treat_as_public(monkeypatch)
    with override_settings(CLOUD_DEPLOYMENT="US"):
        yield


@pytest.fixture
def self_hosted():
    with override_settings(CLOUD_DEPLOYMENT=""):
        yield


@pytest.fixture
def metadata():
    with metadata_stand_in() as server:
        yield server


@pytest.fixture(params=[302, 307])
def internal_redirect(request, metadata):
    """A public endpoint that redirects to the metadata stand-in."""
    target = f"http://[::1]:{metadata.port}/latest/meta-data/iam/security-credentials/r"
    with redirector(target, request.param) as server:
        yield server


def _base(server, path="/v1"):
    return f"http://{PUBLIC_NAME}:{server.port}{path}"


def _openai_key(server, **extra):
    return {"api_key": "sk-tenant", "api_base": _base(server), **extra}


def _azure_key(server, endpoint_type="legacy"):
    path = "" if endpoint_type == "legacy" else "/models"
    key = {
        "api_base": _base(server, path),
        "api_key": "azure-tenant",
        "azure_endpoint_type": endpoint_type,
    }
    if endpoint_type == "legacy":
        key["api_version"] = "2024-02-01"
    return key


def _no_secret(*texts):
    return all(SECRET_MARKER not in str(text) for text in texts)


# ------------------------------------------------ validate_model_working ---


def test_validate_openai_redirect_to_metadata_is_refused(
    hosted, resolver, metadata, internal_redirect
):
    result = validate_model_working(
        model_name="gpt-x",
        api_key=_openai_key(
            internal_redirect, extra_headers={"Metadata-Flavor": "Google"}
        ),
        provider="openai",
    )
    assert isinstance(result, Exception)
    assert str(result) == MESSAGES["redirect"]
    assert metadata.requests == []
    assert len(internal_redirect.requests) == 1


@pytest.mark.parametrize("endpoint_type", ["legacy", "foundry"])
def test_validate_azure_redirect_to_metadata_is_refused(
    hosted, resolver, metadata, internal_redirect, endpoint_type
):
    result = validate_model_working(
        model_name="gpt-x",
        api_key=_azure_key(internal_redirect, endpoint_type),
        provider="azure",
    )
    assert isinstance(result, Exception)
    assert str(result) == MESSAGES["redirect"]
    assert metadata.requests == []


def test_validate_openai_public_endpoint_works(hosted, resolver):
    with model_server() as server:
        result = validate_model_working(
            model_name="gpt-x", api_key=_openai_key(server), provider="openai"
        )
    assert result == MODEL_ANSWER
    [seen] = server.requests
    assert seen.target == "/v1/chat/completions"
    assert seen.headers["authorization"] == "Bearer sk-tenant"


@pytest.mark.parametrize("endpoint_type", ["legacy", "foundry"])
def test_validate_azure_public_endpoint_works(hosted, resolver, endpoint_type):
    with model_server() as server:
        result = validate_model_working(
            model_name="gpt-x",
            api_key=_azure_key(server, endpoint_type),
            provider="azure",
        )
    assert result == MODEL_ANSWER
    assert len(server.requests) == 1


def test_validate_openai_internal_api_base_is_refused(hosted, resolver):
    result = validate_model_working(
        model_name="gpt-x",
        api_key={"api_key": "sk", "api_base": "http://internal-model.example/v1"},
        provider="openai",
    )
    assert str(result) == MESSAGES["blocked"]


def test_validate_error_status_is_generic(hosted, resolver):
    body = b'{"error": {"message": "' + SECRET_MARKER.encode() + b'"}}'
    with LoopbackServer(
        lambda request: (401, {"Content-Type": "application/json"}, body)
    ) as server:
        result = validate_model_working(
            model_name="gpt-x", api_key=_openai_key(server), provider="openai"
        )
    assert str(result) == ssrf_guard.endpoint_status_message(401)


def test_validate_unparseable_answer_is_generic(hosted, resolver):
    body = b'{"AccessKeyId": "' + SECRET_MARKER.encode() + b'"}'
    with model_server(body=body) as server:
        result = validate_model_working(
            model_name="gpt-x", api_key=_openai_key(server), provider="openai"
        )
    assert str(result) == MESSAGES["bad_response"]


def test_validate_self_hosted_localhost_openai_still_works(self_hosted, resolver):
    with model_server() as server:
        result = validate_model_working(
            model_name="gpt-x",
            api_key={"api_key": "sk", "api_base": f"http://localhost:{server.port}/v1"},
            provider="openai",
        )
    assert result == MODEL_ANSWER


def test_validate_self_hosted_metadata_api_base_is_refused(self_hosted, resolver):
    result = validate_model_working(
        model_name="gpt-x",
        api_key={"api_key": "sk", "api_base": "http://169.254.169.254/v1"},
        provider="openai",
    )
    assert str(result) == MESSAGES["blocked"]


def test_validate_through_the_operator_proxy(self_hosted, monkeypatch, resolver):
    with recording_proxy() as proxy:
        monkeypatch.setenv("HTTP_PROXY", proxy.base)
        result = validate_model_working(
            model_name="gpt-x",
            api_key={"api_key": "sk", "api_base": "http://api.example.com/v1"},
            provider="openai",
        )
    assert result == MODEL_ANSWER
    assert proxy.requests[0].target == "http://api.example.com/v1/chat/completions"


# ---------------------------------------------------- evals: LLM.call_llm ---


def _llm(api_key, provider="openai", model="openai/gpt-x"):
    return llm_module.LLM(provider=provider, model_name=model, api_key=api_key)


def test_call_llm_redirect_to_metadata_is_refused(
    hosted, resolver, metadata, internal_redirect
):
    llm = _llm({"key": "sk-tenant", "api_base": _base(internal_redirect)})
    with pytest.raises(ValueError) as excinfo:
        llm.call_llm([{"role": "user", "content": "judge"}], provider="openai")
    assert metadata.requests == []
    assert _no_secret(excinfo.value)
    assert MESSAGES["redirect"] in str(excinfo.value)


def test_call_llm_azure_redirect_to_metadata_is_refused(
    hosted, resolver, metadata, internal_redirect
):
    llm = _llm(_azure_key(internal_redirect), provider="azure", model="gpt-x")
    with pytest.raises(ValueError) as excinfo:
        llm.call_llm([{"role": "user", "content": "judge"}], provider="azure")
    assert metadata.requests == []
    assert _no_secret(excinfo.value)


def test_call_llm_unparseable_answer_does_not_reach_the_tenant(hosted, resolver):
    body = b'{"AccessKeyId": "' + SECRET_MARKER.encode() + b'"}'
    with model_server(body=body) as server:
        llm = _llm({"key": "sk", "api_base": _base(server)})
        with pytest.raises(ValueError) as excinfo:
            llm.call_llm([{"role": "user", "content": "judge"}], provider="openai")
    assert _no_secret(excinfo.value)
    assert MESSAGES["bad_response"] in str(excinfo.value)


def test_call_llm_public_endpoint_works(hosted, resolver):
    with model_server() as server:
        llm = _llm({"key": "sk-tenant", "api_base": _base(server)})
        out = llm.call_llm([{"role": "user", "content": "judge"}], provider="openai")
    assert out == MODEL_ANSWER
    assert server.requests[0].headers["authorization"] == "Bearer sk-tenant"
    assert PLATFORM_KEY not in str(server.requests[0].headers)


# ------------------------------------------- guarded litellm entry points ---


def test_guarded_completion_drops_metadata_headers(hosted, resolver):
    with model_server() as server:
        response = model_endpoints.guarded_completion(
            model="openai/gpt-x",
            messages=[{"role": "user", "content": "hi"}],
            api_key="sk",
            api_base=_base(server),
            extra_headers={
                "Metadata-Flavor": "Google",
                "X-aws-ec2-metadata-token": "t",
                "X-Custom": "kept",
            },
        )
    assert response.choices[0].message.content == MODEL_ANSWER
    seen = server.requests[0].headers
    assert "metadata-flavor" not in seen
    assert "x-aws-ec2-metadata-token" not in seen
    assert seen["x-custom"] == "kept"


def test_guarded_completion_never_sends_the_platform_key(hosted, resolver):
    with model_server() as server:
        model_endpoints.guarded_completion(
            model="openai/gpt-x",
            messages=[{"role": "user", "content": "hi"}],
            api_base=_base(server),
        )
    assert PLATFORM_KEY not in str(server.requests[0].headers)


def test_guarded_completion_without_a_base_url_is_untouched(monkeypatch):
    seen = {}

    def completion(**kwargs):
        seen.update(kwargs)
        return "ok"

    monkeypatch.setattr("litellm.completion", completion)
    assert model_endpoints.guarded_completion(model="gpt-4o", messages=[]) == "ok"
    assert "client" not in seen


def test_guarded_acompletion_refuses_a_redirect(
    hosted, resolver, metadata, internal_redirect
):
    import asyncio

    with pytest.raises(Exception) as excinfo:
        asyncio.run(
            model_endpoints.guarded_acompletion(
                model="openai/gpt-x",
                messages=[{"role": "user", "content": "hi"}],
                api_key="sk",
                api_base=_base(internal_redirect),
            )
        )
    assert metadata.requests == []
    assert (
        model_endpoints.endpoint_call_error_message(excinfo.value)
        == (MESSAGES["redirect"])
    )


def test_guarded_image_generation_public_endpoint_works(hosted, resolver):
    with model_server() as server:
        response = model_endpoints.guarded_image_generation(
            model="dall-e-3", prompt="a cat", api_key="sk", api_base=_base(server)
        )
    assert response.data[0].b64_json
    assert server.requests[0].target == "/v1/images/generations"


def test_guarded_image_generation_refuses_a_redirect(
    hosted, resolver, metadata, internal_redirect
):
    with pytest.raises(Exception) as excinfo:
        model_endpoints.guarded_image_generation(
            model="dall-e-3",
            prompt="a cat",
            api_key="sk",
            api_base=_base(internal_redirect),
        )
    assert metadata.requests == []
    assert (
        model_endpoints.endpoint_call_error_message(excinfo.value)
        == (MESSAGES["redirect"])
    )


# Azure image generation and Azure AI agents with a tenant base URL are no
# longer refused on hosted: test_custom_model_litellm_routes_ssrf.py checks
# that they work against a public endpoint and refuse redirects there.


def test_error_message_categories():
    blocked = ssrf_guard.EndpointBlocked("blocked")
    wrapped = RuntimeError("litellm.APIConnectionError: Connection error.")
    wrapped.__cause__ = ssrf_guard.GuardedRequestError("redirect")
    unparseable = Exception(
        "Invalid response object Traceback ... received_args={'x': '" + SECRET_MARKER
    )
    assert model_endpoints.endpoint_call_error_message(blocked) == MESSAGES["blocked"]
    assert model_endpoints.endpoint_call_error_message(wrapped) == MESSAGES["redirect"]
    assert (
        model_endpoints.endpoint_call_error_message(unparseable)
        == (MESSAGES["bad_response"])
    )
    assert _no_secret(
        model_endpoints.endpoint_call_error_message(Exception(SECRET_MARKER))
    )


# --------------------------------------------- stored config is whitelisted ---


def _make_model(user, workspace, key_config, provider, name="wl-model"):
    return CustomAIModel.objects.create(
        user_model_id=name,
        provider=provider,
        input_token_cost=0,
        output_token_cost=0,
        organization=user.organization,
        workspace=workspace,
        key_config=key_config,
        user=user,
        deleted=False,
    )


@pytest.mark.django_db
def test_runtime_key_lookup_whitelists_a_stored_openai_config(
    hosted, user, workspace, resolver
):
    _make_model(
        user,
        workspace,
        {
            "key": "sk-tenant",
            "api_base": "https://api.example.com/v1",
            "organization": "org-1",
            "extra_headers": {"Metadata-Flavor": "Google"},
            "headers": {"X-Google-Metadata-Request": "True"},
            "custom_llm_provider": "bedrock",
            "aws_sts_endpoint": "http://169.254.169.254/",
        },
        "openai",
    )
    config = LiteLLMModelManager("wl-model").get_api_key(
        organization_id=user.organization.id, workspace_id=workspace.id
    )
    assert config == {
        "key": "sk-tenant",
        "api_base": "https://api.example.com/v1",
        "organization": "org-1",
    }


@pytest.mark.django_db
def test_runtime_key_lookup_whitelists_a_stored_azure_config(
    hosted, user, workspace, resolver
):
    _make_model(
        user,
        workspace,
        {
            "api_base": "https://api.example.com",
            "api_key": "azure-tenant",
            "api_version": "2024-02-01",
            "azure_endpoint_type": "legacy",
            "extra_headers": {"Metadata-Flavor": "Google"},
            "azure_ad_token": "x",
            "tenant_id": "t",
        },
        "azure",
    )
    config = LiteLLMModelManager("wl-model").get_api_key(
        organization_id=user.organization.id, workspace_id=workspace.id
    )
    assert config == {
        "api_base": "https://api.example.com",
        "api_key": "azure-tenant",
        "api_version": "2024-02-01",
        "azure_endpoint_type": "legacy",
    }


@pytest.mark.django_db
def test_create_saves_only_whitelisted_openai_keys(
    hosted, auth_client, resolver, monkeypatch
):
    monkeypatch.setattr(
        "litellm.completion",
        lambda **kwargs: type(
            "R",
            (),
            {"choices": [type("C", (), {"message": type("M", (), {"content": "ok"})})]},
        ),
    )
    response = auth_client.post(
        CREATE_URL,
        {
            "model_provider": "openai",
            "model_name": "wl-openai",
            "input_token_cost": 0,
            "output_token_cost": 0,
            "config_json": {
                "key": "sk-tenant",
                "api_base": "https://api.example.com/v1",
                "extra_headers": {"Metadata-Flavor": "Google"},
            },
        },
        format="json",
    )
    assert response.status_code == 200, response.json()
    saved = CustomAIModel.no_workspace_objects.get(user_model_id="wl-openai")
    assert saved.actual_json == {
        "key": "sk-tenant",
        "api_base": "https://api.example.com/v1",
    }


@pytest.mark.django_db
def test_edit_saves_only_whitelisted_openai_keys(
    hosted, auth_client, user, workspace, resolver, monkeypatch
):
    monkeypatch.setattr(
        "litellm.completion",
        lambda **kwargs: type(
            "R",
            (),
            {"choices": [type("C", (), {"message": type("M", (), {"content": "ok"})})]},
        ),
    )
    model = _make_model(
        user,
        workspace,
        {"key": "sk", "api_base": "https://api.example.com/v1"},
        "openai",
    )
    response = auth_client.patch(
        EDIT_URL,
        {
            "id": str(model.id),
            "config_json": {
                "key": "sk-2",
                "api_base": "https://api.example.com/v1",
                "extra_headers": {"Metadata-Flavor": "Google"},
            },
        },
        format="json",
    )
    assert response.status_code == 200, response.json()
    model.refresh_from_db()
    assert model.actual_json == {
        "key": "sk-2",
        "api_base": "https://api.example.com/v1",
    }


@pytest.mark.parametrize(
    "provider, config, expected",
    [
        (
            "openai",
            {"key": "k", "api_base": "u", "extra_headers": {}, "max_retries": 9},
            {"key": "k", "api_base": "u"},
        ),
        (
            "azure",
            {"apiBase": "u", "apiKey": "k", "headers": {}, "azure_ad_token": "x"},
            {"apiBase": "u", "apiKey": "k"},
        ),
        (
            "bedrock",
            {
                "aws_access_key_id": "a",
                "aws_secret_access_key": "s",
                "aws_region_name": "r",
                "aws_session_token": "t",
                "aws_bedrock_runtime_endpoint": "http://169.254.169.254",
                "api_base": "http://169.254.169.254",
            },
            {
                "aws_access_key_id": "a",
                "aws_secret_access_key": "s",
                "aws_region_name": "r",
                "aws_session_token": "t",
            },
        ),
        (
            "vertex_ai",
            {"type": "service_account", "x": 1},
            {"type": "service_account", "x": 1},
        ),
    ],
)
def test_whitelist_model_config(provider, config, expected):
    assert model_endpoints.whitelist_model_config(provider, config) == expected
