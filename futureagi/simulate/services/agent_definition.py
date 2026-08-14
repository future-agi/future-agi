from django.conf import settings

from simulate.models import AgentDefinition, AgentVersion
from simulate.models.agent_definition import ProviderCredentials
from simulate.services.types.agent_definition import ProviderCredentialsInput

MASKED_VALUE = "********"


def is_masked(value: str) -> bool:
    if not value:
        return False
    if value == MASKED_VALUE:
        return True
    if value == "****":
        return True
    if len(value) == 11 and value[4:7] == "...":
        return True
    return False


def _has_credential_fields(data: ProviderCredentialsInput) -> bool:
    return data.provider_was_provided or any(
        value is not None
        for value in [
            data.api_key,
            data.assistant_id,
            data.livekit_url,
            data.livekit_api_key,
            data.livekit_api_secret,
            data.livekit_agent_name,
            data.livekit_config_json,
            data.livekit_max_concurrency,
        ]
    )


def _apply_secrets(creds, *, api_key: str, api_secret: str, provider_changed: bool):
    if api_key and not is_masked(api_key):
        creds.api_key = api_key
    elif provider_changed:
        creds.api_key = ""
    if api_secret and not is_masked(api_secret):
        creds.api_secret = api_secret
    elif provider_changed:
        creds.api_secret = ""


def _apply_non_secrets(
    creds,
    *,
    assistant_id: str,
    server_url: str,
    agent_name: str,
    config_json: dict | None,
    max_concurrency: int | None,
    provider_changed: bool,
):
    if provider_changed:
        creds.assistant_id = assistant_id
        creds.server_url = server_url
        creds.agent_name = agent_name
        creds.config_json = config_json if config_json is not None else {}
        creds.max_concurrency = (
            int(max_concurrency)
            if max_concurrency is not None
            else settings.DEFAULT_LIVEKIT_MAX_CONCURRENCY
        )
    else:
        if assistant_id:
            creds.assistant_id = assistant_id
        if server_url:
            creds.server_url = server_url
        if agent_name:
            creds.agent_name = agent_name
        if config_json is not None:
            creds.config_json = config_json
        if max_concurrency is not None:
            creds.max_concurrency = int(max_concurrency)


def _adopt_legacy_credentials(version):
    """Migrate credentials from the old agent_definition FK to this version."""
    try:
        agent = version.agent_definition
        legacy = agent.credentials_legacy
        if legacy and not legacy.agent_version:
            legacy.agent_version = version
            legacy.agent_definition = None
            legacy.save(update_fields=["agent_version", "agent_definition"])
            return True
    except ProviderCredentials.DoesNotExist:
        pass
    return False


def sync_provider_credentials(version, data: ProviderCredentialsInput):
    """Create or update version-scoped ProviderCredentials."""
    if not _has_credential_fields(data):
        # When no credential fields are provided, attempt to adopt legacy
        # credentials from the old agent_definition FK if they exist.
        if version.pk is not None:
            _adopt_legacy_credentials(version)
        return

    provider = (data.provider or "").strip()

    if provider in ("livekit", "livekit_bridge"):
        provider_type = ProviderCredentials.ProviderType.LIVEKIT
        api_key = (data.livekit_api_key or "").strip()
        api_secret = (data.livekit_api_secret or "").strip()
        assistant_id = ""
        server_url = (data.livekit_url or "").strip()
        agent_name = (data.livekit_agent_name or "").strip()
        config_json = data.livekit_config_json
        max_concurrency = data.livekit_max_concurrency
    elif provider == "retell":
        provider_type = ProviderCredentials.ProviderType.RETELL
        api_key = (data.api_key or "").strip()
        api_secret = ""
        assistant_id = (data.assistant_id or "").strip()
        server_url = ""
        agent_name = ""
        config_json = None
        max_concurrency = None
    else:
        provider_type = ProviderCredentials.ProviderType.VAPI
        api_key = (data.api_key or "").strip()
        api_secret = ""
        assistant_id = (data.assistant_id or "").strip()
        server_url = ""
        agent_name = ""
        config_json = None
        max_concurrency = None

    try:
        creds = ProviderCredentials.objects.get(agent_version=version)
    except ProviderCredentials.DoesNotExist:
        if _adopt_legacy_credentials(version):
            creds = ProviderCredentials.objects.get(agent_version=version)
        else:
            creds = ProviderCredentials(
                agent_version=version,
                provider_type=provider_type,
            )
    provider_changed = creds.provider_type != provider_type
    creds.provider_type = provider_type

    _apply_secrets(
        creds, api_key=api_key, api_secret=api_secret, provider_changed=provider_changed
    )
    _apply_non_secrets(
        creds,
        assistant_id=assistant_id,
        server_url=server_url,
        agent_name=agent_name,
        config_json=config_json,
        max_concurrency=max_concurrency,
        provider_changed=provider_changed,
    )
    creds.save()


def resolve_api_key_for_version(version):
    """Read API key from version's credentials.

    Falls back to legacy credentials on the agent definition's old FK
    for versions that predate the ProviderCredentials migration (0076).

    Returns decrypted API key string, or None if not configured.
    """
    if version is None:
        return None
    try:
        creds = version.credentials
        if creds and creds.get_api_key():
            return creds.get_api_key()
    except AgentVersion.credentials.RelatedObjectDoesNotExist:
        pass
    # Fallback: check legacy credentials linked to the agent definition.
    # Before migration 0076, ProviderCredentials was OneToOne to
    # AgentDefinition (related_name="credentials_legacy"). Older versions
    # that are not the latest may not have been backfilled with their own
    # agent_version FK.
    try:
        agent = version.agent_definition
        legacy = agent.credentials_legacy
        if legacy and legacy.get_api_key():
            return legacy.get_api_key()
    except ProviderCredentials.DoesNotExist:
        pass
    return None


def resolve_stored_api_key(*, organization, workspace=None, agent_id=None, assistant_id=None, masked_value=None):
    """Resolve the decrypted API key for a masked request, scoped to the caller's tenant.

    When ``masked_value`` is provided, the stored key's masked form is compared
    against it as a security check — a wrong masked prefix/suffix is rejected
    even when the agent_id is valid.

    Returns the key, or None. Never crosses organization/workspace.
    """
    if organization is None:
        return None

    agents = AgentDefinition.objects.filter(organization=organization, deleted=False)
    if workspace is not None:
        agents = agents.filter(workspace=workspace)

    if agent_id:
        try:
            agent = agents.get(id=agent_id)
        except AgentDefinition.DoesNotExist:
            return None
    else:
        return None

    version = agent.active_version or agent.latest_version
    key = resolve_api_key_for_version(version) or None

    if key and masked_value:
        from agentcc.services.credential_manager import mask_key

        if mask_key(key) != masked_value:
            return None

    return key
