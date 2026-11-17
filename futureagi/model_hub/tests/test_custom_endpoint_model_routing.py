"""A custom endpoint model is served by its own endpoint, whatever its name.

A CustomAIModel with provider "custom" is an HTTP endpoint the member
configured. The image, speech-to-text and text-to-speech handlers call a
provider through litellm with only an api_key and api_base taken from the
configuration, so a custom endpoint row named like a catalog image or audio
model must never reach them: its call would carry no key of its own, and the
provider client would fill one from the process environment.

The async Run Prompt path builds its handler on the event loop, where Django
refuses the ORM; the routing must hold there too.
"""

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

import pytest
from django.db import OperationalError

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from model_hub.models.custom_models import CustomAIModel
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import clear_workspace_context

ENV_SENTINEL = "ENV-SENTINEL-NOT-A-KEY"
MODE = "model_hub.utils.utils.get_model_mode"
AUDIO = (
    "agentic_eval.core_evals.run_prompt.runprompt_handlers.utils"
    ".audio_processor.AudioProcessor"
)
PROVIDER_CALLS = [
    "litellm.image_generation",
    "litellm.transcription",
    "litellm.atranscription",
    "litellm.speech",
    "litellm.aspeech",
    "litellm.completion",
    "litellm.acompletion",
]

IMAGE_PROMPT = [{"role": "user", "content": "A lighthouse at dusk"}]
SPEECH_TEXT = [{"role": "user", "content": "Read this sentence aloud."}]
AUDIO_INPUT = [
    {
        "role": "user",
        "content": [{"type": "input_audio", "input_audio": {"data": "ZmFrZQ=="}}],
    }
]

# (model name the catalog knows, its mode, the run's output format, messages)
NAMED_LIKE_CATALOG_MODELS = [
    pytest.param("gpt-image-1", "image_generation", "text", IMAGE_PROMPT, id="image"),
    pytest.param("whisper-1", "stt", "text", AUDIO_INPUT, id="speech-to-text"),
    pytest.param("tts-1", "audio", "audio", SPEECH_TEXT, id="text-to-speech"),
]
CASE = "name, mode, output_format, messages"


@pytest.fixture
def organization(db):
    # Lookups here run outside any request: no workspace filter applies.
    clear_workspace_context()
    organization = Organization.objects.create(name="Custom endpoint routing")
    user = User.objects.create_user(
        email="custom-endpoint-routing@example.test",
        password="unused-password-1",
        name="Routing Test",
        organization=organization,
        organization_role=OrganizationRoles.OWNER,
    )
    workspace = Workspace.objects.create(
        name="Default Workspace",
        organization=organization,
        is_default=True,
        created_by=user,
    )
    return organization, user, workspace


def stored_model(organization, name, provider, key_config):
    org, user, workspace = organization
    return CustomAIModel.objects.create(
        user_model_id=name,
        provider=provider,
        input_token_cost=0.0,
        output_token_cost=0.0,
        organization=org,
        workspace=workspace,
        user=user,
        key_config=key_config,
        deleted=False,
    )


def context_for(organization, name, output_format, messages, api_key=None):
    from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
        ModelHandlerContext,
    )

    return ModelHandlerContext(
        model=name,
        messages=messages,
        organization_id=str(organization[0].id),
        output_format=output_format,
        provider="custom",
        api_key=api_key,
        run_prompt_config={"voice": "alloy"},
    )


def build_handler(context, mode):
    from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
        ModelHandlerFactory,
    )

    with patch(MODE, return_value=mode):
        return ModelHandlerFactory.create_handler(context)


def build_handler_on_the_event_loop(context, mode):
    async def build():
        return build_handler(context, mode)

    return asyncio.run(build())


@pytest.fixture
def listener():
    """A local base URL that records every request it is sent."""
    seen = []

    class Recorder(BaseHTTPRequestHandler):
        def _record(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            seen.append((str(self.headers), body))
            payload = b'{"error": {"message": "refused"}}'
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_POST = _record
        do_GET = _record

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Recorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/v1", seen
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.django_db
@pytest.mark.parametrize(CASE, NAMED_LIKE_CATALOG_MODELS)
def test_a_custom_endpoint_model_is_served_by_its_own_endpoint_whatever_its_name(
    organization, name, mode, output_format, messages
):
    from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.custom_model_handler import (
        CustomModelHandler,
    )

    config = {"endpoint_url": "http://127.0.0.1:9/v1/chat/completions"}
    stored_model(organization, name, "custom", {**config, "headers": {}})
    context = context_for(organization, name, output_format, messages)

    assert isinstance(build_handler(context, mode), CustomModelHandler)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(CASE, NAMED_LIKE_CATALOG_MODELS)
def test_a_custom_endpoint_model_is_served_by_its_own_endpoint_on_the_event_loop(
    organization, monkeypatch, name, mode, output_format, messages
):
    from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.custom_model_handler import (
        CustomModelHandler,
    )

    # As in production: Django refuses the ORM on the event loop.
    monkeypatch.delenv("DJANGO_ALLOW_ASYNC_UNSAFE", raising=False)
    config = {"endpoint_url": "http://127.0.0.1:9/v1/chat/completions"}
    stored_model(organization, name, "custom", {**config, "headers": {}})
    context = context_for(organization, name, output_format, messages)

    handler = build_handler_on_the_event_loop(context, mode)

    assert isinstance(handler, CustomModelHandler)


@pytest.mark.django_db
def test_a_provider_model_with_its_own_key_named_like_an_image_model_makes_images(
    organization,
):
    from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
        ImageHandler,
    )

    stored_model(organization, "gpt-image-1", "openai", {"key": "own-test-key"})
    context = context_for(organization, "gpt-image-1", "text", IMAGE_PROMPT)

    assert isinstance(build_handler(context, "image_generation"), ImageHandler)


@pytest.mark.django_db
def test_a_custom_endpoint_model_named_like_an_image_model_keeps_the_env_key(
    organization, listener, monkeypatch
):
    base, seen = listener
    monkeypatch.setenv("OPENAI_API_KEY", ENV_SENTINEL)
    # A custom endpoint row as create and edit save it: its base and
    # headers, no key.
    config = {"api_base": base, "headers": {}}
    stored_model(organization, "gpt-image-1", "custom", config)
    context = context_for(
        organization, "gpt-image-1", "text", IMAGE_PROMPT, api_key=dict(config)
    )

    try:
        build_handler(context, "image_generation").execute_sync()
    except Exception:
        pass  # refused or failed: what matters is what reached the base

    leaked = [
        headers
        for headers, body in seen
        if ENV_SENTINEL in headers or ENV_SENTINEL.encode() in body
    ]
    assert leaked == []


@pytest.mark.django_db
@pytest.mark.parametrize(
    "endpoint_field",
    [
        # The stored shape create and edit save: the member's base.
        pytest.param("api_base", id="member-base"),
        # Rows saved before that shape: the endpoint the handler called then.
        pytest.param("endpoint_url", id="legacy-endpoint"),
    ],
)
@pytest.mark.parametrize(CASE, NAMED_LIKE_CATALOG_MODELS)
def test_a_custom_endpoint_model_without_its_own_key_makes_no_provider_call(
    organization,
    listener,
    monkeypatch,
    name,
    mode,
    output_format,
    messages,
    endpoint_field,
):
    base, _ = listener
    monkeypatch.setenv("OPENAI_API_KEY", ENV_SENTINEL)
    config = {endpoint_field: base, "headers": {}}
    stored_model(organization, name, "custom", config)
    context = context_for(
        organization, name, output_format, messages, api_key=dict(config)
    )
    refused = RuntimeError("provider call recorded")
    patches = [patch(target, side_effect=refused) for target in PROVIDER_CALLS]
    patches.append(
        patch(f"{AUDIO}.normalize_audio_for_stt", return_value=(b"fake", "mp3"))
    )
    patches.append(patch(f"{AUDIO}.create_audio_buffer"))

    recorders = [started.start() for started in patches]
    try:
        build_handler(context, mode).execute_sync()
    except Exception:
        pass
    finally:
        for started in patches:
            started.stop()

    called = {
        target: recorder.call_args_list
        for target, recorder in zip(
            PROVIDER_CALLS, recorders[: len(PROVIDER_CALLS)], strict=True
        )
        if recorder.call_count
    }
    assert called == {}


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(CASE, NAMED_LIKE_CATALOG_MODELS)
@pytest.mark.parametrize("on_event_loop", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize(
    "failure_type",
    [
        pytest.param(OperationalError, id="database-error"),
        pytest.param(CustomAIModel.MultipleObjectsReturned, id="ambiguous-model"),
    ],
)
def test_mode_routing_refuses_an_unexpected_custom_lookup_error(
    organization,
    monkeypatch,
    name,
    mode,
    output_format,
    messages,
    on_event_loop,
    failure_type,
):
    monkeypatch.delenv("DJANGO_ALLOW_ASYNC_UNSAFE", raising=False)
    config = {"endpoint_url": "http://127.0.0.1:9/v1/chat/completions"}
    stored_model(organization, name, "custom", {**config, "headers": {}})
    context = context_for(organization, name, output_format, messages)
    build = build_handler_on_the_event_loop if on_event_loop else build_handler
    handlers = "agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers"

    with (
        patch.object(
            CustomAIModel.objects,
            "get",
            side_effect=failure_type("PRIVATE_DB_FAILURE"),
        ),
        patch(f"{handlers}.image_handler.ImageHandler") as image,
        patch(f"{handlers}.stt_handler.STTHandler") as stt,
        patch(f"{handlers}.tts_handler.TTSHandler") as tts,
    ):
        with pytest.raises(ValueError) as refused:
            build(context, mode)

        assert str(refused.value) == (
            "Could not check the model configuration. Try again."
        )
        assert refused.value.__cause__ is None
        assert refused.value.__suppress_context__ is True
        image.assert_not_called()
        stt.assert_not_called()
        tts.assert_not_called()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(CASE, NAMED_LIKE_CATALOG_MODELS)
@pytest.mark.parametrize("on_event_loop", [False, True], ids=["sync", "async"])
def test_a_missing_custom_row_keeps_catalog_mode_routing(
    organization, monkeypatch, name, mode, output_format, messages, on_event_loop
):
    monkeypatch.delenv("DJANGO_ALLOW_ASYNC_UNSAFE", raising=False)
    context = context_for(organization, name, output_format, messages)
    build = build_handler_on_the_event_loop if on_event_loop else build_handler
    expected = {
        "gpt-image-1": "ImageHandler",
        "whisper-1": "STTHandler",
        "tts-1": "TTSHandler",
    }

    with patch.object(
        CustomAIModel.objects, "get", side_effect=CustomAIModel.DoesNotExist
    ):
        handler = build(context, mode)

    assert type(handler).__name__ == expected[name]


@pytest.mark.django_db
def test_legacy_custom_lookup_keeps_its_non_strict_error_behavior(organization):
    from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
        ModelHandlerFactory,
    )

    with patch.object(
        CustomAIModel.objects, "get", side_effect=OperationalError("unavailable")
    ):
        assert not ModelHandlerFactory._is_custom_model(
            "chat-model", str(organization[0].id)
        )
