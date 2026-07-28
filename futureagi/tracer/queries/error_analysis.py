import os
import random
import traceback
import uuid
from datetime import datetime, timedelta
from typing import Any

import structlog
from django.conf import settings
from django.db import transaction
from django.db.models import Count, Max, Min, Q, QuerySet
from django.db.models.functions import TruncDate, TruncHour
from django.utils import timezone

logger = structlog.get_logger(__name__)
from accounts.models.workspace import Workspace
from model_hub.models.score import Score
from tracer.models.project import Project, ProjectSourceChoices
from tracer.models.trace import Trace, TraceErrorAnalysisStatus
from tracer.models.trace_error_analysis import (
    AnalysisErrorGroup,
    ErrorClusterTraces,
    ErrorMemory,
    ErrorPattern,
    TraceErrorAnalysis,
    TraceErrorDetail,
    TraceErrorGroup,
)
from tracer.models.trace_error_analysis_task import (
    TraceErrorAnalysisTask,
    TraceErrorTaskStatus,
)
from tracer.services.clickhouse.v2 import get_reader
from tracer.services.clickhouse.v2.span_reader import CHSpan
from tracer.utils.sql_queries import SQL_query_handler


class TraceErrorAnalysisDB:
    def __init__(self):
        pass

    def fetch_children_span_ids(self, root_span):
        """Recursive children-id walk via raw SQL on the PG table.

        Kept untouched: the SQL helper queries tracer_observation_span
        directly (not via Django ORM), so it sits outside the
        ObservationSpan.objects.* migration scope. The dual-write keeps
        this PG-side data alive for now; the helper itself can be
        migrated when the PG `tracer_observation_span` table is finally
        dropped (post-cutover).

        ``root_span`` accepts either a Django ObservationSpan or a CHSpan;
        both expose a stable string `id`, which is all this method reads.
        """
        try:
            rows = SQL_query_handler.fetch_children_ids_query(str(root_span.id))
            result_ids = [str(row[0]) for row in rows]
            return result_ids
        except Exception as e:
            logger.exception(f"Error in fetching children span ids: {str(e)}")
            return []

    def get_all_spans(self, trace_id) -> list[CHSpan]:
        """Return every span in a trace, ordered by start_time.

        Was: filter parent_span_id__isnull=True roots, then recursively
        walk children via raw SQL (`fetch_children_ids_query`), then
        union + sort by start_time-or-created_at.

        CH simplification: `list_by_trace` already returns every span
        for the trace (including roots, children, grandchildren) ordered
        by (start_time, id). The recursive walk is therefore redundant
        — CH stores the full flat span set per trace, and `is_deleted=0`
        filtering matches the legacy `.filter()` default (soft-delete
        semantics live in the writer / merge layer).

        Return type changed from `list[ObservationSpan]` (no callers
        depend on the ORM model type — this method is currently unused
        outside the class).
        """
        with get_reader() as reader:
            return reader.list_by_trace(str(trace_id))

    def get_or_create_error_pattern(
        self,
        project_id: int | None,
        category: str,
        subcategory: str | None,
        specific_error: str | None,
        pattern_description: str | None,
        defaults: dict[str, Any] | None = None,
    ) -> tuple[ErrorPattern, bool]:
        try:
            payload_defaults = {**(defaults or {}), "frequency": 1}
            pattern, created = ErrorPattern.objects.get_or_create(
                project_id=project_id,
                category=category,
                subcategory=subcategory,
                specific_error=specific_error,
                pattern_description=pattern_description,
                defaults=payload_defaults,
            )
            return pattern, created
        except Exception as e:
            logger.exception(f"Error in get_or_create_error_pattern: {str(e)}")
            raise

    def list_error_patterns(
        self,
        project_id: Any | None,
        category_icontains: str | None = None,
    ) -> QuerySet:
        try:
            qs = ErrorPattern.objects.filter(project_id=project_id)
            if category_icontains:
                qs = qs.filter(category__icontains=category_icontains)
            return qs.order_by("-frequency", "-last_seen")
        except Exception as e:
            logger.exception(f"Error in list_error_patterns: {str(e)}")
            return ErrorPattern.objects.none()

    def get_recent_trace_error_analyses(
        self,
        project_id: Any,
        cutoff_date,
        limit: int,
    ) -> QuerySet:
        try:
            qs = (
                TraceErrorAnalysis.objects.filter(
                    project_id=project_id, analysis_date__gte=cutoff_date
                )
                .select_related("trace", "trace__project_version")
                .order_by("-analysis_date")
            )
            return qs[:limit]
        except Exception as e:
            logger.exception(f"Error in get_recent_trace_error_analyses: {str(e)}")
            return TraceErrorAnalysis.objects.none()

    def get_analysis_by_trace_id(
        self,
        trace_id: str,
        organization_id: str | None = None,
        workspace_id: str | None = None,
    ) -> TraceErrorAnalysis | None:
        """
        Get the most recent error analysis for a specific trace
        """
        try:
            logger.debug(
                "get_analysis_by_trace_id_called",
                trace_id=trace_id,
                organization_id=organization_id,
            )
            query = TraceErrorAnalysis.objects.filter(trace__id=trace_id)

            if organization_id:
                query = query.filter(project__organization_id=organization_id)

            if workspace_id:
                workspace_filter = {"id": workspace_id, "is_default": True}
                workspace_filter["organization_id"] = organization_id
                is_default = Workspace.no_workspace_objects.filter(
                    **workspace_filter
                ).exists()

                if is_default:
                    # For default workspace, include projects with:
                    # 1. The specified workspace
                    # 2. No workspace (legacy data created before workspaces)
                    query = query.filter(
                        Q(project__workspace_id=workspace_id)
                        | Q(project__workspace_id__isnull=True)
                    )
                else:
                    # For non-default workspaces, only match the specific workspace
                    query = query.filter(project__workspace_id=workspace_id)

            result = (
                query.select_related("trace", "project")
                .prefetch_related("error_details", "analysis_error_groups")
                .order_by("-analysis_date")
                .first()
            )

            logger.debug(
                "get_analysis_by_trace_id_result",
                trace_id=trace_id,
                found=result is not None,
                analysis_id=result.id if result else None,
            )

            return result
        except Exception as e:
            logger.exception("get_analysis_by_trace_id_failed", trace_id=trace_id)
            return None

    def get_error_details_for_analysis(self, analysis: TraceErrorAnalysis) -> QuerySet:
        try:
            return analysis.error_details.all()
        except Exception as e:
            logger.exception(f"Error in get_error_details_for_analysis: {str(e)}")
            return TraceErrorDetail.objects.none()

    def get_error_groups_for_analysis(self, analysis: TraceErrorAnalysis) -> QuerySet:
        """
        Get error groups for a specific analysis
        """
        try:
            return analysis.analysis_error_groups.all()
        except Exception as e:
            logger.exception(f"Error in get_error_groups_for_analysis: {str(e)}")
            return AnalysisErrorGroup.objects.none()

    def get_error_annotations_for_trace(self, trace: Any) -> QuerySet:
        try:
            return Score.objects.filter(
                trace=trace, label__name__icontains="error", deleted=False
            )
        except Exception as e:
            logger.exception(f"Error in get_error_annotations_for_trace: {str(e)}")
            return Score.objects.none()

    def get_error_spans_for_trace(self, trace: Any) -> list[CHSpan]:
        """Spans on a trace with status="ERROR" (case-insensitive).

        Was: ObservationSpan.objects.filter(trace=trace, status="ERROR").
        Returns a list of CHSpan (was QuerySet). Currently unused
        outside the class so the return-type change is safe.

        CH path: load every span for the trace (bounded set), then
        Python-filter on status — same idiom as analyze_errors.py
        (commit ab19e7ee9) where the reference batch standardized
        case-insensitive Python comparison for status fields.
        """
        try:
            trace_id = str(getattr(trace, "id", trace))
            with get_reader() as reader:
                spans = reader.list_by_trace(trace_id)
            return [s for s in spans if (s.status or "").upper() == "ERROR"]
        except Exception as e:
            logger.exception(f"Error in get_error_spans_for_trace: {str(e)}")
            return []

    def get_tool_usage_patterns(self, project_id, cutoff_date) -> list[dict]:
        """Tool spans per project with per-name aggregates.

        Was: ObservationSpan.filter(trace__project_id=, trace__created_at__gte=,
                                    observation_type="tool")
               .values("name").annotate(usage_count=Count("id"),
                                        error_count=Count(filter=Q(status=ERROR)),
                                        avg_latency=Avg("latency_ms"))
               .order_by("-usage_count")

        CH path: ``CHSpanReader.per_project_group_by_name`` pushes the
        Count/Count(filter)/Avg shape into CH directly. Returns rows with
        ``{name, usage_count, error_count, avg_latency, total_cost}``.

        Semantic note: the original used ``trace__created_at__gte`` (the
        parent Trace's PG ingest timestamp). The CH path filters on
        ``start_time >= since`` (the span's start timestamp). The two are
        usually within ingest-pipeline ms of each other, but they are not
        identical. Acceptable for the recent-window usage pattern this
        method serves; flagged for callers who need exact PG parity.

        ``limit=10000`` keeps parity with the prior unbounded Django path:
        the reader's default cap of 50 would silently truncate tail
        patterns for projects with many tool names. 10k is a safety
        ceiling — projects with that many distinct tool names need a
        product-level rethink anyway.

        Consumer (``ee/agenthub/traceerroragent/memory.py:72``) wraps the
        return in ``list(tool_patterns)`` and assigns directly to the
        ``tool_patterns`` key of the rollup dict, so list[dict] is the
        expected shape.
        """
        with get_reader() as reader:
            return reader.per_project_group_by_name(
                project_id=str(project_id),
                observation_type="tool",
                since=cutoff_date,
                limit=10000,
            )

    def get_error_category_aggregates(self, project_id, cutoff_date) -> QuerySet:
        try:
            return (
                TraceErrorDetail.objects.filter(
                    analysis__project_id=project_id,
                    analysis__analysis_date__gte=cutoff_date,
                )
                .values("category")
                .annotate(
                    error_count=Count("id"),
                    high_impact_count=Count("id", filter=Q(impact="HIGH")),
                    medium_impact_count=Count("id", filter=Q(impact="MEDIUM")),
                    low_impact_count=Count("id", filter=Q(impact="LOW")),
                )
                .order_by("-error_count")
            )
        except Exception as e:
            logger.exception(f"Error in get_error_category_aggregates: {str(e)}")
            return (
                TraceErrorDetail.objects.none()
                .values("category")
                .annotate(
                    error_count=Count("id"),
                    high_impact_count=Count("id", filter=Q(impact="HIGH")),
                    medium_impact_count=Count("id", filter=Q(impact="MEDIUM")),
                    low_impact_count=Count("id", filter=Q(impact="LOW")),
                )
                .order_by("-error_count")
            )

    def get_tool_error_patterns(
        self, project_id, cutoff_date, limit: int = 10
    ) -> QuerySet:
        try:
            qs = TraceErrorDetail.objects.filter(
                analysis__project_id=project_id,
                analysis__analysis_date__gte=cutoff_date,
                location_spans__isnull=False,
            ).exclude(location_spans=[])
            return qs.values("category", "impact", "recommendation", "immediate_fix")[
                :limit
            ]
        except Exception as e:
            logger.exception(f"Error in get_tool_error_patterns: {str(e)}")
            return TraceErrorDetail.objects.none().values(
                "category", "impact", "recommendation", "immediate_fix"
            )[:limit]

    def get_analyses_for_similarity(self, project_id, cutoff_date) -> QuerySet:
        try:
            return TraceErrorAnalysis.objects.filter(
                project_id=project_id, analysis_date__gte=cutoff_date
            ).select_related("trace", "trace__project_version")
        except Exception as e:
            logger.exception(f"Error in get_analyses_for_similarity: {str(e)}")
            return TraceErrorAnalysis.objects.none()

    def get_or_create_memory(
        self,
        project_id: Any,
        memory_type: str,
        memory_key: str,
        memory_data: dict[str, Any],
    ) -> ErrorMemory:
        try:
            memory, _ = ErrorMemory.objects.get_or_create(
                project_id=project_id,
                memory_type=memory_type,
                memory_key=memory_key,
                defaults={"memory_data": memory_data, "is_active": True},
            )
            memory.last_accessed = datetime.now()
            memory.access_count = (memory.access_count or 0) + 1
            memory.save(update_fields=["last_accessed", "access_count"])
            return memory
        except Exception as e:
            logger.exception(f"Error in get_or_create_memory: {str(e)}")
            raise

    def get_recent_notes(
        self,
        project_id: Any,
        memory_type: str,
        cutoff_date: Any | None = None,
        limit: int = 20,
        memory_key_prefix: str | None = None,
        category_icontains: str | None = None,
    ) -> QuerySet:
        try:
            qs = ErrorMemory.objects.filter(
                project_id=project_id,
                memory_type=memory_type,
                is_active=True,
            )
            if cutoff_date:
                qs = qs.filter(last_updated__gte=cutoff_date)
            if memory_key_prefix:
                qs = qs.filter(memory_key__startswith=memory_key_prefix)
            if category_icontains:
                qs = qs.filter(memory_data__category__icontains=category_icontains)
            return qs.order_by("-last_updated")[:limit]
        except Exception as e:
            logger.exception(f"Error in get_recent_notes: {str(e)}")
            return ErrorMemory.objects.none()

    def prune_notes(
        self,
        project_id: Any,
        memory_type: str,
        max_active: int,
        cutoff_date: Any | None = None,
    ) -> int:
        try:
            deactivated = 0
            qs = ErrorMemory.objects.filter(
                project_id=project_id,
                memory_type=memory_type,
                is_active=True,
            )
            if cutoff_date is not None:
                ttl_qs = qs.filter(last_updated__lt=cutoff_date)
                deactivated += ttl_qs.update(is_active=False)
                qs = qs.filter(last_updated__gte=cutoff_date)

            active_count = qs.count()
            if active_count > max_active:
                over = active_count - max_active
                old_ids = list(
                    qs.order_by("last_updated").values_list("id", flat=True)[:over]
                )
                if old_ids:
                    deactivated += ErrorMemory.objects.filter(id__in=old_ids).update(
                        is_active=False
                    )
            return deactivated
        except Exception as e:
            logger.exception(f"Error in prune_notes: {str(e)}")
            return 0

    def get_feedback_annotations(self, project_id) -> QuerySet:
        try:
            return (
                Score.objects.filter(
                    trace__project_id=project_id,
                    label__name__icontains="feedback",
                    deleted=False,
                )
                .values("value", "label__name")
                .annotate(count=Count("id"))
            )
        except Exception as e:
            logger.exception(f"Error in get_feedback_annotations: {str(e)}")
            return Score.objects.none()

    def get_learned_patterns_values(self, project_id) -> QuerySet:
        try:
            return (
                ErrorPattern.objects.filter(project_id=project_id)
                .values(
                    "category",
                    "subcategory",
                    "specific_error",
                    "frequency",
                    "confidence_score",
                    "recommendation",
                    "immediate_fix",
                    "pattern_description",
                    "root_causes",
                )
                .order_by("-last_seen")
            )
        except Exception as e:
            logger.exception(f"Error in get_learned_patterns_values: {str(e)}")
            return ErrorPattern.objects.none().values()

    def get_error_category_statistics(self, project_id) -> QuerySet:
        try:
            return (
                TraceErrorDetail.objects.filter(analysis__project_id=project_id)
                .values("category")
                .annotate(
                    total_count=Count("id"),
                    high_impact_count=Count("id", filter=Q(impact="HIGH")),
                    medium_impact_count=Count("id", filter=Q(impact="MEDIUM")),
                    low_impact_count=Count("id", filter=Q(impact="LOW")),
                )
                .order_by("-total_count")
            )
        except Exception as e:
            logger.exception(f"Error in get_error_category_statistics: {str(e)}")
            return TraceErrorDetail.objects.none().values("category")

    def list_diverse_error_patterns(self, project_id) -> QuerySet:
        try:
            return ErrorPattern.objects.filter(project_id=project_id).order_by(
                "-last_seen", "-confidence_score"
            )
        except Exception as e:
            logger.exception(f"Error in list_diverse_error_patterns: {str(e)}")
            return ErrorPattern.objects.none()

    def list_frequent_error_patterns(
        self, project_id, min_frequency: int = 2, limit: int = 10
    ) -> QuerySet:
        try:
            return ErrorPattern.objects.filter(
                project_id=project_id, frequency__gte=min_frequency
            ).order_by("-frequency", "-confidence_score")[:limit]
        except Exception as e:
            logger.exception(f"Error in list_frequent_error_patterns: {str(e)}")
            return ErrorPattern.objects.none()

    def get_tool_error_aggregates(
        self, project_id, min_count: int = 2, limit: int = 10
    ) -> QuerySet:
        try:
            return (
                TraceErrorDetail.objects.filter(
                    analysis__project_id=project_id,
                    location_spans__isnull=False,
                )
                .exclude(location_spans=[])
                .values("category", "recommendation", "immediate_fix")
                .annotate(error_count=Count("id"))
                .filter(error_count__gte=min_count)
                .order_by("-error_count")[:limit]
            )
        except Exception as e:
            logger.exception(f"Error in get_tool_error_aggregates: {str(e)}")
            return TraceErrorDetail.objects.none().values(
                "category", "recommendation", "immediate_fix"
            )

    def list_unresolved_error_patterns(
        self, project_id, min_frequency: int = 2
    ) -> QuerySet:
        try:
            return ErrorPattern.objects.filter(
                project_id=project_id, is_resolved=False, frequency__gte=min_frequency
            ).order_by("-frequency")
        except Exception as e:
            logger.exception(f"Error in list_unresolved_error_patterns: {str(e)}")
            return ErrorPattern.objects.none()

    def get_retrieval_patterns(self, project_id) -> list[dict]:
        """Retriever spans per project with per-name aggregates.

        Was: ObservationSpan.filter(trace__project_id=,
                                    observation_type="retriever")
               .values("name").annotate(usage_count=Count("id"),
                                        avg_latency=Avg("latency_ms"))
               .order_by("-usage_count")

        CH path: ``CHSpanReader.per_project_group_by_name`` returns the
        same group-by-name aggregate shape. No time filter — the original
        Django query was unbounded, so we pass ``since=None``.

        ``limit=10000`` keeps parity with the prior unbounded Django path
        (reader default is 50 which would silently truncate tail
        patterns). See ``get_tool_usage_patterns`` for the rationale.

        Consumer (``ee/agenthub/traceerroragent/memory.py:373``) wraps the
        return in ``list(retrieval_spans)`` and exposes it under the
        ``retrieval_patterns`` key. Returned dicts include
        ``error_count`` and ``total_cost`` (extra keys from the reader)
        which the consumer ignores; harmless additions.
        """
        with get_reader() as reader:
            return reader.per_project_group_by_name(
                project_id=str(project_id),
                observation_type="retriever",
                limit=10000,
            )

    def get_chunk_usage_patterns(self, project_id) -> list[dict]:
        """CH25-DEFERRED — needs a typed-JSON path predicate (NOT unlocked by wave-3).

        Pattern: ObservationSpan.filter(trace__project_id=,
                                        metadata__chunks__isnull=False)
                   .values("name").annotate(usage_count=Count("id"))
                   .order_by("-usage_count")

        Why wave-3 ``per_project_group_by_name`` does NOT unlock this:
          • The blocker is the JSON-path filter
            ``metadata__chunks__isnull=False``, not the aggregate shape.
          • ``per_project_group_by_name`` accepts ``observation_type`` /
            ``status_filter`` only; there is still no reader API for a
            metadata-key existence predicate.

        Defer until either:
          (a) the reader gains a ``metadata_has_key(...)`` /
              ``metadata_path_exists(...)`` filter (the right place for
              typed-JSON path predicates — companion to wave-3
              ``per_project_group_by_name``), OR
          (b) the chunk-usage feature is rebuilt against a dedicated
              metric or rollup view.

        Currently no production callers — raising NotImplementedError so
        a future caller surfaces the gap rather than silently scanning
        the project's full span set in Python.
        """
        raise NotImplementedError(
            "get_chunk_usage_patterns: pending CHSpanReader JSON-path filter "
            "(metadata.chunks IS NOT NULL). Wave-3 per_project_group_by_name "
            "does NOT unlock this — see method docstring."
        )

    def save_memory_data(
        self,
        project_id: Any,
        memory_type: str,
        memory_key: str,
        memory_data: dict[str, Any],
    ) -> ErrorMemory:
        try:
            memory, created = ErrorMemory.objects.get_or_create(
                project_id=project_id,
                memory_type=memory_type,
                memory_key=memory_key,
                defaults={"memory_data": memory_data, "is_active": True},
            )
            if not created:
                memory.memory_data = memory_data
                memory.last_updated = datetime.now()
            memory.access_count = (memory.access_count or 0) + 1
            memory.last_accessed = datetime.now()
            memory.save()
            return memory
        except Exception as e:
            logger.exception(f"Error in save_memory_data: {str(e)}")
            raise

    def get_memory_data(
        self,
        project_id: Any,
        memory_type: str,
        memory_key: str,
    ) -> ErrorMemory | None:
        try:
            memory = ErrorMemory.objects.filter(
                project_id=project_id,
                memory_type=memory_type,
                memory_key=memory_key,
                is_active=True,
            ).first()
            if memory:
                memory.access_count = (memory.access_count or 0) + 1
                memory.last_accessed = datetime.now()
                memory.save(update_fields=["access_count", "last_accessed"])
            return memory
        except Exception as e:
            logger.exception(f"Error in get_memory_data: {str(e)}")
            return None

    # -------------------- Error clustering helpers --------------------
    def list_recent_error_details(
        self,
        project_id: Any,
        days: int = 30,
        limit: int = 1000,
    ) -> QuerySet:
        """Return recent TraceErrorDetail rows for a project.

        Includes the related analysis for downstream usage. Ordering by analysis date desc.
        """
        try:
            cutoff = datetime.now() - timedelta(days=days)
            return (
                TraceErrorDetail.objects.filter(
                    analysis__project_id=project_id,
                    analysis__analysis_date__gte=cutoff,
                )
                .select_related("analysis")
                .order_by("-analysis__analysis_date")[:limit]
            )
        except Exception as e:
            logger.exception(f"Error in list_recent_error_details: {str(e)}")
            return TraceErrorDetail.objects.none()

    def list_error_details_since(
        self,
        project_id: Any,
        days: int = 30,
        limit: int = 5000,
    ) -> QuerySet:
        """Return TraceErrorDetail rows for a project within a lookback window.

        Includes analysis, trace and project via select_related for efficient access.
        """
        try:
            cutoff = datetime.now() - timedelta(days=days)
            return (
                TraceErrorDetail.objects.filter(
                    analysis__project_id=project_id,
                    analysis__analysis_date__gte=cutoff,
                )
                .select_related("analysis", "analysis__trace", "analysis__project")
                .order_by("-analysis__analysis_date")[:limit]
            )
        except Exception as e:
            logger.exception(f"Error in list_error_details_since: {str(e)}")
            return TraceErrorDetail.objects.none()

    def upsert_trace_error_group(
        self,
        analysis_id: Any,
        cluster_id: str,
        *,
        error_type: str,
        error_ids: list[str],
        combined_impact: str,
        combined_description: str,
        error_count: int,
        trace_impact: Any = None,
    ) -> None:
        """Upsert a TraceErrorGroup row by (analysis_id, cluster_id)."""
        try:
            TraceErrorGroup.objects.update_or_create(
                analysis_id=analysis_id,
                cluster_id=cluster_id,
                defaults={
                    "error_type": error_type,
                    "error_ids": error_ids,
                    "combined_impact": combined_impact,
                    "combined_description": combined_description,
                    "error_count": error_count,
                    "trace_impact": trace_impact,
                },
            )
        except Exception as e:
            logger.exception(f"Error in upsert_trace_error_group: {str(e)}")
            # allow caller to continue

    def get_user_accessible_projects(
        self, organization_id: str, workspace_id: str | None = None
    ) -> list[str]:
        """
        Get list of project IDs that belong to the given organization
        """
        try:
            query = Project.objects.filter(organization_id=organization_id)
            if workspace_id is not None:
                query = query.filter(workspace_id=workspace_id)
            return list(query.values_list("id", flat=True))
        except Exception as e:
            logger.exception(f"Error in get_user_accessible_projects: {str(e)}")
            return []

    def get_clusters_for_feed(
        self,
        project_ids: list[str] | None = None,
        days: int = 7,
        limit: int = 100,
        offset: int = 0,
    ) -> dict:
        """
        Get error clusters for the feed view across multiple projects with pagination
        Returns dict with clusters and total count
        """
        try:

            cutoff_date = timezone.now() - timedelta(days=days)

            # Get total count first (without limit/offset)
            total_count = SQL_query_handler.get_error_clusters_count(
                cutoff_date=cutoff_date, project_ids=project_ids or []
            )

            # Use the SQL query handler method with pagination
            rows = SQL_query_handler.get_error_clusters_for_feed(
                cutoff_date=cutoff_date,
                project_ids=project_ids or [],
                limit=limit,
                offset=offset,
            )

            # Convert rows to list of dicts
            columns = [
                "cluster_id",
                "error_type",
                "combined_impact",
                "combined_description",
                "total_events",
                "unique_traces",
                "last_seen",
                "first_seen",
                "project_id",
                "project_name",
                "unique_users",
                "assignee",
            ]

            clusters = [dict(zip(columns, row, strict=False)) for row in rows]

            return {"clusters": clusters, "total_count": total_count}

        except Exception as e:
            logger.exception(f"Error in get_clusters_for_feed: {str(e)}")
            return {"clusters": [], "total_count": 0}

    def get_cluster_trend_data_hourly(
        self, cluster_id: str, hours: int = 24
    ) -> list[dict[str, Any]]:
        """
        Get hourly event counts for trend visualization
        Returns list of dicts with timestamp and value for each hour
        """
        try:

            now = timezone.now()
            start_time = now - timedelta(hours=hours)

            # Get hourly counts from ErrorClusterTraces
            hourly_data = (
                ErrorClusterTraces.objects.filter(
                    cluster__cluster_id=cluster_id, created_at__gte=start_time
                )
                .annotate(hour=TruncHour("created_at"))
                .values("hour")
                .annotate(count=Count("id"))
                .order_by("hour")
            )

            # Convert to dict for easy lookup
            counts_by_hour = {item["hour"]: item["count"] for item in hourly_data}

            # Build result with all hours, filling in 0 for missing hours
            result = []
            current_hour = start_time.replace(minute=0, second=0, microsecond=0)

            while current_hour <= now:
                result.append(
                    {
                        "timestamp": current_hour.isoformat(),
                        "value": counts_by_hour.get(current_hour, 0),
                    }
                )
                current_hour += timedelta(hours=1)

            return result

        except Exception as e:
            logger.exception(f"Error in get_cluster_trend_data_hourly: {str(e)}")
            # Return empty trend data for the requested hours
            result = []
            start_time = timezone.now() - timedelta(hours=hours)
            current_hour = start_time.replace(minute=0, second=0, microsecond=0)
            end_time = timezone.now()

            while current_hour <= end_time:
                result.append({"timestamp": current_hour.isoformat(), "value": 0})
                current_hour += timedelta(hours=1)

            return result

    def get_cluster_detail_data(
        self, cluster_id: str, organization_id: str | None = None
    ) -> TraceErrorGroup | None:
        """
        Get cluster detail with access validation

        Args:
            cluster_id: The cluster ID
            organization_id: Organization ID for access check

        Returns:
            TraceErrorGroup instance if found and accessible, None otherwise
        """
        try:
            # Get cluster
            cluster = TraceErrorGroup.objects.filter(cluster_id=cluster_id).first()

            if not cluster:
                return None

            # Check project access if organization_id provided
            if organization_id:
                if not Project.objects.filter(
                    id=cluster.project_id, organization_id=organization_id
                ).exists():
                    return None

            return cluster

        except Exception as e:
            logger.exception(f"Error in get_cluster_detail_data: {str(e)}")
            return None

    def get_cluster_trace_navigation(
        self, cluster_id: str, current_trace_id: str | None = None
    ) -> dict[str, Any]:
        """
        Get trace navigation for a cluster (next, previous, first, latest)

        Args:
            cluster_id: The cluster ID
            current_trace_id: Current trace ID for navigation (optional)

        Returns:
            Dict with trace navigation info
        """
        try:

            # Get unique trace_ids ordered by when they first appeared in the cluster
            traces_with_first_seen = (
                ErrorClusterTraces.objects.filter(cluster__cluster_id=cluster_id)
                .exclude(trace=None)
                .values("trace_id")
                .annotate(first_seen=Min("created_at"))
                .order_by("first_seen")
            )

            trace_list = [item["trace_id"] for item in traces_with_first_seen]

            if not trace_list:
                return {
                    "first": None,
                    "latest": None,
                    "next": None,
                    "previous": None,
                    "current_index": 0,
                    "total": 0,
                }

            current_index = len(trace_list) - 1

            navigation = {
                "first": str(trace_list[0]),
                "latest": str(trace_list[-1]),
                "next": None,
                "previous": None,
                "current_index": current_index + 1,
                "total": len(trace_list),
            }

            # If current trace is provided, find navigation
            if current_trace_id:
                try:
                    current_uuid = uuid.UUID(current_trace_id)
                    if current_uuid in trace_list:
                        current_index = trace_list.index(current_uuid)
                    else:
                        logger.warning(
                            f"Trace {current_trace_id} not in cluster, defaulting to latest"
                        )
                except (ValueError, TypeError):
                    logger.warning(
                        f"Invalid trace ID format: {current_trace_id}, defaulting to latest"
                    )
            else:
                logger.debug("No current_trace_id provided, defaulting to latest trace")

            # Set navigation based on current_index
            navigation["current_index"] = current_index + 1
            navigation["current"] = str(trace_list[current_index])

            # Get previous trace if exists
            if current_index > 0:
                navigation["previous"] = str(trace_list[current_index - 1])

            # Get next trace if exists
            if current_index < len(trace_list) - 1:
                navigation["next"] = str(trace_list[current_index + 1])

            return navigation

        except Exception as e:
            logger.exception(f"Error in get_cluster_trace_navigation: {str(e)}")
            return {
                "first": None,
                "latest": None,
                "next": None,
                "previous": None,
                "current_index": 0,
                "total": 0,
            }

    def get_cluster_trend_data(
        self, cluster_id: str, days: int = 30
    ) -> list[dict[str, Any]]:
        """
        Get daily event counts for trend visualization
        Returns list of dicts with timestamp and value for each day
        """
        try:

            now = timezone.now()
            start_date = now - timedelta(days=days)

            # Get daily counts from ErrorClusterTraces
            daily_data = (
                ErrorClusterTraces.objects.filter(
                    cluster__cluster_id=cluster_id, created_at__gte=start_date
                )
                .annotate(date=TruncDate("created_at"))
                .values("date")
                .annotate(count=Count("id"))
                .order_by("date")
            )

            # Convert to dict for easy lookup
            counts_by_date = {item["date"]: item["count"] for item in daily_data}

            # Build result with all days, filling in 0 for missing days
            result = []
            current_date = start_date.date()
            end_date = now.date()

            while current_date <= end_date:
                timestamp = timezone.datetime.combine(
                    current_date, timezone.datetime.min.time()
                ).replace(tzinfo=timezone.get_current_timezone())

                result.append(
                    {
                        "timestamp": timestamp.isoformat(),
                        "value": counts_by_date.get(current_date, 0),
                    }
                )
                current_date += timedelta(days=1)

            return result

        except Exception as e:
            logger.exception(f"Error in get_cluster_trend_data: {str(e)}")
            # Return empty trend data for the requested days
            result = []
            current_date = (timezone.now() - timedelta(days=days)).date()
            end_date = timezone.now().date()

            while current_date <= end_date:
                from datetime import datetime as dt

                timestamp = timezone.make_aware(dt.combine(current_date, dt.min.time()))

                result.append({"timestamp": timestamp.isoformat(), "value": 0})
                current_date += timedelta(days=1)

            return result

    # ============= Falcon Tool Methods =============

    def get_cluster_with_access_check(
        self, cluster_id: str, organization_id: str
    ) -> TraceErrorGroup | None:
        """
        Get a TraceErrorGroup by cluster_id with organization-level access check.
        Returns None if not found or access denied.
        """
        try:
            cluster = (
                TraceErrorGroup.objects.filter(cluster_id=cluster_id, deleted=False)
                .select_related("project")
                .first()
            )

            if not cluster:
                return None

            if not Project.objects.filter(
                id=cluster.project_id, organization_id=organization_id
            ).exists():
                return None

            return cluster
        except Exception as e:
            logger.exception(f"Error in get_cluster_with_access_check: {str(e)}")
            return None

    def get_cluster_trace_ids(self, cluster_id: str, limit: int = 20) -> list[str]:
        """
        Get trace IDs belonging to a cluster, ordered by most recent first.
        Uses .values().annotate() to properly group by trace_id before ordering.
        """
        try:
            rows = (
                ErrorClusterTraces.objects.filter(cluster__cluster_id=cluster_id)
                .exclude(trace=None)
                .values("trace_id")
                .annotate(latest=Max("created_at"))
                .order_by("-latest")[:limit]
            )
            return [row["trace_id"] for row in rows]
        except Exception as e:
            logger.exception(f"Error in get_cluster_trace_ids: {str(e)}")
            return []

    def get_error_details_for_traces(self, trace_ids: list[str]) -> QuerySet:
        """
        Get all TraceErrorDetail records for a set of trace IDs.
        Returns queryset with analysis and trace pre-loaded.
        """
        try:
            return (
                TraceErrorDetail.objects.filter(analysis__trace_id__in=trace_ids)
                .select_related("analysis", "analysis__trace", "analysis__project")
                .order_by("-analysis__analysis_date", "error_id")
            )
        except Exception as e:
            logger.exception(f"Error in get_error_details_for_traces: {str(e)}")
            return TraceErrorDetail.objects.none()

    def get_analyses_for_traces(self, trace_ids: list[str]) -> QuerySet:
        """
        Get the latest TraceErrorAnalysis for each trace in the given set.
        Returns queryset with trace and project pre-loaded.
        """
        try:
            return (
                TraceErrorAnalysis.objects.filter(trace_id__in=trace_ids)
                .select_related("trace", "project")
                .order_by("trace_id", "-analysis_date")
                .distinct("trace_id")
            )
        except Exception as e:
            logger.exception(f"Error in get_analyses_for_traces: {str(e)}")
            return TraceErrorAnalysis.objects.none()

    # ============= TraceErrorAnalysisTask Methods =============

    def get_or_create_task_for_project(self, project, default_sampling_rate=0.01):
        """
        Get or create a TraceErrorAnalysisTask for a project
        """
        try:
            task = TraceErrorAnalysisTask.all_objects.filter(project=project).first()
            if task:
                if task.deleted:
                    task.deleted = False
                    task.deleted_at = None
                    task.sampling_rate = default_sampling_rate
                    task.status = TraceErrorTaskStatus.WAITING
                    task.save(
                        update_fields=[
                            "deleted",
                            "deleted_at",
                            "sampling_rate",
                            "status",
                            "updated_at",
                        ]
                    )
                    return task, True
                return task, False

            return (
                TraceErrorAnalysisTask.objects.create(
                    project=project,
                    sampling_rate=default_sampling_rate,
                    status=TraceErrorTaskStatus.WAITING,
                ),
                True,
            )
        except Exception as e:
            logger.exception(f"Error in get_or_create_task_for_project: {str(e)}")
            return None, False

    def get_active_tasks(self):
        """
        Get all active TraceErrorAnalysisTasks
        """
        try:
            qs = TraceErrorAnalysisTask.objects.filter(
                status__in=[
                    TraceErrorTaskStatus.RUNNING,
                    TraceErrorTaskStatus.WAITING,
                ],
                deleted=False,
            )
            voice_enabled = os.getenv("VOICE_COMPASS_ENABLED", "true").lower() == "true"
            if not voice_enabled:
                qs = qs.exclude(project__source=ProjectSourceChoices.SIMULATOR.value)
            return qs.select_related("project")
        except Exception as e:
            logger.exception(f"Error in get_active_tasks: {str(e)}")
            return TraceErrorAnalysisTask.objects.none()

    def get_traces_to_process_for_task(self, task, days_back=1):
        since_time = timezone.now() - timedelta(days=days_back)

        # Get pending traces
        pending_ids = list(
            Trace.objects.filter(
                project=task.project,
                created_at__gt=since_time,
                error_analysis_status=TraceErrorAnalysisStatus.PENDING,
            ).values_list("id", flat=True)
        )

        if not pending_ids:
            return []

        # Just apply sampling rate to pending traces!
        to_process_count = max(1, int(len(pending_ids) * task.sampling_rate))
        selected_ids = random.sample(
            pending_ids, min(to_process_count, len(pending_ids))
        )

        # Mark rest as SKIPPED
        skipped_ids = set(pending_ids) - set(selected_ids)
        if skipped_ids:
            Trace.objects.filter(id__in=skipped_ids).update(
                error_analysis_status=TraceErrorAnalysisStatus.SKIPPED
            )

        logger.info(
            f"Selected {len(selected_ids)} from {len(pending_ids)} pending traces"
        )
        return selected_ids

    @transaction.atomic
    def commit_traces_for_processing(self, trace_ids: list) -> list:
        """
        Atomically updates a list of trace IDs to PROCESSING state.
        This prevents race conditions between multiple workers.

        Args:
            trace_ids: A list of trace IDs that have been selected for processing.

        Returns:
            A list of the trace IDs that were successfully updated and "claimed"
            by this process.
        """
        if not trace_ids:
            return []

        # Atomically update the status to PROCESSING for PENDING traces
        traces_to_claim = Trace.objects.filter(
            id__in=trace_ids, error_analysis_status=TraceErrorAnalysisStatus.PENDING
        ).select_for_update(skip_locked=True)

        # Get the IDs of the traces we successfully locked.
        claimed_ids = [str(t.id) for t in traces_to_claim]

        if not claimed_ids:
            logger.info(
                "No traces were claimed, another worker may have processed them."
            )
            return []

        if len(claimed_ids) < len(trace_ids):
            logger.warning(
                f"Race condition: Could only claim {len(claimed_ids)} of {len(trace_ids)} traces."
            )

        # Now, update ONLY the traces we successfully claimed.
        Trace.objects.filter(id__in=claimed_ids).update(
            error_analysis_status=TraceErrorAnalysisStatus.PROCESSING
        )

        return claimed_ids

    def update_task_status(self, task, status, **kwargs):
        """
        Update task status and optional fields
        """

        try:
            update_fields = ["status"]
            task.status = status

            # Add any additional fields to update
            for field, value in kwargs.items():
                if hasattr(task, field):
                    setattr(task, field, value)
                    update_fields.append(field)

            task.save(update_fields=update_fields)
            return True
        except Exception as e:
            logger.exception(f"Error in update_task_status: {str(e)}")
            return False

    def update_task_statistics(
        self, task_id, successful=0, failed=0, errors_found=0, last_trace_id=None
    ):
        """
        Update task statistics after processing
        """

        try:
            task = TraceErrorAnalysisTask.objects.get(id=task_id)

            task.total_traces_analyzed += successful
            task.failed_analyses += failed
            task.total_errors_found += errors_found
            task.status = TraceErrorTaskStatus.WAITING

            update_fields = [
                "total_traces_analyzed",
                "failed_analyses",
                "total_errors_found",
                "status",
            ]

            if last_trace_id:
                task.last_trace_analyzed_id = str(last_trace_id)
                update_fields.append("last_trace_analyzed_id")

            task.save(update_fields=update_fields)
            return True

        except Exception as e:
            logger.exception(f"Error in update_task_statistics: {str(e)}")
            return False

    def add_failed_trace(self, task_id, trace_id):
        """
        Add a trace to the failed traces list
        """

        try:
            task = TraceErrorAnalysisTask.objects.get(id=task_id)
            trace = Trace.objects.get(id=trace_id)
            task.failed_traces.add(trace)
            return True
        except Exception as e:
            logger.exception(f"Error in add_failed_trace: {str(e)}")
            return False

    def ensure_all_projects_have_tasks(self, timeout=30):
        """
        Ensure all projects have a TraceErrorAnalysisTask
        """
        try:
            stale_time = timezone.now() - timedelta(minutes=timeout)
            stuck_tasks = TraceErrorAnalysisTask.objects.filter(
                status=TraceErrorTaskStatus.RUNNING, last_run_at__lt=stale_time
            )

            stuck_count = stuck_tasks.count()
            if stuck_count > 0:
                logger.warning(
                    f"Found {stuck_count} stuck tasks. Resetting to WAITING."
                )

                stuck_tasks.update(status=TraceErrorTaskStatus.WAITING)
            all_projects = Project.objects.filter(deleted=False)
            voice_enabled = os.getenv("VOICE_COMPASS_ENABLED", "true").lower() == "true"
            if not voice_enabled:
                all_projects = all_projects.exclude(
                    source=ProjectSourceChoices.SIMULATOR.value
                )
            created_count = 0

            for project in all_projects:
                _, created = self.get_or_create_task_for_project(project)
                if created:
                    created_count += 1
                    logger.info(
                        f"Auto-created trace error task for project {project.name}"
                    )

            return created_count
        except Exception as e:
            logger.exception(f"Error in ensure_all_projects_have_tasks: {str(e)}")
            return 0

    def ingest_trace_error_embeddings(self, trace_id: str) -> int:
        """
        Generate and store embeddings for all errors from a single trace analysis.
        Called after trace analysis completes.

        Returns number of embeddings created.
        """
        try:
            # Get the analysis and error details for this trace
            analysis = (
                TraceErrorAnalysis.objects.filter(trace_id=trace_id)
                .select_related("project", "trace")
                .first()
            )

            if not analysis:
                logger.warning(f"No analysis found for trace {trace_id}")
                return 0

            error_details = list(
                TraceErrorDetail.objects.filter(analysis=analysis).select_related(
                    "analysis__project", "analysis__trace"
                )
            )

            if not error_details:
                logger.info(f"No error details to embed for trace {trace_id}")
                return 0

            # Import here to avoid circular imports
            try:
                from ee.agenthub.traceerroragent.error_cluster import (
                    ErrorEmbeddingClusterer,
                )
            except ImportError:
                if settings.DEBUG:
                    logger.warning("Could not import ee.agenthub.traceerroragent.error_cluster", exc_info=True)
                return None
            from agentic_eval.core.embeddings.embedding_manager import (
                EmbeddingManager,
                model_manager,
            )

            clusterer = ErrorEmbeddingClusterer()
            text_embed = model_manager.text_model
            em = EmbeddingManager()

            vectors = []
            metadata_list = []

            for detail in error_details:
                try:
                    # Check if already embedded
                    if self._embedding_exists_for_detail(
                        detail.id, clusterer.table_name
                    ):
                        logger.debug(
                            f"Embedding already exists for error detail {detail.id}"
                        )
                        continue

                    # Use clusterer's method to build text
                    text = clusterer._build_embedding_text_from_detail(detail)
                    if not text:
                        continue

                    # Generate embedding
                    embedding = text_embed(text)
                    if not embedding:
                        continue

                    # Prepare metadata
                    metadata = {
                        "error_detail_pk": str(detail.id),
                        "error_id": detail.error_id or "",
                        "trace_id": str(analysis.trace_id),
                        "project_id": str(analysis.project_id),
                        "category": detail.category or "Uncategorized",
                        "family": clusterer._normalize_category_family(detail.category),
                        "input_type": "text",
                        "embedded_at": datetime.now().isoformat(),
                        "clustered": "false",  # Track if assigned to cluster yet
                    }

                    vectors.append(embedding)
                    metadata_list.append(metadata)

                except Exception as e:
                    logger.error(
                        f"Failed to generate embedding for error detail {detail.id}: {str(e)}"
                    )
                    continue

            # Bulk insert embeddings
            if vectors and metadata_list:
                try:
                    em.db_client.bulk_upsert_vectors(
                        table_name=clusterer.table_name,
                        eval_id=str(analysis.project_id),
                        vectors=vectors,
                        metadata_list=metadata_list,
                        unique_keys=["error_detail_pk"],
                    )
                    logger.info(
                        f"Ingested {len(vectors)} embeddings for trace {trace_id}"
                    )
                finally:
                    em.close()

            return len(vectors)

        except Exception as e:
            logger.exception(
                f"Error ingesting embeddings for trace {trace_id}: {str(e)}"
            )
            return 0

    def _embedding_exists_for_detail(
        self, error_detail_id: str, table_name: str
    ) -> bool:
        """Check if embedding already exists for an error detail"""
        try:
            from agentic_eval.core.database.ch_vector import ClickHouseVectorDB

            db = ClickHouseVectorDB()
            try:
                # Correctly check if error_detail_pk = detail_id in the metadata
                query = f"""
                    SELECT 1
                    FROM {table_name}
                    WHERE deleted = 0
                    AND metadata.value[indexOf(metadata.key, 'error_detail_pk')] = %(detail_id)s
                    LIMIT 1
                """
                result = db.client.execute(query, {"detail_id": str(error_detail_id)})
                return len(result) > 0
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"Error checking embedding existence: {str(e)}")
            return False
