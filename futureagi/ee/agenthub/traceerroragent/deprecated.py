"""
Deprecated v1 Agent Compass methods.

These were the original step-by-step analysis pipeline (planning + execution
for each category, grouping, scoring, summary). They have been replaced by
the v2 Judge & Chauffeur architecture.

This file is kept temporarily for reference and will be deleted soon.
"""

import json
import re
from typing import Any

import structlog

from ee.agenthub.traceerroragent.prompts import (
    ERROR_TAXONOMY,
    TRACE_ERROR_PROMPTS,
)

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Multimodal content builder (v1 used batched multimodal content)
# ---------------------------------------------------------------------------

def build_multimodal_message_content(spans) -> list[dict[str, Any]]:
    """Build multimodal message content from spans (v1 only)."""
    content: list[dict[str, Any]] = []
    pattern = re.compile(
        r"^llm\.(?:input|output)(?:_messages|Messages)\.\d+\.message\.contents\.\d+\.(?:message_content|messageContent)\.(audio|image|video)$"
    )
    for span in spans:
        sid = str(getattr(span, "id", ""))
        attrs = getattr(span, "eval_attributes", None) or {}
        if not isinstance(attrs, dict):
            continue
        for k, v in attrs.items():
            if not isinstance(v, str):
                continue
            if not (v.startswith("http://") or v.startswith("https://") or v.startswith("data:")):
                continue
            m = pattern.match(k)
            if not m:
                continue
            modality = m.group(1)
            content.extend([
                {"type": "text", "text": f"<{modality}> data for the requested output for the span_id=\"{sid}\""},
                {"type": "file", "file": {"file_data": v}},
                {"type": "text", "text": f"</{modality}>"},
            ])
    return content


# ---------------------------------------------------------------------------
# Batched trace rendering (v1 only)
# ---------------------------------------------------------------------------

def create_batched_trace_summaries(agent, token_budget: int) -> list[str]:
    """Create batched summaries using depth-based budget splitting (v1 only)."""
    from ee.agenthub.traceerroragent.token_utils import BudgetedSubtreeSplitter

    tree = agent.build_span_tree()
    root_nodes = list(tree.values())
    try:
        root_nodes.sort(key=lambda n: (getattr(n.get("span"), "start_time", None) or getattr(n.get("span"), "created_at", None)))
    except Exception:
        pass
    for root in root_nodes:
        agent._sort_tree_children_by_time(root)

    splitter = BudgetedSubtreeSplitter(token_budget=token_budget)
    batches = splitter.split(root_nodes)

    rendered: list[str] = []
    for batch_ids in batches:
        rendered.append(_render_trace_for_batch(agent, root_nodes, set(batch_ids)))
    return rendered


def _render_trace_for_batch(agent, root_nodes: list[dict[str, Any]], include_ids: set) -> str:
    """Render trace for a batch of span IDs (v1 only)."""
    trace_execution = "TRACE EXECUTION TREE (BATCH):\n"
    trace_execution += f"Trace ID: {agent.trace_id}\n"
    trace_execution += f"Total Spans: {len(agent.spans)}\n\n"
    outline_lines: list[str] = []
    details_lines: list[str] = []

    def walk(node, path, step_counter):
        span = node["span"]
        span_id = str(getattr(span, 'id', ''))
        for idx, child in enumerate(node.get("children", []), start=1):
            walk(child, path + [idx], step_counter)
        if span_id not in include_ids:
            return
        span_name = getattr(span, 'name', '') or ''
        span_input = getattr(span, 'input', '') or ''
        span_output = getattr(span, 'output', '') or ''
        span_type = getattr(span, 'observation_type', '') or ''
        path_str = ".".join(str(p) for p in path)
        outline_lines.append(f"{path_str}  {span_name}  [Span ID: {span_id}]")
        step_counter[0] += 1
        details_lines.append(f"STEP {step_counter[0]} [Path {path_str}]:")
        details_lines.append(f"Span ID: {span_id}")
        details_lines.append(f"Span Name: {span_name}")
        details_lines.append(f"Span Type: {span_type}")
        if span_input:
            details_lines.append(f"Input: {span_input}")
        if span_output:
            details_lines.append(f"Output: {span_output}")
        details_lines.append("=" * 50)
        details_lines.append("")

    step_counter = [0]
    for i, root in enumerate(root_nodes, start=1):
        walk(root, [i], step_counter)

    trace_execution += "OUTLINE (Path → Span):\n"
    trace_execution += "\n".join(outline_lines) + "\n\n"
    trace_execution += "DETAILS (Follow paths to reference spans):\n"
    trace_execution += "\n".join(details_lines) + "\n"
    return trace_execution


def _estimate_full_tree_tokens(root_nodes: list[dict[str, Any]]) -> int:
    """Estimate tokens for full outline + details for all nodes (v1 only)."""
    from ee.agenthub.traceerroragent.token_utils import (
        estimate_outline_line_tokens,
        estimate_span_detail_tokens,
    )

    total = 0

    def walk(node, path):
        nonlocal total
        span = node["span"]
        span_id = str(getattr(span, 'id', ''))
        span_name = getattr(span, 'name', '') or ''
        span_input = getattr(span, 'input', '') or ''
        span_output = getattr(span, 'output', '') or ''
        span_type = getattr(span, 'observation_type', '') or ''
        path_str = ".".join(str(p) for p in path)
        total += estimate_outline_line_tokens(path_str, span_name, span_id)
        total += estimate_span_detail_tokens(
            span_id=span_id, span_name=span_name, span_type=span_type,
            span_input=span_input, span_output=span_output,
            head_lines=2, tail_lines=2,
        )
        for idx, child in enumerate(node.get("children", []), start=1):
            walk(child, path + [idx])

    for i, root in enumerate(root_nodes, start=1):
        walk(root, [i])
    return total


# ---------------------------------------------------------------------------
# JSON extraction & repair (v1 only — v2 uses structured tool calls)
# ---------------------------------------------------------------------------

def _extract_and_repair_json(llm, response: str, expected_structure: str | None = None) -> dict:
    """Extract and repair JSON from LLM response using a repair agent if needed (v1 only)."""
    try:
        json_str = None
        if "```json" in response:
            match = re.search(r'```json\s*(.*?)\s*```', response, re.DOTALL)
            if match:
                json_str = match.group(1)
        elif "```" in response:
            match = re.search(r'```\s*(.*?)\s*```', response, re.DOTALL)
            if match:
                json_str = match.group(1)
        if not json_str:
            match = re.search(r'\{.*\}', response, re.DOTALL)
            if match:
                json_str = match.group(0)
            else:
                return {}
        json_str = (
            json_str.strip()
            .replace('\n', ' ')
            .replace('\r', '')
            .replace('\t', ' ')
            .replace('\\r', '')
            .replace('\\t', ' ')
        )
        json_str = re.sub(r',\s*([\]}])', r'\1', json_str)
        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.warning(f"JSON parse error, attempting repair: {str(e)}")
            return _repair_json_with_llm(llm, json_str, str(e), expected_structure)
    except Exception as e:
        logger.error(f"Error extracting/repairing JSON: {str(e)}")
        return {}


def _repair_json_with_llm(llm, invalid_json: str, error_msg: str, expected_structure: str | None = None) -> dict:
    """Use LLM to repair invalid JSON (v1 only)."""
    try:
        structure_hint = ""
        if expected_structure:
            structure_hint = f"\n\nExpected structure:\n{expected_structure}"
        repair_prompt = f"""You are a JSON repair agent. Fix the following invalid JSON to be valid JSON.

Invalid JSON:
{invalid_json}

Error message:
{error_msg}
{structure_hint}

Return ONLY the repaired JSON object, no explanations or markdown.
Rules
1. Extract any key values from the invalid json that are missing.
2. Return the repaired json object.
3. Extract or summarize the explanation
4. Do not add any comments.
5. Do not add any extra text.
6. Do not add any extra keys.
7. Do not add any extra values.
"""
        messages = [{"role": "user", "content": repair_prompt}]
        repaired = llm._get_completion_content(messages=messages, model=llm.model_name)
        match = re.search(r'\{.*\}', repaired, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        else:
            logger.error("Failed to repair JSON with LLM")
            return {}
    except Exception as e:
        logger.error(f"Error in JSON repair agent: {str(e)}")
        return {}


# ---------------------------------------------------------------------------
# V1 compact memory context builder
# ---------------------------------------------------------------------------

def _build_compact_memory_context(category: str, memory_context: dict[str, Any] | None) -> str:
    """Return a small, informative context string for prompts (v1 only)."""
    try:
        if not memory_context:
            return "No memory context available"
        semantic = memory_context.get('semantic') or {}
        episodic = memory_context.get('episodic') or {}

        def pick_notes(notes, k, tag):
            items = []
            for n in (notes or [])[:k]:
                title = (str(n.get('title', '')) or '')[:80]
                note = (str(n.get('note', '')) or '')[:120]
                cat = (str(n.get('category', '')) or '')[:80]
                if category and cat and category.lower() not in cat.lower():
                    pass
                items.append(f"- {tag}: {title} — {note}")
            return items

        lines: list[str] = []
        sem_notes = pick_notes(semantic.get('notes'), 3, "semantic-note")
        if sem_notes:
            lines.append("Semantic Notes:")
            lines.extend(sem_notes)
        epi_recent = pick_notes(episodic.get('recent_notes'), 2, "episodic-recent")
        epi_trace = pick_notes(episodic.get('trace_notes'), 2, "episodic-trace")
        if epi_recent or epi_trace:
            lines.append("Episodic Notes:")
            lines.extend(epi_recent)
            lines.extend(epi_trace)
        learnings = semantic.get('error_taxonomy_learnings') or {}
        patterns_map = learnings.get('error_patterns_by_category') or {}
        cat_patterns = []
        for key, lst in patterns_map.items():
            if category.lower() in str(key).lower():
                cat_patterns = lst
                break
        if cat_patterns:
            lines.append("Learned Patterns (titles):")
            for p in cat_patterns[:3]:
                title = (str(p.get('specific_error') or p.get('subcategory') or 'Pattern'))[:80]
                lines.append(f"- {title}")
        sim_traces = episodic.get('similar_traces') or []
        if sim_traces:
            lines.append(f"Similar Traces: {len(sim_traces)} in window")
        if not lines:
            return "No memory context available"
        return "\n".join(lines)
    except Exception:
        return "No memory context available"


# ---------------------------------------------------------------------------
# V1 step-by-step analysis pipeline
# ---------------------------------------------------------------------------

def analyze_trace_category_step_by_step(agent, category: str) -> list[dict]:
    """Analyze the complete trace for a specific category using step-by-step approach (v1 only)."""
    try:
        user_query = ""
        if agent.spans:
            first_span = agent.spans[0]
            user_query = getattr(first_span, 'input', '') or ''
            if not isinstance(user_query, str):
                try:
                    user_query = json.dumps(user_query, ensure_ascii=False)
                except Exception:
                    user_query = str(user_query)

        if agent.token_budget and agent.token_budget > 0:
            tree = agent.build_span_tree()
            root_nodes = list(tree.values())
            try:
                root_nodes.sort(key=lambda n: (getattr(n.get("span"), "start_time", None) or getattr(n.get("span"), "created_at", None)))
            except Exception:
                pass
            for root in root_nodes:
                agent._sort_tree_children_by_time(root)
            total_tokens = _estimate_full_tree_tokens(root_nodes)
            if total_tokens <= agent.token_budget:
                trace_execution = agent.create_trace_execution_summary()
                trace_executions = [trace_execution]
            else:
                trace_executions = create_batched_trace_summaries(agent, agent.token_budget)
        else:
            trace_execution = agent.create_trace_execution_summary()
            trace_executions = [trace_execution]

        memory_context = agent.get_memory_context()
        memory_context_str = _build_compact_memory_context(category, memory_context)

        planning_prompt_function = TRACE_ERROR_PROMPTS.get("errors_planning")
        if not planning_prompt_function:
            return []

        planning_prompt = planning_prompt_function(
            category_name=category,
            trace_id=agent.trace_id,
            trace_execution=trace_executions[0],
            user_query=user_query,
            memory_context=memory_context_str,
            total_spans=len(agent.spans)
        )

        attachments_content = build_multimodal_message_content(agent.spans)
        if attachments_content:
            planning_messages = [{"role": "user", "content": [{"type": "text", "text": planning_prompt}] + attachments_content}]
        else:
            planning_messages = [{"role": "user", "content": planning_prompt}]
        planning_response = agent.llm._get_completion_content(messages=planning_messages, model=agent.llm.model_name)
        analysis_plan = _parse_planning_response(agent.llm, planning_response)
        if not analysis_plan:
            return []

        execution_prompt_function = TRACE_ERROR_PROMPTS.get("errors_execution")
        if not execution_prompt_function:
            return []

        all_category_errors: list[dict] = []
        for batch_idx, trace_exec in enumerate(trace_executions):
            execution_prompt = execution_prompt_function(
                category_name=category,
                analysis_plan=json.dumps(analysis_plan, indent=2),
                trace_execution=trace_exec,
                memory_context=memory_context_str
            )
            if attachments_content:
                execution_messages = [{"role": "user", "content": [{"type": "text", "text": execution_prompt}] + attachments_content}]
            else:
                execution_messages = [{"role": "user", "content": execution_prompt}]
            execution_response = agent.llm._get_completion_content(messages=execution_messages, model=agent.llm.model_name)
            batch_errors = _parse_errors_execution_response(agent, execution_response, category)
            if batch_errors:
                all_category_errors.extend(batch_errors)
                try:
                    agent._save_notes_from_errors(batch_errors)
                    agent._memory_context = None
                except Exception:
                    pass
        return all_category_errors
    except Exception as e:
        logger.error(f"Error analyzing trace for category {category}: {str(e)}")
        return []


def run_trace_analysis_step_by_step(agent):
    """Run analysis on the complete trace by categories using step-by-step approach (v1 only)."""
    all_errors = []
    categories = list(ERROR_TAXONOMY.keys())
    for category in categories:
        try:
            category_errors = analyze_trace_category_step_by_step(agent, category)
            if category_errors:
                all_errors.extend(category_errors)
                try:
                    agent._save_notes_from_errors(category_errors)
                    agent._memory_context = None
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"Error analyzing category {category} for trace: {str(e)}")
    agent.errors = all_errors


# ---------------------------------------------------------------------------
# V1 parse helpers
# ---------------------------------------------------------------------------

def _parse_planning_response(llm, response: str) -> dict:
    expected_structure = '''{"analysis_plan": {
        "critical_spans": ["span_id_1", "span_id_2"],
        "error_patterns_to_check": ["pattern1", "pattern2"],
        "key_relationships": ["relationship1", "relationship2"],
        "analysis_focus": "Specific focus areas",
        "detection_rules": [...],
        "evidence_policy": "...",
        "applicability_check": {"is_applicable": bool, ...}
    }}'''
    parsed = _extract_and_repair_json(llm, response, expected_structure)
    return parsed.get("analysis_plan", {}) if parsed else {}


def _parse_errors_execution_response(agent, response: str, category: str) -> list[dict]:
    expected_structure = '''{"errors_detected": [{
        "category": "Category > Subcategory > Error",
        "span_ids": ["span_id_1", "span_id_2"],
        "evidence_snippets": ["evidence1", "evidence2"],
        "description": "What went wrong",
        "impact": "HIGH/MEDIUM/LOW",
        "urgency_to_fix": "IMMEDIATE/HIGH/MEDIUM/LOW",
        "root_causes": ["cause1", "cause2"],
        "recommendation": "How to fix",
        "immediate_fix": "Quick fix",
        "applicability": {"applicable": bool, "because": "reason"},
        "confidence": 0.75,
        "confidence_basis": "reasoning"
    }]}'''
    parsed = _extract_and_repair_json(agent.llm, response, expected_structure)
    if not parsed:
        return []
    errors_detected = parsed.get("errors_detected", [])
    category_errors = []
    for error_info in errors_detected:
        error_id = f"E{str(agent.error_counter).zfill(3)}"
        cluster_id = f"C{str(agent.cluster_counter).zfill(2)}"
        agent.error_counter += 1
        category_errors.append({
            "error_id": error_id,
            "cluster_id": cluster_id,
            "category": error_info.get("category", f"{category} > General"),
            "location_spans": error_info.get("span_ids", []),
            "evidence_snippets": error_info.get("evidence_snippets", ["Error detected in trace analysis"]),
            "description": error_info.get("description", "Error detected in trace analysis"),
            "impact": error_info.get("impact", "MEDIUM"),
            "urgency_to_fix": error_info.get("urgency_to_fix", "HIGH"),
            "root_causes": error_info.get("root_causes", ["Unknown root cause"]),
            "recommendation": error_info.get("recommendation", "Review and fix the identified error"),
            "immediate_fix": error_info.get("immediate_fix", "Review and fix the identified error"),
            "applicability": error_info.get("applicability"),
            "confidence": error_info.get("confidence"),
            "confidence_basis": error_info.get("confidence_basis"),
            "agent_memory": error_info.get("agent_memory"),
            "llm_analysis": response,
            "trace_impact": error_info.get("trace_impact", "Affects trace execution"),
            "trace_assessment": error_info.get("trace_assessment", ""),
            "memory_enhanced": True,
        })
    return category_errors


# ---------------------------------------------------------------------------
# V1 grouped errors
# ---------------------------------------------------------------------------

def generate_grouped_errors_step_by_step(agent):
    if not agent.errors:
        agent.grouped_errors = []
        return
    try:
        planning_prompt_function = TRACE_ERROR_PROMPTS.get("grouped_errors_planning")
        if not planning_prompt_function:
            return
        errors_json = json.dumps(agent.errors, indent=2)
        planning_prompt = planning_prompt_function(errors_json)
        attachments_content = build_multimodal_message_content(agent.spans)
        if attachments_content:
            planning_messages = [{"role": "user", "content": [{"type": "text", "text": planning_prompt}] + attachments_content}]
        else:
            planning_messages = [{"role": "user", "content": planning_prompt}]
        planning_response = agent.llm._get_completion_content(messages=planning_messages, model=agent.llm.model_name)
        grouping_plan = _parse_grouping_planning_response(agent.llm, planning_response)
        if not grouping_plan:
            return

        execution_prompt_function = TRACE_ERROR_PROMPTS.get("grouped_errors_execution")
        if not execution_prompt_function:
            return
        execution_prompt = execution_prompt_function(grouping_plan, errors_json)
        if attachments_content:
            execution_messages = [{"role": "user", "content": [{"type": "text", "text": execution_prompt}] + attachments_content}]
        else:
            execution_messages = [{"role": "user", "content": execution_prompt}]
        execution_response = agent.llm._get_completion_content(messages=execution_messages, model=agent.llm.model_name)
        agent.grouped_errors = _parse_grouped_errors_execution_response(agent.llm, execution_response)
    except Exception as e:
        logger.error(f"Error generating grouped errors: {str(e)}")
        agent.grouped_errors = []


def _parse_grouping_planning_response(llm, response: str) -> dict:
    expected_structure = '''{"grouping_strategy": {
        "proposed_clusters": [{"cluster_id": "C01", "error_ids": ["E001", "E002"], "grouping_reason": "Why grouped together"}],
        "grouping_criteria": ["criteria1", "criteria2"],
        "cluster_themes": ["theme1", "theme2"]
    }}'''
    parsed = _extract_and_repair_json(llm, response, expected_structure)
    return parsed.get("grouping_strategy", {}) if parsed else {}


def _parse_grouped_errors_execution_response(llm, response: str) -> list[dict]:
    expected_structure = '''{"grouped_errors": [{
        "cluster_id": "C01", "error_type": "Descriptive error group name",
        "error_ids": ["E001", "E002"], "affected_spans": ["span_01", "span_02"],
        "combined_impact": "HIGH/MEDIUM/LOW", "combined_description": "Summary of errors in group"
    }]}'''
    parsed = _extract_and_repair_json(llm, response, expected_structure)
    if not parsed:
        return []
    grouped_errors = parsed.get("grouped_errors", [])
    for i, group in enumerate(grouped_errors, 1):
        group["cluster_id"] = f"C{str(i).zfill(2)}"
    return grouped_errors


# ---------------------------------------------------------------------------
# V1 scoring
# ---------------------------------------------------------------------------

def compute_scores_step_by_step(agent):
    try:
        planning_prompt_function = TRACE_ERROR_PROMPTS.get("scores_planning")
        if not planning_prompt_function:
            return
        errors_json = json.dumps(agent.errors, indent=2)
        grouped_errors_json = json.dumps(agent.grouped_errors, indent=2)
        memory_context = agent.get_memory_context()
        memory_context_str = json.dumps(memory_context, indent=2) if memory_context else "No memory context available"
        planning_prompt = planning_prompt_function(errors_json, grouped_errors_json, memory_context_str)

        attachments_content = build_multimodal_message_content(agent.spans)
        if attachments_content:
            planning_messages = [{"role": "user", "content": [{"type": "text", "text": planning_prompt}] + attachments_content}]
        else:
            planning_messages = [{"role": "user", "content": planning_prompt}]
        planning_response = agent.llm._get_completion_content(messages=planning_messages, model=agent.llm.model_name)
        scoring_plan = _parse_scoring_planning_response(agent.llm, planning_response)
        if not scoring_plan:
            return

        execution_prompt_function = TRACE_ERROR_PROMPTS.get("scores_execution")
        if not execution_prompt_function:
            return
        execution_prompt = execution_prompt_function(scoring_plan, errors_json, grouped_errors_json, memory_context_str)
        if attachments_content:
            execution_messages = [{"role": "user", "content": [{"type": "text", "text": execution_prompt}] + attachments_content}]
        else:
            execution_messages = [{"role": "user", "content": execution_prompt}]
        execution_response = agent.llm._get_completion_content(messages=execution_messages, model=agent.llm.model_name)
        agent.scores = _parse_scores_execution_response(agent, execution_response)
    except Exception as e:
        logger.error(f"Error computing scores: {str(e)}")
        _compute_fallback_scores(agent)


def _parse_scoring_planning_response(llm, response: str) -> dict:
    expected_structure = '''{"scoring_plan": {
        "factual_grounding_focus": "Focus areas for factual grounding",
        "privacy_safety_focus": "Focus areas for privacy and safety",
        "instruction_adherence_focus": "Focus areas for instruction adherence",
        "plan_execution_focus": "Focus areas for plan execution",
        "weighting_strategy": "How to weight different error types"
    }}'''
    parsed = _extract_and_repair_json(llm, response, expected_structure)
    return parsed.get("scoring_plan", {}) if parsed else {}


def _parse_scores_execution_response(agent, response: str) -> dict:
    expected_structure = '''{"factual_grounding": {"score": "1-5", "reason": "..."},
        "privacy_and_safety": {"score": "1-5", "reason": "..."},
        "instruction_adherence": {"score": "1-5", "reason": "..."},
        "optimal_plan_execution": {"score": "1-5", "reason": "..."}
    }'''
    parsed = _extract_and_repair_json(agent.llm, response, expected_structure)
    if parsed:
        return parsed
    return _compute_fallback_scores(agent)


# ---------------------------------------------------------------------------
# V1 summary
# ---------------------------------------------------------------------------

def generate_summary_step_by_step(agent) -> dict:
    try:
        planning_prompt_function = TRACE_ERROR_PROMPTS.get("summary_planning")
        if not planning_prompt_function:
            return {}
        errors_json = json.dumps(agent.errors, indent=2)
        grouped_errors_json = json.dumps(agent.grouped_errors, indent=2)
        scores_json = json.dumps(agent.scores, indent=2)
        memory_context = agent.get_memory_context()
        memory_context_str = json.dumps(memory_context, indent=2) if memory_context else "No memory context available"
        planning_prompt = planning_prompt_function(
            errors_json, grouped_errors_json, scores_json,
            agent.trace_id, len(agent.spans), memory_context_str
        )
        attachments_content = build_multimodal_message_content(agent.spans)
        if attachments_content:
            planning_messages = [{"role": "user", "content": [{"type": "text", "text": planning_prompt}] + attachments_content}]
        else:
            planning_messages = [{"role": "user", "content": planning_prompt}]
        planning_response = agent.llm._get_completion_content(messages=planning_messages, model=agent.llm.model_name)
        summary_plan = _parse_summary_planning_response(agent.llm, planning_response)
        if not summary_plan:
            return {}

        execution_prompt_function = TRACE_ERROR_PROMPTS.get("summary_execution")
        if not execution_prompt_function:
            return {}
        execution_prompt = execution_prompt_function(
            summary_plan, errors_json, grouped_errors_json, scores_json,
            agent.trace_id, len(agent.spans), memory_context_str
        )
        if attachments_content:
            execution_messages = [{"role": "user", "content": [{"type": "text", "text": execution_prompt}] + attachments_content}]
        else:
            execution_messages = [{"role": "user", "content": execution_prompt}]
        execution_response = agent.llm._get_completion_content(messages=execution_messages, model=agent.llm.model_name)
        return _parse_summary_execution_response(agent, execution_response)
    except Exception as e:
        logger.error(f"Error generating summary: {str(e)}")
        return _generate_fallback_summary(agent)


def _parse_summary_planning_response(llm, response: str) -> dict:
    expected_structure = '''{"summary_plan": {
        "overall_score_calculation": "How to calculate overall score",
        "insights_focus": "Key areas to focus on for insights",
        "priority_criteria": "Criteria for determining priority",
        "memory_integration": "How to integrate memory context"
    }}'''
    parsed = _extract_and_repair_json(llm, response, expected_structure)
    return parsed.get("summary_plan", {}) if parsed else {}


def _parse_summary_execution_response(agent, response: str) -> dict:
    expected_structure = '''{"overall_score": "<calculated_score>",
        "error_count": "<total_error_count>",
        "insights": "Comprehensive insight about trace performance",
        "recommended_priority": "HIGH/MEDIUM/LOW"
    }'''
    parsed = _extract_and_repair_json(agent.llm, response, expected_structure)
    if parsed:
        return parsed
    return _generate_fallback_summary(agent)


# ---------------------------------------------------------------------------
# V1 fallbacks
# ---------------------------------------------------------------------------

def _generate_fallback_summary(agent) -> dict:
    high_impact_count = sum(1 for e in agent.errors if e["impact"] == "HIGH")
    medium_impact_count = sum(1 for e in agent.errors if e["impact"] == "MEDIUM")
    if agent.scores:
        scores_list = [
            agent.scores.get("factual_grounding", {}).get("score", 3),
            agent.scores.get("privacy_and_safety", {}).get("score", 3),
            agent.scores.get("instruction_adherence", {}).get("score", 3),
            agent.scores.get("optimal_plan_execution", {}).get("score", 3),
        ]
        overall_score = sum(scores_list) / len(scores_list)
    else:
        overall_score = 3.0
    if high_impact_count > 0:
        recommended_priority = "HIGH"
    elif medium_impact_count > 2:
        recommended_priority = "MEDIUM"
    else:
        recommended_priority = "LOW"
    return {
        "overall_score": round(overall_score, 1),
        "error_count": len(agent.errors),
        "insights": "Overall trace analysis completed with fallback summary generation.",
        "recommended_priority": recommended_priority,
    }


def _compute_fallback_scores(agent):
    factual_errors = [e for e in agent.errors if "Thinking" in e["category"] or "Tool" in e["category"]]
    privacy_errors = [e for e in agent.errors if "Safety" in e["category"] or "Security" in e["category"]]
    instruction_errors = [e for e in agent.errors if "Instruction" in e["category"] or "Format" in e["category"]]
    execution_errors = [e for e in agent.errors if "Workflow" in e["category"] or "Task" in e["category"]]
    agent.scores = {
        "factual_grounding": {
            "score": max(1, 5 - len(factual_errors)),
            "reason": "Used incorrect tool and misinterpreted retrieved information." if factual_errors else "Good factual grounding with proper tool usage.",
        },
        "privacy_and_safety": {
            "score": max(1, 5 - len(privacy_errors)),
            "reason": "PII was leaked in one span; otherwise no security threats detected." if privacy_errors else "No privacy or safety issues detected.",
        },
        "instruction_adherence": {
            "score": max(1, 5 - len(instruction_errors)),
            "reason": "Did not adhere to format, tone, or summarization instructions." if instruction_errors else "Good alignment with user intent and instructions.",
        },
        "optimal_plan_execution": {
            "score": max(1, 5 - len(execution_errors)),
            "reason": "Reasoning steps were misordered, with tool calls handled after response generation." if execution_errors else "Proper execution flow with logical step ordering.",
        },
    }
