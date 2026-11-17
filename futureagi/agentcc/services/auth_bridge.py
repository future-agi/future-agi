import hashlib
from typing import TYPE_CHECKING

import structlog
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Exists, OuterRef, Q, QuerySet
from django.utils import timezone

from agentcc.models import AgentccAPIKey
from agentcc.models.api_key_ownership import IMMUTABLE_MESSAGE, reject_ownership_fields
from agentcc.services import ee_agent_key_policy, mcp_personal_keys
from agentcc.services.gateway_client import (
    GatewayClientError,
    _stringify_metadata,
    get_gateway_client,
)
from agentcc.services.gateway_membership import live_gateway_memberships
from agentcc.validators import RESERVED_KEY_METADATA

if TYPE_CHECKING:
    from accounts.models import Organization

logger = structlog.get_logger(__name__)


class GatewayKeyIdCollision(Exception):
    """The gateway returned a key ID that already belongs to another key."""


# Gateway keys are "sk-agentcc-" and 24 random bytes as 48 hex characters.
GATEWAY_KEY_SCHEME = "sk-agentcc-"
# Characters of the random part shown from each end of a key.
SHOWN_KEY_CHARACTERS = 4
# A random part shorter than this is not a gateway key: keep the gateway's own
# prefix rather than show a third of a short secret.
_MIN_RANDOM_PART = 32
_ELLIPSIS = "\u2026"
_LEGACY_ELLIPSIS = "..."


def display_key_prefix(raw_key, fallback=""):
    """How a key reads in the key list: ``sk-agentcc-4f2a…9c1d``.

    The first and last four characters of the key's random part, as Future
    AGI's own key list shows its keys, so two keys read apart; the other 40
    stay secret. The gateway's own prefix cuts every key at 12 characters
    (``sk-agentcc-4...``), so keys whose first random character matched read
    alike. A key of any other form keeps ``fallback`` (the gateway's prefix).
    The result is 20 characters, the stored column's length.
    """
    raw_key = raw_key or ""
    if not raw_key.startswith(GATEWAY_KEY_SCHEME):
        return fallback
    body = raw_key[len(GATEWAY_KEY_SCHEME) :]
    if len(body) < _MIN_RANDOM_PART:
        return fallback
    n = SHOWN_KEY_CHARACTERS
    return f"{GATEWAY_KEY_SCHEME}{body[:n]}{_ELLIPSIS}{body[-n:]}"


def _prefix_parts(prefix):
    """A shortened prefix's (head, tail) around its ellipsis; None if whole."""
    for mark in (_ELLIPSIS, _LEGACY_ELLIPSIS):
        if mark in prefix:
            head, _, tail = prefix.partition(mark)
            return head, tail
    return None


def prefixes_agree(a, b):
    """Whether two display prefixes can describe the same key.

    One key reads ``sk-agentcc-4...`` on a gateway that predates
    ``display_key_prefix`` and ``sk-agentcc-4f2a…9c1d`` in Django, since the
    two deploy separately. They agree when one head starts the other and the
    tails, where both show one, match. Prefixes with no ellipsis must be equal.
    """
    if a == b:
        return True
    parts_a, parts_b = _prefix_parts(a or ""), _prefix_parts(b or "")
    if parts_a is None or parts_b is None:
        return False
    (head_a, tail_a), (head_b, tail_b) = parts_a, parts_b
    if not (head_a.startswith(head_b) or head_b.startswith(head_a)):
        return False
    return not (tail_a and tail_b) or tail_a == tail_b


def _live_keys(
    org: "Organization | None" = None, *, status=AgentccAPIKey.ACTIVE,
) -> QuerySet[AgentccAPIKey]:
    """Personal keys in this state with live authority and unexpired lifetime."""
    membership = live_gateway_memberships().filter(
        organization_id=OuterRef("organization_id"),
        user_id=OuterRef("user_id"),
    )
    qs = (
        AgentccAPIKey.no_workspace_objects.using("default")
        .filter(
            status=status,
            deleted=False,
            user__isnull=False,
            user__is_active=True,
        )
        .filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()),
            Exists(membership),
        )
    )
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
    enforcement. Each is loaded with its EE agent-key binding, from which
    the payload derives an agent key's scope and budget."""
    from agentcc.services.ee_provisioning_availability import provisioning_enabled

    keys = _live_keys(org).exclude(key_hash="").select_related("ee_agent_key")
    if not provisioning_enabled():
        keys = keys.filter(ee_agent_key__isnull=True)
    return keys


def gateway_inflight_keys() -> QuerySet[AgentccAPIKey]:
    """Nonterminal personal issuance, not a bulk retirement or positive import.

    Apply the same membership, owner, expiry and deletion checks as ACTIVE
    keys. Only personal issuance creates PENDING: an agent-bound row is not
    eligible for this exception. FAILED and authority loss remain negative.
    """
    return _live_keys(status=AgentccAPIKey.PENDING).filter(
        ee_agent_key__isnull=True,
    ).exclude(key_hash="")


def bound_key_metadata(metadata, organization_id):
    """Key metadata as the gateway may hold it, bound to ``organization_id``.

    org_id is always the key's own organization, never a stored or supplied
    value, and the other reserved entries are dropped: the gateway reads them
    as authority (see ``RESERVED_KEY_METADATA``), so none may come from a
    tenant, including metadata stored before they were rejected.
    """
    bound = {
        key: value
        for key, value in (metadata if isinstance(metadata, dict) else {}).items()
        if key not in RESERVED_KEY_METADATA
    }
    if organization_id:
        bound["org_id"] = str(organization_id)
    return bound


def gateway_key_payload(key, server_metadata=None):
    """A stored key as the gateway loads it by hash (its ``SyncedKey``).

    Startup sync pulls these from the bulk endpoint and Sync pushes them; the
    raw key never leaves Django. ``server_metadata`` is the reserved entries
    Django derives for the key from records no tenant can write
    (``server_owned_metadata``), added after the stored
    metadata is bound: the gateway rebuilds a synced key from this payload,
    so they must ride on every one. None reads them for this key alone;
    gateway_key_payloads reads them for many keys at once.
    """
    if server_metadata is None:
        server_metadata = server_owned_metadata([key]).get(key.pk, {})
    metadata = _stringify_metadata(
        {
            **bound_key_metadata(key.metadata, key.organization_id),
            **server_metadata,
            "org_id": str(key.organization_id),
            "user_id": str(key.user_id) if key.user_id else "",
        }
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


def gateway_key_payloads(keys):
    """gateway_key_payload for each of ``keys`` (gateway_loadable_keys rows),
    with one query for every EE agent key's workspace budget."""
    keys = list(keys)
    derived = server_owned_metadata(keys)
    return [gateway_key_payload(key, derived.get(key.pk, {})) for key in keys]


def server_owned_metadata(keys) -> dict:
    """{key pk: reserved entries} Django derives for ``keys`` from records no
    tenant can write: an EE agent key's scope and budget, and a personal
    key's ``mcp_personal`` (its own user's id, M25x)."""
    derived = ee_agent_key_policy.payload_metadata(keys)
    for pk, entries in mcp_personal_keys.payload_metadata(keys).items():
        derived[pk] = {**derived.get(pk, {}), **entries}
    return derived


def _is_same_key(local_key, key_hash="", key_prefix=""):
    """Whether a stored row is the key the gateway describes under its ID.

    Gateways before random key IDs reissued IDs after a restart, so a shared
    ID alone does not make two keys the same. The hash settles it when both
    sides have one; otherwise the display prefix, a weak but free signal.
    """
    if local_key.key_hash and key_hash:
        return local_key.key_hash == key_hash
    if local_key.key_prefix and key_prefix:
        return prefixes_agree(local_key.key_prefix, key_prefix)
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
    server_metadata=None,
    expires_at=None,
):
    """
    Create an API key on the Go gateway and store the reference locally.
    Returns (AgentccAPIKey, raw_key_string).

    ``server_metadata`` is reserved entries the platform sets on the key it
    mints (an EE agent key's scope and budget): sent to the gateway with the
    key, never stored on the row, so a tenant's edit of the row cannot carry
    them. ``expires_at`` is stored on the row; the gateway learns it at its
    next key sync.
    """
    org = organization or (user.organization if user else None)
    if (
        not user
        or not org
        or not user.is_active
        or not live_gateway_memberships().filter(user=user, organization=org).exists()
    ):
        raise ValueError(
            "A gateway key requires a person with live organization membership"
        )

    gw_metadata = bound_key_metadata(metadata, org.id)

    client = get_gateway_client()
    result = client.create_key(
        name=name,
        owner=owner,
        models=models,
        providers=providers,
        metadata={
            **gw_metadata,
            **(server_metadata or {}),
            "org_id": str(org.pk),
            "user_id": str(user.pk),
        },
    )

    raw_key = result.get("key", "")

    try:
        computed_hash = hashlib.sha256(raw_key.encode()).hexdigest() if raw_key else ""
        key_prefix = display_key_prefix(raw_key, fallback=result.get("key_prefix", ""))

        defaults = {
            "project": project,
            "user": user,
            "organization": org,
            "workspace": None,
            "key_prefix": key_prefix,
            "key_hash": computed_hash,
            "name": name,
            "owner": owner,
            "status": AgentccAPIKey.ACTIVE,
            "allowed_models": result.get("models") or [],
            "allowed_providers": result.get("providers") or [],
            "metadata": gw_metadata,
            "deleted": False,
        }
        if expires_at is not None:
            defaults["expires_at"] = expires_at
        keys = AgentccAPIKey.no_workspace_objects.select_for_update()
        with transaction.atomic(using=keys.db):
            api_key, created = keys.get_or_create(
                gateway_key_id=result["id"], defaults=defaults
            )
            if not created:
                if not _is_same_key(
                    api_key, key_hash=computed_hash, key_prefix=key_prefix
                ):
                    raise GatewayKeyIdCollision(
                        f"The gateway issued key id {result['id']!r}, which already "
                        "belongs to another API key; the new key was discarded. "
                        "Upgrade the gateway so it issues unique key ids, then try again."
                    )
                if user is None or user.pk is None or not computed_hash:
                    raise ValidationError({"user": IMMUTABLE_MESSAGE})
                # Sole first-assignment exception: a sync raced THIS create_key.
                # Do not expose an opt-out on model.save or the guarded queryset.
                # Bypass its blanket refusal only here, with every precondition
                # in the UPDATE predicate, so a completed/different row cannot
                # be adopted even if the lookup's snapshot is stale.
                unfinished = keys.filter(
                    pk=api_key.pk,
                    gateway_key_id=result["id"],
                    user__isnull=True,
                    key_hash="",
                )
                if (
                    QuerySet.update(unfinished, **defaults, updated_at=timezone.now())
                    != 1
                ):
                    raise ValidationError({"user": IMMUTABLE_MESSAGE})
                api_key.refresh_from_db(using=keys.db)
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
    # Refuse, rather than silently discard, authority writes before touching
    # the gateway. Also reject callers passing an already-rebound instance.
    reject_ownership_fields(kwargs)
    api_key.assert_ownership_unchanged()
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
    api_key.status = AgentccAPIKey.REVOKED
    api_key.save(update_fields=["status", "updated_at"])
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

    return api_key, gateway_failed


def revoke_keys_with_readback(keys):
    """Revoke locally even on a failed push; never report confirmation without GET.

    An absent key in a successful complete GET /-/keys list is also confirmed:
    a negative sync may have removed it before readback. Unknown/unreadable state
    is pending, not success. No raw key or hash is returned in the report.
    """
    ids = []
    for key in keys:
        revoke_key(key)
        ids.append(key.gateway_key_id)
    if not ids:
        return {"revoked": [], "pending": [], "message": ""}
    try:
        body = get_gateway_client().list_keys()
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list) or any(
            not isinstance(row, dict)
            or not isinstance(row.get("id"), str)
            or not row["id"]
            or row.get("status") not in ("active", "revoked", "expired")
            for row in data
        ):
            raise ValueError("Incomplete gateway key readback")
        states = {row["id"]: row["status"] for row in data}
        if len(states) != len(data):
            raise ValueError("Ambiguous gateway key readback")
        pending = [
            key_id for key_id in ids if states.get(key_id, "revoked") != "revoked"
        ]
    except (GatewayClientError, ValueError, TypeError):
        pending = ids
    return {
        "revoked": [key_id for key_id in ids if key_id not in pending],
        "pending": pending,
        "message": "gateway revocation pending" if pending else "",
    }


def revoke_person_keys(user, org):
    """Membership-removal hook; retired on the next pull repairs failed pushes."""
    keys = AgentccAPIKey._base_manager.filter(user=user, organization=org)
    return revoke_keys_with_readback(keys)


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
    # Keys deleted here whose gateway copy remains: delete did not revoke on the
    # gateway before delete_key, and a gateway may still hold such a key. They
    # are deleted, not gateway-only keys to adopt. Matched on ID and display
    # prefix, since an older gateway could reissue a deleted key's ID to a new
    # key. The base manager, because the others hide deleted rows and filter by
    # workspace.
    deleted_prefixes = {}
    for deleted_id, deleted_prefix in (
        AgentccAPIKey._base_manager.filter(
            gateway_key_id__in=set(all_gw_ids) - set(existing_keys),
            deleted=True,
        )
        .exclude(key_prefix="")
        .values_list("gateway_key_id", "key_prefix")
    ):
        deleted_prefixes.setdefault(deleted_id, []).append(deleted_prefix)

    eligible_ids = set(_live_keys(org).values_list("pk", flat=True))
    for gk in org_gateway_keys:
        gateway_key_id = gk.get("id", "")
        gateway_prefix = gk.get("key_prefix", "")
        if gateway_prefix and any(
            prefixes_agree(prefix, gateway_prefix)
            for prefix in deleted_prefixes.get(gateway_key_id, ())
        ):
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
            elif (
                local_key.status != AgentccAPIKey.PENDING
                and local_key.pk not in eligible_ids and gw_status == "active"
            ):
                # An after-commit hash import may still be completing. Do not
                # revoke its pending hash in the gap before local activation.
                # FAILED stays ineligible and is revoked by this same branch.
                # A revoke the gateway missed (it was unreachable at the time).
                client.revoke_key(gateway_key_id)
            if org and local_key.organization_id != org.id:
                # Django owns a key's org. A gateway copy naming another org
                # carries a tenant-set org_id, so the row stays with its own
                # org; the gateway gets it back at its next key sync.
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

            user_id = (gk.get("metadata") or {}).get("user_id")
            try:
                member = (
                    live_gateway_memberships()
                    .filter(
                        organization=gw_org,
                        user_id=user_id,
                    )
                    .select_related("user")
                    .first()
                    if user_id
                    else None
                )
            except (ValidationError, ValueError):
                member = None
            if member is None:
                logger.warning(
                    "sync_keys_skip_no_person", gateway_key_id=gateway_key_id
                )
                if gk.get("status", "active") != "revoked":
                    client.revoke_key(gateway_key_id)
                org_key_count -= 1
                continue

            AgentccAPIKey.no_workspace_objects.update_or_create(
                gateway_key_id=gateway_key_id,
                defaults={
                    "organization": gw_org,
                    "user": member.user,
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
    payload = gateway_key_payloads(
        gateway_loadable_keys(org).exclude(gateway_key_id__in=gateway_key_ids)
    )
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
