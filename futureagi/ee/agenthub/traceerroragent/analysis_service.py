from datetime import datetime, timedelta
from typing import Any

import structlog
from django.db import transaction
from django.db.models import Avg, Count, Max, Q

from tracer.models.project import Project
from tracer.models.trace_error_analysis import (
    AnalysisErrorGroup,
    ErrorMemory,
    ErrorPattern,
    TraceErrorAnalysis,
    TraceErrorDetail,
)

logger = structlog.get_logger(__name__)


class TraceErrorAnalysisService:
    """Service class for managing trace error analysis data"""

    def __init__(self, project_id: str):
        self.project_id = project_id

    def save_analysis_result(
        self, trace_id: str, analysis_result: dict[str, Any]
    ) -> TraceErrorAnalysis:
        """Save complete analysis result under the current durable owner."""
        from tracer.services.analysis_result_binding import legacy_attempt

        with legacy_attempt(self.project_id, trace_id):
            return self._save_owned_result(trace_id, analysis_result)

    def _save_owned_result(self, trace_id, analysis_result):
        from ee.agenthub.traceerroragent.background_payload_reports import (
            require_publication,
        )

        publication = analysis_result
        analysis_result = require_publication(analysis_result)
        try:
            with transaction.atomic():
                # Trace lives only in CH post-cutover; the service already knows
                # its project (kept in PG). Store the raw trace_id (the FK is
                # db_constraint=False) rather than a PG Trace object.
                project = Project.objects.get(id=self.project_id)
                from tracer.services.analysis_result_binding import current_attempt

                if current_attempt() is not None:
                    from ee.agenthub.traceerroragent.background_payload_consumers import (
                        save_publication,
                    )

                    return save_publication(
                        trace_id,
                        project,
                        publication,
                        TraceErrorAnalysis,
                        TraceErrorDetail,
                    )

                # Create main analysis record
                analysis = TraceErrorAnalysis.drafts.create(
                    trace_id=trace_id,
                    project=project,
                    agent_version="1.0",
                    memory_enhanced=analysis_result.get("memory_context", {}).get(
                        "episodic_memory_used", False
                    ),
                    # Summary metrics
                    overall_score=analysis_result.get("summary", {}).get(
                        "overall_score"
                    ),
                    total_errors=analysis_result.get("summary", {}).get(
                        "error_count", 0
                    ),
                    high_impact_errors=len(
                        [
                            e
                            for e in analysis_result.get("errors", [])
                            if e.get("impact") == "HIGH"
                        ]
                    ),
                    medium_impact_errors=len(
                        [
                            e
                            for e in analysis_result.get("errors", [])
                            if e.get("impact") == "MEDIUM"
                        ]
                    ),
                    low_impact_errors=len(
                        [
                            e
                            for e in analysis_result.get("errors", [])
                            if e.get("impact") == "LOW"
                        ]
                    ),
                    recommended_priority=analysis_result.get("summary", {}).get(
                        "recommended_priority", "LOW"
                    ),
                    # Updated detailed scores
                    factual_grounding_score=analysis_result.get("scores", {})
                    .get("factual_grounding", {})
                    .get("score"),
                    factual_grounding_reason=analysis_result.get("scores", {})
                    .get("factual_grounding", {})
                    .get("reason"),
                    privacy_and_safety_score=analysis_result.get("scores", {})
                    .get("privacy_and_safety", {})
                    .get("score"),
                    privacy_and_safety_reason=analysis_result.get("scores", {})
                    .get("privacy_and_safety", {})
                    .get("reason"),
                    instruction_adherence_score=analysis_result.get("scores", {})
                    .get("instruction_adherence", {})
                    .get("score"),
                    instruction_adherence_reason=analysis_result.get("scores", {})
                    .get("instruction_adherence", {})
                    .get("reason"),
                    optimal_plan_execution_score=analysis_result.get("scores", {})
                    .get("optimal_plan_execution", {})
                    .get("score"),
                    optimal_plan_execution_reason=analysis_result.get("scores", {})
                    .get("optimal_plan_execution", {})
                    .get("reason"),
                    # Insights and memory
                    insights=analysis_result.get("summary", {}).get("insights"),
                    memory_context=analysis_result.get("memory_context", {}),
                    grouped_errors_count=len(analysis_result.get("grouped_errors", [])),
                )

                # Save individual error details with new structure
                for error in analysis_result.get("errors", []):
                    TraceErrorDetail.drafts.create(
                        analysis=analysis,
                        error_id=error.get("error_id"),
                        cluster_id=error.get("cluster_id"),
                        category=error.get("category", ""),
                        impact=error.get("impact", "MEDIUM"),
                        urgency_to_fix=error.get("urgency_to_fix", "HIGH"),
                        location_spans=error.get("location_spans", []),
                        evidence_snippets=error.get("evidence_snippets", []),
                        description=error.get("description"),
                        root_causes=error.get("root_causes", []),
                        recommendation=error.get("recommendation"),
                        immediate_fix=error.get("immediate_fix"),
                        trace_impact=error.get("trace_impact"),
                        trace_assessment=error.get("trace_assessment"),
                        llm_analysis=error.get("llm_analysis"),
                        memory_enhanced=error.get("memory_enhanced", False),
                    )

                for group in analysis_result.get("grouped_errors", []):
                    AnalysisErrorGroup.drafts.create(
                        analysis=analysis,
                        cluster_id=group.get("cluster_id"),
                        error_type=group.get("error_type"),
                        error_ids=group.get("error_ids", []),
                        affected_spans=group.get("affected_spans", []),
                        combined_impact=group.get("combined_impact", "MEDIUM"),
                        combined_description=group.get("combined_description"),
                        error_count=group.get("error_count", 0),
                        trace_impact=group.get("trace_impact"),
                    )

                # Legacy service calls retain their immediate pattern updates.
                from tracer.services.analysis_result_binding import current_attempt

                if current_attempt() is None:
                    self._update_error_patterns(analysis_result.get("errors", []))

                logger.info(
                    f"Saved analysis result for trace {trace_id} with {len(analysis_result.get('errors', []))} errors"
                )
                return analysis

        except Exception as e:
            logger.error(f"Error saving analysis result: {str(e)}")
            raise

    def _learn_published_result(self, analysis):
        """Called only by the winning CAS, inside its publication transaction."""
        from tracer.services.analysis_result_snapshot import materialize_analysis

        # Read persisted immutable children, not the model's mutable draft dict
        # or a fresh lookup through a possibly replaced lifecycle binding.
        snapshot = materialize_analysis(analysis)
        # The immutable published snapshot is the typed learning input. Do not
        # turn it back into a permissive dictionary with evidence defaults.
        self._update_error_patterns(snapshot._analysis_snapshot_details, strict=True)

    def _update_error_patterns(self, errors: list[dict], *, strict=False):
        """Upsert error patterns for semantic memory with incremental merge."""
        try:
            for error in errors:
                category_path = error.category if strict else error.get("category", "")
                parts = category_path.split(" > ") if category_path else []
                if len(parts) >= 3:
                    _category, subcategory, specific_error = (
                        parts[0],
                        parts[1],
                        parts[2],
                    )
                elif len(parts) == 2:
                    _category, subcategory, specific_error = parts[0], parts[1], None
                else:
                    _category, subcategory, specific_error = (
                        category_path,
                        "General",
                        None,
                    )

                pattern_description = (
                    error.description if strict else error.get("description", "")
                )
                recommendation = (
                    error.recommendation if strict else error.get("recommendation", "")
                )
                immediate_fix = (
                    error.immediate_fix if strict else error.get("immediate_fix", "")
                )
                root_causes = (
                    error.root_causes if strict else error.get("root_causes", [])
                )

                defaults = {
                    "pattern_type": "error_pattern",
                    "recommendation": recommendation,
                    "immediate_fix": immediate_fix,
                    "root_causes": root_causes,
                    "confidence_score": 0.5,
                    "frequency": 1,
                }

                pattern, created = ErrorPattern.objects.get_or_create(
                    project_id=self.project_id,
                    category=category_path,
                    subcategory=subcategory,
                    specific_error=specific_error,
                    pattern_description=pattern_description,
                    defaults=defaults,
                )

                if not created:
                    # Increment frequency and lightly merge fields
                    pattern.frequency = (pattern.frequency or 0) + 1
                    if recommendation and not pattern.recommendation:
                        pattern.recommendation = recommendation
                    if immediate_fix and not pattern.immediate_fix:
                        pattern.immediate_fix = immediate_fix
                    try:
                        existing_rc = pattern.root_causes or []
                        pattern.root_causes = list({*existing_rc, *root_causes})
                    except Exception:
                        pass
                    pattern.save(
                        update_fields=[
                            "frequency",
                            "recommendation",
                            "immediate_fix",
                            "root_causes",
                            "last_seen",
                        ]
                    )
        except Exception as e:
            logger.error(f"Error updating error patterns: {str(e)}")
            if strict:
                raise

    def get_analysis_by_trace(self, trace_id: str) -> TraceErrorAnalysis | None:
        """Get analysis result for a specific trace"""
        try:
            from tracer.services.analysis_result_snapshot import read_analysis

            return read_analysis(TraceErrorAnalysis.objects.filter(trace_id=trace_id))
        except Exception as e:
            logger.error(f"Error getting analysis for trace {trace_id}: {str(e)}")
            return None

    def get_recent_analyses(
        self, days: int = 30, limit: int = 100
    ) -> list[TraceErrorAnalysis]:
        """Get recent analysis results"""
        try:
            from tracer.services.analysis_result_snapshot import read_analyses

            cutoff_date = datetime.now() - timedelta(days=days)
            queryset = TraceErrorAnalysis.objects.filter(
                project_id=self.project_id, analysis_date__gte=cutoff_date
            ).order_by("-analysis_date")[:limit]
            return read_analyses(queryset)
        except Exception as e:
            logger.error(f"Error getting recent analyses: {str(e)}")
            return []

    def get_error_statistics(self, days: int = 30) -> dict[str, Any]:
        """Get error statistics for the project with new structure"""
        try:
            cutoff_date = datetime.now() - timedelta(days=days)

            # Get error counts by category (using full category path)
            category_stats = (
                TraceErrorDetail.objects.filter(
                    analysis__project_id=self.project_id,
                    analysis__analysis_date__gte=cutoff_date,
                )
                .values("category")
                .annotate(
                    count=Count("id"),
                    high_impact=Count("id", filter=Q(impact="HIGH")),
                    medium_impact=Count("id", filter=Q(impact="MEDIUM")),
                    low_impact=Count("id", filter=Q(impact="LOW")),
                )
                .order_by("-count")
            )

            # Get error counts by subcategory
            subcategory_stats = (
                TraceErrorDetail.objects.filter(
                    analysis__project_id=self.project_id,
                    analysis__analysis_date__gte=cutoff_date,
                )
                .values("subcategory")
                .annotate(count=Count("id"))
                .order_by("-count")[:20]
            )

            # Get tool error patterns
            TraceErrorDetail.objects.filter(
                analysis__project_id=self.project_id,
                analysis__analysis_date__gte=cutoff_date,
                location_spans__isnull=False,
            ).exclude(location_spans=[])

            return {
                "category_statistics": list(category_stats),
                "subcategory_statistics": list(subcategory_stats),
                "total_analyses": TraceErrorAnalysis.objects.filter(
                    project_id=self.project_id, analysis_date__gte=cutoff_date
                ).count(),
                "average_score": TraceErrorAnalysis.objects.filter(
                    project_id=self.project_id, analysis_date__gte=cutoff_date
                ).aggregate(avg_score=Avg("overall_score"))["avg_score"]
                or 0.0,
            }

        except Exception as e:
            logger.error(f"Error getting error statistics: {str(e)}")
            return {}

    def get_frequent_errors(self, limit: int = 10) -> list[dict]:
        """Get most frequent errors for the project with new structure"""
        try:
            queryset = (
                TraceErrorDetail.objects.filter(analysis__project_id=self.project_id)
                .values("category", "subcategory")
                .annotate(
                    frequency=Count("id"),
                    avg_impact=Avg("impact"),
                    last_seen=Max("analysis__analysis_date"),
                )
                .order_by("-frequency")[:limit]
            )
            return list(queryset)

        except Exception as e:
            logger.error(f"Error getting frequent errors: {str(e)}")
            return []

    def get_error_patterns(self, category: str | None = None) -> list[ErrorPattern]:
        """Get learned error patterns with new structure"""
        try:
            queryset = ErrorPattern.objects.filter(
                project_id=self.project_id, is_active=True
            )

            if category:
                queryset = queryset.filter(category__icontains=category)

            ordered_queryset = queryset.order_by("-frequency", "-last_seen")
            return list(ordered_queryset)

        except Exception as e:
            logger.error(f"Error getting error patterns: {str(e)}")
            return []

    def save_memory(self, memory_type: str, memory_key: str, memory_data: dict):
        """Save memory data for future use"""
        try:
            memory, created = ErrorMemory.objects.get_or_create(
                project_id=self.project_id,
                memory_type=memory_type,
                memory_key=memory_key,
                defaults={"memory_data": memory_data, "is_active": True},
            )

            if not created:
                # Update existing memory
                memory.memory_data = memory_data
                memory.last_updated = datetime.now()
                memory.access_count += 1
                memory.last_accessed = datetime.now()
                memory.save()

            return memory

        except Exception as e:
            logger.error(f"Error saving memory: {str(e)}")
            return None

    def get_memory(self, memory_type: str, memory_key: str) -> ErrorMemory | None:
        """Get memory data"""
        try:
            memory = ErrorMemory.objects.filter(
                project_id=self.project_id,
                memory_type=memory_type,
                memory_key=memory_key,
                is_active=True,
            ).first()

            if memory:
                memory.access_count += 1
                memory.last_accessed = datetime.now()
                memory.save()

            return memory

        except Exception as e:
            logger.error(f"Error getting memory: {str(e)}")
            return None

    def search_analyses(self, filters: dict[str, Any]) -> list[TraceErrorAnalysis]:
        """Search analyses with filters"""
        try:
            from tracer.services.analysis_result_snapshot import read_analyses

            queryset = TraceErrorAnalysis.objects.filter(project_id=self.project_id)

            # Apply filters
            if filters.get("min_score"):
                queryset = queryset.filter(overall_score__gte=filters["min_score"])

            if filters.get("max_score"):
                queryset = queryset.filter(overall_score__lte=filters["max_score"])

            if filters.get("priority"):
                queryset = queryset.filter(recommended_priority=filters["priority"])

            if filters.get("min_errors"):
                queryset = queryset.filter(total_errors__gte=filters["min_errors"])

            if filters.get("max_errors"):
                queryset = queryset.filter(total_errors__lte=filters["max_errors"])

            if filters.get("memory_enhanced") is not None:
                queryset = queryset.filter(memory_enhanced=filters["memory_enhanced"])

            if filters.get("date_from"):
                queryset = queryset.filter(analysis_date__gte=filters["date_from"])

            if filters.get("date_to"):
                queryset = queryset.filter(analysis_date__lte=filters["date_to"])

            return read_analyses(queryset.order_by("-analysis_date"))

        except Exception as e:
            logger.error(f"Error searching analyses: {str(e)}")
            return []
