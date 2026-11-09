"""Short-lived collector credentials; deployment rollout remains default OFF."""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import structlog
from django.conf import settings
from django.db import transaction

from accounts.models import Organization
from accounts.models.workspace import Workspace
from accounts.services.envelope_keys import current_kid, pair_keys
from accounts.services.service_grants import issue_person_grant
from tfc.routers import primary_required
from tfc.service_envelope import EnvelopeRejected, issue_grant

logger = structlog.get_logger(__name__)
PLATFORM_EMISSION_STATE = "OFF"
# The launch planner runs before an attempt exists and its job has no stored
# launcher identity. It must stay OFF until that trusted attribution is wired.
SANDBOX_EXPORT_STATE = "OFF"
DISABLED_REASON = "run_token_unavailable"


@dataclass(frozen=True)
class ExportCredentials:
    api_key: str = field(repr=False)
    secret_key: str = field(repr=False)
    enabled: bool


def collector_grants_enabled():
    value = getattr(
        settings,
        "COLLECTOR_SERVICE_GRANTS_ENABLED",
        os.environ.get("COLLECTOR_SERVICE_GRANTS_ENABLED", "false"),
    )
    return value is True or value == "true"


@primary_required
def issue_platform_grant(organization_id, workspace_id, *, source="sim"):
    if not Organization.objects.filter(pk=organization_id).exists():
        raise EnvelopeRejected()
    if (
        workspace_id
        and not Workspace._base_manager.filter(
            pk=workspace_id,
            organization_id=organization_id,
            is_active=True,
            deleted=False,
        ).exists()
    ):
        raise EnvelopeRejected()
    token, claims = issue_grant(
        keys=pair_keys("fa", "collector-emit"),
        kid=current_kid(),
        kind="platform-emit",
        org=organization_id,
        workspace=workspace_id,
        sub="emit:" + source,
        ttl=60,
    )
    return ExportCredentials("fit_" + claims["id"], token, True)


def resolve_system_emit_credentials(organization_id, workspace_id):
    """Compatibility name: resolves no API key, only a per-export grant."""
    if not collector_grants_enabled():
        logger.info("platform_span_export_off", state="OFF", reason=DISABLED_REASON)
        return None
    credentials = issue_platform_grant(organization_id, workspace_id)
    return credentials.api_key, credentials.secret_key


@primary_required
def issue_sandbox_export_credentials(
    *, organization_id, workspace_id, agent_definition_id, attempt_id, user_id
):
    """Only a trusted launcher may call this with its persisted person identity."""
    from simulate.models import AgentDefinition, HostedHarnessAttempt

    with transaction.atomic():
        attempt = (
            HostedHarnessAttempt._base_manager.select_for_update()
            .select_related("job")
            .get(pk=attempt_id)
        )
        if (
            str(attempt.job.organization_id) != str(organization_id)
            or str(attempt.job.workspace_id) != str(workspace_id)
            or attempt.deleted
            or attempt.job.deleted
            or attempt.job.cancel_requested_at
            or attempt.state not in {"registered", "provisioning", "running"}
        ):
            raise EnvelopeRejected()
        if not AgentDefinition._base_manager.filter(
            pk=agent_definition_id,
            organization_id=organization_id,
            workspace_id=workspace_id,
            deleted=False,
        ).exists():
            raise EnvelopeRejected()
        now = int(time.time())
        expiry = min(int(attempt.job.deadline_at.timestamp()) + 600, now + 86400)
        if expiry <= now:
            raise EnvelopeRejected()
        # Lock and touch the attempt in the same transaction as its registry row.
        attempt.save(update_fields=["updated_at"])
        token, claims, _ = issue_person_grant(
            kind="sandbox",
            org=organization_id,
            workspace=workspace_id,
            user=user_id,
            sub=f"attempt:{agent_definition_id}:{attempt_id}",
            ttl=expiry - now,
        )
    return ExportCredentials("fit_" + claims["id"], token, True)


def get_or_create_export_credentials(
    *,
    organization_id: str | None,
    workspace_id: str | None,
    agent_definition_id: str | uuid.UUID | None,
) -> ExportCredentials:
    # No person or attempt can be inferred safely from an organization/agent.
    logger.info(
        "sandbox_span_export_off", state=SANDBOX_EXPORT_STATE, reason=DISABLED_REASON
    )
    return ExportCredentials("", "", False)


def emit_spans_to_collector(
    spans: list[dict[str, Any]],
    *,
    project_name: str,
    project_type: str,
    organization_id: str,
    workspace_id: str | None,
    service_name: str = "fi-simulation",
) -> int:
    if not spans:
        return 0
    if not collector_grants_enabled():
        logger.info("platform_span_export_off", state="OFF", reason=DISABLED_REASON)
        return 0
    try:
        source = (
            "voice"
            if "voice" in service_name
            else "provider"
            if "provider" in service_name
            else "sim"
        )
        credential = issue_platform_grant(organization_id, workspace_id, source=source)
        from simulate.services.sim_collector_emit import export_sim_spans

        return export_sim_spans(
            spans,
            project_name=project_name,
            project_type=project_type,
            api_key=credential.api_key,
            secret_key=credential.secret_key,
            service_name=service_name,
        )
    except Exception as exc:
        logger.warning("platform_span_export_unavailable", reason=type(exc).__name__)
        return 0
