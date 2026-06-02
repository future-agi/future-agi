"""
Service layer for the Error Feed API.

Orchestrates queries and applies business rules.
Views call services here — never queries directly.
Returns typed dataclasses from tracer.types.feed_types.
"""

from typing import List, Optional

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
    project_ids: List[str],
    *,
    search: Optional[str] = None,
    status: Optional[str] = None,
    severity: Optional[str] = None,
    fix_layer: Optional[str] = None,
    source: Optional[str] = None,
    issue_group: Optional[str] = None,
    time_range_days: Optional[int] = None,
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
    project_ids: List[str], *, time_range_days: Optional[int] = None
) -> FeedStats:
    """Top stats bar totals for the list view."""
    return feed_queries.get_stats(project_ids, time_range_days=time_range_days)


def get_feed_detail(
    cluster_id: str, project_ids: Optional[List[str]] = None
) -> Optional[FeedDetailCore]:
    """Detail core (list row fields + success/representative trace previews)."""
    return feed_queries.get_cluster_detail(cluster_id, project_ids)


def update_feed_issue(
    cluster_id: str,
    project_ids: Optional[List[str]],
    payload: FeedUpdatePayload,
) -> Optional[FeedDetailCore]:
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
    cluster_id: str, project_ids: Optional[List[str]] = None
) -> Optional[OverviewResponse]:
    """Overview tab: events over time, pattern summary, representative traces."""
    return feed_queries.get_overview(cluster_id, project_ids)


def get_traces_tab(
    cluster_id: str,
    project_ids: Optional[List[str]] = None,
    *,
    limit: int = 50,
    offset: int = 0,
) -> Optional[TracesTabResponse]:
    """Traces tab: aggregates + paginated trace list."""
    return feed_queries.get_traces_tab(
        cluster_id, project_ids, limit=limit, offset=offset
    )


def get_trends_tab(
    cluster_id: str, project_ids: Optional[List[str]] = None, *, days: int = 14
) -> Optional[TrendsTabResponse]:
    """Trends tab: KPI metrics, daily events, score trends, heatmap."""
    return feed_queries.get_trends_tab(cluster_id, project_ids, days=days)


def get_sidebar(
    cluster_id: str,
    project_ids: Optional[List[str]] = None,
    *,
    trace_id: Optional[str] = None,
) -> Optional[FeedSidebar]:
    """Right panel: timeline, AI metadata, evaluations, co-occurring issues.

    ``trace_id`` scopes AI Metadata + Evaluations to that specific trace
    so the sidebar stays in sync with the Overview tab's selection.
    """
    return feed_queries.get_sidebar(cluster_id, project_ids, trace_id=trace_id)


def get_deep_analysis(
    cluster_id: str, project_ids: Optional[List[str]] = None, *, trace_id: str
) -> Optional[DeepAnalysisResponse]:
    """Read the cached deep analysis for a cluster's trace."""
    return feed_queries.get_deep_analysis(cluster_id, trace_id, project_ids)


def dispatch_deep_analysis(
    cluster_id: str,
    project_ids: Optional[List[str]] = None,
    *,
    trace_id: str,
    force: bool = False,
) -> Optional[DeepAnalysisDispatchResponse]:
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
