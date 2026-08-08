"""Background refresh workers for exact aggregate snapshots.

Only normalized query identities cross the task boundary. Authentication and
tenant ownership are checked by the HTTP endpoint before enqueue; workers
re-resolve tenant records and publish only a fully complete exact payload.
"""

from __future__ import annotations

from inspect import unwrap
from types import SimpleNamespace
from typing import Any

import structlog

from tfc.temporal import temporal_activity
from tracer.services.exact_aggregation_cache import (
    activate_exact_refresh,
    exact_payload_is_complete,
    finish_exact_refresh,
    publish_exact_snapshot_for_refresh,
    refresh_claim_is_current,
)

logger = structlog.get_logger(__name__)


def _observe_payload(namespace: str, identity: dict[str, Any]) -> Any:
    from tracer.services.clickhouse.exact_graph_reads import (
        read_exact_agent_graph,
        read_exact_all_system_metrics,
        read_exact_annotation_graph,
        read_exact_eval_graph,
        read_exact_session_system_graph,
        read_exact_system_graph,
        read_exact_user_system_graph,
    )
    from tracer.services.clickhouse.v2.query_service import V2AnalyticsQueryService

    analytics = V2AnalyticsQueryService()
    if namespace == "observe-agent-graph":
        return read_exact_agent_graph(
            analytics=analytics,
            project_id=str(identity["project_id"]),
            filters=list(identity.get("filters") or []),
        )
    common = {
        "analytics": analytics,
        "project_id": str(identity["project_id"]),
        "filters": list(identity.get("filters") or []),
        "interval": str(identity["interval"]),
    }
    if namespace == "observe-system-graph":
        return read_exact_system_graph(
            **common,
            metric_id=str(identity.get("metric_id") or ""),
            observe_type=str(identity.get("observe_type") or "trace"),
        )
    if namespace == "observe-all-system-graphs":
        return read_exact_all_system_metrics(**common)
    if namespace == "observe-user-system-graph":
        return read_exact_user_system_graph(
            **common,
            metric_id=str(identity.get("metric_id") or "active_users"),
        )
    if namespace == "observe-session-system-graph":
        return read_exact_session_system_graph(
            **common,
            metric_id=str(identity.get("metric_id") or "session_count"),
        )
    if namespace in {"observe-eval-graph", "observe-eval-chart-series"}:
        return read_exact_eval_graph(
            **common,
            req_data_config=dict(identity.get("req_data_config") or {}),
            observe_type=str(identity.get("observe_type") or "trace"),
            all_series=namespace == "observe-eval-chart-series",
            aggregation_context=str(identity.get("aggregation_context") or "trace"),
        )
    if namespace == "observe-annotation-graph":
        return read_exact_annotation_graph(
            **common,
            req_data_config=dict(identity.get("req_data_config") or {}),
            observe_type=str(identity.get("observe_type") or "trace"),
            aggregation_context=str(identity.get("aggregation_context") or "trace"),
        )
    raise ValueError("unsupported exact Observe refresh namespace")


def _dashboard_payload(identity: dict[str, Any]) -> Any:
    from accounts.models import Workspace
    from tracer.views.dashboard import DashboardWidgetViewSet

    workspace = Workspace.objects.select_related("organization").get(
        id=identity["workspace_id"],
        is_active=True,
    )
    response = DashboardWidgetViewSet()._execute_ch_query_config(
        dict(identity.get("query_config") or {}),
        workspace,
        refresh=True,
        _exact_worker=True,
        cache_identity_override=identity,
    )
    payload = getattr(response, "data", {}).get("result")
    if not exact_payload_is_complete(payload):
        raise RuntimeError("dashboard exact refresh did not complete")
    return payload


def _eval_usage_payload(identity: dict[str, Any]) -> Any:
    from accounts.models import Organization, Workspace
    from model_hub.serializers.contracts import EvalUsageQuerySerializer
    from model_hub.views.separate_evals import EvalUsageStatsView

    organization = Organization.objects.get(id=identity["organization_id"])
    workspace_id = identity.get("workspace_id")
    workspace = None
    if workspace_id:
        workspace = Workspace.objects.get(
            id=workspace_id,
            organization=organization,
            is_active=True,
        )
    query_serializer = EvalUsageQuerySerializer(
        data={
            "page": identity["page"],
            "page_size": identity["page_size"],
            "period": identity["period"],
            "start_date": identity.get("start_date"),
            "end_date": identity.get("end_date"),
            "refresh": True,
        }
    )
    query_serializer.is_valid(raise_exception=True)
    request = SimpleNamespace(
        validated_query_data=query_serializer.validated_data,
        organization=organization,
        workspace=workspace,
        user=SimpleNamespace(organization=organization),
        _exact_aggregation_worker=True,
    )
    # Bypass only the HTTP serializer decorator. The original method still
    # performs template ownership checks and wire-format serialization.
    response = unwrap(EvalUsageStatsView.get)(
        EvalUsageStatsView(),
        request,
        identity["template_id"],
    )
    payload = getattr(response, "data", {}).get("result")
    if not exact_payload_is_complete(payload):
        raise RuntimeError("Eval Usage exact refresh did not complete")
    return payload


def _attribute_detail_payload(identity: dict[str, Any]) -> Any:
    """Re-authorize then compute one exact span-attribute snapshot."""

    from tracer.models.project import Project
    from tracer.services.clickhouse.exact_attribute_detail import (
        read_exact_attribute_detail,
    )

    if not Project.objects.filter(
        id=identity["project_id"],
        workspace_id=identity["workspace_id"],
    ).exists():
        raise ValueError("attribute detail project scope is unavailable")
    return read_exact_attribute_detail(
        project_id=str(identity["project_id"]),
        attribute_key=str(identity["attribute_key"]),
        horizon_days=int(identity.get("horizon_days") or 365),
    )


def _load_exact_payload(namespace: str, identity: dict[str, Any]) -> Any:
    if namespace.startswith("observe-"):
        return _observe_payload(namespace, identity)
    if namespace == "dashboard-query":
        return _dashboard_payload(identity)
    if namespace == "eval-usage":
        return _eval_usage_payload(identity)
    if namespace == "attribute-detail":
        return _attribute_detail_payload(identity)
    raise ValueError("unsupported exact aggregation refresh namespace")


@temporal_activity(
    name="tracer.refresh_exact_aggregation_snapshot",
    time_limit=60 * 60,
    queue="tasks_xl",
    # Exact reads can be expensive. Do not multiply ClickHouse load after a
    # timeout; the cache state exposes a sanitized failure and an explicit
    # user refresh creates a new, deduplicated workflow. The activity remains
    # idempotent under Temporal's at-least-once delivery semantics.
    max_retries=0,
)
def refresh_exact_aggregation_snapshot(
    *,
    namespace: str,
    identity: dict[str, Any],
    refresh_token: str,
) -> None:
    """Compute then atomically publish one exact snapshot."""

    succeeded = False
    try:
        # A workflow accepted by an old/misconfigured worker can fail before
        # this function is called.  Claims therefore begin as short dispatch
        # leases and are promoted here, before ClickHouse is touched.  A late
        # delivery after lease expiry is fenced out by a newer poll's token.
        if not activate_exact_refresh(
            namespace,
            identity,
            refresh_token,
        ):
            return
        payload = _load_exact_payload(namespace, identity)
        if not refresh_claim_is_current(
            namespace,
            identity,
            refresh_token,
        ):
            return
        published = publish_exact_snapshot_for_refresh(
            namespace,
            identity,
            payload,
            refresh_token,
        )
        succeeded = published is not None
    except Exception as exc:
        # Never log identity values, SQL, or database diagnostics at this
        # boundary. The refresh state is intentionally only running/failed.
        logger.warning(
            "exact_aggregation_background_refresh_failed",
            namespace=namespace,
            error_type=type(exc).__name__,
        )
        # Mark the Temporal workflow failed without copying ClickHouse/tenant
        # diagnostics into the workflow error or activity wrapper logs.
        raise RuntimeError("exact aggregation refresh failed") from None
    finally:
        finish_exact_refresh(
            namespace,
            identity,
            refresh_token,
            succeeded=succeeded,
        )


__all__ = ["refresh_exact_aggregation_snapshot"]
