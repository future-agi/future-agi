import hashlib
from datetime import datetime, timedelta
from typing import Any

import structlog

logger = structlog.get_logger(__name__)
from tracer.queries.error_analysis import TraceErrorAnalysisDB
from tracer.models.trace_error_analysis import ErrorMemory
from tracer.queries.error_analysis import TraceErrorAnalysisDB


class EpisodicMemory:
    """Stores episodic memory of past trace executions and their outcomes"""

    def __init__(self, project_id: str):
        self.project_id = project_id
        self.memory_window = 30  # days to look back for episodic memory
        self.db = TraceErrorAnalysisDB()

    def get_recent_traces(self, limit: int = 50) -> list[dict]:
        cutoff_date = datetime.now() - timedelta(days=self.memory_window)

        recent_analyses = self.db.get_recent_trace_error_analyses(
            project_id=self.project_id,
            cutoff_date=cutoff_date,
            limit=limit,
        )

        episodic_memories = []
        for analysis in recent_analyses:
            error_details = self.db.get_error_details_for_analysis(analysis)
            error_annotations = self.db.get_error_annotations_for_trace(analysis.trace)
            error_spans = self.db.get_error_spans_for_trace(analysis.trace)

            episodic_memories.append({
                'trace_id': str(analysis.trace.id),
                'analysis_id': str(analysis.id),
                'created_at': analysis.analysis_date.isoformat(),
                'input': analysis.trace.input,
                'output': analysis.trace.output,
                'overall_score': analysis.overall_score,
                'total_errors': analysis.total_errors,
                'high_impact_errors': analysis.high_impact_errors,
                'medium_impact_errors': analysis.medium_impact_errors,
                'low_impact_errors': analysis.low_impact_errors,
                'memory_enhanced': analysis.memory_enhanced,
                'error_annotations': list(error_annotations.values()),
                'error_spans_count': error_spans.count(),
                'project_version': analysis.trace.project_version.name if analysis.trace.project_version else 'unknown',
                'error_details': [
                    {
                        'error_id': detail.error_id,
                        'cluster_id': detail.cluster_id,
                        'category': detail.category,
                        'impact': detail.impact,
                        'urgency_to_fix': detail.urgency_to_fix,
                        'recommendation': detail.recommendation,
                        'immediate_fix': detail.immediate_fix,
                        'root_causes': detail.root_causes
                    } for detail in error_details
                ]
            })

        return episodic_memories

    def get_tool_usage_patterns(self) -> dict[str, Any]:
        """Analyze tool usage patterns across recent traces using database models"""
        cutoff_date = datetime.now() - timedelta(days=self.memory_window)

        # Get tool usage patterns from spans
        tool_patterns = self.db.get_tool_usage_patterns(self.project_id, cutoff_date)
        error_patterns = self.db.get_error_category_aggregates(self.project_id, cutoff_date)
        tool_error_patterns = self.db.get_tool_error_patterns(self.project_id, cutoff_date, limit=10)

        return {
            'tool_patterns': list(tool_patterns),
            'error_patterns': list(error_patterns),
            'tool_error_patterns': list(tool_error_patterns.values('category', 'impact', 'recommendation', 'immediate_fix')[:10]),
            'memory_window_days': self.memory_window
        }

    def get_similar_traces(self, current_trace_input: str, limit: int = 10) -> list[dict]:
        """Find traces with similar inputs to learn from past experiences using database models"""
        cutoff_date = datetime.now() - timedelta(days=self.memory_window)

        # Get analyses with similar inputs
        similar_analyses = self.db.get_analyses_for_similarity(
            project_id=self.project_id,
            cutoff_date=cutoff_date
        )

        # Filter by similar input characteristics
        current_input_str = str(current_trace_input).lower()
        similar_results = []

        for analysis in similar_analyses:
            trace_input_str = str(analysis.trace.input).lower()

            # Calculate similarity score (simple keyword matching)
            current_words = set(current_input_str.split())
            trace_words = set(trace_input_str.split())

            if current_words and trace_words:
                similarity = len(current_words.intersection(trace_words)) / len(current_words.union(trace_words))

                if similarity > 0.3:  # 30% similarity threshold
                    # Get error details for this analysis
                    error_details = self.db.get_error_details_for_analysis(analysis)

                    similar_results.append({
                        'trace_id': str(analysis.trace.id),
                        'analysis_id': str(analysis.id),
                        'similarity_score': similarity,
                        'input': analysis.trace.input,
                        'output': analysis.trace.output,
                        'overall_score': analysis.overall_score,
                        'total_errors': analysis.total_errors,
                        'memory_enhanced': analysis.memory_enhanced,
                        'created_at': analysis.analysis_date.isoformat(),
                        'project_version': analysis.trace.project_version.name if analysis.trace.project_version else 'unknown',
                        'error_details': [
                            {
                                'error_id': detail.error_id,
                                'cluster_id': detail.cluster_id,
                                'category': detail.category,
                                'impact': detail.impact,
                                'recommendation': detail.recommendation,
                                'immediate_fix': detail.immediate_fix,
                                'root_causes': detail.root_causes
                            } for detail in error_details
                        ]
                    })

        return sorted(similar_results, key=lambda x: x['similarity_score'], reverse=True)[:limit]

    def save_note(self, trace_id: str, note: dict[str, Any]) -> ErrorMemory | None:
        """Save a compact episodic note (agent scratchpad) with deduplication.

        Stored shape (example): {"title": str, "note": str, "category": str, "tags": list}
        """
        try:
            minimal = {
                'title': str(note.get('title', ''))[:120],
                'note': str(note.get('note', ''))[:600],
                'category': str(note.get('category', ''))[:200],
                'tags': note.get('tags', [])[:10],
            }
            raw = f"{trace_id}|{minimal['title']}|{minimal['note']}|{minimal['category']}|{','.join(minimal.get('tags') or [])}"
            h = hashlib.sha1(raw.encode('utf-8')).hexdigest()  # stable short hash
            key = f"note:trace:{trace_id}:{h}"

            memory = self.db.get_or_create_memory(
                project_id=self.project_id,
                memory_type='episodic',
                memory_key=key,
                memory_data=minimal,
            )
            return memory
        except Exception as e:
            logger.error(f"Error saving episodic note: {str(e)}")
            return None

    def get_recent_notes(self, limit: int = 20, days: int | None = None) -> list[dict[str, Any]]:
        """Fetch recent episodic scratchpad notes with light shaping.

        Returns a compact list of dicts with: title, note, category, tags, last_updated, trace_id (if parsable).
        """
        try:
            lookback_days = days if days is not None else self.memory_window
            cutoff_date = datetime.now() - timedelta(days=lookback_days)
            qs = self.db.get_recent_notes(
                project_id=self.project_id,
                memory_type='episodic',
                cutoff_date=cutoff_date,
                limit=limit,
            )

            results: list[dict[str, Any]] = []
            for m in qs:
                data = m.memory_data or {}
                # Try to parse trace_id from key: note:trace:<trace_id>:<hash>
                trace_id = None
                if isinstance(m.memory_key, str) and m.memory_key.startswith('note:trace:'):
                    parts = m.memory_key.split(':')
                    if len(parts) >= 3:
                        trace_id = parts[2]
                results.append({
                    'title': str(data.get('title', ''))[:120],
                    'note': str(data.get('note', ''))[:400],
                    'category': str(data.get('category', ''))[:200],
                    'tags': (data.get('tags') or [])[:10],
                    'last_updated': m.last_updated.isoformat() if m.last_updated else None,
                    'trace_id': trace_id,
                })
            return results
        except Exception as e:
            logger.error(f"Error fetching episodic notes: {str(e)}")
            return []

    def get_recent_notes_for_trace(self, trace_id: str, limit: int = 10) -> list[dict[str, Any]]:
        """Fetch recent episodic notes for a specific trace_id."""
        try:
            key_prefix = f"note:trace:{trace_id}:"
            qs = self.db.get_recent_notes(
                project_id=self.project_id,
                memory_type='episodic',
                cutoff_date=None,
                limit=limit,
                memory_key_prefix=key_prefix,
            )

            results: list[dict[str, Any]] = []
            for m in qs:
                data = m.memory_data or {}
                results.append({
                    'title': str(data.get('title', ''))[:120],
                    'note': str(data.get('note', ''))[:400],
                    'category': str(data.get('category', ''))[:200],
                    'tags': (data.get('tags') or [])[:10],
                    'last_updated': m.last_updated.isoformat() if m.last_updated else None,
                    'trace_id': trace_id,
                })
            return results
        except Exception as e:
            logger.error(f"Error fetching episodic notes for trace {trace_id}: {str(e)}")
            return []

    def prune_notes(self, max_active: int = 1000, ttl_days: int | None = None) -> int:
        """Soft-prune episodic notes by TTL and cap oldest beyond max_active.

        Returns the number of notes deactivated.
        """
        try:
            cutoff_date = None
            if ttl_days is not None:
                cutoff_date = datetime.now() - timedelta(days=ttl_days)

            deactivated = self.db.prune_notes(
                project_id=self.project_id,
                memory_type='episodic',
                max_active=max_active,
                cutoff_date=cutoff_date,
            )
            return deactivated
        except Exception as e:
            logger.error(f"Error pruning episodic notes: {str(e)}")
            return 0


class SemanticMemory:
    """Stores semantic knowledge learned from feedback and patterns"""

    def __init__(self, project_id: str):
        self.project_id = project_id
        self.db = TraceErrorAnalysisDB()
    def get_error_taxonomy_learnings(self) -> dict[str, Any]:
        """Get learned patterns about error categories and their solutions using database models"""
        # Get human feedback from annotations
        feedback_annotations = self.db.get_feedback_annotations(self.project_id)

        # Get learned error patterns from database - get variety of patterns with new structure
        learned_patterns = self.db.get_learned_patterns_values(self.project_id)

        # Group patterns by category and get variety
        error_patterns: dict[Any, Any] = {}
        for pattern in learned_patterns:
            category = pattern['category']
            if category not in error_patterns:
                error_patterns[category] = []
            error_patterns[category].append(pattern)

        # Get pattern variety statistics
        pattern_variety = {}
        for category, patterns in error_patterns.items():
            unique_subcategories = len({p['subcategory'] for p in patterns})
            total_patterns = len(patterns)
            pattern_variety[category] = {
                'total_patterns': total_patterns,
                'unique_subcategories': unique_subcategories,
                'average_confidence': sum(p['confidence_score'] for p in patterns) / len(patterns) if patterns else 0
            }

        # Get error statistics by category from error details with new structure
        category_stats = self.db.get_error_category_statistics(self.project_id)

        return {
            'feedback_patterns': list(feedback_annotations),
            'error_patterns_by_category': error_patterns,
            'pattern_variety': pattern_variety,
            'category_statistics': list(category_stats),
            'total_feedback_count': feedback_annotations.count(),
            'total_learned_patterns': learned_patterns.count()
        }

    def get_improvement_recommendations(self) -> list[dict]:
        """Generate improvement recommendations based on learned patterns using database models"""
        recommendations = []

        # Get diverse error patterns from database - consider variety with new structure
        diverse_patterns = self.db.list_diverse_error_patterns(self.project_id)

        # Group by category and get variety recommendations
        category_patterns: dict[Any, Any] = {}
        for pattern in diverse_patterns:
            category = pattern.category
            if category not in category_patterns:
                category_patterns[category] = []
            category_patterns[category].append(pattern)

        # Generate recommendations based on pattern variety
        for category, patterns in category_patterns.items():
            if len(patterns) >= 3:  # Only recommend for categories with sufficient variety
                unique_subcategories = len({p.subcategory for p in patterns})
                avg_confidence = sum(p.confidence_score for p in patterns) / len(patterns)

                recommendations.append({
                    'type': 'pattern_variety_improvement',
                    'category': category,
                    'total_patterns': len(patterns),
                    'unique_subcategories': unique_subcategories,
                    'average_confidence': avg_confidence,
                    'recommendation': f"Address {unique_subcategories} different {category} issues with {len(patterns)} total patterns",
                    'priority': 'HIGH' if unique_subcategories >= 5 else 'MEDIUM'
                })

        # Get high-frequency specific patterns
        frequent_patterns = self.db.list_frequent_error_patterns(self.project_id, min_frequency=2, limit=10)

        for pattern in frequent_patterns:
            recommendations.append({
                'type': 'specific_pattern_improvement',
                'category': pattern.category,
                'subcategory': pattern.subcategory,
                'specific_error': pattern.specific_error,
                'frequency': pattern.frequency,
                'confidence_score': pattern.confidence_score,
                'recommendation': pattern.recommendation or f"Address {pattern.subcategory} in {pattern.category}",
                'immediate_fix': pattern.immediate_fix
            })

        # Analyze tool-specific errors from error details with new structure
        tool_errors = self.db.get_tool_error_aggregates(self.project_id, min_count=2, limit=10)

        for tool_error in tool_errors:
            recommendations.append({
                'type': 'tool_improvement',
                'category': tool_error['category'],
                'error_count': tool_error['error_count'],
                'recommendation': tool_error['recommendation'] or f"Improve {tool_error['category']} handling",
                'immediate_fix': tool_error['immediate_fix']
            })

        # Get unresolved patterns that need attention
        unresolved_patterns = self.db.list_unresolved_error_patterns(self.project_id, min_frequency=2)

        for pattern in unresolved_patterns:
            recommendations.append({
                'type': 'unresolved_pattern',
                'category': pattern.category,
                'subcategory': pattern.subcategory,
                'specific_error': pattern.specific_error,
                'frequency': pattern.frequency,
                'recommendation': f"Resolve recurring {pattern.subcategory} issue in {pattern.category}",
                'priority': 'HIGH' if pattern.frequency >= 5 else 'MEDIUM'
            })

        return recommendations

    def get_context_patterns(self) -> dict[str, Any]:
        """Learn patterns about context usage and retrieval"""
        # Analyze retrieval patterns
        retrieval_spans = self.db.get_retrieval_patterns(self.project_id)

        # Analyze chunk usage patterns
        chunk_usage = self.db.get_chunk_usage_patterns(self.project_id)

        return {
            'retrieval_patterns': list(retrieval_spans),
            'chunk_usage_patterns': list(chunk_usage)
        }

    def save_note(self, note: dict[str, Any]) -> ErrorMemory | None:
        """Save a compact semantic note (agent scratchpad) with deduplication.

        Stored shape (example): {"title": str, "note": str, "category": str, "tags": list}
        """
        try:
            minimal = {
                'title': str(note.get('title', ''))[:120],
                'note': str(note.get('note', ''))[:600],
                'category': str(note.get('category', ''))[:200],
                'tags': note.get('tags', [])[:10],
            }
            raw = f"{minimal['title']}|{minimal['note']}|{minimal['category']}|{','.join(minimal.get('tags') or [])}"
            h = hashlib.sha1(raw.encode('utf-8')).hexdigest()
            key = f"note:semantic:{h}"

            memory = self.db.get_or_create_memory(
                project_id=self.project_id,
                memory_type='semantic',
                memory_key=key,
                memory_data=minimal,
            )
            return memory
        except Exception as e:
            logger.error(f"Error saving semantic note: {str(e)}")
            return None

    def get_notes(self, limit: int = 50, days: int | None = None, category: str | None = None) -> list[dict[str, Any]]:
        """Fetch semantic scratchpad notes with optional lookback and category filter."""
        try:
            cutoff_date = datetime.now() - timedelta(days=days) if days is not None else None
            qs = self.db.get_recent_notes(
                project_id=self.project_id,
                memory_type='semantic',
                cutoff_date=cutoff_date,
                limit=limit,
                memory_key_prefix=None,
                category_icontains=category,
            )

            results: list[dict[str, Any]] = []
            for m in qs:
                data = m.memory_data or {}
                results.append({
                    'title': str(data.get('title', ''))[:120],
                    'note': str(data.get('note', ''))[:500],
                    'category': str(data.get('category', ''))[:200],
                    'tags': (data.get('tags') or [])[:10],
                    'last_updated': m.last_updated.isoformat() if m.last_updated else None,
                })
            return results
        except Exception as e:
            logger.error(f"Error fetching semantic notes: {str(e)}")
            return []

    def prune_notes(self, max_active: int = 2000, ttl_days: int | None = None) -> int:
        """Soft-prune semantic notes by TTL and cap oldest beyond max_active.

        Returns the number of notes deactivated.
        """
        try:
            cutoff_date = None
            if ttl_days is not None:
                cutoff_date = datetime.now() - timedelta(days=ttl_days)

            qs = self.db.prune_notes(
                project_id=self.project_id,
                memory_type='semantic',
                max_active=max_active,
                cutoff_date=cutoff_date,
            )
            return qs
        except Exception as e:
            logger.exception(f"Error pruning notes: {e}")
            return 0

    def save_memory_data(self, memory_type: str, memory_key: str, memory_data: dict) -> ErrorMemory:
        """Save memory data to database"""
        try:
            return self.db.save_memory_data(
                project_id=self.project_id,
                memory_type=memory_type,
                memory_key=memory_key,
                memory_data=memory_data,
            )
        except Exception as e:
            logger.error(f"Error saving memory data: {str(e)}")
            return None

    def get_memory_data(self, memory_type: str, memory_key: str) -> ErrorMemory | None:
        try:
            return self.db.get_memory_data(
                project_id=self.project_id,
                memory_type=memory_type,
                memory_key=memory_key,
            )
        except Exception as e:
            logger.error(f"Error getting memory data: {str(e)}")
            return None
