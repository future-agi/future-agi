from collections import defaultdict
from typing import TypedDict
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone

from simulate.models import AgentDefinition, AgentVersion
from simulate.models.agent_definition import ProviderCredentials
from simulate.services.types.agent_definition import ProviderCredentialsInput

MASKED_VALUE = "********"
SERVER_URL_CHANGE_NEEDS_SECRETS = (
    "Enter the API key and secret again to change the server URL."
)


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


class CredentialsNeedReentry(Exception):
    """Saved secrets would be sent to a new address, so they must be typed again."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def mask_websocket_headers(headers: dict[str, str] | None) -> dict[str, str] | None:
    if not isinstance(headers, dict):
        return headers
    return dict.fromkeys(headers, MASKED_VALUE)


def mask_websocket_url(url: str | None) -> str | None:
    if not url:
        return url
    parts = urlsplit(url)
    netloc = parts.netloc
    if "@" in netloc:
        netloc = f"{MASKED_VALUE}@{netloc.rsplit('@', 1)[1]}"
    query = urlencode(
        [
            (name, MASKED_VALUE)
            for name, _ in parse_qsl(parts.query, keep_blank_values=True)
        ],
        safe="*",
    )
    return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))


class WebsocketFields(TypedDict, total=False):
    websocket_url: str | None
    websocket_headers: dict[str, str] | None


def _destination(url: str) -> tuple[str, str | None, int | str | None]:
    parts = urlsplit(url)
    try:
        port: int | str | None = parts.port
    except ValueError:
        port = parts.netloc.rsplit(":", 1)[-1]
    return parts.scheme, parts.hostname, port


def _restore_url(url: str | None, stored: str | None, moved: bool) -> str | None:
    if not url or not stored or MASKED_VALUE not in url:
        return url
    if moved:
        raise CredentialsNeedReentry(
            "Enter the websocket URL's credentials again to change where it connects."
        )
    new, old = urlsplit(url), urlsplit(stored)
    old_values: defaultdict[str, list[str]] = defaultdict(list)
    for name, value in parse_qsl(old.query, keep_blank_values=True):
        old_values[name].append(value)
    seen: defaultdict[str, int] = defaultdict(int)
    restored = []
    for name, value in parse_qsl(new.query, keep_blank_values=True):
        index = seen[name]
        seen[name] += 1
        if value == MASKED_VALUE and index < len(old_values[name]):
            value = old_values[name][index]
        restored.append((name, value))
    query = urlencode(restored, safe="*")
    netloc = old.netloc if new.netloc.startswith(f"{MASKED_VALUE}@") else new.netloc
    return urlunsplit((new.scheme, netloc, new.path, query, new.fragment))


def _restore_headers(
    headers: dict[str, str] | None, stored: dict[str, str] | None, moved: bool
) -> dict[str, str] | None:
    if not isinstance(headers, dict) or MASKED_VALUE not in headers.values():
        return headers
    if moved:
        raise CredentialsNeedReentry(
            "Enter the websocket header values again to send them to a new URL."
        )
    stored = stored if isinstance(stored, dict) else {}
    return {
        name: stored.get(name, value) if value == MASKED_VALUE else value
        for name, value in headers.items()
    }


def merge_websocket_secrets(
    incoming: WebsocketFields,
    stored_url: str | None,
    stored_headers: dict[str, str] | None,
) -> WebsocketFields:
    """Masked websocket values sent back on save take their stored values, but only
    while the URL still points at the same host; otherwise they must be typed again.
    Saved headers are never kept for a new host: they must be sent again or cleared."""
    url = incoming.get("websocket_url", stored_url)
    moved = bool(stored_url and url and _destination(url) != _destination(stored_url))
    if moved and stored_headers and "websocket_headers" not in incoming:
        raise CredentialsNeedReentry(
            "Enter the websocket header values again, or clear them, to change "
            "where it connects."
        )
    merged: WebsocketFields = {}
    if "websocket_url" in incoming:
        merged["websocket_url"] = _restore_url(
            incoming["websocket_url"], stored_url, moved
        )
    if "websocket_headers" in incoming:
        merged["websocket_headers"] = _restore_headers(
            incoming["websocket_headers"], stored_headers, moved
        )
    return merged


def locked_credential_version(agent, *, lock_source=False):
    """Select the edit target while the caller holds the definition lock."""
    versions = agent.versions.select_for_update(of=("self",)).order_by(
        "-version_number"
    )
    if lock_source:
        rows = ProviderCredentials.objects.select_for_update()
        versions = versions.prefetch_related(
            Prefetch("credentials", queryset=rows),
            Prefetch("agent_definition__credentials_legacy", queryset=rows),
        )
    return (
        versions.filter(status=AgentVersion.StatusChoices.ACTIVE).first()
        or versions.first()
    )


def credential_provider(agent, version):
    snapshot = version.configuration_snapshot if version else None
    if isinstance(snapshot, dict) and snapshot.get("provider"):
        return snapshot["provider"]
    return agent.provider or ""


def snapshot_created_version(version):
    """Include new settings without replacing the version's credential identity."""
    version.refresh_from_db(using="default")
    snapshot = version.create_snapshot(commit_message=version.commit_message or "")
    for field in ("provider", "assistant_id"):
        if field in version.configuration_snapshot:
            snapshot[field] = version.configuration_snapshot[field]
    version.configuration_snapshot = snapshot
    version.save(update_fields=["configuration_snapshot"])


def _typed(value: str) -> bool:
    return bool(value) and not is_masked(value)


def refuse_moved_secrets(
    *, stored_url: str, new_url: str, api_key: str, api_secret: str
) -> None:
    if not (stored_url and new_url) or new_url == stored_url:
        return
    if _typed(api_key) and _typed(api_secret):
        return
    raise CredentialsNeedReentry(SERVER_URL_CHANGE_NEEDS_SECRETS)


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
    if provider_changed or assistant_id is not None:
        creds.assistant_id = assistant_id or ""
    if provider_changed:
        creds.server_url = server_url
        creds.agent_name = agent_name
        creds.config_json = config_json if config_json is not None else {}
        creds.max_concurrency = (
            int(max_concurrency)
            if max_concurrency is not None
            else settings.DEFAULT_LIVEKIT_MAX_CONCURRENCY
        )
    else:
        if server_url:
            creds.server_url = server_url
        if agent_name:
            creds.agent_name = agent_name
        if config_json is not None:
            creds.config_json = config_json
        if max_concurrency is not None:
            creds.max_concurrency = int(max_concurrency)


def _adopt_legacy_credentials(version):
    row = (
        ProviderCredentials.objects.select_for_update()
        .filter(agent_definition_id=version.agent_definition_id, agent_version=None)
        .first()
    )
    if row is not None:
        row.agent_version = version
        row.agent_definition = None
        row.save(update_fields=["agent_version", "agent_definition"])
    return row


@transaction.atomic
def sync_provider_credentials(
    version, data: ProviderCredentialsInput, *, new_version=False
):
    """Write credentials and authored identity fields to the same version."""
    # The target is already authorized, including harness versions created in a
    # different workspace from the ambient request. Never select a sibling here.
    target = AgentVersion.no_workspace_objects.select_for_update(of=("self",)).get(
        pk=version.pk
    )
    creds = (
        ProviderCredentials.objects.select_for_update()
        .filter(agent_version=target)
        .first()
    )
    if creds is None:
        creds = _adopt_legacy_credentials(target)
    if not data.has_credential_fields:
        if creds is not None:
            version.credentials = creds
        return

    provider = data.provider.strip()
    snapshot = target.configuration_snapshot
    if not isinstance(snapshot, dict):
        snapshot = {}
    assistant_supplied = "assistant_id" in data.model_fields_set
    assistant_id = (data.assistant_id or "").strip() if assistant_supplied else None
    if creds is None and not data.needs_credential_row:
        # A clear still belongs in the snapshot, but an agent without credentials
        # does not acquire an empty row just because a form sent null.
        target.configuration_snapshot = {**snapshot, "assistant_id": ""}
        target.save(update_fields=["configuration_snapshot"])
        version.configuration_snapshot = target.configuration_snapshot
        return

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

    if creds is None:
        creds = ProviderCredentials(agent_version=target, provider_type=provider_type)
    provider_changed = creds.provider_type != provider_type
    if (
        assistant_id is None
        and not creds.assistant_id
        and not provider_changed
        and snapshot.get("provider", provider) == provider
    ):
        creds.assistant_id = snapshot.get("assistant_id") or ""
    if not provider_changed and (creds.api_key or creds.api_secret):
        refuse_moved_secrets(
            stored_url=creds.server_url,
            new_url=server_url,
            api_key=api_key,
            api_secret=api_secret,
        )
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
    identity = {}
    if data.provider_was_provided or (new_version and provider):
        identity["provider"] = provider
    if assistant_supplied or provider_changed or new_version:
        identity["assistant_id"] = creds.assistant_id
    if identity:
        target.configuration_snapshot = {**snapshot, **identity}
        target.save(update_fields=["configuration_snapshot"])
    version.configuration_snapshot = target.configuration_snapshot
    version.credentials = creds


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


def resolve_server_url_for_version(version) -> str:
    if version is None:
        return ""
    try:
        if version.credentials.server_url:
            return str(version.credentials.server_url)
    except AgentVersion.credentials.RelatedObjectDoesNotExist:
        pass
    try:
        legacy = version.agent_definition.credentials_legacy
        if legacy and legacy.server_url:
            return str(legacy.server_url)
    except ProviderCredentials.DoesNotExist:
        pass
    return ""


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


def _soft_delete(ids: list[UUID]) -> tuple[int, int]:
    now = timezone.now()
    agents_deleted = AgentDefinition.objects.filter(id__in=ids).update(
        deleted=True, deleted_at=now, updated_at=now
    )
    versions_deleted = AgentVersion.objects.filter(agent_definition_id__in=ids).update(
        deleted=True, deleted_at=now, updated_at=now
    )
    return agents_deleted, versions_deleted


def delete_agent(organization, agent_id) -> tuple[int, int]:
    """Soft-delete one of the organization's agents and its versions. Refuses,
    deleting nothing, while an environment still tests it; the agent is locked so
    an environment can't bind to it in between."""
    from simulate.services.agent_overview import environment_counts
    from simulate.services.hosted_harness import HostedHarnessError

    with transaction.atomic():
        agent = (
            AgentDefinition.objects.select_for_update(of=("self",))
            .filter(id=agent_id, organization=organization, deleted=False)
            .only("id", "agent_name")
            .first()
        )
        if agent is None:
            return 0, 0
        total = environment_counts([agent.id]).get(agent.id, 0)
        if total:
            raise HostedHarnessError(
                "agent_in_use",
                f"{agent.agent_name} is tested by {total} environment"
                f"{'s' if total != 1 else ''}. Delete those environments first.",
                status_code=409,
            )
        return _soft_delete([agent.id])


def delete_agents(organization, agent_ids) -> tuple[int, int, list[UUID]]:
    """Soft-delete the organization's agents that no environment tests, with their
    versions, and skip the rest; returns the skipped ids in the order asked. The
    agents are locked so an environment can't bind to one in between."""
    from simulate.services.agent_overview import environment_counts

    with transaction.atomic():
        agents = list(
            AgentDefinition.objects.select_for_update(of=("self",))
            .filter(id__in=agent_ids, organization=organization, deleted=False)
            .only("id")
            .order_by("id")
        )
        counts = environment_counts([agent.id for agent in agents])
        found = {agent.id for agent in agents}
        skipped = [
            agent_id
            for agent_id in dict.fromkeys(agent_ids)
            if agent_id in found and counts.get(agent_id)
        ]
        ids = [agent.id for agent in agents if not counts.get(agent.id)]
        if not ids:
            return 0, 0, skipped
        agents_deleted, versions_deleted = _soft_delete(ids)
    return agents_deleted, versions_deleted, skipped
