import os

import structlog

logger = structlog.get_logger(__name__)
from agentic_eval.core_evals.run_prompt.available_models import AVAILABLE_MODELS
# (available_models always available)
from model_hub.models.api_key import ApiKey
from model_hub.models.custom_models import CustomAIModel
from accounts.models.organization import Organization
from accounts.models.workspace import Workspace


class LiteLLMModelManager:
    def __init__(self, model_name, organization_id=None, exclude_providers=None):
        if exclude_providers is None:
            exclude_providers = []
        self.model_name = model_name
        self.organization_id = organization_id
        self.models = AVAILABLE_MODELS
        self.exclude_providers = exclude_providers
        if organization_id:
            self._add_custom_models(organization_id)
        self._remove_failed_models()

    def _add_custom_models(self, organization_id):
        custom_models = CustomAIModel.objects.filter(
            organization=organization_id,
        ).values("user_model_id", "provider")

        # Convert self.models list into dict keyed by model_name
        models_dict = {m["model_name"]: m for m in self.models}

        for model in custom_models:
            # Rename keys and add "mode"
            model_name = model.pop("user_model_id")
            providers = model.pop("provider")

            if self.exclude_providers and providers in self.exclude_providers:
                continue

            updated_model = {
                "model_name": model_name,
                "providers": providers,
                "mode": "chat",
            }

            # Update or add the model
            models_dict[model_name] = updated_model

        # Convert back to list if needed
        self.models = list(models_dict.values())

    def _remove_failed_models(self):
        """Remove models that are known to fail or be deprecated"""
        failed_models = {
            # Audio preview models that are not yet supported
            # "gpt-4o-audio-preview",
            # "gpt-4o-audio-preview-2024-10-01",
            # Deprecated/unsupported Perplexity models
            "perplexity/codellama-34b-instruct",
            "perplexity/codellama-70b-instruct",
            "perplexity/pplx-7b-chat",
            "perplexity/pplx-70b-chat",
            "perplexity/pplx-7b-online",
            "perplexity/pplx-70b-online",
            "perplexity/llama-2-70b-chat",
            "perplexity/mistral-7b-instruct",
            "perplexity/mixtral-8x7b-instruct",
            "perplexity/sonar-small-chat",
            "perplexity/sonar-small-online",
            "perplexity/sonar-medium-chat",
            "perplexity/sonar-medium-online",
            # OpenAI embedding models
            "text-embedding-3-large",
            "text-embedding-3-small",
            "text-embedding-ada-002",
            # OpenAI moderation models
            "text-moderation-stable",
            "text-moderation-007",
            "text-moderation-latest",
            # OpenAI audio models (keep TTS models available)
            # "whisper-1",  # STT model - keep filtered
            # "tts-1",     # allow
            # "tts-1-hd",  # allow
            # Anthropic legacy models
            "claude-instant-1",
            "claude-2",
            # bedrock - Anthropic regional variants
            "eu.anthropic.claude-3-5-sonnet-20240620-v1:0",
            "eu.anthropic.claude-3-haiku-20240307-v1:0",
            "eu.anthropic.claude-3-opus-20240229-v1:0",
            # Deprecated Perplexity sonar-llama models (retired 2025-02-22)
            "perplexity/llama-3.1-sonar-huge-128k-online",
            "perplexity/llama-3.1-sonar-large-128k-online",
            "perplexity/llama-3.1-sonar-large-128k-chat",
            "perplexity/llama-3.1-sonar-small-128k-chat",
            "perplexity/llama-3.1-sonar-small-128k-online",
        }

        self.models = [
            model for model in self.models if model["model_name"] not in failed_models
        ]

        # Keep chat, audio, and image_generation mode models
        self.models = [
            model
            for model in self.models
            if model.get("mode") in ("chat", "audio", "stt", "tts", "image_generation")
        ]

    def set_api_key(self):
        api_key_name = None
        for model in self.models:
            if self.model_name == model.get("model_name"):
                api_key_name = model.get("api_key_name")
                break

        if api_key_name is None:
            raise ValueError(f"LiteLLMModel {self.model_name} not found")

        api_key = os.environ.get(api_key_name)
        if api_key is None:
            raise ValueError(
                f"API key not found for {model.provider.value}. Please set the {api_key_name} environment variable."
            )

        os.environ[api_key_name] = api_key

    def get_api_key(self, organization_id, workspace_id=None, provider=None):

        try:
            custom_models = CustomAIModel.objects.get(
                organization=organization_id,
                user_model_id=self.model_name,
                deleted=False,
            )
            return custom_models.actual_json
        except CustomAIModel.DoesNotExist:
            pass
        if not provider:
            provider = self.get_provider(self.model_name)

        # Build query with optional workspace filtering
        query = {"organization_id": organization_id, "provider": provider}
        if workspace_id:
            query["workspace_id"] = workspace_id
        else:
            try:
                org = Organization.objects.get(id=organization_id)
                if org.ws_enabled:
                    default_workspace = Workspace.objects.get(
                        organization=org, is_default=True
                    )
                    query["workspace_id"] = default_workspace.id
            except Organization.DoesNotExist:
                pass
            except Workspace.DoesNotExist:
                pass

        try:
            api_key_entry = ApiKey.objects.get(**query)
        except ApiKey.DoesNotExist:
            raise ValueError(
                f"API key not configured for {provider}. Please add your API key in settings."
            )
        except ApiKey.MultipleObjectsReturned:
            # Multiple rows matched — pick the most-recently-created one.
            # This is a data integrity issue: log it so operators can clean up duplicate keys.
            logger.warning(
                "api_key_multiple_rows_for_provider",
                provider=provider,
                query=str(query),
            )
            api_key_entry = ApiKey.objects.filter(**query).order_by("-created_at").first()
            if not api_key_entry:
                raise ValueError(
                    f"API key not configured for {provider}. Please add your API key in settings."
                )

        if api_key_entry.key:
            return api_key_entry.actual_key

        if api_key_entry.actual_json:
            return api_key_entry.actual_json

        raise ValueError(
            f"API key not configured for {provider}. Please add your API key in settings."
        )

    def get_provider(self, model_name, organization_id=None, workspace_id=None):
        provider = None

        for model in self.models:
            if model_name == model.get("model_name"):
                provider = model.get("providers")
                return provider

        try:
            custom_models = CustomAIModel.objects.get(
                organization=organization_id,
                workspace=workspace_id,
                user_model_id=model_name,
            )

            return custom_models.provider

        except CustomAIModel.MultipleObjectsReturned:
            raise ValueError(
                f"Multiple custom models found for {model_name} for organization {organization_id} and workspace {workspace_id}"
            )

        except CustomAIModel.DoesNotExist:
            raise ValueError(
                f"Custom model not found for {model_name} for organization {organization_id} and workspace {workspace_id}"
            )

    def get_model_by_provider(self, provider):
        model_name = []
        for model in self.models:
            if provider == model.get("providers"):
                model_name.append(model.get("model_name"))

        return model_name
