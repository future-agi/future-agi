"""Only the credential selected by the actual call can authorize it.

All credentials and endpoints below are synthetic; every dispatch is replaced.
"""

import os
from types import SimpleNamespace
from unittest.mock import patch

from rest_framework import status

from agentic_eval.core.llm import llm as llm_module
from agentic_eval.core_evals.run_prompt.litellm_models import LiteLLMModelManager
from model_hub.models.api_key import ApiKey
from model_hub.models.custom_models import CustomAIModel
from model_hub.tests.test_custom_models_api import BASE_URL, CustomModelsAPITestCase
from model_hub.utils.model_key import ModelKeyMissing
from model_hub.utils.utils import validate_model_working

MESSAGE = "Add an API key for this model."
OWN = "MODEL-OWN-SYNTHETIC-CREDENTIAL"
OTHER = "UNUSED-ALIAS-SYNTHETIC-CREDENTIAL"
ENV = "ENV-SENTINEL-NOT-A-CREDENTIAL"
MODEL = "selected-credential-probe"
CHAT = [{"role": "user", "content": "hi"}]
BAD_STORED = (
    {"api_key": OTHER},
    {"key": "", "api_key": OTHER},
    {"key": None, "api_key": OTHER},
    {"key": "   ", "api_key": OTHER},
)
AZURE = {
    "apiBase": "https://synthetic.openai.azure.com",
    "apiKey": OWN,
    "apiVersion": "2024-01-01",
    "azureEndpointType": "legacy",
}


def outcome(action):
    """Capture refusals too, so RED is an assertion about the behavior."""
    try:
        return action()
    except Exception as error:
        return error


class TestModelKeyAliasesUseTheSelectedCredential(CustomModelsAPITestCase):
    def setUp(self):
        super().setUp()
        # Inherited API tests keep their original fixtures and dispatch mocks.
        if self._testMethodName not in type(self).__dict__:
            return
        self.calls = []
        endpoint_check = patch(
            "agentic_eval.core_evals.run_prompt.litellm_models.assert_model_config_endpoints_allowed"
        )
        self.endpoint_check = endpoint_check.start()
        self.addCleanup(endpoint_check.stop)
        patches = [
            patch.dict(
                os.environ,
                {
                    "OPENAI_API_KEY": ENV,
                    "AZURE_API_KEY": ENV,
                    "AWS_ACCESS_KEY_ID": ENV,
                    "AWS_SECRET_ACCESS_KEY": ENV,
                },
            )
        ]
        for target in (
            "litellm.completion",
            "litellm.acompletion",
            "litellm.image_generation",
            "requests.post",
            "model_hub.utils.utils.guarded_completion",
            "model_hub.utils.utils.guarded_post",
            "agentic_eval.core.llm.llm.guarded_completion",
            "agentic_eval.core.llm.llm.guarded_post",
            "agentic_eval.core.llm.llm.LLM._call_llm_through_gateway",
        ):
            patches.append(patch(target, side_effect=self.record))
        for started in patches:
            started.start()
            self.addCleanup(started.stop)

    def record(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]
        )

    def assert_no_calls(self):
        self.assertEqual(self.calls, [])
        self.assertNotIn(ENV, repr(self.calls))

    def assert_refused(self, action):
        result = outcome(action)
        self.assertIsInstance(result, ModelKeyMissing)
        self.assertEqual(str(result), MESSAGE)
        self.assert_no_calls()
        self.endpoint_check.assert_not_called()

    def manager(self):
        return LiteLLMModelManager(
            model_name=MODEL, organization_id=self.organization.id
        )

    def store(self, provider, config):
        return self.create_custom_model(
            user_model_id=MODEL, provider=provider, key_config=dict(config)
        )

    def lookup(self):
        return self.manager().get_api_key(self.organization.id, self.workspace.id)

    def llm(self, config):
        value = llm_module.LLM(
            provider="bedrock", model_name=MODEL, api_key=dict(config)
        )
        value._update_token_usage = lambda response: None
        value._update_cost = lambda response: None
        return value

    def test_custom_lookup_refuses_alias_dropped_by_openai_whitelist(self):
        for config in BAD_STORED:
            with self.subTest(config=config):
                CustomAIModel.objects.filter(user_model_id=MODEL).delete()
                self.store("openai", config)
                self.assert_refused(self.lookup)

    def test_provider_json_lookup_refuses_alias_dropped_by_openai_whitelist(self):
        for config in BAD_STORED:
            with self.subTest(config=config):
                ApiKey.objects.filter(provider="openai").delete()
                ApiKey.objects.create(
                    organization=self.organization,
                    workspace=self.workspace,
                    user=self.user,
                    provider="openai",
                    config_json=dict(config),
                )
                self.assert_refused(
                    lambda: self.manager().get_provider_api_key(
                        self.organization.id,
                        "openai",
                        self.workspace.id,
                    )
                )

    def test_create_refuses_dropped_alias_without_saving_a_row(self):
        # This is a preservation control: create already selects config.key.
        # Distinct IDs keep every case independent even on an unexpected save.
        for index, config in enumerate(BAD_STORED):
            with self.subTest(config=config):
                self.calls.clear()
                name = f"{MODEL}-create-{index}"
                response = self.client.post(
                    f"{BASE_URL}/custom_models/create/",
                    {
                        "model_provider": "openai",
                        "model_name": name,
                        "input_token_cost": 0.0,
                        "output_token_cost": 0.0,
                        "config_json": dict(config),
                    },
                    format="json",
                )
                self.assert_no_calls()
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn(MESSAGE, str(response.data))
                self.assertFalse(
                    CustomAIModel.objects.filter(user_model_id=name).exists()
                )

    def test_edit_refuses_empty_selected_key_and_preserves_saved_row(self):
        for index, key in enumerate((None, "", "   ")):
            with self.subTest(key=key):
                self.calls.clear()
                row = self.create_custom_model(
                    user_model_id=f"{MODEL}-edit-{index}",
                    provider="openai",
                    key_config={"key": OWN},
                )
                response = self.client.patch(
                    f"{BASE_URL}/custom_models/edit/",
                    {"id": str(row.id), "config_json": {"key": key, "api_key": OTHER}},
                    format="json",
                )
                self.assert_no_calls()
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn(MESSAGE, str(response.data))
                row.refresh_from_db()
                self.assertEqual(row.actual_json, {"key": OWN})

    def test_validation_refuses_ignored_alias_when_key_branch_is_selected(self):
        for key in (None, "", "   "):
            with self.subTest(key=key):
                result = validate_model_working(
                    MODEL, {"key": key, "api_key": OTHER}, "openai"
                )
                self.assertIsInstance(result, ModelKeyMissing)
                self.assertEqual(str(result), MESSAGE)
                self.assert_no_calls()

    def test_call_llm_refuses_alias_before_any_gateway_or_provider_dispatch(self):
        for config in BAD_STORED:
            with self.subTest(config=config):
                llm = self.llm(config)
                self.assert_refused(
                    lambda llm=llm: llm.call_llm(CHAT, provider="openai")
                )

    def test_keyed_provider_without_a_dispatch_branch_cannot_use_an_ignored_key(self):
        for provider in ("openai/probe", "bedrock/probe", "sagemaker/probe"):
            with self.subTest(provider=provider):
                llm = self.llm({"key": OWN})
                self.assert_refused(
                    lambda llm=llm, provider=provider: llm.call_llm(
                        CHAT, provider=provider
                    )
                )

    def test_azure_call_provider_refuses_an_alias_its_branch_never_normalizes(self):
        for provider in ("azure_ai", "azure/probe"):
            with self.subTest(provider=provider):
                llm = self.llm({"key": None, "apiKey": OTHER})
                self.assert_refused(
                    lambda llm=llm, provider=provider: llm.call_llm(
                        CHAT, provider=provider
                    )
                )
                llm = self.llm({"apiKey": OTHER})
                self.assert_refused(
                    lambda llm=llm, provider=provider: llm.call_llm(
                        CHAT, provider=provider
                    )
                )

    def test_openai_conflicting_aliases_send_exactly_selected_key(self):
        config = {"key": OWN, "api_key": OTHER}
        self.store("openai", config)
        self.assertEqual(self.lookup(), {"key": OWN})
        ApiKey.objects.create(
            organization=self.organization,
            workspace=self.workspace,
            user=self.user,
            provider="openai",
            config_json=dict(config),
        )
        self.assertEqual(
            self.manager().get_provider_api_key(
                self.organization.id,
                "openai",
                self.workspace.id,
            ),
            {"key": OWN},
        )
        validate_model_working(MODEL, config, "openai")
        self.assertEqual(self.llm(config).call_llm(CHAT, provider="openai"), "ok")
        self.assertEqual(len(self.calls), 2)
        for _args, kwargs in self.calls:
            self.assertEqual(kwargs["api_key"], OWN)
            self.assertNotIn(OTHER, repr(kwargs))
            self.assertNotIn(ENV, repr(kwargs))

    def test_validation_uses_api_key_when_no_key_branch_exists(self):
        result = validate_model_working(MODEL, {"api_key": OWN}, "openai")
        self.assertEqual(result, "ok")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][1]["api_key"], OWN)

    def test_azure_camel_alias_survives_both_stored_lookups(self):
        self.store("azure", AZURE)
        self.assertEqual(outcome(self.lookup), AZURE)
        ApiKey.objects.create(
            organization=self.organization,
            workspace=self.workspace,
            user=self.user,
            provider="azure",
            config_json=dict(AZURE),
        )
        self.assertEqual(
            outcome(
                lambda: self.manager().get_provider_api_key(
                    self.organization.id,
                    "azure",
                    self.workspace.id,
                )
            ),
            AZURE,
        )
        self.assert_no_calls()

    def test_azure_camel_alias_is_normalized_for_validation_and_direct_call(self):
        self.assertEqual(validate_model_working(MODEL, dict(AZURE), "azure"), "ok")
        self.assertEqual(
            outcome(lambda: self.llm(AZURE).call_llm(CHAT, provider="azure")), "ok"
        )
        self.assertEqual(len(self.calls), 2)
        for _args, kwargs in self.calls:
            self.assertEqual(kwargs["api_key"], OWN)
            self.assertNotIn("apiKey", kwargs)
            self.assertNotIn(ENV, repr(kwargs))

    def test_azure_conflicting_aliases_send_exactly_selected_key(self):
        for config in (
            {**AZURE, "key": OWN, "api_key": OTHER, "apiKey": OTHER},
            {**AZURE, "api_key": OWN, "apiKey": OTHER},
        ):
            with self.subTest(config=config):
                self.calls.clear()
                validate_model_working(MODEL, dict(config), "azure")
                self.assertEqual(
                    self.llm(config).call_llm(CHAT, provider="azure"), "ok"
                )
                self.assertEqual(len(self.calls), 2)
                for _args, kwargs in self.calls:
                    self.assertEqual(kwargs["api_key"], OWN)
                    self.assertNotIn(OTHER, repr(kwargs))
                    self.assertNotIn(ENV, repr(kwargs))

    def test_azure_truthy_whitespace_canonical_key_cannot_use_camel_alias(self):
        config = {**AZURE, "api_key": "   "}
        result = validate_model_working(MODEL, dict(config), "azure")
        self.assertIsInstance(result, ModelKeyMissing)
        self.assertEqual(str(result), MESSAGE)
        self.assert_refused(lambda: self.llm(config).call_llm(CHAT, provider="azure"))

    def test_azure_falsey_canonical_key_uses_the_normalizers_camel_fallback(self):
        for key in (None, ""):
            with self.subTest(key=key):
                self.calls.clear()
                config = {**AZURE, "api_key": key}
                self.assertEqual(
                    validate_model_working(MODEL, dict(config), "azure"), "ok"
                )
                self.assertEqual(
                    outcome(
                        lambda config=config: self.llm(config).call_llm(
                            CHAT, provider="azure"
                        )
                    ),
                    "ok",
                )
                self.assertEqual(len(self.calls), 2)
                for _args, kwargs in self.calls:
                    self.assertEqual(kwargs["api_key"], OWN)
                    self.assertNotIn(ENV, repr(kwargs))

    def test_aws_validation_cannot_replace_the_selected_pair_with_api_key(self):
        for provider in ("bedrock", "sagemaker"):
            with self.subTest(provider=provider):
                result = validate_model_working(MODEL, {"api_key": OTHER}, provider)
                self.assertIsInstance(result, ModelKeyMissing)
                self.assertEqual(str(result), MESSAGE)
                self.assert_no_calls()

    def test_google_kind_refusal_precedes_missing_selected_key(self):
        from model_hub.utils.model_endpoints import ModelConfigurationRefused

        config = {"key": "", "type": "authorized_user", "refresh_token": "synthetic"}
        self.store("openai", config)
        self.assertIsInstance(outcome(self.lookup), ModelConfigurationRefused)
        self.assertIsInstance(
            outcome(lambda: self.llm(config).call_llm(CHAT, provider="openai")),
            ModelConfigurationRefused,
        )
        self.assert_no_calls()
