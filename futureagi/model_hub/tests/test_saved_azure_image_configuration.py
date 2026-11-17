"""Saved Azure image configuration at the real handler/public factory boundary.

Frozen source: B5t11 4f505cb1989026b76b77a706dfd7bd88a06b6cdd.
Provider recorders prove app kwargs and final guard scope, NOT SDK/provider
acceptance, real public reachability, stored-row retrieval, or paid generation.
All DNS answers, provider output, cost and image storage are test fixtures.
"""

import asyncio
import copy
import json
import socket
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import litellm
import pytest
import requests
from django.test import override_settings

from agentic_eval.core_evals.run_prompt.runprompt_handlers.base_handler import (
    ModelHandlerContext,
)
from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import image_handler
from model_hub.utils import model_endpoints
from model_hub.utils.model_key import ADD_API_KEY_MESSAGE, ModelKeyMissing
from tfc.utils import ssrf_guard
from tfc.utils.tests.guarded_post_fakes import FakeResolver

BASE = "https://images.openai.azure.com"
VERSION = "2024-06-01"
DEPLOYMENT = "azure/tenant-image-deployment"
OWN_KEY = "synthetic-image-tenant-key"
OTHER_KEY = "synthetic-image-unselected-key"
PROVIDER_URL = "https://provider.example.test/generated.png"
OUTPUT_URL = "https://storage.example.test/generated.png"
OPTIONS = {"n": 2, "size": "1024x1792", "quality": "hd", "style": "natural"}


@pytest.fixture(autouse=True)
def image_boundary(monkeypatch):
    resolver = FakeResolver().install(monkeypatch)
    # Synthetic DNS admission only; no claim these endpoints are reachable.
    resolver.add("images.openai.azure.com", "93.184.216.34")
    resolver.add("images.services.ai.azure.com", "93.184.216.34")
    resolver.add("images.example.test", "93.184.216.34")
    monkeypatch.delenv("CUSTOM_MODEL_DENY_PRIVATE_ENDPOINTS", raising=False)
    for name in ("OPENAI_API_KEY", "AZURE_API_KEY", "AZURE_OPENAI_API_KEY"):
        monkeypatch.setenv(name, "synthetic-ambient-key-must-not-be-borrowed")
    monkeypatch.setenv("AZURE_API_VERSION", "synthetic-ambient-version")
    # Keep the actual guard and context variable; never install SDK hooks or
    # construct provider SDK clients in this application-boundary suite.
    monkeypatch.setattr(model_endpoints, "_install_litellm_client_hooks", lambda: None)

    def forbidden(*args, **kwargs):
        raise AssertionError("Unexpected network dispatch in image boundary test")

    async def aforbidden(*args, **kwargs):
        forbidden()

    monkeypatch.setattr(httpx.Client, "send", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "send", aforbidden)
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(model_endpoints, "_guarded_http_client", forbidden)
    monkeypatch.setattr(
        model_endpoints,
        "_openai_client",
        lambda kwargs, policy, **options: SimpleNamespace(
            api_key=kwargs.get("api_key"), base_url=kwargs["api_base"]
        ),
    )
    upload = Mock(return_value=OUTPUT_URL)
    monkeypatch.setattr(image_handler, "upload_image_to_s3", upload)
    monkeypatch.setattr(
        image_handler,
        "calculate_total_cost",
        lambda *args, **kwargs: {
            "total_cost": 0,
            "prompt_cost": 0,
            "completion_cost": 0,
        },
    )
    boundary = SimpleNamespace(
        seen=[],
        upload=upload,
        image=SimpleNamespace(
            url=PROVIDER_URL, b64_json=None, revised_prompt="A blue bird"
        ),
    )

    def record(**kwargs):
        boundary.seen.append((kwargs, model_endpoints._scope_policy()))
        return SimpleNamespace(data=[boundary.image])

    monkeypatch.setattr(litellm, "image_generation", record)
    with override_settings(CLOUD_DEPLOYMENT="US"):
        assert (
            model_endpoints.model_endpoint_policy()
            == ssrf_guard.EndpointPolicy.PUBLIC_ONLY
        )
        yield boundary
    assert model_endpoints._scope_policy() is None


def context(configuration, *, provider="azure", model=DEPLOYMENT):
    return ModelHandlerContext(
        model=model,
        messages=[{"role": "user", "content": "Draw a blue bird"}],
        organization_id="synthetic-image-org",
        workspace_id="synthetic-image-workspace",
        output_format="image",
        provider=provider,
        api_key=configuration,
        run_prompt_config=dict(OPTIONS),
    )


def execute(configuration, asynchronous, **kwargs):
    target = image_handler.ImageHandler(context(configuration, **kwargs))
    return (
        asyncio.run(target.execute_async()) if asynchronous else target.execute_sync()
    )


def saved(shape="camel", *, base=BASE):
    if shape == "canonical":
        return {"api_key": OWN_KEY, "api_base": base, "api_version": VERSION}
    key_field = "apiKey" if shape == "camel" else "key"
    return {key_field: OWN_KEY, "apiBase": base, "apiVersion": VERSION}


def assert_image_options(kwargs):
    assert kwargs["prompt"] == "Draw a blue bird"
    assert kwargs["drop_params"] is True
    for name, value in OPTIONS.items():
        assert kwargs[name] == value


def assert_azure_call(boundary, *, model=DEPLOYMENT):
    [(kwargs, policy)] = boundary.seen
    assert kwargs.get("api_key") == OWN_KEY
    assert kwargs.get("api_base") == BASE
    assert kwargs.get("api_version") == VERSION
    assert kwargs["model"] == model
    assert kwargs.get("custom_llm_provider") == "azure"
    assert model_endpoints._litellm_provider(kwargs) == "azure"
    assert policy == ssrf_guard.EndpointPolicy.PUBLIC_ONLY
    assert model_endpoints._scope_policy() is None
    assert_image_options(kwargs)


def assert_output(response, info, boundary):
    assert response == OUTPUT_URL
    assert info["failure"] is None
    assert info["data"]["response"] == OUTPUT_URL
    assert info["metadata"]["usage"]["images_generated"] == OPTIONS["n"]
    assert info["metadata"]["usage"]["total_tokens"] == 0
    assert info["metadata"]["revised_prompt"] == "A blue bird"
    assert info["metadata"]["response_time_unit"] == "ms"
    for name in ("size", "quality", "style"):
        assert info["metadata"][name] == OPTIONS[name]
    boundary.upload.assert_called_once_with(PROVIDER_URL)


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize("shape", ["camel", "key"])
def test_saved_azure_image_direct_mapping(image_boundary, asynchronous, shape):
    configuration = saved(shape)
    original = copy.deepcopy(configuration)
    result = execute(configuration, asynchronous)
    assert_azure_call(image_boundary)
    assert configuration == original
    assert_output(*result.to_value_info(), image_boundary)


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize("shape", ["camel", "key"])
def test_public_runprompt_saved_azure_image(
    image_boundary, monkeypatch, asynchronous, shape
):
    from agentic_eval.core_evals.run_prompt import litellm_response as public
    from agentic_eval.core_evals.run_prompt.runprompt_handlers.factory import (
        ModelHandlerFactory,
    )
    from model_hub.utils import utils as model_utils

    configuration = saved(shape)
    original = copy.deepcopy(configuration)
    ctx = context(configuration)
    prompt = public.RunPrompt.__new__(public.RunPrompt)
    prompt.__dict__.update(vars(ctx))
    manager = Mock()
    manager.get_provider.return_value = "azure"
    manager.get_api_key.side_effect = lambda **kwargs: copy.deepcopy(configuration)
    monkeypatch.setattr(public, "LiteLLMModelManager", lambda *args, **kwargs: manager)
    monkeypatch.setattr(public, "USE_NEW_RUNPROMPT_HANDLERS", True)
    monkeypatch.setattr(model_utils, "get_model_mode", lambda model: "image_generation")
    monkeypatch.setattr(
        ModelHandlerFactory,
        "_is_custom_model",
        staticmethod(lambda *args, **kwargs: False),
    )
    # Spy delegates to the original factory; neither factory choice nor the
    # ImageHandler execution is replaced. This pins the actual public route.
    create = ModelHandlerFactory.create_handler
    routed = []

    def create_real(cls, handler_context):
        target = create(handler_context)
        routed.append(target)
        return target

    monkeypatch.setattr(ModelHandlerFactory, "create_handler", classmethod(create_real))
    response, info = (
        asyncio.run(prompt.litellm_response_async())
        if asynchronous
        else prompt.litellm_response()
    )
    manager.get_api_key.assert_called_once()
    manager.get_provider.assert_called_once_with(
        model_name=DEPLOYMENT, organization_id=ctx.organization_id
    )
    assert manager.get_api_key.call_args.kwargs["provider"] == "azure"
    assert len(routed) == 1
    assert type(routed[0]) is image_handler.ImageHandler
    assert_azure_call(image_boundary)
    assert configuration == original
    assert_output(response, info, image_boundary)


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
def test_canonical_azure_image_keeps_version_and_deployment(
    image_boundary, asynchronous
):
    result = execute(saved("canonical"), asynchronous)
    # Existing canonical key/base admission remains a control; the omitted
    # canonical api_version must fail as a mapping assertion on the baseline.
    [(kwargs, policy)] = image_boundary.seen
    assert kwargs["api_key"] == OWN_KEY
    assert kwargs["api_base"] == BASE
    assert kwargs["model"] == DEPLOYMENT
    assert policy == ssrf_guard.EndpointPolicy.PUBLIC_ONLY
    assert_azure_call(image_boundary)
    assert_output(*result.to_value_info(), image_boundary)


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
def test_azure_image_normalizes_legacy_deployment_url(image_boundary, asynchronous):
    configuration = saved(
        base=BASE
        + "/openai/deployments/tenant-image-deployment/images/generations?api-version="
        + VERSION
    )
    configuration.pop("apiVersion")
    configuration["azureEndpointType"] = "legacy"
    result = execute(configuration, asynchronous)
    assert_azure_call(image_boundary)
    assert result.failure is None


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize("selected_field", ["key", "api_key"])
def test_azure_image_selected_key_precedence(
    image_boundary, asynchronous, selected_field
):
    configuration = saved()
    configuration["apiKey"] = OTHER_KEY
    configuration[selected_field] = OWN_KEY
    if selected_field == "key":
        configuration["api_key"] = OTHER_KEY
    result = execute(configuration, asynchronous)
    assert_azure_call(image_boundary)
    assert result.failure is None


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize("canonical", [None, ""], ids=["null", "empty"])
def test_azure_image_empty_canonical_uses_existing_alias_precedence(
    image_boundary, asynchronous, canonical
):
    configuration = saved()
    configuration["api_key"] = canonical
    result = execute(configuration, asynchronous)
    assert_azure_call(image_boundary)
    assert result.failure is None


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize(
    "selected_field, selected",
    [
        ("key", ""),
        ("key", None),
        ("api_key", "  "),
        ("api_key", {"nested": "not-a-key"}),
    ],
    ids=["selected-empty", "selected-null", "canonical-whitespace", "canonical-object"],
)
def test_azure_image_invalid_selected_key_never_borrows_ambient(
    image_boundary, asynchronous, selected_field, selected
):
    configuration = saved()
    configuration[selected_field] = selected
    with pytest.raises(ModelKeyMissing) as failure:
        execute(configuration, asynchronous)
    assert str(failure.value) == ADD_API_KEY_MESSAGE
    assert image_boundary.seen == []
    image_boundary.upload.assert_not_called()


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize(
    "shape, base",
    [
        ("camel", "http://127.0.0.1"),
        ("camel", "http://10.0.0.8"),
        ("camel", "http://169.254.169.254"),
        ("canonical", "http://127.0.0.1"),
    ],
    ids=["camel-loopback", "camel-private", "camel-metadata", "canonical-control"],
)
def test_azure_image_private_endpoint_refused_before_provider(
    image_boundary, asynchronous, shape, base
):
    configuration = saved(shape, base=base)
    configuration["azure_endpoint_type"] = "legacy"
    with pytest.raises(ssrf_guard.EndpointBlocked) as failure:
        execute(configuration, asynchronous)
    assert str(failure.value) == ssrf_guard.ENDPOINT_ERROR_MESSAGES["blocked"]
    assert image_boundary.seen == []
    image_boundary.upload.assert_not_called()


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize(
    "configuration",
    [
        OWN_KEY,
        {"api_key": OWN_KEY, "api_base": "https://images.example.test/v1"},
    ],
    ids=["scalar", "canonical-endpoint"],
)
def test_existing_openai_image_behavior(image_boundary, asynchronous, configuration):
    result = execute(
        copy.deepcopy(configuration), asynchronous, provider="openai", model="dall-e-3"
    )
    [(kwargs, policy)] = image_boundary.seen
    assert kwargs["api_key"] == OWN_KEY
    assert kwargs["model"] == "dall-e-3"
    if isinstance(configuration, dict):
        assert kwargs["api_base"] == configuration["api_base"]
        assert policy == ssrf_guard.EndpointPolicy.PUBLIC_ONLY
        assert kwargs["client"].api_key == OWN_KEY
    else:
        assert "api_base" not in kwargs
        assert policy is None
    assert_image_options(kwargs)
    assert_output(*result.to_value_info(), image_boundary)


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
def test_existing_vertex_image_service_account_behavior(image_boundary, asynchronous):
    account = {
        "type": "service_account",
        "private_key": "synthetic-not-a-real-private-key",
        "client_email": "synthetic@example.test",
        "project_id": "synthetic-project",
        "location": "us-central1",
    }
    original = copy.deepcopy(account)
    result = execute(
        account,
        asynchronous,
        provider="vertex_ai",
        model="vertex_ai/imagen-3.0-generate-001",
    )
    [(kwargs, policy)] = image_boundary.seen
    assert "api_key" not in kwargs
    assert kwargs["vertex_location"] == "us-central1"
    assert json.loads(kwargs["vertex_credentials"]) == {
        name: value for name, value in account.items() if name != "location"
    }
    assert account == original
    assert policy is None
    assert_image_options(kwargs)
    assert_output(*result.to_value_info(), image_boundary)


def test_image_base64_upload_formatting_control(image_boundary):
    image_boundary.image = SimpleNamespace(
        url=None, b64_json="c3ludGhldGlj", revised_prompt=None
    )
    result = execute(OWN_KEY, False, provider="openai", model="dall-e-3")
    assert result.response == OUTPUT_URL
    assert result.metadata["revised_prompt"] is None
    image_boundary.upload.assert_called_once_with("c3ludGhldGlj")


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
def test_azure_image_provider_normalization_preserves_deployment(
    image_boundary, asynchronous
):
    base = "https://images.services.ai.azure.com"
    configuration = saved(base=base)
    configuration["azureEndpointType"] = "foundry"
    result = execute(configuration, asynchronous)
    assert result.failure is None
    [(kwargs, policy)] = image_boundary.seen
    assert kwargs["api_key"] == OWN_KEY
    assert kwargs["api_base"] == base
    assert kwargs["api_version"] == VERSION
    assert kwargs["custom_llm_provider"] == "azure_ai"
    assert kwargs["model"] == "tenant-image-deployment"
    assert model_endpoints._litellm_provider(kwargs) == "azure_ai"
    assert policy == ssrf_guard.EndpointPolicy.PUBLIC_ONLY
    assert_image_options(kwargs)
    # This pins app routing only, not live Foundry model availability.
