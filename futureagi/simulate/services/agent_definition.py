from dataclasses import dataclass, field

from django.conf import settings
from django.db import transaction
from rest_framework.exceptions import ValidationError

from simulate.models import AgentDefinition, AgentVersion
from simulate.models.agent_definition import ProviderCredentials
from simulate.services.agent_setup import LEGACY_TYPE
from simulate.services.types.agent_definition import ProviderCredentialsInput
from tfc.middleware.workspace_context import (
    get_current_workspace,
    set_workspace_context,
)

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


def provider_family(provider):
    """Normalize supported aliases for comparisons, never for authored routing.

    Do not use the credential enum's VAPI default: Bland shares that storage
    encoding but remains a different provider authority.
    """
    return LEGACY_TYPE.get(provider, provider)


def validate_provider_change(*, source_provider, provider, api_key):
    """Require a fresh key before changing a key-based provider's authority.

    An unversioned legacy row has no durable provider provenance (Bland and
    Vapi even share its storage encoding). Reject an incomplete transition
    rather than clearing the selected row and reopening that legacy fallback.
    The source provider must be captured before the definition is changed.
    """
    key = (api_key or "").strip()
    if (
        provider_family(provider) != provider_family(source_provider)
        and provider in ("vapi", "bland", "retell")
        and (not key or is_masked(key))
    ):
        raise ValidationError(
            {"api_key": "A new API key is required when changing provider."}
        )


def _has_credential_fields(data: ProviderCredentialsInput) -> bool:
    return (
        data.provider_was_provided
        or "assistant_id" in data.model_fields_set
        or any(
            value is not None
            for value in [
                data.api_key,
                data.livekit_url,
                data.livekit_api_key,
                data.livekit_api_secret,
                data.livekit_agent_name,
                data.livekit_config_json,
                data.livekit_max_concurrency,
            ]
        )
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
    assistant_id: str | None,
    server_url: str,
    agent_name: str,
    config_json: dict | None,
    max_concurrency: int | None,
    provider_changed: bool,
):
    if provider_changed:
        creds.assistant_id = assistant_id or ""
        creds.server_url = server_url
        creds.agent_name = agent_name
        creds.config_json = config_json if config_json is not None else {}
        creds.max_concurrency = (
            int(max_concurrency)
            if max_concurrency is not None
            else settings.DEFAULT_LIVEKIT_MAX_CONCURRENCY
        )
    else:
        if assistant_id is not None:
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
    if ProviderCredentials.objects.filter(agent_version=version).exists():
        return False
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


@transaction.atomic
def sync_provider_credentials(version, data: ProviderCredentialsInput):
    """Save a version's credentials and assistant snapshot as one configuration.

    All writers (CRUD serializers, edit/create views and harness versioning) use
    this boundary. Only the supplied version changes; key-only edits must not
    replace its assistant with the mutable definition's current assistant.
    """
    # The caller resolved and authorized ``version``; re-read it by key alone so
    # the thread-local workspace scope (which can name another workspace, e.g.
    # during harness creation) never hides the row being locked.
    locked_version = AgentVersion.no_workspace_objects.select_for_update(
        of=("self",)
    ).get(pk=version.pk)
    if not _has_credential_fields(data):
        # When no credential fields are provided, attempt to adopt legacy
        # credentials from the old agent_definition FK if they exist.
        if version.pk is not None:
            _adopt_legacy_credentials(version)
        return

    provider = (data.provider or "").strip()
    raw_snapshot = locked_version.configuration_snapshot
    snapshot = dict(raw_snapshot) if isinstance(raw_snapshot, dict) else {}
    previous_provider = (
        snapshot.get("provider")
        or data.previous_provider
        or locked_version.agent_definition.provider
    )
    # Bland and Vapi share the row's VAPI encoding. Only an explicit authored
    # provider change (not that encoding, nor a mutable definition alone) can
    # change the selected version's provider authority.
    authored_provider_changed = data.provider_was_provided and provider_family(
        provider
    ) != provider_family(previous_provider)
    if authored_provider_changed:
        validate_provider_change(
            source_provider=previous_provider, provider=provider, api_key=data.api_key
        )
    assistant_was_provided = "assistant_id" in data.model_fields_set
    # Explicit null clears the non-null credential identity; omission preserves it.
    assistant_id = (data.assistant_id or "").strip() if assistant_was_provided else None

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
        server_url = ""
        agent_name = ""
        config_json = None
        max_concurrency = data.livekit_max_concurrency
    else:
        provider_type = ProviderCredentials.ProviderType.VAPI
        api_key = (data.api_key or "").strip()
        api_secret = ""
        server_url = ""
        agent_name = ""
        config_json = None
        max_concurrency = data.livekit_max_concurrency

    if provider == "vapi" and is_masked(api_key):
        # CRUD serializers can pass a mask directly (the version/edit views
        # resolve it earlier). Materialize a legacy key only with its identity;
        # leave a raw-empty selected row empty so it keeps selecting definition.
        configuration = (
            ProviderConfiguration(None, None)
            if authored_provider_changed
            else resolve_vapi_configuration_for_version(locked_version, for_save=True)
        )
        api_key = configuration.api_key or ""
        if api_key and not assistant_was_provided:
            assistant_id = configuration.assistant_id or ""
            assistant_was_provided = True

    if authored_provider_changed and (not api_key or is_masked(api_key)):
        # The definition may already name the destination provider even while
        # the selected historical version is authored for another one. Clearing
        # only on a definition-field change would leave its stale fallback key.
        agent = locked_version.agent_definition
        agent.api_key = ""
        agent.save(update_fields=["api_key", "updated_at"])
        version.agent_definition.api_key = ""

    credentials = ProviderCredentials.objects.select_for_update(of=("self",))
    try:
        creds = credentials.get(agent_version=version)
    except ProviderCredentials.DoesNotExist:
        if not authored_provider_changed and _adopt_legacy_credentials(version):
            creds = credentials.get(agent_version=version)
        else:
            creds = ProviderCredentials(
                agent_version=version,
                provider_type=provider_type,
            )
    provider_changed = creds.provider_type != provider_type or authored_provider_changed
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

    if assistant_was_provided or provider_changed or data.provider_was_provided:
        # Merge into the locked, current snapshot, not a caller's stale copy.
        # Never re-snapshot the whole definition over a historical version.
        if assistant_was_provided or provider_changed:
            snapshot["assistant_id"] = creds.assistant_id
        if data.provider_was_provided:
            snapshot["provider"] = provider
        locked_version.configuration_snapshot = snapshot
        locked_version.save(update_fields=["configuration_snapshot"])
        version.configuration_snapshot = snapshot


@dataclass(frozen=True)
class ProviderConfiguration:
    api_key: str | None = field(repr=False)
    assistant_id: str | None
    # Internal source marker, not an API field. A raw-empty Vapi row permits
    # definition fallback; a wrong-provider or unusable keyed row closes it.
    uses_definition_fallback: bool = False


def provider_for_version(version):
    """The authored provider, not the credential table's storage encoding.

    Migration 0071 (and credential writes) encode Bland as ``vapi`` too.
    A historical version's explicit provider wins over the mutable definition.
    """
    snapshot = version.configuration_snapshot or {}
    return snapshot.get("provider") or version.agent_definition.provider


def resolve_vapi_configuration_for_version(
    version, *, agent_definition=None, for_save=False
):
    """Select one Vapi source for both key and assistant, without writing.

    A keyed row owns its assistant (or its own version's snapshot). Only a
    missing row permits legacy lookup, including the legacy row's version link
    from migration 0076. A raw-empty row selects the definition pair on reads;
    saves keep that empty marker. Wrong-provider rows and nonempty ciphertext
    never borrow another key, even if decryption produces an empty value.
    """
    agent = agent_definition or (version.agent_definition if version else None)
    provider = (
        provider_for_version(version) if version else getattr(agent, "provider", None)
    )
    if provider != ProviderCredentials.ProviderType.VAPI:
        return ProviderConfiguration(None, None)

    credentials = None
    credential_version = version
    if version is not None:
        try:
            credentials = version.credentials
        except ProviderCredentials.DoesNotExist:
            pass
    if credentials is None:
        try:
            credentials = agent.credentials_legacy
        except ProviderCredentials.DoesNotExist:
            pass
        else:
            credential_version = credentials.agent_version
    if credentials is not None and (
        credentials.provider_type != ProviderCredentials.ProviderType.VAPI
        or (
            credential_version is not None
            and provider_for_version(credential_version)
            != ProviderCredentials.ProviderType.VAPI
        )
    ):
        return ProviderConfiguration(None, None)
    if credentials is None or not credentials.api_key:
        # Do not take a newly changed definition's credentials for an older
        # version authored for a different provider.
        if agent.provider != ProviderCredentials.ProviderType.VAPI:
            return ProviderConfiguration(None, None)
        return ProviderConfiguration(
            None if for_save else (agent.api_key or None),
            agent.assistant_id or None,
            uses_definition_fallback=True,
        )
    fallback_assistant = (
        (credential_version.configuration_snapshot or {}).get("assistant_id")
        if credential_version is not None
        else agent.assistant_id
    )
    return ProviderConfiguration(
        credentials.get_api_key() or None,
        credentials.assistant_id or fallback_assistant or None,
    )


def resolve_provider_configuration_for_version(version, *, for_save=False):
    """Resolve version-bound call inputs; preserve non-Vapi snapshot semantics."""
    if version is None:
        return ProviderConfiguration(None, None)
    if provider_for_version(version) == ProviderCredentials.ProviderType.VAPI:
        return resolve_vapi_configuration_for_version(version, for_save=for_save)

    # Other providers retain their legacy key fallback and authored assistant.
    # In particular, an empty Bland row encoded as VAPI must not select Vapi's
    # definition fallback or suppress its migrated legacy credential.
    assistant_id = (version.configuration_snapshot or {}).get("assistant_id") or None
    try:
        creds = version.credentials
        key = creds.get_api_key() if creds else None
        if key:
            return ProviderConfiguration(key, assistant_id)
    except ProviderCredentials.DoesNotExist:
        pass
    try:
        legacy = version.agent_definition.credentials_legacy
        # 0076 keeps the definition link while assigning the row to a version.
        # Its owner's authored provider, not 0071's shared VAPI encoding, must
        # match. An in-place Vapi -> Bland switch must not resurrect Vapi's key.
        legacy_version = legacy.agent_version
        if legacy_version is not None and provider_family(
            provider_for_version(legacy_version)
        ) != provider_family(provider_for_version(version)):
            return ProviderConfiguration(None, assistant_id)
        key = legacy.get_api_key() if legacy else None
        if key:
            return ProviderConfiguration(key, assistant_id)
    except ProviderCredentials.DoesNotExist:
        pass
    return ProviderConfiguration(None, assistant_id)


def resolve_api_key_for_version(version, *, for_save=False):
    """Scalar compatibility for key-only consumers; paired reads use the resolver.

    ``for_save`` preserves Vapi's raw-empty source marker instead of copying the
    definition key beside a historical assistant. Non-Vapi legacy fallback must
    still match the legacy row owner's authored provider.
    """
    return resolve_provider_configuration_for_version(
        version, for_save=for_save
    ).api_key


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


def _credential_source_queryset(model, *, organization, workspace=None):
    """Reuse the manager's default-workspace visibility, including legacy NULLs.

    Construct the queryset under the supplied, already authorized workspace so
    non-request callers get the same rules. Restore the caller's context before
    returning; the manager's scope is already part of the queryset.
    """
    previous_workspace = get_current_workspace()
    try:
        if workspace is not None:
            set_workspace_context(workspace=workspace)
        return model.objects.filter(organization=organization, deleted=False)
    finally:
        set_workspace_context(workspace=previous_workspace)


def accessible_credential_agents(*, organization, workspace=None):
    return _credential_source_queryset(
        AgentDefinition, organization=organization, workspace=workspace
    )


def source_version_for_agent(agent, source_version_id=None, *, workspace=None):
    """Select a visible version within an already authorized agent and org.

    Explicit IDs never fall back to active/latest. Workspace visibility uses
    the same manager as the parent lookup: default workspaces include legacy
    NULLs, while a nondefault workspace cannot borrow them or another workspace.
    """
    workspace = workspace or get_current_workspace() or agent.workspace
    versions = _credential_source_queryset(
        AgentVersion, organization=agent.organization_id, workspace=workspace
    ).filter(agent_definition=agent)
    if workspace is None:
        versions = versions.filter(workspace__isnull=True)
    if source_version_id is None:
        return versions.filter(status="active").order_by("-version_number").first() or (
            versions.order_by("-version_number").first()
        )
    return versions.get(pk=source_version_id)


def resolve_stored_api_key(
    *,
    organization,
    workspace=None,
    agent_id=None,
    assistant_id=None,
    masked_value=None,
    source_version_id=None,
    provider=None,
):
    """Resolve the decrypted API key for a masked request, scoped to the caller's tenant.

    When ``masked_value`` is provided, the stored key's masked form is compared
    against it as a security check — a wrong masked prefix/suffix is rejected
    even when the agent_id is valid.

    Explicit source IDs must belong to the authorized agent and its org/workspace;
    omission keeps active/latest selection. A requested provider must match the
    source's authored provider before any stored key can be sent upstream.
    Returns the key, or None. Never crosses organization/workspace.
    """
    if organization is None:
        return None

    agents = accessible_credential_agents(
        organization=organization, workspace=workspace
    )

    if agent_id:
        try:
            agent = agents.get(id=agent_id)
        except AgentDefinition.DoesNotExist:
            return None
    else:
        return None

    try:
        version = source_version_for_agent(
            agent, source_version_id, workspace=workspace
        )
    except AgentVersion.DoesNotExist:
        return None
    if provider is not None and (
        version is None or provider_for_version(version) != provider
    ):
        return None
    key = resolve_api_key_for_version(version) or None

    if key and masked_value:
        from agentcc.services.credential_manager import mask_key

        if mask_key(key) != masked_value:
            return None

    return key
