from simulate.models import AgentDefinition
from simulate.serializers.agent_definition import _is_masked


class MaskedKeyError(ValueError):
    """Raised when a masked API key cannot be resolved."""


def resolve_api_key(api_key: str, agent_definition_id: str | None) -> tuple[str, str]:
    """Resolve a possibly-masked API key for upstream provider calls.

    Returns (key_for_upstream, key_for_response).
    - If key is not masked, both values are the original key.
    - If key is masked, key_for_upstream is the decrypted key from
      ProviderCredentials, key_for_response is the masked key.
    - Raises MaskedKeyError if masked but can't be resolved.
    """
    response_key = api_key
    if _is_masked(api_key):
        if not agent_definition_id:
            raise MaskedKeyError(
                "The API key is masked. Please paste the full API key and try again."
            )
        try:
            agent_def = AgentDefinition.objects.select_related("credentials").get(
                id=agent_definition_id
            )
        except AgentDefinition.DoesNotExist as e:
            raise MaskedKeyError("Agent definition not found.") from e
        creds = getattr(agent_def, "credentials", None)
        if creds:
            real = creds.get_api_key()
            if real:
                api_key = real
            else:
                raise MaskedKeyError(
                    "Stored credentials have no API key. Please re-enter the API key and try again."
                )
    return api_key, response_key
