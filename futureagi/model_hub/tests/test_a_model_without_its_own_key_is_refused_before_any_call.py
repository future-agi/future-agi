"""A custom model without its own key is refused before any call.

An OpenAI, Azure, Bedrock or SageMaker model configuration whose key is
missing, None, empty or only whitespace (for Bedrock and SageMaker: with no
access key pair either) is refused with "Add an API key for this model."
before any model call, wherever such a configuration is submitted or read:
the custom-model create and edit forms, the model check behind them, the
stored-key lookup every Run Prompt mode and every evaluation reads, an
evaluation's own key lookup, and an evaluation model's call. A model with its
own key is checked and called with exactly that key, as before.

The tests set made-up environment keys (never a key's shape) and replace
every model call and every HTTP post with a recorder that sends nothing; each
refusal test asserts that nothing was called and that no environment value
reached any call.
"""

import os
from unittest.mock import patch

import litellm
import requests
from rest_framework import status

from agentic_eval.core.llm import llm as llm_module
from agentic_eval.core_evals.run_prompt.litellm_models import LiteLLMModelManager
from agentic_eval.core_evals.run_prompt.runprompt_handlers.utils.payload_builder import (
    PayloadBuilder,
)
from model_hub.models.custom_models import CustomAIModel
from model_hub.tests.test_custom_models_api import BASE_URL, CustomModelsAPITestCase

ADD_API_KEY = "Add an API key for this model."
ENVIRONMENT = {
    "OPENAI_API_KEY": "ENV-SENTINEL-NOT-A-KEY",
    "AWS_ACCESS_KEY_ID": "ENV-SENTINEL-NOT-AN-AWS-ID",
    "AWS_SECRET_ACCESS_KEY": "ENV-SENTINEL-NOT-AN-AWS-SECRET",
}
USER_HOST = "https://user-host.example.test/v1"
OWN_KEY = "sk-test-not-a-real-key"
MODEL = "probe-model"
CHAT = [{"role": "user", "content": "hi"}]

# Configurations that give the model no key of its own: (provider, value).
WITHOUT_ITS_OWN_KEY = {
    "openai-empty-key-and-a-base-url": ("openai", {"key": "", "api_base": USER_HOST}),
    "openai-empty-key": ("openai", {"key": ""}),
    "openai-whitespace-key": ("openai", {"key": "   ", "api_base": USER_HOST}),
    "openai-no-key-and-a-base-url": ("openai", {"api_base": USER_HOST}),
    "bedrock-a-region-alone": ("bedrock", {"aws_region_name": "us-east-1"}),
    "sagemaker-no-access-key-pair": (
        "sagemaker",
        {
            "aws_access_key_id": "",
            "aws_secret_access_key": "",
            "aws_region_name": "us-east-1",
        },
    ),
}
# The create form's openai shapes without a key of the model's own.
CREATE_WITHOUT_ITS_OWN_KEY = {
    "empty-key-and-a-base-url": {"key": "", "api_base": USER_HOST},
    "empty-key": {"key": ""},
    "whitespace-key": {"key": "   "},
    "null-key": {"key": None, "api_base": USER_HOST},
    "no-key-and-a-base-url": {"api_base": USER_HOST},
}


class _CallMade(Exception):
    """A model call or an HTTP post was reached."""


class _Recorder:
    """Stands in for every model call and HTTP post: records what it is
    given, sends nothing."""

    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise _CallMade()

    def environment_reached_a_call(self):
        recorded = repr(self.calls)
        return any(value in recorded for value in ENVIRONMENT.values())


def _raised(function, *args, **kwargs):
    """The exception ``function`` raised, or None."""
    try:
        function(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - the test inspects it
        return exc
    return None


class TestAModelWithoutItsOwnKeyIsRefusedBeforeAnyCall(CustomModelsAPITestCase):
    def setUp(self):
        super().setUp()
        self.recorder = _Recorder()
        patches = [patch.dict(os.environ, ENVIRONMENT)]
        for name in ("completion", "acompletion", "image_generation"):
            patches.append(patch.object(litellm, name, self.recorder))
        patches.append(patch.object(requests, "post", self.recorder))
        for started in patches:
            started.start()
            self.addCleanup(started.stop)

    def assert_nothing_was_called(self):
        self.assertEqual(self.recorder.calls, [], "a model call or a post was made")
        self.assertFalse(
            self.recorder.environment_reached_a_call(),
            "an environment value reached a call",
        )

    def store(self, provider, value):
        return CustomAIModel.objects.create(
            user_model_id=MODEL,
            provider=provider,
            input_token_cost=0.0,
            output_token_cost=0.0,
            organization=self.organization,
            workspace=self.workspace,
            user=self.user,
            key_config=value,
        )

    def stored_key(self):
        manager = LiteLLMModelManager(
            model_name=MODEL, organization_id=self.organization.id
        )
        return manager.get_api_key(
            organization_id=self.organization.id, workspace_id=self.workspace.id
        )

    # ---------------------------------------------------- create and edit ---

    def test_create_refuses_an_openai_model_without_its_own_key(self):
        for case, config in CREATE_WITHOUT_ITS_OWN_KEY.items():
            with self.subTest(case=case):
                self.recorder.calls.clear()
                response = self.client.post(
                    f"{BASE_URL}/custom_models/create/",
                    {
                        "model_provider": "openai",
                        "model_name": f"{MODEL}-{case}",
                        "input_token_cost": 0.0,
                        "output_token_cost": 0.0,
                        "config_json": config,
                    },
                    format="json",
                )

                self.assert_nothing_was_called()
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn(ADD_API_KEY, str(response.data))
                self.assertFalse(
                    CustomAIModel.objects.filter(
                        user_model_id=f"{MODEL}-{case}"
                    ).exists(),
                    "the model was saved",
                )

    def test_create_checks_a_model_with_its_own_key_with_exactly_that_key(self):
        self.client.post(
            f"{BASE_URL}/custom_models/create/",
            {
                "model_provider": "openai",
                "model_name": MODEL,
                "input_token_cost": 0.0,
                "output_token_cost": 0.0,
                "config_json": {"key": OWN_KEY},
            },
            format="json",
        )

        self.assertEqual(len(self.recorder.calls), 1, "the model was not checked")
        _args, arguments = self.recorder.calls[0]
        self.assertEqual(arguments.get("api_key"), OWN_KEY)
        self.assertFalse(self.recorder.environment_reached_a_call())

    def test_edit_refuses_a_configuration_without_its_own_key(self):
        model = self.create_custom_model(
            user_model_id=MODEL, key_config={"key": OWN_KEY}
        )
        for case, config in CREATE_WITHOUT_ITS_OWN_KEY.items():
            with self.subTest(case=case):
                self.recorder.calls.clear()
                response = self.client.patch(
                    f"{BASE_URL}/custom_models/edit/",
                    {"id": str(model.id), "config_json": config},
                    format="json",
                )

                self.assert_nothing_was_called()
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn(ADD_API_KEY, str(response.data))
                saved = CustomAIModel.objects.get(id=model.id)
                self.assertEqual(saved.actual_json, {"key": OWN_KEY})

    # ----------------------------------------------------- the model check ---

    def test_the_model_check_refuses_a_configuration_without_its_own_key(self):
        from model_hub.utils.utils import validate_model_working

        shapes = {
            "openai-empty-key-and-a-base-url": (
                "openai",
                {"api_key": "", "api_base": USER_HOST},
            ),
            "openai-whitespace-key": ("openai", {"api_key": "   "}),
            "openai-null-key": ("openai", {"api_key": None}),
            "azure-no-key": (
                "azure",
                {"api_base": USER_HOST, "api_key": "", "api_version": "2024-01-01"},
            ),
            "bedrock-empty-access-key-pair": (
                "bedrock",
                {
                    "aws_access_key_id": "",
                    "aws_secret_access_key": "",
                    "aws_region_name": "us-east-1",
                },
            ),
        }
        for case, (provider, config) in shapes.items():
            with self.subTest(case=case):
                self.recorder.calls.clear()
                result = validate_model_working(
                    model_name=MODEL, api_key=config, provider=provider
                )

                self.assert_nothing_was_called()
                self.assertEqual(str(result), ADD_API_KEY)

    # -------------------------------------- the stored-key lookup (choke) ---

    def test_the_stored_key_lookup_refuses_a_model_without_its_own_key(self):
        for case, (provider, value) in WITHOUT_ITS_OWN_KEY.items():
            with self.subTest(case=case):
                CustomAIModel.objects.filter(user_model_id=MODEL).delete()
                self.store(provider, value)

                error = _raised(self.stored_key)

                self.assertEqual(str(error), ADD_API_KEY, "the row was handed on")

    def test_the_stored_key_lookup_returns_a_model_with_its_own_key_as_before(self):
        self.store("openai", {"key": OWN_KEY})

        self.assertEqual(self.stored_key(), {"key": OWN_KEY})

    # --------------------------------------------------------- Run Prompt ---

    def _run_prompt_call(self):
        """Read the stored row the way every Run Prompt mode reads it
        (litellm_response's normalization after get_api_key) and make the
        chat call and the image call the way the payload builder and the
        image handler make them."""
        api_key = self.stored_key()
        if isinstance(api_key, dict) and "key" in api_key:
            if "api_base" in api_key:
                api_key["api_key"] = api_key.pop("key")
            else:
                api_key = api_key.get("key")
        payload = {"model": MODEL, "messages": CHAT}
        PayloadBuilder._add_api_key(payload, "openai", api_key)
        _raised(litellm.completion, **payload)
        image = {"model": MODEL, "prompt": "a cat"}
        if isinstance(api_key, dict):
            if "api_key" in api_key:
                image["api_key"] = api_key["api_key"]
            if "api_base" in api_key:
                image["api_base"] = api_key["api_base"]
        elif api_key:
            image["api_key"] = api_key
        _raised(litellm.image_generation, **image)

    def test_run_prompt_with_a_model_without_its_own_key_makes_no_call(self):
        for case in ("openai-empty-key-and-a-base-url", "openai-empty-key"):
            with self.subTest(case=case):
                CustomAIModel.objects.filter(user_model_id=MODEL).delete()
                self.recorder.calls.clear()
                self.store(*WITHOUT_ITS_OWN_KEY[case])

                error = _raised(self._run_prompt_call)

                self.assert_nothing_was_called()
                self.assertEqual(str(error), ADD_API_KEY)

    # --------------------------------------------------------- evaluations ---

    def test_an_evaluation_stops_at_its_key_lookup(self):
        from evaluations.engine import instance as evaluation_engine

        self.store(*WITHOUT_ITS_OWN_KEY["openai-empty-key-and-a-base-url"])

        error = _raised(
            evaluation_engine._get_api_key,
            MODEL,
            self.organization.id,
            self.workspace.id,
        )

        self.assertEqual(str(error), ADD_API_KEY)
        self.assert_nothing_was_called()

    def test_an_evaluation_models_call_refuses_a_configuration_without_its_own_key(
        self,
    ):
        for case in (
            "openai-empty-key-and-a-base-url",
            "openai-empty-key",
            "openai-whitespace-key",
            "bedrock-a-region-alone",
        ):
            provider, value = WITHOUT_ITS_OWN_KEY[case]
            with self.subTest(case=case):
                self.recorder.calls.clear()
                # Built as a provider that needs no client of its own; the
                # call takes its provider from the call.
                llm = llm_module.LLM(
                    provider="bedrock", model_name=MODEL, api_key=dict(value)
                )

                error = _raised(llm.call_llm, CHAT, provider=provider)

                self.assert_nothing_was_called()
                self.assertEqual(str(error), ADD_API_KEY)

    def test_an_evaluation_models_call_sends_its_own_key_as_before(self):
        llm = llm_module.LLM(
            provider="bedrock", model_name=MODEL, api_key={"key": OWN_KEY}
        )

        _raised(llm.call_llm, CHAT, provider="openai")

        self.assertEqual(len(self.recorder.calls), 1, "the model was not called")
        _args, arguments = self.recorder.calls[0]
        self.assertEqual(arguments.get("api_key"), OWN_KEY)
        self.assertFalse(self.recorder.environment_reached_a_call())

    # ---------------------------------------- a stored SageMaker key pair ---

    def test_a_stored_sagemaker_model_is_called_with_its_own_key_pair(self):
        pair = {
            "aws_access_key_id": "test-access-key-id-not-real",
            "aws_secret_access_key": "test-secret-access-key-not-real",
            "aws_region_name": "us-east-1",
        }
        self.store("sagemaker", dict(pair))
        llm = llm_module.LLM(
            provider="bedrock", model_name=MODEL, api_key=self.stored_key()
        )

        _raised(llm.call_llm, CHAT, provider="sagemaker")

        self.assertEqual(len(self.recorder.calls), 1, "the model was not called")
        _args, arguments = self.recorder.calls[0]
        for field, value in pair.items():
            self.assertEqual(arguments.get(field), value, f"{field} was not sent")
        self.assertFalse(self.recorder.environment_reached_a_call())

    def test_a_stored_sagemaker_model_without_its_own_key_pair_is_refused(self):
        self.store(*WITHOUT_ITS_OWN_KEY["sagemaker-no-access-key-pair"])

        error = _raised(self.stored_key)

        self.assertEqual(str(error), ADD_API_KEY)
        llm = llm_module.LLM(
            provider="bedrock",
            model_name=MODEL,
            api_key=dict(WITHOUT_ITS_OWN_KEY["sagemaker-no-access-key-pair"][1]),
        )
        error = _raised(llm.call_llm, CHAT, provider="sagemaker")
        self.assert_nothing_was_called()
        self.assertEqual(str(error), ADD_API_KEY)
