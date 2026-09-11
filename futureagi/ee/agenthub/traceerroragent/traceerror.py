import json
import threading
from datetime import datetime
from typing import Any

from opentelemetry.trace.status import Status, StatusCode

from ee.agenthub.traceerroragent.analysis_service import (
    TraceErrorAnalysisService,
)
from ee.agenthub.traceerroragent.chauffeur import ChauffeurAgent
from ee.agenthub.traceerroragent.judge import JudgeAgent
from ee.agenthub.traceerroragent.memory import EpisodicMemory, SemanticMemory
import structlog

logger = structlog.get_logger(__name__)
from agentic_eval.observability.project_registrations import (
    get_user_attributes,
    get_user_id_from_trace,
    register_project,
    set_user_attributes,
    toggle_instrumentation,
)
from tracer.queries.error_analysis import TraceErrorAnalysisDB


class TraceErrorAnalysisAgent:
    def __init__(self, trace_id, llm=None, enable_memory=True, save_to_db=True, token_budget: int | None = None):
        self.trace_id = trace_id
        self.spans = TraceErrorAnalysisDB().get_all_spans(trace_id)
        self.db = TraceErrorAnalysisDB()
        self.span_map = {str(span.id): span for span in self.spans}
        self.errors: list[dict] = []
        self.grouped_errors: list[dict] = []
        self.scores: dict[str, float] = {}
        self.error_counter = 1
        self.cluster_counter = 1
        self.enable_memory = enable_memory
        self.save_to_db = save_to_db
        self._counter_lock = threading.Lock()
        self._memory_context = None
        self._memory_context_saved = False
        self.token_budget = token_budget if token_budget and token_budget > 0 else 500000
        # Tracing variables
        self.user_id = get_user_id_from_trace(trace_id)
        self.project_name = str(self.__class__.__name__) or "TraceErrorAnalysisAgent"


        # Initialize database service. CH spans carry project_id as a string
        # (there is no PG `project` FK object post-cutover), so read it directly.
        if self.spans:
            project_id = getattr(self.spans[0], "project_id", None)
            self.analysis_service = TraceErrorAnalysisService(
                str(project_id) if project_id else ""
            )
        else:
            self.analysis_service = TraceErrorAnalysisService("")

        # Initialize memory systems if enabled and project_id is valid
        self.episodic_memory = None
        self.semantic_memory = None
        if self.enable_memory and self.spans:
            project_id = getattr(self.spans[0], "project_id", None)
            if project_id:
                self.episodic_memory = EpisodicMemory(str(project_id))
                self.semantic_memory = SemanticMemory(str(project_id))

        self.llm = llm
    def build_span_tree(self) -> dict[str, Any]:
        # Build a tree: span_id -> {span, children: [subtree, ...]}
        tree = {}
        id_to_node = {}
        for span in self.spans:
            node: dict[str, Any] = {"span": span, "children": []}
            id_to_node[span.id] = node
        for span in self.spans:
            if span.parent_span_id and span.parent_span_id in id_to_node:
                children_list = id_to_node[span.parent_span_id].get("children", [])
                if isinstance(children_list, list):
                    children_list.append(id_to_node[span.id])
            else:
                tree[span.id] = id_to_node[span.id]
        return tree

    def _sort_tree_children_by_time(self, node: dict[str, Any]) -> None:
        stack = [node]
        while stack:
            current = stack.pop()
            children = current.get("children") or []
            try:
                children.sort(
                    key=lambda n: (
                        getattr(n.get("span"), "start_time", None)
                        or getattr(n.get("span"), "created_at", None)
                        or datetime.max  # missing -> last
                    )
                )
            except (TypeError, AttributeError, KeyError):
                pass
            stack.extend(children)

    def create_trace_execution_summary(self) -> str:
        """Create a hierarchical trace execution summary with path numbering (e.g., 1, 1.1, 1.1.1)."""
        trace_execution = "TRACE EXECUTION TREE:\n"
        trace_execution += f"Trace ID: {self.trace_id}\n"
        trace_execution += f"Total Spans: {len(self.spans)}\n\n"

        # Extract user query from the chronologically first span (normalize for readability)
        user_query = ""
        if self.spans:
            first_span = self.spans[0]
            user_query = getattr(first_span, 'input', '') or ''
            if not isinstance(user_query, str):
                try:
                    user_query = json.dumps(user_query, ensure_ascii=False)
                except Exception:
                    user_query = str(user_query)

        trace_execution += f"USER QUERY: {user_query}\n\n"

        # Build and sort the tree
        tree = self.build_span_tree()
        root_nodes = list(tree.values())
        try:
            root_nodes.sort(key=lambda n: (getattr(n["span"], "start_time", None) or getattr(n["span"], "created_at", None)))
        except Exception:
            pass
        for root in root_nodes:
            self._sort_tree_children_by_time(root)

        # Prepare outline and detailed sections
        outline_lines: list[str] = []
        details_lines: list[str] = []

        def walk(node: dict[str, Any], path: list[int], step_counter: list[int]):
            span = node["span"]
            span_id = getattr(span, 'id', '')
            span_name = getattr(span, 'name', '') or ''
            span_input = getattr(span, 'input', '') or ''
            span_output = getattr(span, 'output', '') or ''
            span_metadata = getattr(span, 'metadata', {}) or {}
            span_type = getattr(span, 'observation_type', '') or ''

            path_str = ".".join(str(p) for p in path)
            outline_lines.append(f"{path_str}  {span_name}  [Span ID: {span_id}]")

            # Details for this node
            step_counter[0] += 1
            details_lines.append(f"STEP {step_counter[0]} [Path {path_str}]:")
            details_lines.append(f"Span ID: {span_id}")
            details_lines.append(f"Span Name: {span_name}")
            details_lines.append(f"Span Type: {span_type}")
            if span_input:
                details_lines.append(f"Input: {span_input}")
            if span_output:
                details_lines.append(f"Output: {span_output}")

            # Include only relevant metadata keys
            if span_metadata:
                relevant_metadata = {}
                for key, value in span_metadata.items():
                    if value and key in ['tool_result', 'tool_call', 'expected_format', 'goal', 'subtasks']:
                        relevant_metadata[key] = value
                if relevant_metadata:
                    details_lines.append(f"Metadata: {json.dumps(relevant_metadata, indent=2)}")
            details_lines.append("="*50)
            details_lines.append("")

            # Recurse over children
            for idx, child in enumerate(node["children"], start=1):
                walk(child, path + [idx], step_counter)

        # Walk all roots with path numbering starting from 1
        step_counter = [0]
        for i, root in enumerate(root_nodes, start=1):
            walk(root, [i], step_counter)

        # Stitch the final prompt
        trace_execution += "OUTLINE (Path → Span):\n"
        trace_execution += "\n".join(outline_lines) + "\n\n"
        trace_execution += "DETAILS (Follow paths to reference spans):\n"
        trace_execution += "\n".join(details_lines) + "\n"

        return trace_execution

    def get_memory_context(self) -> dict[str, Any]:
        """Get memory context for enhanced analysis using database models.

        """
        if not self.enable_memory:
            return {}
        if self._memory_context is not None:
            return self._memory_context

        memory_context: dict[str, Any] = {}
        try:
            # Episodic context (lean scratchpad only)
            if self.episodic_memory and self.spans:
                try:
                    memory_context['episodic'] = {
                        'recent_notes': self.episodic_memory.get_recent_notes(limit=12),
                        'trace_notes': self.episodic_memory.get_recent_notes_for_trace(self.trace_id, limit=5),
                    }
                except Exception as e:
                    logger.error(f"Episodic memory error: {str(e)}")
            # Semantic context (lean scratchpad only)
            if self.semantic_memory:
                try:
                    memory_context['semantic'] = {
                        'notes': self.semantic_memory.get_notes(limit=20, days=180),
                    }
                except Exception as e:
                    logger.error(f"Semantic memory error: {str(e)}")

            # Avoid saving full context to DB; keep only in-process cache

        except Exception as e:
            logger.error(f"Error getting memory context: {str(e)}")
            memory_context = {}

        self._memory_context = memory_context
        return memory_context

    def _save_agent_memory_notes(self) -> None:
        """Persist compact agent memory notes emitted by the model (if any)."""
        if not self.enable_memory or not self.errors:
            return
        for err in self.errors:
            note = err.get('agent_memory')
            if not note:
                continue
            # Gate by applicability and confidence (>= 0.7)
            applicability = err.get('applicability') or {}
            if isinstance(applicability, dict) and applicability.get('applicable') is False:
                continue
            try:
                conf = float(err.get('confidence')) if err.get('confidence') is not None else None
            except Exception:
                conf = None
            if conf is None or conf < 0.7:
                continue
            scope = str(note.get('scope', '')).lower()
            if scope == 'semantic' and self.semantic_memory:
                self.semantic_memory.save_note(note)
            elif scope == 'episodic' and self.episodic_memory:
                self.episodic_memory.save_note(self.trace_id, note)

    def _save_notes_from_errors(self, errors: list[dict[str, Any]]) -> None:
        """Persist notes from a provided error list (used mid-run after each category)."""
        if not self.enable_memory or not errors:
            return
        for err in errors:
            note = err.get('agent_memory')
            if not note:
                continue
            applicability = err.get('applicability') or {}
            if isinstance(applicability, dict) and applicability.get('applicable') is False:
                continue
            try:
                conf = float(err.get('confidence')) if err.get('confidence') is not None else None
            except Exception:
                conf = None
            if conf is None or conf < 0.7:
                continue
            scope = str(note.get('scope', '')).lower()
            if scope == 'semantic' and self.semantic_memory:
                self.semantic_memory.save_note(note)
            elif scope == 'episodic' and self.episodic_memory:
                self.episodic_memory.save_note(self.trace_id, note)

    def summarize(self):
        """Main method to run the complete step-by-step trace analysis with memory enhancement and Observe instrumentation"""

        try:
            tracer_provider = register_project(
                project_name=self.project_name,
            )
            user_id = get_user_id_from_trace(self.trace_id)
            user_attributes = get_user_attributes(user_id)

        except Exception as e:
            logger.error(f"Error registering project: {str(e)}")
            return self._summarize_logic()

        if not tracer_provider:
            return self._summarize_logic()

        tracer = tracer_provider.get_tracer(__name__)

        try:
            toggle_instrumentation(
                framework="litellm",
                toggle=True,
                tracer_provider=tracer_provider,
            )

            with tracer.start_as_current_span("traceerroragent.summarize") as span:
                span.set_attribute("input.value", self.trace_id)
                span.set_attribute("input.mime_type", "text/plain")
                span.set_attribute("gen_ai.span.kind", "AGENT")

                if user_attributes:
                    set_user_attributes(span, user_attributes)
                try:
                    # THIS IS WHERE AGENT IS CALLED
                    result = self._summarize_logic()

                    if result:
                        span.set_attribute("output.value", json.dumps(result, default=str, indent=2))
                    span.set_status(Status(StatusCode.OK))
                    return result
                except Exception as e:
                    logger.error(f"Error summarizing trace: {str(e)}")
                    raise
        except Exception as e:
            logger.error(f"Error during trace summarization with instrumentation: {str(e)}")
            return self._summarize_logic()

        finally:
            if tracer_provider:
                toggle_instrumentation(
                    framework="litellm",
                    toggle=False,
                    tracer_provider=tracer_provider,
                )

    def _summarize_logic(self):
        """Run the v2 Judge & Chauffeur trace analysis pipeline."""
        return self._summarize_logic_v2()

    def _build_trace_outline(self) -> str:
        """Build a lightweight trace outline with span tree structure but NO input/output data.

        Used by v2 Judge agent — it reads full span details via tool calls.
        """
        tree = self.build_span_tree()
        root_nodes = list(tree.values())
        try:
            root_nodes.sort(
                key=lambda n: (
                    getattr(n["span"], "start_time", None)
                    or getattr(n["span"], "created_at", None)
                )
            )
        except Exception:
            pass
        for root in root_nodes:
            self._sort_tree_children_by_time(root)

        lines = [
            f"TRACE OUTLINE (Trace ID: {self.trace_id}, Total Spans: {len(self.spans)})",
            "",
        ]

        def walk(node: dict[str, Any], path: list[int]):
            span = node["span"]
            span_id = getattr(span, "id", "")
            span_name = getattr(span, "name", "") or ""
            span_type = getattr(span, "observation_type", "") or ""
            span_status = getattr(span, "status", "") or ""
            latency = getattr(span, "latency_ms", None)
            path_str = ".".join(str(p) for p in path)

            latency_str = f" ({latency}ms)" if latency else ""
            status_str = f" [{span_status}]" if span_status else ""
            lines.append(
                f"{path_str}  [{span_type}] {span_name}{status_str}{latency_str}  (ID: {span_id})"
            )

            for idx, child in enumerate(node["children"], start=1):
                walk(child, path + [idx])

        for i, root in enumerate(root_nodes, start=1):
            walk(root, [i])

        return "\n".join(lines)

    def _build_parent_map(self) -> dict[str, list[str]]:
        """Build a mapping of parent_span_id -> list of child span_ids."""
        parent_map: dict[str, list[str]] = {}
        for span in self.spans:
            pid = getattr(span, "parent_span_id", None)
            if pid:
                parent_map.setdefault(str(pid), []).append(str(span.id))
        return parent_map

    def _summarize_logic_v2(self):
        """V2 analysis using Judge & Chauffeur architecture."""
        # The LiteLLMInstrumentor (traceai_litellm) wraps litellm.completion()
        # with OTEL tracing context managers that add stack frames.  Combined with
        # botocore's recursive JSON serialisation for Bedrock, large payloads
        # exceed Python's default recursion limit.  Instead of bumping the limit,
        # we simply uninstrument for the v2 code path.  The outer OTEL span from
        # summarize() is unaffected; only per-call litellm tracing is disabled.
        try:
            from traceai_litellm import LiteLLMInstrumentor
            LiteLLMInstrumentor().uninstrument()
        except Exception:
            pass  # traceai_litellm may not be installed or already uninstrumented

        logger.info(f"Starting v2 (Judge & Chauffeur) trace analysis for trace: {self.trace_id}")

        # Extract user query from first span
        user_query = ""
        if self.spans:
            first_span = self.spans[0]
            user_query = getattr(first_span, "input", "") or ""
            if not isinstance(user_query, str):
                try:
                    user_query = json.dumps(user_query, ensure_ascii=False)
                except Exception:
                    user_query = str(user_query)

        # STEP 1: Chauffeur — read full trace cheaply with Haiku
        logger.info("V2 Step 1: Running Chauffeur (Haiku) to group spans")
        trace_execution = self.create_trace_execution_summary()
        chauffeur = ChauffeurAgent(trace_execution_summary=trace_execution)
        chauffeur_report = chauffeur.run()
        chauffeur_failed = not chauffeur_report.get("sub_flows")
        logger.info(
            f"Chauffeur found {len(chauffeur_report.get('sub_flows', []))} sub-flows"
            + (" (FAILED — Judge will read spans directly)" if chauffeur_failed else "")
        )

        # STEP 2: Build trace outline (no details) for Judge
        trace_outline = self._build_trace_outline()

        # STEP 3: Get memory context
        memory_context = self.get_memory_context()
        memory_context_str = json.dumps(memory_context, indent=2) if memory_context else "No memory context available"

        # STEP 4: Judge — active investigation with Sonnet
        logger.info("V2 Step 4: Running Judge (Sonnet) for active investigation")
        parent_map = self._build_parent_map()
        judge = JudgeAgent(
            span_map=self.span_map,
            parent_map=parent_map,
            chauffeur=chauffeur if not chauffeur_failed else None,
        )
        result = judge.run(
            trace_outline=trace_outline,
            chauffeur_report=chauffeur_report,
            memory_context=memory_context_str,
            trace_id=self.trace_id,
            total_spans=len(self.spans),
            user_query=user_query,
            chauffeur_failed=chauffeur_failed,
        )

        self.errors = result["errors"]
        self.scores = result["scores"]
        summary = result["summary"]

        logger.info(
            f"V2 analysis complete. Found {len(self.errors)} errors, "
            f"overall score: {summary.get('overall_score', 0)}"
        )

        # Build final result (same format as v1)
        final_result = {
            "trace_id": self.trace_id,
            "summary": summary,
            "errors": self.errors,
            "grouped_errors": [],  # v2 doesn't do separate grouping step
            "scores": self.scores,
        }

        # Add memory info
        if self.enable_memory:
            try:
                memory_context_data = self.get_memory_context()
                final_result["memory_context"] = {
                    "episodic_memory_used": bool(memory_context_data.get("episodic")),
                    "semantic_memory_used": bool(memory_context_data.get("semantic")),
                    "memory_enhanced_analysis": False,
                }
            except Exception as e:
                logger.error(f"Error adding memory context to v2 result: {str(e)}")

        # Save to DB
        if self.save_to_db and self.analysis_service:
            try:
                saved_analysis = self.analysis_service.save_analysis_result(
                    self.trace_id, final_result
                )
                final_result["analysis_id"] = str(saved_analysis.id)
                logger.info(f"Saved v2 analysis to database with ID: {saved_analysis.id}")
            except Exception as e:
                logger.error(f"Error saving v2 analysis to database: {str(e)}")
                final_result["analysis_id"] = None

        # Save memory notes
        if self.enable_memory:
            self._save_agent_memory_notes()
            if not self.analysis_service:
                self.save_error_patterns_to_memory()
            try:
                if self.episodic_memory:
                    self.episodic_memory.prune_notes(max_active=1000, ttl_days=90)
                if self.semantic_memory:
                    self.semantic_memory.prune_notes(max_active=2000, ttl_days=365)
            except Exception as e:
                logger.error(f"Memory prune failed: {str(e)}")

        # Log and store token usage
        chauffeur_tokens = chauffeur.get_token_usage()
        judge_tokens = judge.token_usage
        final_result["token_usage"] = {
            "chauffeur": chauffeur_tokens,
            "judge": judge_tokens,
            "total": {
                "prompt_tokens": chauffeur_tokens.get("prompt_tokens", 0) + judge_tokens.get("prompt_tokens", 0),
                "completion_tokens": chauffeur_tokens.get("completion_tokens", 0) + judge_tokens.get("completion_tokens", 0),
                "total_tokens": chauffeur_tokens.get("total_tokens", 0) + judge_tokens.get("total_tokens", 0),
            },
        }
        logger.info(
            f"V2 token usage — Chauffeur: {chauffeur_tokens}, "
            f"Judge: {judge_tokens}"
        )

        return final_result

    def save_error_patterns_to_memory(self):
        """Save current error patterns to semantic memory for learning with new structure"""
        if not self.enable_memory or not self.semantic_memory or not self.errors:
            return

        try:

            for error in self.errors:
                # Parse category path to extract components
                category_path = error.get("category", "")
                category_parts = category_path.split(" > ")

                if len(category_parts) >= 3:
                    category_parts[0]
                    subcategory = category_parts[1]
                    specific_error = category_parts[2]
                elif len(category_parts) == 2:
                    category_parts[0]
                    subcategory = category_parts[1]
                    specific_error = None
                else:
                    subcategory = "General"
                    specific_error = None

                # Create or update unique pattern (idempotent)
                pattern_description = error.get("description", "")
                recommendation = error.get("recommendation", "")
                immediate_fix = error.get("immediate_fix", "")
                evidence = error.get("evidence_snippets", [])
                root_causes = error.get("root_causes", [])

                defaults = {
                    'pattern_type': 'error_pattern',
                    'recommendation': recommendation,
                    'immediate_fix': immediate_fix,
                    'root_causes': root_causes,
                    'confidence_score': 0.5,
                    'metadata': {
                        'error_id': error.get("error_id", ""),
                        'trace_id': self.trace_id,
                        'evidence_snippets': evidence,
                        'impact': error.get("impact", "MEDIUM"),
                        'urgency_to_fix': error.get("urgency_to_fix", "HIGH"),
                        'span_locations': error.get("location_spans", [])
                    },
                }

                pattern, created = self.db.get_or_create_error_pattern(
                    project_id=self.spans[0].project_id if self.spans else None,
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
                    # Merge root causes
                    try:
                        existing_rc = pattern.root_causes or []
                        pattern.root_causes = list({*existing_rc, *root_causes})
                    except Exception:
                        pass
                    # Update metadata evidence (append unique)
                    try:
                        md = pattern.metadata or {}
                        ev = md.get('evidence_snippets', []) or []
                        md['evidence_snippets'] = list({*ev, *evidence})
                        pattern.metadata = md
                    except Exception:
                        pass
                    pattern.save()

            logger.info(f"Saved {len(self.errors)} unique error patterns to semantic memory")

        except Exception as e:
            logger.error(f"Error saving error patterns to memory: {str(e)}")

    def get_learned_patterns(self, category: str | None = None) -> list[dict]:
        """Get learned error patterns from semantic memory with new structure"""
        if not self.enable_memory or not self.semantic_memory:
            return []

        try:
            first_span_project = getattr(self.spans[0], 'project', None) if self.spans else None
            project_id = first_span_project.id if first_span_project else None
            patterns = self.db.list_error_patterns(
                project_id=project_id,
                category_icontains=category,
            )

            return [
                {
                    'category': pattern.category,
                    'subcategory': pattern.subcategory,
                    'specific_error': pattern.specific_error,
                    'frequency': pattern.frequency,
                    'confidence_score': pattern.confidence_score,
                    'recommendation': pattern.recommendation,
                    'immediate_fix': pattern.immediate_fix,
                    'root_causes': pattern.root_causes,
                    'last_seen': pattern.last_seen.isoformat(),
                    'is_resolved': pattern.is_resolved
                } for pattern in patterns
            ]

        except Exception as e:
            logger.error(f"Error getting learned patterns: {str(e)}")
            return []

