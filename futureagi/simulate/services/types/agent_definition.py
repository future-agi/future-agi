"""Pydantic input models for AgentDefinition service layer.

These types define the structured credential input contract between
views/serializers and the service functions in
``simulate.services.agent_definition``.
"""

from collections.abc import Collection, Mapping
from typing import Any

from pydantic import BaseModel, Field


class ProviderCredentialsInput(BaseModel):
    """Credential payload for :func:`simulate.services.agent_definition.sync_provider_credentials`.

    Captures every field that can land in a ``ProviderCredentials`` row,
    regardless of provider. ``provider`` is the discriminator:

    - ``livekit`` / ``livekit_bridge`` → uses the six ``livekit_*`` fields.
    - ``retell`` / default (``vapi``) → uses ``api_key`` + ``assistant_id``.

    Fields are ``Optional`` because DRF may or may not include them in
    ``validated_data`` depending on the request payload. Missing values are
    treated as "don't touch" by the sync logic (secrets are never cleared
    by a missing key).
    """

    provider: str
    api_key: str | None = Field(default=None, repr=False)
    assistant_id: str | None = None
    livekit_url: str | None = None
    livekit_api_key: str | None = Field(default=None, repr=False)
    livekit_api_secret: str | None = Field(default=None, repr=False)
    livekit_agent_name: str | None = None
    livekit_config_json: dict[str, Any] | None = None
    livekit_max_concurrency: int | None = None
    provider_was_provided: bool = False

    @classmethod
    def from_validated(
        cls, data: Mapping[str, Any], submitted: Collection[str], fallback_provider: str
    ) -> "ProviderCredentialsInput":
        # DRF can supply defaults for absent fields. In particular, its default
        # null assistant on create is not a request to clear an identity.
        fields = {k: data[k] for k in cls.model_fields if k in submitted and k in data}
        fields["provider"] = fields.get("provider", fallback_provider) or ""
        fields["provider_was_provided"] = "provider" in submitted
        return cls.model_validate(fields)

    @property
    def has_credential_fields(self) -> bool:
        return "assistant_id" in self.model_fields_set or self.needs_credential_row

    @property
    def needs_credential_row(self) -> bool:
        other_fields = self.model_fields_set - {
            "provider",
            "provider_was_provided",
            "assistant_id",
        }
        return (
            self.provider_was_provided
            or bool(self.assistant_id)
            or any(getattr(self, name) is not None for name in other_fields)
        )
