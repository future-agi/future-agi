from django.conf import settings

from simulate.models import AgentDefinition, AgentVersion
from simulate.models.agent_definition import ProviderCredentials
from simulate.services.agent_api_key_storage import encrypt_api_key
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
    return (
        data.provider_was_provided
        or bool(getattr(data, "remove_vapi_public_key", False))
        or any(
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
                getattr(data, "vapi_public_key", None),
            ]
        )
    )


def _apply_secrets(
    creds,
    *,
    api_key: str,
    api_secret: str,
    provider_changed: bool,
    encrypt_secret: bool = False,
):
    if api_key and not is_masked(api_key):
        # Client input is always clear, even if it starts with enc::.
        # ProviderCredentials.save must only see authenticated storage here.
        creds.api_key = encrypt_api_key(api_key)
    elif provider_changed:
        creds.api_key = ""
    if api_secret and not is_masked(api_secret):
        # A Vapi public key is client input too: encrypt it here so a value
        # that starts with enc:: is never stored as if it were ciphertext.
        if encrypt_secret:
            api_secret = encrypt_api_key(api_secret)
        creds.api_secret = api_secret
    elif provider_changed:
        creds.api_secret = ""


def _apply_vapi_public_key(creds, *, public_key: str, remove: bool):
    """Save or remove a Vapi assistant's public key and its policy flag.

    The key lives in the encrypted ``api_secret`` column, which Vapi rows use
    for nothing else. Saving a key says the assistant has control
    authentication on; removing it says it has not, and removes an older key
    kept in ``config_json`` too, which the runner would read otherwise. A blank
    key changes nothing.
    """
    if remove:
        creds.api_secret = ""
        flag = False
    elif public_key and not is_masked(public_key):
        flag = True
    else:
        return
    config = dict(creds.config_json) if isinstance(creds.config_json, dict) else {}
    if remove:
        config.pop("public_api_key", None)
    plan = config.get("monitorPlan")
    plan = dict(plan) if isinstance(plan, dict) else {}
    plan["controlAuthenticationEnabled"] = flag
    config["monitorPlan"] = plan
    creds.config_json = config


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
    vapi_public_key = ""
    remove_vapi_public_key = False

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
        max_concurrency = data.livekit_max_concurrency
    else:
        provider_type = ProviderCredentials.ProviderType.VAPI
        api_key = (data.api_key or "").strip()
        # Only a Vapi assistant has a public key; it is saved in api_secret,
        # which no other Vapi setting uses.
        is_vapi = provider in ("", "vapi")
        vapi_public_key = (getattr(data, "vapi_public_key", None) or "").strip()
        remove_vapi_public_key = bool(getattr(data, "remove_vapi_public_key", False))
        if not is_vapi:
            vapi_public_key = ""
            remove_vapi_public_key = False
        api_secret = vapi_public_key
        assistant_id = (data.assistant_id or "").strip()
        server_url = ""
        agent_name = ""
        config_json = None
        max_concurrency = data.livekit_max_concurrency

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
        creds,
        api_key=api_key,
        api_secret=api_secret,
        provider_changed=provider_changed,
        encrypt_secret=provider_type == ProviderCredentials.ProviderType.VAPI,
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
    if provider_type == ProviderCredentials.ProviderType.VAPI:
        # After the non-secrets: a provider change resets config_json there.
        _apply_vapi_public_key(
            creds, public_key=vapi_public_key, remove=remove_vapi_public_key
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


def resolve_api_secret_for_version(version):
    """Read the API secret from a version's credentials (LiveKit).

    Mirrors :func:`resolve_api_key_for_version` for the secret field, used to
    preserve a masked ``livekit_api_secret`` across a new version instead of
    blanking it. Returns the decrypted secret, or None if not configured.
    """
    if version is None:
        return None
    try:
        creds = version.credentials
        if creds and creds.get_api_secret():
            return creds.get_api_secret()
    except AgentVersion.credentials.RelatedObjectDoesNotExist:
        pass
    try:
        agent = version.agent_definition
        legacy = agent.credentials_legacy
        if legacy and legacy.get_api_secret():
            return legacy.get_api_secret()
    except ProviderCredentials.DoesNotExist:
        pass
    return None


def resolve_stored_api_key(
    *, organization, workspace=None, agent_id=None, assistant_id=None, masked_value=None
):
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


def has_vapi_public_key(creds) -> bool:
    """True when a Vapi credentials row holds a saved public key.

    Answers only whether a key is saved; the key itself is never returned.
    """
    return bool(
        creds is not None
        and getattr(creds, "provider_type", "") == "vapi"
        and getattr(creds, "api_secret", "")
    )


def resolve_vapi_public_key_for_version(version):
    """Read a version's saved Vapi public key, decrypted, or None.

    Used to carry the key onto a new version when the edit form sends it blank.
    """
    if version is None:
        return None
    try:
        creds = version.credentials
    except AgentVersion.credentials.RelatedObjectDoesNotExist:
        return None
    if creds is None or creds.provider_type != "vapi":
        return None
    return creds.get_api_secret() or None
