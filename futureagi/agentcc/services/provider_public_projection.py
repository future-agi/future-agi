"""One credential-aware, read-only model projection for all public row reads."""

from agentcc.services.provider_model_safety import (
    InvalidProviderCredentialContext,
    filter_provider_model_ids,
)
from agentcc.services.provider_secret_policy import extra_config_secret_values
from agentcc.services.provider_url_auth import provider_url_auth_values
from integrations.services.credentials import CredentialManager

_UNSET = object()


def public_provider_models(instance, *, credentials=_UNSET, prior_credentials=()):
    values = instance.models_list
    values = values if isinstance(values, (list, tuple)) else []
    if credentials is _UNSET:
        try:
            credentials = CredentialManager.decrypt(bytes(instance.encrypted_credentials))
        except Exception:
            credentials = None
    try:
        if not isinstance(credentials, dict):
            raise InvalidProviderCredentialContext
        return filter_provider_model_ids(
            values,
            (
                credentials,
                prior_credentials,
                extra_config_secret_values(instance.extra_config),
                provider_url_auth_values(instance.base_url),
            ),
            provider_name=instance.provider_name,
            api_format=instance.api_format,
            enforce_discovery_grammar=False,
            saved_projection=True,
        )
    except InvalidProviderCredentialContext:
        return {
            "models": [],
            "dropped_model_count": len(values),
            "code": InvalidProviderCredentialContext.code,
            "model_warning": (
                "Saved provider credential could not be validated. "
                "Repair the encryption configuration or replace the credential "
                "before re-entering model IDs."
            ),
        }
