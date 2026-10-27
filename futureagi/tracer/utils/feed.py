"""
Service layer for the Error Feed API.

Orchestrates queries and applies business rules.
Views call services here — never queries directly.
Returns typed dataclasses from tracer.types.feed_types.
"""

import structlog

from tracer.queries import feed as feed_queries
from tracer.types.feed_types import (
    DeepAnalysisDispatchResponse,
    DeepAnalysisResponse,
    FeedDetailCore,
    FeedListResponse,
    FeedSidebar,
    FeedStats,
    FeedUpdatePayload,
    OverviewResponse,
    TracesTabResponse,
    TrendsTabResponse,
)

logger = structlog.get_logger(__name__)


def list_feed_issues(
    project_ids: list[str],
    *,
    search: str | None = None,
    status: str | None = None,
    severity: str | None = None,
    fix_layer: str | None = None,
    source: str | None = None,
    issue_group: str | None = None,
    time_range_days: int | None = None,
    sort_by: str = "last_seen",
    sort_dir: str = "desc",
    limit: int = 25,
    offset: int = 0,
) -> FeedListResponse:
    """Paginated Error Feed list across the given projects."""
    return feed_queries.list_clusters(
        project_ids,
        search=search,
        status=status,
        severity=severity,
        fix_layer=fix_layer,
        source=source,
        issue_group=issue_group,
        time_range_days=time_range_days,
        sort_by=sort_by,
        sort_dir=sort_dir,
        limit=limit,
        offset=offset,
    )


def get_feed_stats(
    project_ids: list[str], *, time_range_days: int | None = None
) -> FeedStats:
    """Top stats bar totals for the list view."""
    return feed_queries.get_stats(project_ids, time_range_days=time_range_days)


def get_feed_detail(
    cluster_id: str, project_ids: list[str] | None = None
) -> FeedDetailCore | None:
    """Detail core (list row fields + success/representative trace previews)."""
    return feed_queries.get_cluster_detail(cluster_id, project_ids)


def update_feed_issue(
    cluster_id: str,
    project_ids: list[str] | None,
    payload: FeedUpdatePayload,
) -> FeedDetailCore | None:
    """Update status/severity/assignee on a cluster. Returns fresh detail."""
    logger.info(
        "feed_issue_updated",
        cluster_id=cluster_id,
        status=payload.status,
        severity=payload.severity,
        assignee=payload.assignee,
    )
    return feed_queries.update_cluster(cluster_id, project_ids, payload)


def get_overview_tab(
    cluster_id: str,
    project_ids: list[str] | None = None,
    *,
    rep_limit: int = 20,
) -> OverviewResponse | None:
    """Overview tab: events over time, pattern summary, representative traces."""
    return feed_queries.get_overview(cluster_id, project_ids, rep_limit=rep_limit)


def get_traces_tab(
    cluster_id: str,
    project_ids: list[str] | None = None,
    *,
    limit: int = 50,
    offset: int = 0,
) -> TracesTabResponse | None:
    """Traces tab: aggregates + paginated trace list."""
    return feed_queries.get_traces_tab(
        cluster_id, project_ids, limit=limit, offset=offset
    )


def get_trends_tab(
    cluster_id: str, project_ids: list[str] | None = None, *, days: int = 14
) -> TrendsTabResponse | None:
    """Trends tab: KPI metrics, daily events, score trends, heatmap."""
    return feed_queries.get_trends_tab(cluster_id, project_ids, days=days)


def get_sidebar(
    cluster_id: str,
    project_ids: list[str] | None = None,
    *,
    trace_id: str | None = None,
) -> FeedSidebar | None:
    """Right panel: timeline, AI metadata, evaluations, co-occurring issues.

    ``trace_id`` scopes AI Metadata + Evaluations to that specific trace
    so the sidebar stays in sync with the Overview tab's selection.
    """
    return feed_queries.get_sidebar(cluster_id, project_ids, trace_id=trace_id)


def get_deep_analysis(
    cluster_id: str, project_ids: list[str] | None = None, *, trace_id: str
) -> DeepAnalysisResponse | None:
    """Read the cached deep analysis for a cluster's trace."""
    return feed_queries.get_deep_analysis(cluster_id, trace_id, project_ids)


def dispatch_deep_analysis(
    cluster_id: str,
    project_ids: list[str] | None = None,
    *,
    trace_id: str,
    force: bool = False,
) -> DeepAnalysisDispatchResponse | None:
    """Kick off (or no-op) a deep analysis run on the given trace."""
    logger.info(
        "deep_analysis_dispatch",
        cluster_id=cluster_id,
        trace_id=trace_id,
        force=force,
    )
    return feed_queries.dispatch_deep_analysis(
        cluster_id, trace_id, project_ids, force=force
    )


def post_rca_comment_to_linked_issue(cluster_pk: str) -> bool:
    """Post the cluster's cached RCA as a comment on its linked Linear issue.

    Called after an RCA run persists its synthesis: a ticket filed before the
    analysis ran would otherwise stay bare forever (Linear issues are
    user-owned after creation, so we comment rather than rewrite the
    description). Best-effort — returns False when there's no linked issue,
    no connection, or the API call fails; never raises.
    """
    # Lazy imports: views.feed imports this module, so a module-level import
    # of linear_issue_view would be circular; integrations stays off the
    # feed-query hot path.
    from integrations.models.integration_connection import (
        ConnectionStatus,
        IntegrationConnection,
        IntegrationPlatform,
    )
    from integrations.services.credentials import CredentialManager
    from integrations.services.linear_service import LinearService
    from tracer.models.trace_error_analysis import TraceErrorGroup
    from tracer.views.feed.linear_issue_view import _cluster_url

    cluster = (
        TraceErrorGroup.objects.filter(id=cluster_pk).select_related("project").first()
    )
    if cluster is None or not cluster.external_issue_id or not cluster.rca_synthesis:
        return False

    connection = (
        IntegrationConnection.objects.filter(
            organization_id=cluster.project.organization_id,
            platform=IntegrationPlatform.LINEAR,
            deleted=False,
        )
        .exclude(status=ConnectionStatus.ERROR)
        .order_by("-created_at")
        .first()
    )
    if connection is None:
        return False

    parts = [f"**RCA completed** (confidence: {cluster.rca_confidence or '—'})"]
    url = _cluster_url(cluster.cluster_id)
    if url:
        parts[0] += f" — [view cluster]({url})"
    parts.append(cluster.rca_synthesis)
    if cluster.rca_fix:
        parts.append(f"**Recommended fix**\n\n{cluster.rca_fix}")

    try:
        credentials = CredentialManager.decrypt(connection.encrypted_credentials)
        LinearService().create_comment(
            credentials, cluster.external_issue_id, "\n\n".join(parts)
        )
    except Exception:
        logger.exception(
            "linear_rca_comment_failed",
            cluster_id=cluster.cluster_id,
            issue_id=cluster.external_issue_id,
        )
        return False

    logger.info(
        "linear_rca_comment_posted",
        cluster_id=cluster.cluster_id,
        issue_id=cluster.external_issue_id,
    )
    return True
