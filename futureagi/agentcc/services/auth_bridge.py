import hashlib
from typing import TYPE_CHECKING

import structlog
from django.db.models import Q, QuerySet
from django.utils import timezone

from agentcc.models import AgentccAPIKey
from agentcc.services.gateway_client import (
    GatewayClientError,
    _stringify_metadata,
    get_gateway_client,
)
from agentcc.validators import RESERVED_KEY_METADATA

if TYPE_CHECKING:
    from accounts.models import Organization

logger = structlog.get_logger(__name__)


class GatewayKeyIdCollision(Exception):
    """The gateway returned a key ID that already belongs to another key."""


def _live_keys(org: "Organization | None" = None) -> QuerySet[AgentccAPIKey]:
    """Active, not deleted and not expired; the org's only, when given."""
    qs = AgentccAPIKey.no_workspace_objects.filter(
        status=AgentccAPIKey.ACTIVE,
        deleted=False,
    ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))
    if org:
        qs = qs.filter(organization=org)
    return qs


def gateway_loadable_keys(
    org: "Organization | None" = None,
) -> QuerySet[AgentccAPIKey]:
    """The keys the gateway should hold: live ones with a stored hash, since
    the gateway loads keys by hash. The gateway's startup pull and Sync's push
    both send exactly these, so a key Sync pushes is one the next pull keeps.
    Expired keys are left out even for gateways that predate expiry
    enforcement."""
    return _live_keys(org).exclude(key_hash="")


def bound_key_metadata(metadata, organization_id):
    """Key metadata as the gateway may hold it, bound to ``organization_id``.

    org_id is always the key's own organization, never a stored or supplied
    value, and the other reserved entries are dropped: the gateway reads them
    as authority (see ``RESERVED_KEY_METADATA``), so none of them comes from
    stored or supplied metadata.
    """
    bound = {
        key: value
        for key, value in (metadata if isinstance(metadata, dict) else {}).items()
        if key not in RESERVED_KEY_METADATA
    }
    if organization_id:
        bound["org_id"] = str(organization_id)
    return bound


def gateway_key_payload(key):
    """A stored key as the gateway loads it by hash (its ``SyncedKey``).

    Startup sync pulls these from the bulk endpoint and Sync pushes them; the
    raw key never leaves Django.
    """
    metadata = _stringify_metadata(
        bound_key_metadata(key.metadata, key.organization_id)
    )
    return {
        "id": key.gateway_key_id,
        "name": key.name,
        "owner": key.owner,
        "key_hash": key.key_hash,
        "key_prefix": key.key_prefix,
        "models": key.allowed_models or [],
        "providers": key.allowed_providers or [],
        "metadata": metadata,
        "expires_at": key.expires_at.isoformat() if key.expires_at else None,
    }


def _is_same_key(local_key, key_hash="", key_prefix=""):
    """Whether a stored row is the key the gateway describes under its ID.

    Gateways before random key IDs reissued IDs after a restart, so a shared
    ID alone does not make two keys the same. The hash settles it when both
    sides have one; otherwise the display prefix, a weak but free signal.
    """
    if local_key.key_hash and key_hash:
        return local_key.key_hash == key_hash
    if local_key.key_prefix and key_prefix:
        return local_key.key_prefix == key_prefix
    return True


def provision_key(
    name,
    owner="",
    user=None,
    project=None,
    models=None,
    providers=None,
    metadata=None,
    organization=None,
):
    """
    Create an API key on the Go gateway and store the reference locally.
    Returns (AgentccAPIKey, raw_key_string).
    """
    org = organization or (user.organization if user else None)

    gw_metadata = bound_key_metadata(metadata, org.id if org else None)

    client = get_gateway_client()
    result = client.create_key(
        name=name,
        owner=owner,
        models=models,
        providers=providers,
        metadata=gw_metadata,
    )

    raw_key = result.get("key", "")

    try:
        computed_hash = hashlib.sha256(raw_key.encode()).hexdigest() if raw_key else ""

        # Never store a new key over another key's row. A row for this same key
        # can exist: a Sync that raced this create stores it without a hash.
        for existing in AgentccAPIKey.no_workspace_objects.filter(
            gateway_key_id=result["id"]
        ):
            if not _is_same_key(
                existing,
                key_hash=computed_hash,
                key_prefix=result.get("key_prefix", ""),
            ):
                raise GatewayKeyIdCollision(
                    f"The gateway issued key id {result['id']!r}, which already "
                    "belongs to another API key; the new key was discarded. "
                    "Upgrade the gateway so it issues unique key ids, then try again."
                )

        api_key, created = AgentccAPIKey.no_workspace_objects.update_or_create(
            gateway_key_id=result["id"],
            defaults={
                "project": project,
                "user": user,
                "organization": org,
                "workspace": None,
                "key_prefix": result.get("key_prefix", ""),
                "key_hash": computed_hash,
                "name": name,
                "owner": owner,
                "status": AgentccAPIKey.ACTIVE,
                "allowed_models": result.get("models") or [],
                "allowed_providers": result.get("providers") or [],
                "metadata": gw_metadata,
                "deleted": False,
            },
        )
    except Exception:
        # Compensate: remove orphan key from Go gateway
        try:
            client.revoke_key(result["id"])
        except Exception:
            logger.error("failed_to_rollback_gateway_key", key_id=result["id"])
        raise

    return api_key, raw_key


def update_key(api_key, **kwargs):
    """
    Update an API key on the Go gateway and update the local record.
    Accepted kwargs: name, owner, models, providers, metadata.
    """
    if "metadata" in kwargs:
        kwargs["metadata"] = bound_key_metadata(
            kwargs["metadata"], api_key.organization_id
        )
    client = get_gateway_client()
    gw_kwargs = {}
    field_map = {
        "name": "name",
        "owner": "owner",
        "allowed_models": "models",
        "allowed_providers": "providers",
        "metadata": "metadata",
    }
    for local_field, gw_field in field_map.items():
        if local_field in kwargs:
            gw_kwargs[gw_field] = kwargs[local_field]

    if gw_kwargs:
        client.update_key(api_key.gateway_key_id, **gw_kwargs)

    # Update local DB fields
    update_fields = ["updated_at"]
    for field in ("name", "owner", "allowed_models", "allowed_providers", "metadata"):
        if field in kwargs:
            setattr(api_key, field, kwargs[field])
            update_fields.append(field)

    api_key.save(update_fields=update_fields)
    return api_key


def revoke_key(api_key):
    """
    Revoke an API key on the Go gateway and mark it locally as revoked.

    Django is always marked REVOKED (security-first). If the gateway is
    unreachable, ``gateway_failed`` is returned as True so the caller
    can surface a warning to the user.

    Returns (AgentccAPIKey, gateway_failed: bool).
    """
    gateway_failed = False
    try:
        client = get_gateway_client()
        client.revoke_key(api_key.gateway_key_id)
    except GatewayClientError:
        logger.warning(
            "gateway_unreachable_on_revoke",
            key_id=api_key.gateway_key_id,
        )
        gateway_failed = True

    api_key.status = AgentccAPIKey.REVOKED
    api_key.save(update_fields=["status", "updated_at"])
    return api_key, gateway_failed


def delete_key(api_key):
    """Revoke an API key on the gateway, then soft-delete it here.

    The row is marked REVOKED first, so from then on no key sync hands the key
    back to the gateway. Then the gateway revokes it, so it stops working on
    its next request rather than at the gateway's next key sync. A 404 means
    the gateway does not hold the key, which leaves nothing to revoke there.

    Any other gateway failure is raised, and the row stays REVOKED but not
    deleted: the caller reports it, the gateway drops the key at its next key
    sync or restart, ``sync_keys`` revokes it there, and deleting it again
    retries the revoke.
    """
    if api_key.status != AgentccAPIKey.REVOKED:
        api_key.status = AgentccAPIKey.REVOKED
        api_key.save(update_fields=["status", "updated_at"])

    if api_key.gateway_key_id:
        try:
            get_gateway_client().revoke_key(api_key.gateway_key_id)
        except GatewayClientError as e:
            if e.status_code != 404:
                logger.warning(
                    "gateway_revoke_failed_on_delete",
                    key_id=api_key.gateway_key_id,
                    status_code=e.status_code,
                    error=str(e),
                )
                raise

    api_key.delete()
    return api_key


def sync_keys(org=None):
    """
    Bidirectional sync between Go gateway and Django DB.
    - Keys on gateway but not in Django → create local record
    - Keys in Django but not on gateway → push them to the gateway (by hash)
    - Keys revoked or deleted in Django but active on gateway → revoke on the
      gateway
    Django is the source of truth; the gateway is ephemeral.

    Only syncs keys belonging to the given org (matched via metadata.org_id).
    Returns how many of the org's keys the gateway holds afterwards.
    """
    client = get_gateway_client()
    result = client.list_keys()
    gateway_keys = result.get("data", [])

    org_id_str = str(org.id) if org else None
    gateway_key_ids = set()
    org_key_count = 0

    # Pre-filter to org keys and collect their IDs
    org_gateway_keys = []
    for gk in gateway_keys:
        gateway_key_id = gk.get("id", "")
        if not gateway_key_id:
            continue
        gw_metadata = gk.get("metadata") or {}
        gw_org_id = gw_metadata.get("org_id", "")
        if org_id_str and gw_org_id != org_id_str:
            continue
        org_gateway_keys.append(gk)

    # Bulk-fetch all local keys in one query instead of N+1
    all_gw_ids = [gk["id"] for gk in org_gateway_keys if gk.get("id")]
    existing_keys = {
        k.gateway_key_id: k
        for k in AgentccAPIKey.no_workspace_objects.filter(
            gateway_key_id__in=all_gw_ids,
        )
    }
    # Keys deleted here that a gateway still holds are revoked there, never
    # adopted as gateway-only keys. Matched on ID and display prefix. The base
    # manager, because the others hide deleted rows and filter by workspace.
    deleted_keys = set(
        AgentccAPIKey._base_manager.filter(
            gateway_key_id__in=set(all_gw_ids) - set(existing_keys),
            deleted=True,
        )
        .exclude(key_prefix="")
        .values_list("gateway_key_id", "key_prefix")
    )

    for gk in org_gateway_keys:
        gateway_key_id = gk.get("id", "")
        if (gateway_key_id, gk.get("key_prefix", "")) in deleted_keys:
            if gk.get("status", "active") != "revoked":
                client.revoke_key(gateway_key_id)
                logger.warning(
                    "sync_keys_revoked_deleted_key", gateway_key_id=gateway_key_id
                )
            continue
        gateway_key_ids.add(gateway_key_id)
        org_key_count += 1

        local_key = existing_keys.get(gateway_key_id)

        if local_key and not _is_same_key(
            local_key, key_prefix=gk.get("key_prefix", "")
        ):
            # An ID reissued by an older gateway: the gateway key is a different
            # key, and the stored one is lost from the gateway under this ID.
            logger.warning(
                "sync_keys_id_collision",
                gateway_key_id=gateway_key_id,
                hint="revoke the stored key and create a new one",
            )
            continue

        if local_key:
            gw_status = gk.get("status", "active")
            update_fields = []
            # Django is source of truth. Only sync Go→Django if the
            # gateway revoked a key that Django still considers active
            # (gateway-initiated revoke). Never un-revoke: if Django
            # says REVOKED, keep it REVOKED regardless of Go state.
            if local_key.status == AgentccAPIKey.ACTIVE and gw_status == "revoked":
                local_key.status = AgentccAPIKey.REVOKED
                update_fields.append("status")
            elif local_key.status == AgentccAPIKey.REVOKED and gw_status == "active":
                # A revoke the gateway missed (it was unreachable at the time).
                client.revoke_key(gateway_key_id)
            if org and local_key.organization_id != org.id:
                # Django owns a key's org: the row stays with its own org, and
                # the gateway gets the row's org back at its next key sync.
                logger.warning(
                    "sync_keys_org_mismatch",
                    gateway_key_id=gateway_key_id,
                    hint="run manage.py agentcc_check_key_org",
                )
            if update_fields:
                update_fields.append("updated_at")
                local_key.save(update_fields=update_fields)
        else:
            gw_org = org
            gk_org_id = (gk.get("metadata") or {}).get("org_id", "")
            if not gw_org and gk_org_id:
                from accounts.models import Organization

                gw_org = Organization.objects.filter(id=gk_org_id).first()

            if not gw_org:
                logger.warning(
                    "sync_keys_skip_no_org",
                    gateway_key_id=gateway_key_id,
                )
                continue  # Skip — can't create without org

            AgentccAPIKey.no_workspace_objects.update_or_create(
                gateway_key_id=gateway_key_id,
                defaults={
                    "organization": gw_org,
                    "key_prefix": gk.get("key_prefix", ""),
                    "name": gk.get("name", ""),
                    "owner": gk.get("owner", ""),
                    "status": (
                        AgentccAPIKey.ACTIVE
                        if gk.get("status") != "revoked"
                        else AgentccAPIKey.REVOKED
                    ),
                    "allowed_models": gk.get("models") or [],
                    "allowed_providers": gk.get("providers") or [],
                    "metadata": gk.get("metadata") or {},
                    "deleted": False,
                },
            )

    return org_key_count + _restore_missing_keys(client, org, gateway_key_ids)


def _restore_missing_keys(client, org, gateway_key_ids):
    """Push the keys the gateway should hold but lacks (e.g. after a restart
    without startup sync) and return how many it loaded."""
    payload = [
        gateway_key_payload(key)
        for key in gateway_loadable_keys(org).exclude(
            gateway_key_id__in=gateway_key_ids
        )
    ]
    unrecoverable = (
        _live_keys(org)
        .filter(key_hash="")
        .exclude(gateway_key_id__in=gateway_key_ids)
        .count()
    )

    if unrecoverable:
        logger.warning(
            "sync_keys_unrecoverable",
            count=unrecoverable,
            hint="no key hash is stored for these keys; revoke them and create new ones",
        )
    if not payload:
        return 0

    try:
        loaded = client.import_keys(payload).get("loaded", 0)
    except GatewayClientError as e:
        # A gateway older than POST /-/keys/sync cannot take keys back. Sync
        # still did the rest, so report what it could not restore.
        if e.status_code not in (404, 405):
            raise
        logger.warning(
            "sync_keys_missing_from_gateway",
            missing=len(payload),
            hint="this gateway cannot import keys: upgrade it, or restart it to trigger startup sync",
        )
        return 0
    logger.info("sync_keys_restored", pushed=len(payload), loaded=loaded)
    return loaded
