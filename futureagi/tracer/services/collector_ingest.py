"""Shared interface for emitting spans to the fi-collector (TH-5642).

The fi-collector's ClickHouse exporter is the single steady-state writer of CH
``spans`` (the legacy PG→CH PeerDB CDC chain is dropped by default,
``CH25_DROP_LEGACY_CDC_CHAIN``). Any span source that must appear in the
CH-backed observe UI therefore has to export through the collector, not just
write PG ``tracer_observation_span``. This module is that one seam:

  - simulate.services.sim_observability (simulated conversations)
  - tracer.utils.observability_provider (provider-pulled call logs)

both resolve the org's system ingest key here and ship OTLP spans via
simulate.services.sim_collector_emit.export_sim_spans.
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Name of the per-org system key reused to authenticate span emission to the
# collector. The collector resolves org + workspace from the key and the
# project from the ``project_name`` resource attribute.
_INGEST_KEY_NAME = "system_org_key"


def resolve_ingest_credentials(
    organization_id: str, workspace_id: str | None
) -> tuple[str, str] | None:
    """Resolve the (api_key, secret_key) used to emit spans to the collector.

    The collector authenticates against the org's system key and resolves the
    target project under that key's (org, workspace), so the credential is the
    single lever for landing spans in the right project. Strictly org-scoped —
    read/created only for the given organization, never another's. Reuses the
    org's existing system key when present (one per org by model constraint);
    otherwise creates it carrying the workspace so the collector resolves
    projects in that workspace rather than the org default.
    """
    if not organization_id:
        return None
    from accounts.models.user import OrgApiKey

    key = (
        OrgApiKey.no_workspace_objects.filter(
            organization_id=organization_id, type="system", deleted=False, enabled=True
        )
        .order_by("created_at")
        .first()
    )
    if key is None:
        key = OrgApiKey.no_workspace_objects.create(
            organization_id=organization_id,
            workspace_id=workspace_id,
            name=_INGEST_KEY_NAME,
            type="system",
        )
    return key.api_key, key.secret_key


def emit_spans_to_collector(
    spans: list[dict[str, Any]],
    *,
    project_name: str,
    project_type: str,
    organization_id: str,
    workspace_id: str | None,
    service_name: str = "fi-simulation",
) -> int:
    """Resolve credentials and export ``spans`` (build_sim_spans-shaped dicts) to
    the collector. Best-effort: returns the number exported, 0 on missing
    credentials or a failed export (logged), never raising into the caller.
    """
    if not spans:
        return 0
    credentials = resolve_ingest_credentials(organization_id, workspace_id)
    if credentials is None:
        logger.warning("collector_emit_no_credentials", organization_id=organization_id)
        return 0
    api_key, secret_key = credentials
    from simulate.services.sim_collector_emit import export_sim_spans

    return export_sim_spans(
        spans,
        project_name=project_name,
        project_type=project_type,
        api_key=api_key,
        secret_key=secret_key,
        service_name=service_name,
    )
