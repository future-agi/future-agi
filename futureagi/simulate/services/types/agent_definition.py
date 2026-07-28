"""Pydantic input models for AgentDefinition service layer.

These types define the structured credential input contract between
views/serializers and the service functions in
``simulate.services.agent_definition``.
"""

from typing import Any

from pydantic import BaseModel


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
    api_key: str | None = None
    assistant_id: str | None = None
    livekit_url: str | None = None
    livekit_api_key: str | None = None
    livekit_api_secret: str | None = None
    livekit_agent_name: str | None = None
    livekit_config_json: dict[str, Any] | None = None
    livekit_max_concurrency: int | None = None
    provider_was_provided: bool = False
