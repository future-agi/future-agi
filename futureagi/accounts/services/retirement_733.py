"""Compose FA's journal with the gateway-owned purpose retirement function."""

from importlib import import_module

import structlog
from django.core.management.base import CommandError
from django.db import transaction

from accounts.models.retirement_manifest import RetirementManifest
from accounts.services.retirement_fence import FENCE_VERSION, gateway_retirement_writes
from accounts.services.retirement_reconnect import unjournaled_connections
from accounts.services.retirement_rows import (
    fingerprint,
    journal,
    retire_connections,
    retire_fa_rows,
    reverse_rows,
)
from tfc.routers import primary_required

logger = structlog.get_logger(__name__)


class GatewayAdapter:
    def issuance_is_off(self):
        from agentcc.services.delegated_availability import delegated_enabled

        return not (delegated_enabled() or delegated_enabled(batch=True))

    def record(self, *, key, prior_state, confirmed):
        key.refresh_from_db()
        # The gateway interface supplies only these two non-secret fields.
        prior = {field: prior_state[field] for field in ("status", "expires_at")}
        irreversible = (
            (key.metadata or {}).get("purpose") in {"ee-agent-key", "omega-ee-agent"}
            or key.owner == "omega-ee"
            or hasattr(key, "ee_agent_key")
        )
        journal(
            "gateway",
            key,
            "irreversible" if irreversible else "revoke",
            prior,
            {
                **fingerprint(key, ("status", "expires_at"), include_updated_at=False),
                "customer_change_fence": FENCE_VERSION,
            },
            confirmed=confirmed,
        )


def gateway_adapter():
    return GatewayAdapter()


def gateway_retirement():
    try:
        return import_module("agentcc.services.retirement_733").retire_selected_keys
    except (ImportError, AttributeError) as exc:
        raise CommandError(
            "Gateway retirement is not installed; compose the gateway lane first"
        ) from exc


def repush_configs(*, dry_run=False):
    from agentcc.models import AgentccOrgConfig
    from agentcc.services.config_push import (
        MCPCredentialPlacementRefused,
        push_org_config,
    )

    count, pending, refused = 0, 0, {}
    configs = AgentccOrgConfig._base_manager.filter(is_active=True, deleted=False)
    for pk in configs.values_list("pk", flat=True).iterator():
        count += 1
        if dry_run:
            continue
        with transaction.atomic():
            config = AgentccOrgConfig._base_manager.select_for_update().get(pk=pk)
            applied = fingerprint(config, ("version", "is_active"))
            # journal() resets confirmation on retry for other retirement kinds.
            # Skip only this unchanged, unreverted, already-confirmed config.
            if (
                RetirementManifest.objects.select_for_update()
                .filter(
                    service="config",
                    object_id=str(pk),
                    action="repush",
                    confirmed=True,
                    reverted_at__isnull=True,
                    superseded_by="",
                    applied_state=applied,
                )
                .first()
            ):
                continue
            row = journal("config", config, "repush", {"revision_id": str(pk)}, applied)
            if row.superseded_by or row.applied_state != applied:
                # A later config edit is not overwritten as part of a retry.
                row.superseded_by = "config_changed"
                row.save(update_fields=["superseded_by"])
                continue
            try:
                # Preserve the journal outside the push savepoint. A refused org
                # remains pending for retry without stopping every later org.
                with transaction.atomic():
                    row.confirmed = bool(
                        push_org_config(str(config.organization_id), config)
                    )
            except MCPCredentialPlacementRefused as exc:
                row.confirmed = False
                refused[exc.code] = refused.get(exc.code, 0) + 1
                logger.warning(
                    "retirement_config_push_refused",
                    org_id=str(config.organization_id),
                    code=exc.code,
                    param=exc.param,
                )
            row.save(update_fields=["confirmed"])
            pending += int(not row.confirmed)
    return count, pending, refused


@primary_required
def run_retirement(*, dry_run=False, reverse=False, accept_open_batch_loss=False):
    if not gateway_adapter().issuance_is_off():
        raise CommandError("Console delegation must be OFF during retirement")
    # Preflight before changing even FA rows: a partial install is not a composed
    # cutover. The real gateway function writes through our transactional adapter.
    retire_gateway = gateway_retirement()
    missing_prior = unjournaled_connections()
    if missing_prior and not dry_run:
        raise CommandError(
            f"Reconnect prior state is unavailable for {missing_prior} rows; "
            "compose migration-time journaling before retirement"
        )
    if reverse:
        result = reverse_rows(dry_run=dry_run)
        result["gateway_restart_required"] = True
        result["previous_config_pusher_required"] = True
        result["unjournaled_connections"] = missing_prior
        return result
    from agentcc.models import AgentccBatchJob
    from agentcc.services.batch_jobs import finish_lost
    from agentcc.services.gateway_client import get_gateway_client

    # The cutover ends legacy jobs only. Delegated submissions (especially the
    # pending- outbox rows skipped by the poller) keep their recovery lifecycle.
    open_jobs = (
        AgentccBatchJob.objects.filter(notified=False)
        .exclude(gateway_batch_id__startswith="pending-")
        .exclude(gateway_key_id="")
        .exclude(gateway_key_id__startswith="dlg_")
    )
    open_count = open_jobs.count()
    if open_count and not dry_run and not accept_open_batch_loss:
        raise CommandError(
            f"{open_count} open legacy batches; pass --accept-open-batch-loss "
            "to acknowledge their failure at cutover"
        )
    result = {
        "unjournaled_connections": missing_prior,
        "open_legacy_batches": open_count,
        "fa": retire_fa_rows(dry_run=dry_run),
        "mcp": retire_connections(dry_run=dry_run),
    }
    # Retry writes are distinct from normal API/offboarding writes. The database
    # trigger fences every other connection atomically with its key update.
    with gateway_retirement_writes():
        gateway = retire_gateway(gateway_adapter(), dry_run=dry_run)
    result.update(gateway=gateway["selected"], gateway_pending=len(gateway["pending"]))
    result["lost_batches"] = 0
    if not dry_run:
        client = get_gateway_client()
        for job in open_jobs.iterator():
            result["lost_batches"] += int(finish_lost(job, client))
    result["configs"], result["config_pending"], result["config_refused"] = (
        repush_configs(dry_run=dry_run)
    )
    result["manifest_rows"] = RetirementManifest.objects.count()
    return result
