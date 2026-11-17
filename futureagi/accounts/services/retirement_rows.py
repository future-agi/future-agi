"""Database half of retirement and compare-before-restore journal semantics."""

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.api_key_constants import RETIRED_KEY_TYPES, RETIREMENT_REASON
from accounts.api_key_policy import live_membership
from accounts.api_key_revocation import publish_key_revocation
from accounts.models import OrgApiKey
from accounts.models.retirement_manifest import RetirementManifest
from accounts.services.retirement_fence import FENCE_VERSION, gateway_retirement_writes

KEY_FIELDS = ("enabled", "retired_at", "retired_reason", "prior_enabled")


def state(row, fields):
    result = {}
    for field in fields:
        value = getattr(row, field)
        result[field] = value.isoformat() if hasattr(value, "isoformat") else value
    return result


def fingerprint(row, fields, *, include_updated_at=True):
    result = state(row, fields)
    result["deleted"] = row.deleted
    if include_updated_at:
        result["updated_at"] = row.updated_at.isoformat()
    for field in ("user_id", "organization_id", "api_key_id"):
        if hasattr(row, field):
            value = getattr(row, field)
            result[field] = str(value) if value is not None else None
    return result


def journal(service, row, action, prior, applied, *, confirmed=False):
    """Caller locks the object first; both writes commit in that transaction."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("Retirement journal requires the object's transaction")
    manifest, created = RetirementManifest.objects.select_for_update().get_or_create(
        service=service,
        object_id=str(row.pk),
        action=action,
        defaults={
            "prior_state": prior,
            "applied_state": applied,
            "applied_at": timezone.now(),
            "confirmed": confirmed,
        },
    )
    if not created and manifest.reverted_at is not None and not manifest.superseded_by:
        manifest.prior_state, manifest.applied_state = prior, applied
        manifest.applied_at, manifest.reverted_at = timezone.now(), None
        manifest.confirmed = confirmed
        manifest.save()
    elif not created and not manifest.superseded_by:
        # A retry never overwrites the original prior state or ownership fence.
        manifest.confirmed = confirmed
        manifest.save(update_fields=["confirmed"])
    return manifest


def retire_fa_rows(*, dry_run=False):
    rows = OrgApiKey._base_manager.filter(
        type__in=RETIRED_KEY_TYPES, deleted=False
    ).filter(Q(retired_reason__isnull=True) | Q(retired_reason=RETIREMENT_REASON))
    count = rows.count()
    if dry_run:
        return count
    for pk in rows.values_list("pk", flat=True).iterator():
        with transaction.atomic():
            key = OrgApiKey._base_manager.select_for_update().get(pk=pk)
            existing = RetirementManifest.objects.filter(
                service="fa", object_id=str(pk), action="disable"
            ).first()
            if existing and existing.reverted_at is None:
                if fingerprint(key, KEY_FIELDS) != existing.applied_state:
                    existing.superseded_by = "object_changed"
                    existing.save(update_fields=["superseded_by"])
                    continue
            prior = state(key, KEY_FIELDS)
            if key.retired_reason == RETIREMENT_REASON:
                # Phase-one migration retained exactly this prior bit. Adopt its
                # change without guessing the state of an already-disabled key.
                if key.prior_enabled is None:
                    continue
                prior = {
                    "enabled": key.prior_enabled,
                    "retired_at": None,
                    "retired_reason": None,
                    "prior_enabled": None,
                }
            else:
                key.prior_enabled = key.enabled
                key.enabled = False
                key.retired_reason = RETIREMENT_REASON
                key.retired_at = timezone.now()
                key.save(update_fields=[*KEY_FIELDS, "updated_at"])
            manifest = journal(
                "fa", key, "disable", prior, fingerprint(key, KEY_FIELDS)
            )
        # Retry delivery independently of the already-committed state change.
        publish_key_revocation(key, strict=True)
        RetirementManifest.objects.filter(pk=manifest.pk).update(confirmed=True)
    return count


def retire_connections(*, dry_run=False):
    from mcp_server.models import MCPConnection

    rows = MCPConnection._base_manager.filter(
        deleted=False, api_key__retired_reason=RETIREMENT_REASON
    ).exclude(is_active=False, reconnect_required=True)
    count = rows.count()
    if dry_run:
        return count
    fields = ("is_active", "reconnect_required")
    for pk in rows.values_list("pk", flat=True).iterator():
        with transaction.atomic():
            row = MCPConnection._base_manager.select_for_update().get(pk=pk)
            prior = state(row, fields)
            row.is_active, row.reconnect_required = False, True
            row.save(update_fields=[*fields, "updated_at"])
            journal(
                "mcp", row, "reconnect", prior, fingerprint(row, fields), confirmed=True
            )
    return count


def reverse_rows(*, dry_run=False):
    from agentcc.models import AgentccAPIKey, AgentccOrgConfig
    from mcp_server.models import MCPConnection

    kinds = {
        "fa": (OrgApiKey, KEY_FIELDS),
        "gateway": (AgentccAPIKey, ("status", "expires_at")),
        "mcp": (MCPConnection, ("is_active", "reconnect_required")),
        "config": (AgentccOrgConfig, ("version", "is_active")),
    }
    counts = {
        "restored": 0,
        "superseded": 0,
        "irreversible": 0,
        "config_repush_owed": 0,
    }
    manifests = RetirementManifest.objects.filter(reverted_at__isnull=True).order_by(
        "pk"
    )
    for pk in manifests.values_list("pk", flat=True).iterator():
        with transaction.atomic():
            manifest = RetirementManifest.objects.get(pk=pk)
            if manifest.reverted_at is not None:
                continue
            if manifest.service not in kinds or manifest.action == "irreversible":
                counts["irreversible"] += 1
                continue
            model, fields = kinds[manifest.service]
            row = (
                model._base_manager.select_for_update()
                .filter(pk=manifest.object_id)
                .first()
            )
            # Match forward and the UPDATE trigger: object first, journal second.
            # Reload only after acquiring both locks, so a concurrent customer
            # revoke cannot be missed or deadlock on the opposite lock order.
            manifest = RetirementManifest.objects.select_for_update().get(pk=pk)
            if manifest.reverted_at is not None:
                continue
            superseded = manifest.superseded_by
            expected = dict(manifest.applied_state)
            is_gateway = manifest.service == "gateway"
            if is_gateway:
                # Retry-mutated timestamps are not an ownership fence. Normal
                # API/SQL writes persist customer_changed in the same transaction.
                # Older unfenced journals cannot prove that no customer revoked
                # the already-revoked key; never upgrade that evidence on retry.
                expected.pop("updated_at", None)
                if expected.pop("customer_change_fence", None) != FENCE_VERSION:
                    superseded = "customer_fence_unavailable"
            if (
                row is None
                or fingerprint(row, fields, include_updated_at=not is_gateway)
                != expected
            ):
                superseded = "object_changed"
            elif (
                getattr(row, "user_id", None) is not None
                and live_membership(row.user_id, row.organization_id) is None
            ):
                superseded = "membership_ended"
            if superseded:
                counts["superseded"] += 1
                if not dry_run:
                    manifest.superseded_by = superseded
                    manifest.save(update_fields=["superseded_by"])
                continue
            if manifest.service == "config":
                # Config JSON is not changed or saved here. The old image must
                # re-push this revision after restart; never copy credentials.
                counts["config_repush_owed"] += 1
            else:
                counts["restored"] += 1
                if not dry_run:
                    values = {
                        field: model._meta.get_field(field).to_python(value)
                        for field, value in manifest.prior_state.items()
                        if field in fields
                    }
                    # Operator restoration is not a new customer action.
                    with gateway_retirement_writes():
                        model._base_manager.filter(pk=row.pk).update(
                            **values, updated_at=timezone.now()
                        )
            if not dry_run:
                manifest.reverted_at = timezone.now()
                manifest.save(update_fields=["reverted_at"])
    return counts
