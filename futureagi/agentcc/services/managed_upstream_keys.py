"""Per-org managed upstream keys: one upstream API key per org and provider.

Managed traffic runs on FutureAGI's shared upstream account unless an org has
its own key. OpenRouter provisioning keys give each org a
key with its own credit limit, so one org's traffic, rate limits and spend
stay its own.

Keys live in ``AgentccManagedUpstreamKey``, encrypted through
``CredentialManager``. Only ``assemble_for_push`` decrypts them, and only
``config_push`` calls it, while it builds a push payload: a key exists in what
is pushed to the gateway (``managed_models.upstream_keys``,
managed-upstream-keys-push-v1) and nowhere else, never in an org config, its
history, a read or a log.

Every write takes the org's config lock first, so two writers for one org run
one after the other, and once it commits it pushes the org's active config
under that lock again, so pushes stay in the order of the writes.

``provision`` and ``rotate_provisioned`` get the key from OpenRouter's
provisioning API through a client from ``openrouter_provisioning``, which
calls the live API only behind its three switches.
"""

from dataclasses import dataclass
from decimal import Decimal

import structlog
from django.db import transaction
from django.utils import timezone

from accounts.models import Organization, User
from agentcc.models.managed_upstream_key import AgentccManagedUpstreamKey
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services import config_push
from agentcc.services.credential_manager import (
    ENCRYPTED_PREFIX,
    decrypt_token,
    encrypt_token,
)
from agentcc.services.openrouter_provisioning import (
    OpenRouterProvisioningClient,
    ProvisionedKey,
    ProvisioningError,
)
from agentcc.services.org_config_writer import lock_org

logger = structlog.get_logger(__name__)

Status = AgentccManagedUpstreamKey.Status

OPENROUTER = "openrouter"

_MAX_LENGTHS = {
    "provider_id": AgentccManagedUpstreamKey._meta.get_field("provider_id").max_length,
    "key_hash": AgentccManagedUpstreamKey._meta.get_field("key_hash").max_length,
    "label": AgentccManagedUpstreamKey._meta.get_field("label").max_length,
}


class ManagedUpstreamKeyError(ValueError):
    """A managed upstream key write was refused. Messages never carry a key."""


class ManagedUpstreamKeyExists(ManagedUpstreamKeyError):
    def __init__(self, provider_id: str):
        self.provider_id = provider_id
        super().__init__(
            f"The org already has an active {provider_id} upstream key. "
            "Rotate or revoke it instead."
        )


class ManagedUpstreamKeyMissing(ManagedUpstreamKeyError):
    def __init__(self, provider_id: str):
        self.provider_id = provider_id
        super().__init__(f"The org has no active {provider_id} upstream key.")


class InvalidUpstreamKey(ManagedUpstreamKeyError):
    """A field of a key write is unusable. The message names the field only."""


class ProvisionedKeyNotStored(ManagedUpstreamKeyError):
    """A key was provisioned upstream but could not be stored."""


@dataclass
class KeyChange:
    """A key write and the gateway push that follows its commit.

    ``gateway_synced`` stays None until the push runs (inside an outer
    transaction it waits for that commit), and also when the org has no
    active config to push. ``replaced`` is the key a rotation disabled.
    """

    key: AgentccManagedUpstreamKey
    replaced: AgentccManagedUpstreamKey | None = None
    gateway_synced: bool | None = None


def set_key(
    org: Organization,
    provider_id: str,
    api_key: str,
    *,
    key_hash: str,
    label: str = "",
    actor: User | None = None,
    limit_usd: Decimal | None = None,
) -> KeyChange:
    """Store the org's first active key for ``provider_id``.

    Raises ``ManagedUpstreamKeyExists`` when it already has one.
    """
    _check_key_fields(provider_id, api_key, key_hash, label)
    with transaction.atomic():
        lock_org(org.pk)
        if active_key(org, provider_id) is not None:
            raise ManagedUpstreamKeyExists(provider_id)
        change = KeyChange(
            _create_key(org, provider_id, api_key, key_hash, label, actor, limit_usd)
        )
        _push_on_commit(org.pk, change)
    _log_write("managed_upstream_key_set", change.key, actor)
    return change


def rotate(
    org: Organization,
    provider_id: str,
    new_key: str,
    *,
    key_hash: str,
    actor: User | None = None,
    label: str | None = None,
    limit_usd: Decimal | None = None,
) -> KeyChange:
    """Replace the org's active key for ``provider_id`` with ``new_key``.

    The old row is kept, disabled, so its ``key_hash`` stays available for
    reconciling the old key's usage. The new row keeps the old label and
    limit unless they are given. Raises ``ManagedUpstreamKeyMissing`` when the
    org has no active key to rotate.
    """
    _check_key_fields(provider_id, new_key, key_hash, label or "")
    with transaction.atomic():
        lock_org(org.pk)
        current = active_key(org, provider_id, for_update=True)
        if current is None:
            raise ManagedUpstreamKeyMissing(provider_id)
        if current.key_hash == key_hash:
            raise InvalidUpstreamKey(
                "key_hash is the current key's: rotate to a new key"
            )
        current.status = Status.DISABLED
        current.rotated_at = timezone.now()
        current.save(update_fields=["status", "rotated_at"])
        key = _create_key(
            org,
            provider_id,
            new_key,
            key_hash,
            current.label if label is None else label,
            actor,
            current.limit_usd if limit_usd is None else limit_usd,
        )
        change = KeyChange(key, replaced=current)
        _push_on_commit(org.pk, change)
    _log_write("managed_upstream_key_rotated", key, actor, replaced=current.key_hash)
    return change


def revoke(
    org: Organization, provider_id: str, *, actor: User | None = None
) -> KeyChange:
    """Revoke the org's active key for ``provider_id``; the push drops it.

    Raises ``ManagedUpstreamKeyMissing`` when the org has no active key.
    """
    with transaction.atomic():
        lock_org(org.pk)
        current = active_key(org, provider_id, for_update=True)
        if current is None:
            raise ManagedUpstreamKeyMissing(provider_id)
        current.status = Status.REVOKED
        current.save(update_fields=["status"])
        change = KeyChange(current)
        _push_on_commit(org.pk, change)
    _log_write("managed_upstream_key_revoked", current, actor)
    return change


def provisioned_key_name(org: Organization) -> str:
    """The name an org's provisioned key carries upstream."""
    return f"futureagi-org-{org.pk}"


def provision(
    org: Organization,
    client: OpenRouterProvisioningClient,
    *,
    limit_usd: Decimal | None = None,
    label: str | None = None,
    actor: User | None = None,
) -> KeyChange:
    """Create the org's OpenRouter key through ``client`` and store it.

    Refused before any upstream call when the org already has an active key.
    A key that is created but cannot be stored is deleted upstream again.
    """
    if active_key(org, OPENROUTER) is not None:
        raise ManagedUpstreamKeyExists(OPENROUTER)
    name = provisioned_key_name(org)
    created = client.create_key(name, limit_usd)
    try:
        return set_key(
            org,
            OPENROUTER,
            created.key,
            key_hash=created.key_hash,
            label=name if label is None else label,
            actor=actor,
            limit_usd=created.limit_usd,
        )
    except Exception as e:
        _discard_unstored(client, created, e)
        raise


def rotate_provisioned(
    org: Organization,
    client: OpenRouterProvisioningClient,
    *,
    limit_usd: Decimal | None = None,
    label: str | None = None,
    actor: User | None = None,
) -> KeyChange:
    """Replace the org's active OpenRouter key with one newly provisioned
    through ``client``, with the old key's limit unless ``limit_usd`` is
    given. A new key that cannot be stored is deleted upstream again."""
    current = active_key(org, OPENROUTER)
    if current is None:
        raise ManagedUpstreamKeyMissing(OPENROUTER)
    created = client.create_key(
        provisioned_key_name(org),
        current.limit_usd if limit_usd is None else limit_usd,
    )
    try:
        return rotate(
            org,
            OPENROUTER,
            created.key,
            key_hash=created.key_hash,
            label=label,
            actor=actor,
            limit_usd=created.limit_usd,
        )
    except Exception as e:
        _discard_unstored(client, created, e)
        raise


def active_key(
    org: Organization, provider_id: str, *, for_update: bool = False
) -> AgentccManagedUpstreamKey | None:
    """The org's active key row for ``provider_id``, or None."""
    rows = AgentccManagedUpstreamKey.objects.filter(
        organization=org, provider_id=provider_id, status=Status.ACTIVE
    )
    if for_update:
        rows = rows.select_for_update()
    return rows.first()


def keys_for_org(org: Organization) -> list[AgentccManagedUpstreamKey]:
    """Every key row of the org, newest first per provider; for display, so
    the encrypted value is not loaded."""
    return list(
        AgentccManagedUpstreamKey.objects.filter(organization=org)
        .defer("encrypted_key")
        .order_by("provider_id", "-created_at")
    )


def assemble_for_push(org_id) -> dict[str, dict[str, str]]:
    """The org's active keys as ``{provider_id: {api_key, key_hash, label}}``.

    Called only by ``config_push`` while it builds a push payload, never at
    save time. A row that cannot be decrypted is left out and logged by
    provider id, key hash and error type, so one bad row does not drop the
    org's other keys.
    """
    rows = AgentccManagedUpstreamKey.objects.filter(
        organization_id=org_id, status=Status.ACTIVE
    ).order_by("provider_id")
    keys = {}
    for row in rows:
        api_key = _decrypted_key(row)
        if api_key:
            keys[row.provider_id] = {
                "api_key": api_key,
                "key_hash": row.key_hash,
                "label": row.label,
            }
    return keys


def push_active_config(org_id) -> bool | None:
    """Push the org's active config under the org lock.

    Returns whether the gateway took it, or None when the org has no active
    config yet (its first config push will carry the keys).
    """
    try:
        with transaction.atomic():
            lock_org(org_id)
            config = AgentccOrgConfig.no_workspace_objects.filter(
                organization_id=org_id, is_active=True, deleted=False
            ).first()
            if config is None:
                logger.info(
                    "managed_upstream_keys_push_skipped",
                    org_id=str(org_id),
                    reason="no active config",
                )
                return None
            return config_push.push_org_config(str(org_id), config)
    except Exception:
        # Fail open, as every config push does: the key change is committed,
        # and the gateway picks it up on its next config sync. Only the lock
        # and the config read run here; push_org_config handles its own
        # errors, so no key is in this exception.
        logger.exception("managed_upstream_keys_push_failed", org_id=str(org_id))
        return False


def _discard_unstored(
    client: OpenRouterProvisioningClient, created: ProvisionedKey, error: Exception
) -> None:
    """Delete a provisioned key that was not stored, so no live key is left
    that nothing tracks. Raises ``ProvisionedKeyNotStored`` for a refused
    write; any other ``error`` is re-raised by the caller."""
    try:
        client.delete_key(created.key_hash)
    except ProvisioningError as e:
        logger.warning(
            "managed_upstream_key_orphaned", key_hash=created.key_hash, error=str(e)
        )
        outcome = (
            f"Deleting the new upstream key failed ({e}): delete key "
            f"{created.key_hash} at OpenRouter."
        )
    else:
        outcome = f"The new upstream key {created.key_hash} was deleted."
    if isinstance(error, ManagedUpstreamKeyError):
        raise ProvisionedKeyNotStored(f"{error} {outcome}") from error


def _push_on_commit(org_id, change: KeyChange) -> None:
    def push():
        change.gateway_synced = push_active_config(org_id)

    transaction.on_commit(push)


def _create_key(org, provider_id, api_key, key_hash, label, actor, limit_usd):
    return AgentccManagedUpstreamKey.objects.create(
        organization=org,
        provider_id=provider_id,
        encrypted_key=encrypt_token(api_key),
        key_hash=key_hash,
        label=label,
        limit_usd=limit_usd,
        created_by=actor,
    )


def _check_key_fields(provider_id, api_key, key_hash, label) -> None:
    for field, value in (("provider_id", provider_id), ("key_hash", key_hash)):
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise InvalidUpstreamKey(
                f"{field} must be a non-empty string without surrounding spaces"
            )
    if not isinstance(label, str):
        raise InvalidUpstreamKey("label must be a string")
    for field, value in (
        ("provider_id", provider_id),
        ("key_hash", key_hash),
        ("label", label),
    ):
        if len(value) > _MAX_LENGTHS[field]:
            raise InvalidUpstreamKey(
                f"{field} is longer than {_MAX_LENGTHS[field]} characters"
            )
    if not isinstance(api_key, str) or not api_key or api_key != api_key.strip():
        raise InvalidUpstreamKey(
            "the API key must be a non-empty string without surrounding spaces"
        )
    if api_key.startswith(ENCRYPTED_PREFIX):
        # A key write takes the plain key only: a stored key is always
        # ciphertext made here.
        raise InvalidUpstreamKey(
            f"the API key must be the plain key, not {ENCRYPTED_PREFIX} ciphertext"
        )


def _decrypted_key(row) -> str | None:
    if not row.encrypted_key.startswith(ENCRYPTED_PREFIX):
        # The model refuses such a row; one written around it is never pushed.
        _log_unreadable(row, "NotEncrypted")
        return None
    try:
        api_key = decrypt_token(row.encrypted_key)
    except Exception as e:
        # Any failure drops this row only: the org's other keys still push,
        # and the gateway fails closed for this provider in per_org mode.
        # Logged by type, as config_push does, never by message.
        _log_unreadable(row, type(e).__name__)
        return None
    if not isinstance(api_key, str) or not api_key:
        _log_unreadable(row, "EmptyKey")
        return None
    return api_key


def _log_unreadable(row, error_type: str) -> None:
    logger.warning(
        "managed_upstream_key_unreadable",
        org_id=str(row.organization_id),
        provider_id=row.provider_id,
        key_hash=row.key_hash,
        error_type=error_type,
    )


def _log_write(event: str, key, actor, **extra) -> None:
    logger.info(
        event,
        org_id=str(key.organization_id),
        provider_id=key.provider_id,
        key_hash=key.key_hash,
        status=key.status,
        actor_id=str(actor.pk) if actor is not None else None,
        **extra,
    )
