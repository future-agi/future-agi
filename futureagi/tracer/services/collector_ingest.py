"""Emit spans to the fi-collector.

The collector's ClickHouse exporter is the sole writer of CH ``spans`` (legacy
PG->CH CDC chain dropped by default), so any span that must appear in observe
exports through it. Resolves the org's system ingest key and ships OTLP spans.
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Per-org system key reused to authenticate span emission to the collector.
_INGEST_KEY_NAME = "system_org_key"


def resolve_ingest_credentials(
    organization_id: str, workspace_id: str | None
) -> tuple[str, str] | None:
    """Resolve the org-scoped (api_key, secret_key) for span emission.

    Reuses the org's system key (one per org); else creates it carrying the
    workspace so the collector resolves projects there.
    """
    if not organization_id:
        return None
    from django.db import IntegrityError

    from accounts.models.user import OrgApiKey

    # IntegrityError catch re-fetches the winner of a create/create race.
    # Disabled keys are returned as-is so the collector surfaces the misconfig.
    lookup = {
        "organization_id": organization_id,
        "type": "system",
        "deleted": False,
    }
    try:
        key, _ = OrgApiKey.no_workspace_objects.get_or_create(
            defaults={"workspace_id": workspace_id, "name": _INGEST_KEY_NAME},
            **lookup,
        )
    except IntegrityError:
        key = (
            OrgApiKey.no_workspace_objects.filter(**lookup)
            .order_by("created_at")
            .first()
        )
    if key is None:
        return None
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
    """Export ``spans`` to the collector. Best-effort: returns count exported, 0 on failure."""
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
