import os

import structlog

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from agentic_eval.core_evals.run_prompt.available_models import AVAILABLE_MODELS
from model_hub.models.api_key import ApiKey
from model_hub.models.custom_models import CustomAIModel
from model_hub.utils.model_endpoints import (
    assert_model_config_endpoints_allowed,
    whitelist_model_config,
)

logger = structlog.get_logger(__name__)


class MissingProviderKeyError(ValueError):
    """No key saved under Settings -> AI Providers for a provider."""

    def __init__(self, provider):
        self.provider = provider
        super().__init__(missing_provider_key_message(provider))


def missing_provider_key_message(provider):
    """User-facing text naming the key to add; only a known provider id is
    echoed (a custom model's provider is free text)."""
    from model_hub.models.choices import LiteLlmModelProvider

    known = {choice.value for choice in LiteLlmModelProvider}
    name = provider if provider in known else "this model's provider"
    return f"API key not configured for {name}. Please add your API key in settings."


class StoredCredentialLookup:
    """Where the stored-credential (BYOK) resolution ended: the credential
    (``found``) or the provider and workspace it looked under. Its repr
    never shows the credential."""

    __slots__ = ("found", "value", "provider", "workspace_id")

    def __init__(self, found, value, provider, workspace_id):
        self.found = found
        self.value = value
        self.provider = provider
        self.workspace_id = workspace_id

    def __repr__(self):
        return (
            f"StoredCredentialLookup(found={self.found!r}, provider={self.provider!r})"
        )


class StoredCredentialIndex:
    """For which models ``stored_credential_lookup`` would find a stored
    credential, so that availability labels match what the runtime does:
    the organization's custom model names, and the providers whose chosen
    ApiKey row (the workspace's row, else the organization row without a
    workspace) has a key or a config. Holds no credential."""

    __slots__ = ("custom_model_names", "providers")

    def __init__(self, custom_model_names, providers):
        self.custom_model_names = frozenset(custom_model_names)
        self.providers = frozenset(providers)

    def covers(self, model_name, provider):
        return model_name in self.custom_model_names or provider in self.providers

    def __repr__(self):
        return (
            f"StoredCredentialIndex(custom_models={len(self.custom_model_names)}, "
            f"providers={sorted(self.providers)!r})"
        )


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
            # Deprecated OpenAI image generation models
            "256-x-256/dall-e-2",
            "512-x-512/dall-e-2",
            "1024-x-1024/dall-e-2",
            "hd/1024-x-1792/dall-e-3",
            "hd/1792-x-1024/dall-e-3",
            "hd/1024-x-1024/dall-e-3",
            "standard/1024-x-1792/dall-e-3",
            "standard/1792-x-1024/dall-e-3",
            "standard/1024-x-1024/dall-e-3",
            # Deprecated Azure dall-e variants
            "azure/standard/1024-x-1024/dall-e-3",
            "azure/hd/1024-x-1024/dall-e-3",
            "azure/standard/1024-x-1792/dall-e-3",
            "azure/standard/1792-x-1024/dall-e-3",
            "azure/hd/1024-x-1792/dall-e-3",
            "azure/hd/1792-x-1024/dall-e-3",
            "azure/standard/1024-x-1024/dall-e-2",
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

    def get_api_key(
        self,
        organization_id,
        workspace_id=None,
        provider=None,
        *,
        allow_managed=False,
        operation=None,
    ):
        """The stored credentials for a provider call. A stored openai,
        azure, bedrock or sagemaker config is cut down to the keys that
        provider needs (``whitelist_model_config``), and a stored base URL
        (openai/azure ``api_base``, a custom model's endpoint) is checked
        against the deployment's address policy, on every call, so rows saved
        before the save-time checks are covered too; EndpointBlocked (a
        ValueError with generic text) when it is refused.

        One key: a caller that can carry a managed route passes
        ``allow_managed=True``. When no stored credential exists it then gets
        a ``ManagedRouteBinding`` from ``model_hub.services.model_route`` (or
        that module's typed refusal), never a provider key and never None.
        The binding admits nothing: each completion dispatched with it is
        admitted on its own, at the send. A stored credential always wins
        and is returned exactly as before; without ``allow_managed`` nothing
        changes."""
        lookup = self.stored_credential_lookup(organization_id, workspace_id, provider)
        if lookup.found:
            return lookup.value
        if not allow_managed:
            raise MissingProviderKeyError(lookup.provider)

        from model_hub.services.model_route import OP_RUN_PROMPT, bind_managed_route

        return bind_managed_route(
            organization_id=organization_id,
            workspace_id=lookup.workspace_id,
            model_name=self.model_name,
            provider=lookup.provider,
            operation=operation or OP_RUN_PROMPT,
            model_mode=self.model_mode(),
        )

    def stored_credential_lookup(
        self, organization_id, workspace_id=None, provider=None
    ):
        """The stored-credential resolution of ``get_api_key``, unchanged:
        the organization's custom model, else the provider's workspace key,
        else its organization key. ``found`` is False where ``get_api_key``
        used to raise "API key not configured"."""
        try:
            custom_models = CustomAIModel.objects.get(
                organization=organization_id,
                user_model_id=self.model_name,
                deleted=False,
            )
            key_config = whitelist_model_config(
                custom_models.provider, custom_models.actual_json
            )
            assert_model_config_endpoints_allowed(key_config)
            return StoredCredentialLookup(
                True, key_config, custom_models.provider, workspace_id
            )
        except CustomAIModel.DoesNotExist:
            pass
        if not provider:
            provider = self.get_provider(self.model_name)

        return self._stored_provider_credential_lookup(
            organization_id, provider, workspace_id
        )

    def get_provider_api_key(self, organization_id, provider, workspace_id=None):
        """The key saved under Settings -> AI Providers for ``provider``.

        Unlike get_api_key this never consults custom models, so a model with
        its own transport (Jev's System One API) can't pick up a custom model's
        credentials by name.
        """
        lookup = self._stored_provider_credential_lookup(
            organization_id, provider, workspace_id
        )
        if not lookup.found:
            raise MissingProviderKeyError(provider)
        return lookup.value

    def _stored_provider_credential_lookup(
        self, organization_id, provider, workspace_id=None
    ):
        """Provider-only stored lookup shared by routing and the BYOK accessor."""
        if not workspace_id:
            workspace_id = self._resolve_default_workspace_id(organization_id)

        api_key_entry = self._find_api_key_entry(
            organization_id, workspace_id, provider
        )
        if api_key_entry is None:
            return StoredCredentialLookup(False, None, provider, workspace_id)

        if api_key_entry.key:
            return StoredCredentialLookup(
                True, api_key_entry.actual_key, provider, workspace_id
            )

        if api_key_entry.actual_json:
            config_json = whitelist_model_config(provider, api_key_entry.actual_json)
            assert_model_config_endpoints_allowed(config_json)
            return StoredCredentialLookup(True, config_json, provider, workspace_id)

        return StoredCredentialLookup(False, None, provider, workspace_id)

    def stored_credential_index(self, organization_id, workspace_id=None):
        """``stored_credential_lookup``'s answer for many models at once, for
        model lists: the same custom-model match, the same ApiKey row choice
        (``_find_api_key_entry``, after the same default-workspace
        resolution) and the same non-empty test (``key``, else
        ``actual_json``). Values are only tested, never returned."""
        if not organization_id:
            return StoredCredentialIndex((), ())
        custom_model_names = CustomAIModel.objects.filter(
            organization=organization_id, deleted=False
        ).values_list("user_model_id", flat=True)
        if not workspace_id:
            workspace_id = self._resolve_default_workspace_id(organization_id)
        candidates = set(
            ApiKey.objects.filter(organization_id=organization_id).values_list(
                "provider", flat=True
            )
        )
        providers = set()
        for provider in candidates:
            entry = self._find_api_key_entry(organization_id, workspace_id, provider)
            if entry is not None and (entry.key or entry.actual_json):
                providers.add(provider)
        return StoredCredentialIndex(custom_model_names, providers)

    def model_mode(self, model_name=None):
        """The catalog mode ("chat", "tts", ...) of a model, or None."""
        name = self.model_name if model_name is None else model_name
        for model in self.models:
            if model.get("model_name") == name:
                return model.get("mode")
        return None

    def _resolve_default_workspace_id(self, organization_id):
        try:
            org = Organization.objects.get(id=organization_id)
        except Organization.DoesNotExist:
            return None
        if not org.ws_enabled:
            return None
        try:
            return Workspace.objects.get(organization=org, is_default=True).id
        except Workspace.DoesNotExist:
            return None

    def _find_api_key_entry(self, organization_id, workspace_id, provider):
        """Workspace-scoped key wins if present; falls back to the
        org-level key (workspace unset). Keys can be saved without a
        workspace (e.g. added before a workspace was selected), so a
        strict workspace-only match would miss a key that's clearly
        configured for the org."""
        if workspace_id:
            entry = ApiKey.objects.filter(
                organization_id=organization_id,
                workspace_id=workspace_id,
                provider=provider,
            ).first()
            if entry is not None:
                return entry

        return ApiKey.objects.filter(
            organization_id=organization_id,
            workspace_id__isnull=True,
            provider=provider,
        ).first()

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
            ) from None

        except CustomAIModel.DoesNotExist:
            raise ValueError(
                f"Model '{model_name}' is not available in the current model catalog. "
                "It may be deprecated or retired. Please select a supported model from the latest available models list."
            ) from None

    def get_model_by_provider(self, provider):
        model_name = []
        for model in self.models:
            if provider == model.get("providers"):
                model_name.append(model.get("model_name"))

        return model_name
