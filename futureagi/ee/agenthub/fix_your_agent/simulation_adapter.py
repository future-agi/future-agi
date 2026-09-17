import json
import statistics
from typing import Any, Dict, List, Optional

import structlog

from ee.agenthub.fix_your_agent.metrics_config import METRICS_CONFIG
from ee.agenthub.fix_your_agent.simulation_prompts import (
    AGENT_LEVEL_FROM_CLUSTERS_PROMPT_CHAT,
    AGENT_LEVEL_FROM_CLUSTERS_PROMPT_VOICE,
    DOMAIN_LEVEL_ANALYSIS_PROMPT_CHAT,
    DOMAIN_LEVEL_ANALYSIS_PROMPT_VOICE,
    EVAL_SEMANTICS_PROMPT_TEMPLATE,
    HUMAN_COMPARISON_PROMPT_TEMPLATE_CHAT,
    HUMAN_COMPARISON_PROMPT_TEMPLATE_VOICE,
    OVERALL_INSIGHTS_PROMPT_CHAT,
    OVERALL_INSIGHTS_PROMPT_VOICE,
    SYSTEM_LEVEL_ANALYSIS_PROMPT_CHAT,
    SYSTEM_LEVEL_ANALYSIS_PROMPT_VOICE,
)
from agentic_eval.core.llm.llm import LLM

logger = structlog.get_logger(__name__).bind(tag="FIX_YOUR_AGENT")

PROMPT_EXCLUDED_METRIC_KEYS = {
    # Customer interruptions are computed but excluded from prompts to keep the
    # analysis focused on agent-side behavior.
    "agg_user_interruption_count",
    "user_interruption_count",
    # Token counts are highly dependent on prompt length and conversation history.
    # Exclude them to keep recommendations focused on behavior and outcomes.
    "total_tokens",
    "input_tokens",
    "output_tokens",
    "avg_total_tokens",
    "avg_input_tokens",
    "avg_output_tokens",
    "agg_total_tokens",
    "agg_input_tokens",
    "agg_output_tokens",
    "tokens_per_turn",
}


class SimulationAdapter:
    # =================================================================
    # CORE UTILITIES
    # =================================================================
    def __init__(self, llm: LLM):
        self.llm = llm

    def _call_llm(self, prompt: str, **kwargs) -> str:
        """Call the LLM with a single user prompt."""
        content = [{"type": "text", "text": prompt}]
        messages = [{"role": "user", "content": content}]
        return self.llm._get_completion_content(
            messages=messages, model=self.llm.model_name, **kwargs
        )

    def _parse_json(self, text: str) -> Dict[str, Any]:
        """Parse an LLM response into a JSON dict."""
        try:
            import json_repair

            result = json_repair.loads(text)
            if isinstance(result, dict):
                return result
            else:
                logger.warning(
                    "llm_json_unexpected_type",
                    result_type=str(type(result)),
                    response_length=len(text or ""),
                )
                return {}
        except Exception as e:
            logger.error(
                "llm_json_parse_error",
                error=str(e),
                response_length=len(text or ""),
            )
            return {}

    def _normalize_chat_aggregate_metrics_for_prompt(
        self, chat_aggregate: Dict[str, Any]
    ) -> Dict[str, float]:
        """
        Filter chat aggregate metrics for prompt usage.

        Chat aggregate metrics are expected to be emitted from core-backend using the
        canonical ``agg_*`` prefix (e.g., ``agg_total_tokens``, ``agg_avg_latency_ms``).
        This function:
        - keeps only numeric values, and
        - drops metrics excluded from prompt construction.
        """
        normalized: Dict[str, float] = {}
        for key, raw_value in (chat_aggregate or {}).items():
            if key in PROMPT_EXCLUDED_METRIC_KEYS:
                continue

            value: Optional[float]
            if isinstance(raw_value, (int, float)):
                value = float(raw_value)
            else:
                # Ignore non-numeric aggregates defensively (prompt blocks expect numbers).
                continue

            normalized[key] = value

        return normalized

    # =================================================================
    # AGENT LEVEL ANALYSIS
    # =================================================================
    def _select_clusters_for_agent_level(
        self,
        cluster_dict_by_eval: Dict[str, List[Dict[str, Any]]],
        max_fail_per_eval: int,
        max_success_per_eval: int,
        max_total_clusters: int,
    ) -> List[Dict[str, Any]]:
        """Select a bounded subset of clusters for the agent-level prompt."""
        selected: List[Dict[str, Any]] = []

        for eval_name, clusters in cluster_dict_by_eval.items():
            if not clusters:
                continue

            # Ensure eval_name is present inside each cluster
            for c in clusters:
                c.setdefault("eval_name", eval_name)

            failures = [
                c
                for c in clusters
                if str(c.get("kind", "")).lower() in {"failure", "mixed"}
            ]
            successes = [
                c for c in clusters if str(c.get("kind", "")).lower() == "success"
            ]

            selected.extend(failures[:max_fail_per_eval])
            selected.extend(successes[:max_success_per_eval])

        # If too many overall, keep the first N (already severity-ordered per eval).
        if len(selected) > max_total_clusters:
            selected = selected[:max_total_clusters]

        return selected

    def _format_clusters_block(self, clusters: List[Dict[str, Any]]) -> str:
        """Format cluster summaries into an LLM-friendly block."""
        if not clusters:
            return (
                "No cluster summaries were provided from the evaluation explanations."
            )

        lines: List[str] = []
        for idx, c in enumerate(clusters):
            eval_name = c.get("eval_name", "") or c.get("dimension_name", "")
            theme = (c.get("theme") or "").strip()
            kind = (c.get("kind") or "").strip()
            size = c.get("size", None)
            confidence = (c.get("confidence") or "").strip()

            # ExplanationAgent cluster fields (if present).
            capabilities = c.get("capabilities") or []
            issues = c.get("issues") or []
            application_contexts = c.get("application_contexts") or []
            triggers = c.get("triggers") or []
            evidence = (c.get("evidence_summary") or "").strip()
            guidance = (c.get("guidance") or "").strip()

            header_bits = [f"Cluster {idx}"]
            if eval_name:
                header_bits.append(f"metric={eval_name}")
            if kind:
                header_bits.append(f"kind={kind}")
            if size is not None:
                header_bits.append(f"size={size}")
            if confidence:
                header_bits.append(f"confidence={confidence}")

            lines.append(" | ".join(header_bits))

            if theme:
                lines.append(f"  Theme: {theme}")

            # Treat capabilities as strengths
            if capabilities:
                lines.append("  Strengths (capabilities):")
                for s in capabilities:
                    if s:
                        lines.append(f"    - {s}")

            # Treat issues as weaknesses
            if issues:
                lines.append("  Weaknesses (issues):")
                for w in issues:
                    if w:
                        lines.append(f"    - {w}")

            if application_contexts:
                lines.append("  Application Contexts:")
                for ctx in application_contexts:
                    if ctx:
                        lines.append(f"    - {ctx}")

            if triggers:
                lines.append(f"  Triggers: {', '.join(t for t in triggers if t)}")

            if evidence:
                lines.append(f"  Evidence: {evidence}")

            if guidance:
                lines.append(f"  Guidance: {guidance}")

            lines.append("")  # blank line between clusters

        return "\n".join(lines).strip()

    def _generate_agent_level_analysis_from_clusters(
        self,
        *,
        cluster_dict_by_eval: Dict[str, Any],
        modality: str,
        prompt_template: str,
        max_fail_per_eval: int,
        max_success_per_eval: int,
        max_total_clusters: int,
    ) -> Dict[str, Any]:
        """Shared implementation for agent-level analysis using cluster summaries."""
        # Normalize input shape: combine failure_clusters + success_clusters if present.
        normalized: Dict[str, List[Dict[str, Any]]] = {}
        for eval_name, val in cluster_dict_by_eval.items():
            if isinstance(val, dict) and (
                "failure_clusters" in val or "success_clusters" in val
            ):
                failures = val.get("failure_clusters", []) or []
                successes = val.get("success_clusters", []) or []
                normalized[eval_name] = failures + successes
            elif isinstance(val, list):
                normalized[eval_name] = val
            else:
                normalized[eval_name] = []

        # Select a reasonable subset of clusters for the prompt.
        selected_clusters = self._select_clusters_for_agent_level(
            normalized,
            max_fail_per_eval=max_fail_per_eval,
            max_success_per_eval=max_success_per_eval,
            max_total_clusters=max_total_clusters,
        )

        # Build a map from local cluster index -> member_ids (call ids)
        # Handle both snake_case and camelCase input just in case.
        cluster_members_map: Dict[int, List[str]] = {}
        for idx, c in enumerate(selected_clusters):
            raw_member_ids = c.get("member_ids") or c.get("memberIds") or []
            if isinstance(raw_member_ids, list):
                cluster_members_map[idx] = [str(m) for m in raw_member_ids]
            else:
                cluster_members_map[idx] = []

        clusters_block = self._format_clusters_block(selected_clusters)

        prompt = prompt_template.format(
            cluster_block=clusters_block,
        )

        logger.info(
            "agent_level_llm_call_started",
            modality=modality,
            selected_cluster_count=len(selected_clusters),
            prompt_chars=len(prompt),
        )
        raw = self._call_llm(prompt, response_format={"type": "json_object"})
        parsed = self._parse_json(raw)
        logger.debug(
            "agent_level_llm_call_completed",
            modality=modality,
            response_chars=len(str(raw or "")),
            parsed_keys=list(parsed.keys()) if isinstance(parsed, dict) else [],
        )

        # Normalize to list[str] for working_well
        def _to_str_list(value: Any) -> List[str]:
            if isinstance(value, list):
                return [str(v) for v in value]
            if isinstance(value, str):
                return [value]
            return []

        working_well = _to_str_list(parsed.get("working_well") or [])

        # actionable_recommendations should be a list of objects; preserve structure
        actionable_raw = parsed.get("actionable_recommendations") or []
        if isinstance(actionable_raw, list):
            actionable_recommendations: List[Dict[str, Any]] = []
            for item in actionable_raw:
                if not isinstance(item, dict):
                    # Ignore non-dict entries to keep schema clean.
                    continue

                # Light normalization
                heading = str(item.get("heading", "")).strip()
                recommendation = str(item.get("recommendation", "")).strip()

                priority = str(item.get("priority", "")).strip().lower()
                if priority not in {"high", "medium", "low"}:
                    # fallback if LLM misbehaves
                    priority = "medium"

                # breaking_points normalization
                breaking_points_val = item.get("breaking_points") or []
                if isinstance(breaking_points_val, list):
                    breaking_points = [
                        str(b).strip() for b in breaking_points_val if str(b).strip()
                    ]
                elif isinstance(breaking_points_val, str):
                    breaking_points = [breaking_points_val.strip()]
                else:
                    breaking_points = []

                # cluster_ids -> union of member_ids
                raw_cluster_ids = (
                    item.get("cluster_ids") or item.get("clusterIds") or []
                )
                cluster_ids: List[int] = []
                if isinstance(raw_cluster_ids, list):
                    for cid in raw_cluster_ids:
                        try:
                            cluster_ids.append(int(cid))
                        except (TypeError, ValueError):
                            continue
                elif isinstance(raw_cluster_ids, (int, str)):
                    try:
                        cluster_ids.append(int(raw_cluster_ids))
                    except (TypeError, ValueError):
                        pass

                # Union of member_ids across referenced clusters
                member_ids_set = set()
                for cid in cluster_ids:
                    for mid in cluster_members_map.get(cid, []):
                        member_ids_set.add(mid)

                member_ids = sorted(member_ids_set)

                actionable_recommendations.append(
                    {
                        "heading": heading,
                        "priority": priority,
                        "recommendation": recommendation,
                        "breaking_points": breaking_points,
                        "call_execution_ids": member_ids,
                    }
                )
        else:
            actionable_recommendations = []

        return {
            "working_well": working_well,
            "actionable_recommendations": actionable_recommendations,
        }

    def generate_agent_level_analysis_voice(
        self,
        cluster_dict_by_eval: Dict[str, Any],
        max_fail_per_eval: int = 2,
        max_success_per_eval: int = 2,
        max_total_clusters: int = 10,
    ) -> Dict[str, Any]:
        """Generate agent-level recommendations from evaluation clusters."""
        return self._generate_agent_level_analysis_from_clusters(
            cluster_dict_by_eval=cluster_dict_by_eval,
            modality="voice",
            prompt_template=AGENT_LEVEL_FROM_CLUSTERS_PROMPT_VOICE,
            max_fail_per_eval=max_fail_per_eval,
            max_success_per_eval=max_success_per_eval,
            max_total_clusters=max_total_clusters,
        )

    def generate_agent_level_analysis_chat(
        self,
        cluster_dict_by_eval: Dict[str, Any],
        max_fail_per_eval: int = 2,
        max_success_per_eval: int = 2,
        max_total_clusters: int = 10,
    ) -> Dict[str, Any]:
        """Chat agent-level recommendations from evaluation clusters."""
        return self._generate_agent_level_analysis_from_clusters(
            cluster_dict_by_eval=cluster_dict_by_eval,
            modality="chat",
            prompt_template=AGENT_LEVEL_FROM_CLUSTERS_PROMPT_CHAT,
            max_fail_per_eval=max_fail_per_eval,
            max_success_per_eval=max_success_per_eval,
            max_total_clusters=max_total_clusters,
        )

    # =================================================================
    # DOMAIN LEVEL ANALYSIS
    # =================================================================
    def _normalize_eval_output_type(self, raw_output_type: str) -> str:
        """Normalize a raw eval output_type to: pass_fail / score / choices / unknown."""
        value = (raw_output_type or "").strip().lower()
        if not value:
            return "unknown"

        if "pass" in value and "fail" in value:
            return "pass_fail"
        if "score" in value or value in {"numeric", "number", "float", "int", "rating"}:
            return "score"
        if "choice" in value or "label" in value or "category" in value:
            return "choices"

        # Fallback: keep as-is when it already matches canonical labels.
        if value in {"pass_fail", "score", "choices"}:
            return value
        return "unknown"

    def _default_eval_semantics(
        self, eval_id: str, template: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Return deterministic fallback semantics for one eval template."""
        raw_type = str(template.get("output_type") or "").strip()
        normalized_type = self._normalize_eval_output_type(raw_type)

        if normalized_type == "pass_fail":
            failure_labels = ["failed", "fail", "false", "no", "0"]
        else:
            failure_labels = []

        score_failure_condition = None
        score_threshold = None
        if normalized_type in ("score", "numeric"):
            hint = template.get("failure_threshold")
            if isinstance(hint, (int, float)):
                score_failure_condition = "<"
                score_threshold = float(hint)

        return {
            "output_type": normalized_type,
            "pass_fail_semantics": {
                "pass_is_desired": True,
                "failure_labels": failure_labels,
            },
            "score_semantics": {
                "failure_condition": score_failure_condition,
                "threshold": score_threshold,
            },
            "choice_semantics": {
                "bad_choices": [],
            },
        }

    def _infer_eval_semantics(
        self,
        eval_templates: Dict[str, Dict[str, Any]],
        batch_size: int = 15,
    ) -> Dict[str, Dict[str, Any]]:
        """Infer how to interpret eval outputs for domain aggregation."""
        if not eval_templates:
            return {}

        eval_ids: List[str] = list(eval_templates.keys())
        semantics_by_id: Dict[str, Dict[str, Any]] = {}

        total_batches = (len(eval_ids) + batch_size - 1) // batch_size
        logger.info(
            "domain_eval_semantics_inference_started",
            eval_template_count=len(eval_ids),
            batch_size=batch_size,
            total_batches=total_batches,
        )
        for start_idx in range(0, len(eval_ids), batch_size):
            batch_ids = eval_ids[start_idx : start_idx + batch_size]
            batch_payload: List[Dict[str, Any]] = []
            local_id_map: Dict[str, str] = {}

            for local_idx, global_eval_id in enumerate(batch_ids, start=1):
                local_eval_id = str(local_idx)
                local_id_map[local_eval_id] = global_eval_id

                template = eval_templates[global_eval_id]
                raw_type = str(template.get("output_type") or "").strip()
                normalized_type = self._normalize_eval_output_type(raw_type)
                if normalized_type == "pass_fail":
                    prompt_type = "Pass/Fail"
                elif normalized_type in ("score", "numeric"):
                    prompt_type = "score"
                elif normalized_type == "choices":
                    prompt_type = "choices"
                else:
                    prompt_type = ""

                batch_payload.append(
                    {
                        "eval_id": local_eval_id,
                        "name": template.get("name") or global_eval_id,
                        "output_type": prompt_type,
                        "criteria": template.get("criteria") or "",
                        "score_range_hint": template.get("score_range_hint"),
                        "failure_threshold_hint": template.get("failure_threshold"),
                        "choices": template.get("choices"),
                        "multi_choice": template.get("multi_choice"),
                    }
                )

            evals_json = json.dumps({"evals": batch_payload}, ensure_ascii=False)
            prompt = EVAL_SEMANTICS_PROMPT_TEMPLATE.format(evals_json=evals_json)

            batch_number = (start_idx // batch_size) + 1
            try:
                raw = self._call_llm(prompt, response_format={"type": "json_object"})
                parsed = self._parse_json(raw)
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.error(
                    "domain_eval_semantics_llm_call_failed",
                    error=str(exc),
                    batch_number=batch_number,
                    total_batches=total_batches,
                    batch_size=len(batch_payload),
                )
                parsed = {}

            eval_semantics_list = parsed.get("eval_semantics") or []
            if not isinstance(eval_semantics_list, list):
                eval_semantics_list = []

            for item in eval_semantics_list:
                if not isinstance(item, dict):
                    continue
                local_eval_id = str(item.get("eval_id") or "").strip()
                if not local_eval_id:
                    continue

                global_eval_id = local_id_map.get(local_eval_id)
                if not global_eval_id or global_eval_id not in eval_templates:
                    continue

                template = eval_templates[global_eval_id]
                template_type = self._normalize_eval_output_type(
                    str(template.get("output_type") or "").strip()
                )
                item_type = self._normalize_eval_output_type(
                    str(item.get("output_type") or "").strip()
                )
                normalized_type = (
                    template_type if template_type != "unknown" else item_type
                )
                if normalized_type == "unknown":
                    normalized_type = item_type

                # Pass/fail semantics
                pf = item.get("pass_fail_semantics") or {}
                pass_is_desired = pf.get("pass_is_desired")
                if not isinstance(pass_is_desired, bool):
                    pass_is_desired = True

                # Do not ask the LLM to guess exact output strings. Use a conservative,
                # deterministic set of failure labels.
                failure_labels = (
                    ["failed", "fail", "false", "no", "0"]
                    if normalized_type == "pass_fail"
                    else []
                )

                # Score semantics
                score_sem = item.get("score_semantics") or {}

                # Score failure condition and threshold (for "score" evals only)
                failure_condition = score_sem.get("failure_condition")
                if failure_condition not in {"<", "<=", ">", ">="}:
                    failure_condition = None

                threshold_val = score_sem.get("threshold")
                if isinstance(threshold_val, (int, float)):
                    threshold = float(threshold_val)
                else:
                    threshold = None

                # Validate threshold against any provided score_range_hint.
                range_hint = template.get("score_range_hint")
                if (
                    isinstance(range_hint, dict)
                    and threshold is not None
                    and isinstance(range_hint.get("min"), (int, float))
                    and isinstance(range_hint.get("max"), (int, float))
                ):
                    range_min = float(range_hint["min"])
                    range_max = float(range_hint["max"])
                    if threshold < range_min or threshold > range_max:
                        threshold = None

                # If LLM could not provide usable score semantics, fall back to the
                # template's failure_threshold when available (else leave unknown).
                if normalized_type in ("score", "numeric") and (
                    failure_condition is None or threshold is None
                ):
                    hint = template.get("failure_threshold")
                    if isinstance(hint, (int, float)):
                        failure_condition = "<"
                        threshold = float(hint)
                    else:
                        failure_condition = None
                        threshold = None

                # If threshold is missing, treat score semantics as unknown.
                if threshold is None:
                    failure_condition = None

                # Choice semantics
                choice_sem = item.get("choice_semantics") or {}
                bad_choices_raw = choice_sem.get("bad_choices") or []

                def _to_str_list(values: Any) -> List[str]:
                    if not isinstance(values, list):
                        return []
                    return [
                        str(v).strip()
                        for v in values
                        if isinstance(v, (str, int, float)) and str(v).strip()
                    ]

                bad_choices = _to_str_list(bad_choices_raw)
                if normalized_type == "choices":
                    allowed = template.get("choices")
                    if isinstance(allowed, list) and allowed:
                        allowed_norm = {
                            str(v).strip().lower(): str(v).strip()
                            for v in allowed
                            if isinstance(v, (str, int, float)) and str(v).strip()
                        }
                        filtered: List[str] = []
                        for v in bad_choices:
                            mapped = allowed_norm.get(str(v).strip().lower())
                            if mapped and mapped not in filtered:
                                filtered.append(mapped)
                        bad_choices = filtered

                semantics_by_id[global_eval_id] = {
                    "output_type": normalized_type,
                    "pass_fail_semantics": {
                        "pass_is_desired": pass_is_desired,
                        "failure_labels": failure_labels,
                    },
                    "score_semantics": {
                        "failure_condition": failure_condition,
                        "threshold": threshold,
                    },
                    "choice_semantics": {
                        "bad_choices": bad_choices,
                    },
                }

        # Ensure every eval has semantics (LLM + deterministic default).
        fallback_default_count = 0
        for eval_id, template in eval_templates.items():
            if eval_id not in semantics_by_id:
                semantics_by_id[eval_id] = self._default_eval_semantics(
                    eval_id, template
                )
                fallback_default_count += 1

        logger.info(
            "domain_eval_semantics_inference_completed",
            semantics_count=len(semantics_by_id),
            fallback_default_count=fallback_default_count,
        )
        return semantics_by_id

    def _compute_branch_eval_stats(
        self,
        scenario_details: List[Dict[str, Any]],
        eval_semantics: Optional[Dict[str, Dict[str, Any]]] = None,
        eval_templates_by_id: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Aggregate evaluation outcomes per branch_category."""
        branches: Dict[str, Dict[str, Any]] = {}
        eval_templates_by_id = eval_templates_by_id or {}

        for scenario in scenario_details or []:
            branch_category = (
                scenario.get("branch_category")
                or scenario.get("conversation_branch")
                or "Unknown"
            )
            conversation_branch = scenario.get("conversation_branch", "Unknown Branch")
            call_id = scenario.get("call_execution_id")
            if not call_id:
                continue

            branch = branches.setdefault(
                branch_category,
                {
                    "branch_category": branch_category,
                    "conversation_branches": set(),
                    "call_execution_ids": [],
                    "evals": {},
                },
            )
            branch["conversation_branches"].add(conversation_branch)
            branch["call_execution_ids"].append(str(call_id))

            eval_outputs = scenario.get("eval_outputs") or {}

            for eval_config_id, eval_output in eval_outputs.items():
                if not isinstance(eval_output, dict):
                    continue

                eval_template_id = eval_output.get("eval_template_id")
                if not isinstance(eval_template_id, str) or not eval_template_id:
                    eval_template_id = None

                template = (
                    eval_templates_by_id.get(eval_template_id)
                    if eval_template_id
                    else None
                ) or {}

                evals = branch["evals"]
                entry = evals.setdefault(
                    eval_config_id,
                    {
                        "eval_config_id": eval_config_id,
                        # User-visible eval name (what they see in the frontend).
                        "config_name": eval_output.get("name") or eval_config_id,
                        "eval_template_id": eval_template_id,
                        "eval_template_name": template.get("name")
                        or eval_output.get("name")
                        or eval_config_id,
                        "eval_template_criteria": template.get("criteria") or "",
                        "choices": template.get("choices"),
                        "multi_choice": template.get("multi_choice"),
                        "output_type": str(eval_output.get("output_type", "")).strip(),
                        "call_count": 0,
                        "pass_count": 0,
                        "fail_count": 0,
                        "scores": [],
                    },
                )

                entry["call_count"] += 1
                output = eval_output.get("output")
                sem_key = str(eval_template_id or eval_config_id)
                semantics = (eval_semantics or {}).get(sem_key)
                normalized_type = self._normalize_eval_output_type(
                    semantics.get("output_type")
                    if isinstance(semantics, dict)
                    else entry["output_type"]
                )

                # Helper: classify pass/fail outputs with semantics + conservative defaults.
                def _classify_pass_fail(value: Any) -> None:
                    pf = (semantics or {}).get("pass_fail_semantics") or {}
                    pass_is_desired = pf.get("pass_is_desired")
                    if not isinstance(pass_is_desired, bool):
                        pass_is_desired = True

                    failure_labels_raw = pf.get("failure_labels") or []
                    if isinstance(failure_labels_raw, list):
                        failure_labels = {
                            str(v).strip().lower()
                            for v in failure_labels_raw
                            if isinstance(v, (str, int, float)) and str(v).strip()
                        }
                    else:
                        failure_labels = set()

                    if not failure_labels:
                        failure_labels = {"failed", "fail", "false", "no", "0"}

                    is_pass = False
                    is_fail = False

                    if isinstance(value, str):
                        val = value.strip().lower()
                        if not val:
                            return

                        # Default semantics: strings in failure_labels are failures; any other
                        # non-empty string is treated as pass.
                        raw_is_fail = val in failure_labels
                        raw_is_pass = not raw_is_fail

                        # Some evals are defined such that "Passed" means the presence of a
                        # bad trait (e.g., "return Passed if PII is present"). In that case
                        # passing is undesirable and the interpretation is inverted.
                        if pass_is_desired:
                            is_fail = raw_is_fail
                            is_pass = raw_is_pass
                        else:
                            is_fail = raw_is_pass
                            is_pass = raw_is_fail
                    elif isinstance(value, bool):
                        if pass_is_desired:
                            is_pass = bool(value)
                            is_fail = not is_pass
                        else:
                            # Invert semantics when "pass" represents an issue for the agent.
                            is_fail = bool(value)
                            is_pass = not is_fail

                    if is_pass:
                        entry["pass_count"] += 1
                    elif is_fail:
                        entry["fail_count"] += 1

                if normalized_type == "pass_fail":
                    _classify_pass_fail(output)
                elif normalized_type in ("score", "numeric"):
                    if isinstance(output, (int, float, str)):
                        try:
                            score_val = float(output)
                        except (TypeError, ValueError):
                            score_val = None
                        if score_val is not None:
                            entry["scores"].append(score_val)

                            score_sem = (
                                (semantics or {}).get("score_semantics") or {}
                                if isinstance(semantics, dict)
                                else {}
                            )
                            cond = score_sem.get("failure_condition")
                            thr = score_sem.get("threshold")
                            if cond in {"<", "<=", ">", ">="} and isinstance(
                                thr, (int, float)
                            ):
                                threshold = float(thr)
                                if cond == "<":
                                    is_fail = score_val < threshold
                                elif cond == "<=":
                                    is_fail = score_val <= threshold
                                elif cond == ">":
                                    is_fail = score_val > threshold
                                else:  # ">="
                                    is_fail = score_val >= threshold

                                if is_fail:
                                    entry["fail_count"] += 1
                                else:
                                    entry["pass_count"] += 1
                elif normalized_type == "choices":
                    # Optional handling for choice-style outputs when semantics are present.
                    if isinstance(semantics, dict):
                        choice_sem = semantics.get("choice_semantics") or {}
                        bad_choices = {
                            str(v).strip().lower()
                            for v in (choice_sem.get("bad_choices") or [])
                            if isinstance(v, (str, int, float)) and str(v).strip()
                        }
                        if isinstance(output, str):
                            vals = [output.strip().lower()] if output.strip() else []
                        elif isinstance(output, list):
                            vals = [
                                str(v).strip().lower()
                                for v in output
                                if isinstance(v, (str, int, float)) and str(v).strip()
                            ]
                        else:
                            vals = []

                        if bad_choices:
                            if any(v in bad_choices for v in vals):
                                entry["fail_count"] += 1
                            elif vals:
                                entry["pass_count"] += 1

        # Compute branch-level aggregate failure rates
        for branch in branches.values():
            total_pass_fail = 0
            total_fail = 0
            all_scores: List[float] = []

            for eval_stats in branch["evals"].values():
                pf_total = eval_stats["pass_count"] + eval_stats["fail_count"]
                total_pass_fail += pf_total
                total_fail += eval_stats["fail_count"]
                all_scores.extend(eval_stats["scores"])

            branch["total_pass_fail_events"] = total_pass_fail
            branch["total_fail_events"] = total_fail
            branch["failure_rate"] = (
                float(total_fail) / float(total_pass_fail)
                if total_pass_fail > 0
                else 0.0
            )
            branch["avg_score_across_evals"] = (
                float(sum(all_scores)) / len(all_scores) if all_scores else None
            )

            # Convert conversation_branches to a sorted list for downstream use.
            branch["conversation_branches"] = sorted(branch["conversation_branches"])

        return branches

    def _select_top_branches_for_domain_analysis(
        self,
        branch_stats: Dict[str, Any],
        min_calls: int,
        max_branches: int,
    ) -> List[Dict[str, Any]]:
        """Select a bounded subset of branches for the domain prompt."""
        candidates: List[Dict[str, Any]] = []
        for branch in branch_stats.values():
            call_count = len(branch.get("call_execution_ids") or [])
            if call_count < min_calls:
                continue
            if not branch.get("evals"):
                continue
            candidates.append(branch)

        if not candidates:
            # Fallback: take the largest branches even if below min_calls.
            candidates = list(branch_stats.values())

        candidates.sort(
            key=lambda b: (
                -(b.get("failure_rate") or 0.0),
                -len(b.get("call_execution_ids") or []),
            )
        )

        if len(candidates) > max_branches:
            candidates = candidates[:max_branches]

        return candidates

    def _build_domain_performance_block(
        self,
        selected_branches: List[Dict[str, Any]],
        max_evals_per_branch: int,
        *,
        modality: str,
    ) -> str:
        """Build a compact performance summary for the domain prompt."""
        if not selected_branches:
            return (
                "No branch-level evaluation data was available for "
                "domain analysis.\n"
            )

        lines: List[str] = []
        lines.append("## Branch Performance Summary\n")

        for branch in selected_branches:
            group_id = branch.get("group_id") or ""
            branch_category = branch.get("branch_category", "Unknown")
            call_ids = branch.get("call_execution_ids") or []
            conv_branches = branch.get("conversation_branches") or []
            evals = branch.get("evals") or {}

            if group_id:
                lines.append(f"GROUP_ID: {group_id}")
            # Avoid internal backend terminology in the prompt; keep values exact.
            lines.append(f"GROUP_LABEL: {branch_category}")
            if modality == "chat":
                lines.append(f"Conversations: {len(call_ids)}")
            else:
                lines.append(f"Calls: {len(call_ids)}")

            if conv_branches:
                lines.append("Flow paths:")
                for cb in conv_branches[:3]:
                    lines.append(f"- {cb}")
                if len(conv_branches) > 3:
                    lines.append(f"- ... ({len(conv_branches) - 3} more)")

            total_fail = branch.get("total_fail_events") or 0
            total_pf = branch.get("total_pass_fail_events") or 0
            failure_rate = float(total_fail) / float(total_pf) if total_pf > 0 else 0.0
            lines.append(
                f"Overall eval failure rate: {failure_rate * 100.0:.1f}% "
                f"(fail events={total_fail}, total events={total_pf})"
            )

            # Rank evaluations within the branch to keep the block compact.
            eval_summaries: List[Dict[str, Any]] = []
            for eval_stats in evals.values():
                pf_total = eval_stats["pass_count"] + eval_stats["fail_count"]
                fail_rate = (
                    float(eval_stats["fail_count"]) / float(pf_total)
                    if pf_total > 0
                    else 0.0
                )
                scores = eval_stats["scores"]
                avg_score = float(sum(scores)) / len(scores) if scores else None
                eval_summaries.append(
                    {
                        "stats": eval_stats,
                        "fail_rate": fail_rate,
                        "avg_score": avg_score,
                        "pf_total": pf_total,
                    }
                )

            # Sort: highest fail_rate first, then lowest avg_score.
            eval_summaries.sort(
                key=lambda e: (
                    -(e["fail_rate"] or 0.0),
                    e["avg_score"] if e["avg_score"] is not None else float("inf"),
                )
            )

            lines.append("Evaluations:")
            if not eval_summaries:
                lines.append("  (no evaluations recorded for this branch)")
            else:
                max_evals = max(0, int(max_evals_per_branch))
                for summary in eval_summaries[:max_evals]:
                    stats = summary["stats"]
                    # Use the user-visible eval name (config_name) in the LLM prompt.
                    friendly_name = stats.get("config_name") or stats.get(
                        "eval_config_id"
                    )
                    output_type = stats.get("output_type") or "unknown"
                    pf_total = summary["pf_total"]
                    fail_rate = summary["fail_rate"]
                    scores = stats.get("scores") or []
                    avg_score = summary["avg_score"]

                    lines.append(f"- Evaluation: {friendly_name} (type: {output_type})")
                    if pf_total > 0:
                        lines.append(
                            "  Pass/Fail: "
                            f"{stats['pass_count']} passed, "
                            f"{stats['fail_count']} failed "
                            f"(fail rate={fail_rate * 100.0:.1f}%)"
                        )
                    if scores:
                        avg_pct = (
                            avg_score * 100.0
                            if isinstance(avg_score, (int, float))
                            else None
                        )
                        min_pct = min(scores) * 100.0
                        max_pct = max(scores) * 100.0
                        lines.append(
                            "  Scores: "
                            f"avg={avg_pct:.1f}%, "
                            f"min={min_pct:.1f}%, "
                            f"max={max_pct:.1f}%"
                        )

                    raw_choices = stats.get("choices")
                    if isinstance(raw_choices, list) and raw_choices:
                        max_choices = 20
                        sample = [str(c) for c in raw_choices[:max_choices]]
                        suffix = (
                            f" (+{len(raw_choices) - max_choices} more)"
                            if len(raw_choices) > max_choices
                            else ""
                        )
                        multi = stats.get("multi_choice")
                        multi_str = (
                            " (multi-select)"
                            if isinstance(multi, bool) and multi
                            else ""
                        )
                        lines.append(
                            "  Allowed choices"
                            f"{multi_str}: {json.dumps(sample, ensure_ascii=False)}{suffix}"
                        )

                    criteria = (stats.get("eval_template_criteria") or "").strip()
                    if criteria:
                        # Single-line description of what the eval measures.
                        criteria_single_line = " ".join(criteria.split())
                        lines.append(f"  Measures: {criteria_single_line}")

            lines.append("")  # blank line between branches

        return "\n".join(lines).strip()

    def _generate_domain_level_analysis(
        self,
        scenarios: List[Dict[str, Any]],
        eval_templates: Optional[List[Dict[str, Any]]] = None,
        *,
        modality: str,
        prompt_template: str,
        eval_semantics_batch_size: int,
        min_calls: int,
        max_branches: int,
        max_evals_per_branch: int,
    ) -> Dict[str, Any]:
        """Generate domain-level recommendations grouped by branch_category."""
        scenarios = scenarios or []
        eval_templates = eval_templates or []
        eval_templates_by_id: Dict[str, Dict[str, Any]] = {
            str(t["eval_id"]): t
            for t in eval_templates
            if isinstance(t, dict)
            and isinstance(t.get("eval_id"), str)
            and t.get("eval_id")
        }
        logger.info(
            "domain_analysis_started",
            scenario_count=len(scenarios),
            eval_template_count=len(eval_templates_by_id),
            min_calls=int(min_calls),
            max_branches=int(max_branches),
            max_evals_per_branch=int(max_evals_per_branch),
        )
        # 1) Infer eval semantics (good/bad direction) so that failure rates
        # are aligned with how the agent is actually intended to behave.
        if not eval_templates_by_id:
            logger.warning(
                "domain_analysis_missing_eval_templates",
                scenario_count=len(scenarios),
            )
            logger.info(
                "domain_analysis_returning_empty", reason="missing_eval_templates"
            )
            return {"actionable_recommendations": []}

        eval_semantics: Dict[str, Dict[str, Any]] = {}
        if eval_templates_by_id:
            try:
                eval_semantics = self._infer_eval_semantics(
                    eval_templates_by_id, batch_size=eval_semantics_batch_size
                )
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.error(
                    "domain_analysis_eval_semantics_inference_failed",
                    error=str(exc),
                )
                logger.warning(
                    "domain_analysis_eval_semantics_fallback_defaults",
                    eval_template_count=len(eval_templates_by_id),
                )
                eval_semantics = {
                    eval_id: self._default_eval_semantics(eval_id, template)
                    for eval_id, template in eval_templates_by_id.items()
                }
        logger.info(
            "domain_analysis_eval_semantics_ready", semantics_count=len(eval_semantics)
        )
        # 2) Aggregate per-branch eval statistics using the inferred semantics.
        branch_stats = self._compute_branch_eval_stats(
            scenarios,
            eval_semantics=eval_semantics,
            eval_templates_by_id=eval_templates_by_id,
        )
        logger.debug(
            "domain_analysis_branch_stats_ready",
            modality=modality,
            branch_count=len(branch_stats),
        )
        if not branch_stats:
            logger.info("domain_analysis_returning_empty", reason="no_branch_stats")
            return {"actionable_recommendations": []}

        total_pf_events = sum(
            int(b.get("total_pass_fail_events") or 0) for b in branch_stats.values()
        )
        total_fail_events = sum(
            int(b.get("total_fail_events") or 0) for b in branch_stats.values()
        )
        if total_pf_events > 0 and total_fail_events == 0:
            logger.info(
                "domain_analysis_returning_empty",
                reason="no_failures_detected",
                total_pass_fail_events=total_pf_events,
            )
            return {"actionable_recommendations": []}

        # 3) Select the most problematic branches for the LLM to reason about.
        selected_branches = self._select_top_branches_for_domain_analysis(
            branch_stats,
            min_calls=int(min_calls),
            max_branches=int(max_branches),
        )
        logger.info(
            "domain_analysis_branches_selected",
            modality=modality,
            selected_branch_count=len(selected_branches),
        )
        if not selected_branches:
            logger.info(
                "domain_analysis_returning_empty",
                reason="no_branches_selected",
                branch_count=len(branch_stats),
            )
            return {"actionable_recommendations": []}

        # Assign small, deterministic ids for LLM reference
        group_id_to_branch_category: Dict[str, str] = {}
        for idx, branch in enumerate(selected_branches, start=1):
            group_id = f"G{idx}"
            branch["group_id"] = group_id
            group_id_to_branch_category[group_id] = str(
                branch.get("branch_category") or ""
            )

        domain_block = self._build_domain_performance_block(
            selected_branches,
            max_evals_per_branch=int(max_evals_per_branch),
            modality=modality,
        )

        # 4) Ask LLM for domain-level recommendations.
        prompt = prompt_template.format(domain_performance_block=domain_block)
        logger.info(
            "domain_analysis_llm_call_started",
            modality=modality,
            selected_branch_count=len(selected_branches),
        )
        response = self._call_llm(prompt, response_format={"type": "json_object"})
        parsed = self._parse_json(response)
        logger.debug(
            "domain_analysis_llm_call_completed",
            modality=modality,
            response_chars=len(str(response or "")),
            parsed_keys=list(parsed.keys()) if isinstance(parsed, dict) else [],
        )
        if not isinstance(parsed, dict):
            parsed = {}

        raw_recs = parsed.get("actionable_recommendations") or []
        domain_recommendations: List[Dict[str, Any]] = []

        def _normalize_priority(value: Any) -> str:
            v = str(value or "").strip().lower()
            return v if v in {"high", "medium", "low"} else "medium"

        def _normalize_string_list(value: Any, max_items: int) -> List[str]:
            if isinstance(value, str):
                items = [value]
            elif isinstance(value, list):
                items = value
            else:
                return []
            out: List[str] = []
            for item in items:
                if not isinstance(item, (str, int, float)):
                    continue
                s = str(item).strip()
                if not s:
                    continue
                out.append(s)
                if len(out) >= max_items:
                    break
            return out

        for rec in raw_recs:
            if not isinstance(rec, dict):
                continue

            group_id = rec.get("group_id") or rec.get("groupId")
            branch_category: Optional[str] = None
            if isinstance(group_id, str) and group_id.strip():
                group_id = group_id.strip()
                branch_category = group_id_to_branch_category.get(group_id)

            # Defensive fallback (LLM might echo labels despite instructions).
            if not branch_category:
                bc_raw = rec.get("branch_category") or rec.get("branchCategory")
                if isinstance(bc_raw, str) and bc_raw:
                    if bc_raw in branch_stats:
                        branch_category = bc_raw
                    else:
                        matches = [
                            bc
                            for bc in branch_stats.keys()
                            if isinstance(bc, str) and bc.lower() == bc_raw.lower()
                        ]
                        if len(matches) == 1:
                            branch_category = matches[0]

            if not branch_category:
                continue

            stats = branch_stats.get(branch_category)
            if not stats:
                continue

            call_ids = stats.get("call_execution_ids") or []
            conv_branches = stats.get("conversation_branches") or []

            conversation_branch = (
                str(conv_branches[0])
                if isinstance(conv_branches, list) and conv_branches
                else "Unknown Branch"
            )

            # Eval names: keep only those that match the user-visible eval names in this branch.
            raw_eval_names = rec.get("eval_names")
            if raw_eval_names is None:
                raw_eval_names = rec.get("eval_templates") or rec.get("evalNames")
            requested_eval_names = _normalize_string_list(raw_eval_names, max_items=10)

            known_eval_names: List[str] = [
                str(e.get("config_name") or "").strip()
                for e in (stats.get("evals") or {}).values()
                if isinstance(e, dict) and str(e.get("config_name") or "").strip()
            ]
            known_by_lower = {n.lower(): n for n in known_eval_names}

            normalized_eval_names: List[str] = []
            for name in requested_eval_names:
                mapped = known_by_lower.get(name.lower())
                if mapped and mapped not in normalized_eval_names:
                    normalized_eval_names.append(mapped)

            domain_recommendations.append(
                {
                    "branch_category": branch_category,
                    "conversation_branch": conversation_branch,
                    "heading": str(rec.get("heading") or "").strip(),
                    "priority": _normalize_priority(rec.get("priority")),
                    "recommendation": str(rec.get("recommendation") or "").strip(),
                    "breaking_points": _normalize_string_list(
                        rec.get("breaking_points"), max_items=4
                    ),
                    "eval_names": normalized_eval_names,
                    "call_execution_ids": sorted(str(cid) for cid in call_ids),
                }
            )

        logger.info(
            "domain_analysis_completed",
            recommendation_count=len(domain_recommendations),
        )
        return {"actionable_recommendations": domain_recommendations}

    def generate_domain_level_analysis_voice(
        self,
        scenarios: List[Dict[str, Any]],
        eval_templates: Optional[List[Dict[str, Any]]] = None,
        *,
        eval_semantics_batch_size: int = 10,
        min_calls: int = 2,
        max_branches: int = 5,
        max_evals_per_branch: int = 5,
    ) -> Dict[str, Any]:
        """Generate domain-level recommendations grouped by branch_category."""
        return self._generate_domain_level_analysis(
            scenarios,
            eval_templates,
            modality="voice",
            prompt_template=DOMAIN_LEVEL_ANALYSIS_PROMPT_VOICE,
            eval_semantics_batch_size=eval_semantics_batch_size,
            min_calls=min_calls,
            max_branches=max_branches,
            max_evals_per_branch=max_evals_per_branch,
        )

    def generate_domain_level_analysis_chat(
        self,
        scenarios: List[Dict[str, Any]],
        eval_templates: Optional[List[Dict[str, Any]]] = None,
        *,
        eval_semantics_batch_size: int = 10,
        min_calls: int = 2,
        max_branches: int = 5,
        max_evals_per_branch: int = 5,
    ) -> Dict[str, Any]:
        """Generate domain-level recommendations grouped by branch_category."""
        return self._generate_domain_level_analysis(
            scenarios,
            eval_templates,
            modality="chat",
            prompt_template=DOMAIN_LEVEL_ANALYSIS_PROMPT_CHAT,
            eval_semantics_batch_size=eval_semantics_batch_size,
            min_calls=min_calls,
            max_branches=max_branches,
            max_evals_per_branch=max_evals_per_branch,
        )

    # =================================================================
    # SYSTEM LEVEL ANALYSIS
    # =================================================================
    def _get_metric_cfg(self, key: str) -> Dict[str, Any]:
        """Get metric config by key (empty dict if missing)."""
        return METRICS_CONFIG.get(key, {})

    def _metric_enabled_for_modality(
        self, cfg: Dict[str, Any], *, modality: str
    ) -> bool:
        modalities = cfg.get("modalities") or ["voice"]
        return str(modality).lower() in {str(m).lower() for m in modalities}

    def _flatten_per_scenario_metrics(
        self, scenario_details: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Flatten per-call metrics into a single dict per scenario."""
        scenario_details = scenario_details or []
        flattened: List[Dict[str, Any]] = []

        for scenario in scenario_details:
            call_id = scenario.get("call_execution_id")
            base_metrics = scenario.get("metrics") or {}

            flat: Dict[str, Any] = {}

            # Copy all existing per-call metrics
            for k, v in base_metrics.items():
                if k == "customer_latency_metrics":
                    continue
                flat[k] = v

            # Merge systemMetrics if present
            customer_latency = base_metrics.get("customer_latency_metrics") or {}
            system_metrics = customer_latency.get("systemMetrics") or {}
            if isinstance(system_metrics, dict):
                for k, v in system_metrics.items():
                    flat[k] = v

            flattened.append(
                {
                    "call_execution_id": call_id,
                    "metrics": flat,
                }
            )

        return flattened

    def _compute_human_comparable_averages(
        self,
        aggregated_metrics: Dict[str, float],
        scenario_details: List[Dict[str, Any]],
    ) -> Dict[str, float]:
        """Compute agg_* metrics suitable for human-benchmark comparison."""

        def _safe_mean(values) -> float:
            numeric_values = [v for v in values if isinstance(v, (int, float))]
            if not numeric_values:
                return 0.0
            return float(sum(numeric_values)) / len(numeric_values)

        per_call_metrics = [s.get("metrics", {}) for s in scenario_details]

        agg_agent_latency_ms = _safe_mean(
            m.get("avg_agent_latency_ms") for m in per_call_metrics
        )
        agg_response_time_ms = _safe_mean(
            m.get("response_time_ms") for m in per_call_metrics
        )
        agg_talk_ratio = _safe_mean(m.get("talk_ratio") for m in per_call_metrics)
        agg_user_interruptions = _safe_mean(
            m.get("user_interruption_count") for m in per_call_metrics
        )
        agg_agent_interruptions = _safe_mean(
            m.get("agent_interruption_count") for m in per_call_metrics
        )
        agg_csat = _safe_mean(m.get("csat") for m in per_call_metrics)

        # Talk percentages: prefer provided aggregate, else derive from agg_talk_ratio
        agent_pct = aggregated_metrics.get("agentTalkPercentage")
        cust_pct = aggregated_metrics.get("customerTalkPercentage")
        if not isinstance(agent_pct, (int, float)) or not isinstance(
            cust_pct, (int, float)
        ):
            if agg_talk_ratio > 0:
                agent_pct = (agg_talk_ratio / (agg_talk_ratio + 1.0)) * 100.0
                cust_pct = 100.0 - agent_pct
            else:
                agent_pct = 0.0
                cust_pct = 0.0

        return {
            "agg_agent_latency_ms": agg_agent_latency_ms,
            "agg_response_time_ms": agg_response_time_ms,
            "agg_talk_ratio": agg_talk_ratio,
            "agg_agent_talk_percentage": float(agent_pct),
            "agg_customer_talk_percentage": float(cust_pct),
            "agg_user_interruption_count": agg_user_interruptions,
            "agg_agent_interruption_count": agg_agent_interruptions,
            "agg_csat": agg_csat,
        }

    def _format_metric_value_for_prompt(
        self,
        value: float,
        unit: str,
        *,
        modality: str = "voice",
    ) -> str:
        """Format a numeric metric value for prompts."""
        if unit == "ms":
            return f"{value:.1f} ms"
        if unit == "percent":
            return f"{value:.1f} %"
        if unit == "csat_1_10":
            return f"{value:.2f} / 10"
        if unit == "count_per_call":
            if str(modality).lower() == "chat":
                return f"{value:.2f} per conversation"
            return f"{value:.2f} per call"
        if unit == "ratio":
            return f"{value:.4f}"
        # Default fallback
        return f"{value:.4f}"

    def _select_extreme_metric_values(
        self, values: List[float], metric_key: str, max_display: int = 5
    ) -> List[float]:
        """Select extreme metric values using a simple IQR rule."""
        if len(values) < 4:
            return []

        # Avoid heavy numeric dependencies for simple quartiles.
        quartiles = statistics.quantiles(values, n=4, method="inclusive")
        q1, q3 = quartiles[0], quartiles[2]
        iqr = q3 - q1
        lower_bound = q1 - 1.5 * iqr
        upper_bound = q3 + 1.5 * iqr

        prefer_upper = self._get_metric_cfg(metric_key).get(
            "prefer_upper_outliers", True
        )

        if prefer_upper:
            outliers = [x for x in values if x > upper_bound]
            outliers.sort(reverse=True)
        else:
            outliers = [x for x in values if x < lower_bound]
            outliers.sort()

        if len(outliers) > max_display:
            return outliers[:max_display]
        return outliers

    def _build_metrics_summary_block(
        self,
        aggregated_metrics: Dict[str, float],
        scenario_details: List[Dict[str, Any]],
        *,
        modality: str = "voice",
    ) -> str:
        """Build the metrics summary block for the system prompt."""
        metrics_block = ""

        metrics_block += "\n## Agent Performance Statistical Analysis\n\n"
        if modality == "chat":
            metrics_block += (
                f"Total chat conversations analyzed: {len(scenario_details)}\n\n"
            )
        else:
            metrics_block += (
                f"Total customer calls analyzed: {len(scenario_details)}\n\n"
            )

        # ---------- Aggregated metrics ----------
        # Drive this from whatever keys are present in aggregated_metrics,
        # but only include those that exist in METRICS_CONFIG so that future
        # metric additions don't break this code. Skip metrics that have been
        # explicitly excluded from prompts.
        if modality == "chat":
            metrics_block += "\n## Aggregated Conversation Metrics\n"
        else:
            metrics_block += "\n## Aggregated Call Metrics\n"
        for key in aggregated_metrics.keys():
            if key in PROMPT_EXCLUDED_METRIC_KEYS:
                continue
            cfg = self._get_metric_cfg(key)
            if not cfg:
                continue
            if not self._metric_enabled_for_modality(cfg, modality=modality):
                continue
            label = cfg.get("label", key)
            desc = cfg.get("description", "")
            value = aggregated_metrics.get(key)
            metrics_block += f"\n### {label}\n"
            if desc:
                metrics_block += f"**Description:** {desc}\n\n"
            if value is not None:
                # Format with a unit when human_benchmark.unit is available.
                hb = cfg.get("human_benchmark") or {}
                unit = hb.get("unit")
                if unit:
                    value_str = self._format_metric_value_for_prompt(
                        float(value),
                        unit,
                        modality=modality,
                    )
                else:
                    value_str = f"{value}"
                metrics_block += f"Average value: {value_str}\n"
            else:
                metrics_block += "Average value: N/A\n"

        # ---------- Per-scenario distributions ----------
        # Dynamically collect all metrics that are configured as per_scenario
        # in METRICS_CONFIG.issue_group.category.
        per_call_keys: List[str] = []
        for key, cfg in METRICS_CONFIG.items():
            ig = cfg.get("issue_group")
            if isinstance(ig, dict) and ig.get("category") == "per_scenario":
                if not self._metric_enabled_for_modality(cfg, modality=modality):
                    continue
                per_call_keys.append(key)

        # Pre-extract metrics per scenario (once) to avoid repeated lookups
        per_call_metrics = [s.get("metrics", {}) for s in scenario_details]

        for key in per_call_keys:
            if key in PROMPT_EXCLUDED_METRIC_KEYS:
                continue
            cfg = self._get_metric_cfg(key)
            if not cfg:
                continue
            label = cfg.get("label", key)
            desc = cfg.get("description", "")

            values: List[float] = []
            for m in per_call_metrics:
                v = m.get(key)
                if isinstance(v, (int, float)):
                    values.append(float(v))

            if not values:
                continue

            metrics_block += f"\n## {label}\n"
            if desc:
                metrics_block += f"**Description:** {desc}\n\n"

            metrics_block += f"Mean: {statistics.mean(values):.4f}\n"
            metrics_block += f"Median: {statistics.median(values):.4f}\n"
            metrics_block += f"Min: {min(values):.4f}\n"
            metrics_block += f"Max: {max(values):.4f}\n"

            outliers = self._select_extreme_metric_values(values, key)
            metrics_block += (
                "Most concerning values: "
                f"{[f'{x:.4f}' for x in outliers] if outliers else 'None'}\n"
            )

        # ---------- Rich system latency metrics (if present) ----------
        # Dynamically collect all metrics that are configured as system_latency.
        rich_latency_keys: List[str] = []
        for key, cfg in METRICS_CONFIG.items():
            ig = cfg.get("issue_group")
            if isinstance(ig, dict) and ig.get("category") == "system_latency":
                if not self._metric_enabled_for_modality(cfg, modality=modality):
                    continue
                rich_latency_keys.append(key)

        def _has_latency(m: Dict[str, Any]) -> bool:
            # Flattened case: metrics already contain keys like 'turn', 'model', etc.
            if any(k in m for k in rich_latency_keys):
                return True
            # Legacy nested case: check customer_latency_metrics.systemMetrics
            sysm = m.get("customer_latency_metrics", {}).get("systemMetrics", {})
            return bool(sysm)

        has_rich_metrics = any(
            _has_latency(s.get("metrics", {}) or {}) for s in scenario_details
        )

        if has_rich_metrics:
            metrics_block += "\n## System Latency Metrics Breakdown\n"

            # Collect per-scenario system latency averages
            per_latency_values: Dict[str, List[float]] = {
                k: [] for k in rich_latency_keys
            }

            for s in scenario_details:
                m = s.get("metrics", {}) or {}
                nested_sysm = (
                    m.get("customer_latency_metrics", {}).get("systemMetrics", {})
                    if isinstance(m.get("customer_latency_metrics"), dict)
                    else {}
                )
                for k in rich_latency_keys:
                    v = m.get(k)
                    if not isinstance(v, (int, float)) and isinstance(
                        nested_sysm, dict
                    ):
                        v = nested_sysm.get(k)
                    if isinstance(v, (int, float)):
                        per_latency_values[k].append(float(v))

            for key in rich_latency_keys:
                values = per_latency_values.get(key) or []
                if not values:
                    continue
                cfg = self._get_metric_cfg(key)
                if not self._metric_enabled_for_modality(cfg, modality=modality):
                    continue
                label = cfg.get("label", key)
                desc = cfg.get("description", "")

                metrics_block += f"\n### {label}\n"
                if desc:
                    metrics_block += f"**Description:** {desc}\n\n"

                metrics_block += f"Mean: {statistics.mean(values):.2f}\n"
                metrics_block += f"Median: {statistics.median(values):.2f}\n"
                metrics_block += f"Min: {min(values):.2f}\n"
                metrics_block += f"Max: {max(values):.2f}\n"

                outliers = self._select_extreme_metric_values(values, key)
                metrics_block += (
                    "Most concerning values: "
                    f"{[f'{x:.2f}' for x in outliers] if outliers else 'None'}\n"
                )

        return metrics_block

    def _compute_issue_groups_for_calls(
        self,
        scenario_details: List[Dict[str, Any]],
        *,
        modality: str = "voice",
    ) -> Dict[str, Dict[str, Any]]:
        """Group calls into deterministic issue groups from METRICS_CONFIG."""
        # 1) Build metadata for each issue_group_id from METRICS_CONFIG
        issue_group_meta: Dict[str, Dict[str, Any]] = {}

        for metric_key, cfg in METRICS_CONFIG.items():
            if not self._metric_enabled_for_modality(cfg, modality=modality):
                continue
            ig = cfg.get("issue_group")
            if not isinstance(ig, dict):
                continue

            ig_id = ig.get("id")
            if not ig_id:
                continue

            # Only populate once per id
            if ig_id not in issue_group_meta:
                issue_group_meta[ig_id] = {
                    "issue_group_id": ig_id,
                    "metric_key": metric_key,
                    "category": ig.get("category"),
                    "description": ig.get("description", ""),
                    "unit": ig.get("unit"),
                    "threshold": ig.get("threshold"),
                    "issue_group_criteria": ig.get("criteria"),
                    "call_execution_ids": [],
                }

        # Prepare sets for call ids per issue group
        group_to_calls: Dict[str, set] = {gid: set() for gid in issue_group_meta.keys()}

        def _violates(value: float, threshold: float, op: str) -> bool:
            try:
                if op == ">":
                    return value > threshold
                if op == ">=":
                    return value >= threshold
                if op == "<":
                    return value < threshold
                if op == "<=":
                    return value <= threshold
            except TypeError:
                return False
            return False

        # 2) Evaluate each scenario against thresholds
        for scenario in scenario_details:
            call_id = scenario.get("call_execution_id")
            if not call_id:
                continue

            metrics = scenario.get("metrics") or {}

            for metric_key, cfg in METRICS_CONFIG.items():
                if not self._metric_enabled_for_modality(cfg, modality=modality):
                    continue
                ig = cfg.get("issue_group")
                if not isinstance(ig, dict):
                    continue

                ig_id = ig.get("id")
                category = ig.get("category")
                threshold = ig.get("threshold")
                op = ig.get("criteria")

                if not ig_id or category is None or threshold is None or op is None:
                    continue

                # Extract value depending on category
                value = None
                if category == "per_scenario":
                    value = metrics.get(metric_key)
                elif category == "system_latency":
                    # Prefer flattened system-latency metrics if present
                    if metric_key in metrics:
                        value = metrics.get(metric_key)
                    else:
                        # Support nested customer_latency_metrics.systemMetrics payload shape.
                        customer_latency = metrics.get("customer_latency_metrics") or {}
                        system_metrics = customer_latency.get("systemMetrics") or {}
                        value = system_metrics.get(metric_key)
                else:
                    continue

                if not isinstance(value, (int, float)):
                    continue

                if _violates(float(value), float(threshold), str(op)):
                    if ig_id in group_to_calls:
                        group_to_calls[ig_id].add(call_id)

        # 3) Attach sorted call ids back into metadata
        for ig_id, call_ids in group_to_calls.items():
            meta = issue_group_meta.get(ig_id)
            if not meta:
                continue
            meta["call_execution_ids"] = sorted(list(call_ids))

        return issue_group_meta

    def _build_issue_groups_block_for_prompt(
        self, issue_groups: Dict[str, Dict[str, Any]]
    ) -> str:
        """Build the issue groups block for the system prompt."""
        # Collect only groups with at least one call
        non_empty: List[Dict[str, Any]] = []
        for issue_group_id, data in issue_groups.items():
            if not isinstance(data, dict):
                continue

            metric_key = data.get("metric_key")
            if metric_key in PROMPT_EXCLUDED_METRIC_KEYS:
                continue

            call_ids = data.get("call_execution_ids") or []
            if not isinstance(call_ids, list):
                continue

            affected_count = len(call_ids)
            if affected_count <= 0:
                continue

            data_copy = dict(data)
            data_copy["affected_execution_count"] = affected_count
            non_empty.append(data_copy)

        if not non_empty:
            return (
                "## Issue Groups (Deterministic Buckets)\n\n"
                "No issue groups currently have any affected executions based on the "
                "configured thresholds.\n"
            )

        # Sort by descending affected_execution_count, then by issue_group_id for stability
        non_empty.sort(
            key=lambda d: (
                -d.get("affected_execution_count", 0),
                str(d.get("issue_group_id", "")),
            )
        )

        lines: List[str] = []
        lines.append("## Issue Groups (Deterministic Buckets)\n")
        lines.append(
            "Each issue group is a deterministic bucket of executions that violate a "
            "specific metric threshold. For each group you see:\n"
            "- the issue_group_id (used to reference this group)\n"
            "- which metric it corresponds to\n"
            '- how an "issue" is defined\n'
            "- how many executions fall into this bucket\n"
        )
        lines.append("ISSUE_GROUPS:")

        for group in non_empty:
            issue_group_id = group.get("issue_group_id", "")
            metric_key = group.get("metric_key", "")
            category = group.get("category", "")
            description = group.get("description", "")
            unit = group.get("unit", "")
            threshold = group.get("threshold")
            criteria = group.get("issue_group_criteria", "")
            affected_count = group.get("affected_execution_count", 0)

            # Build a simple issue definition string like: "csat < 7.0 (unit: score_1_10)"
            if threshold is not None and criteria:
                issue_def = f"{metric_key} {criteria} {threshold}"
                if unit:
                    issue_def += f" (unit: {unit})"
            else:
                issue_def = "(issue definition not configured)"

            lines.append(f"- issue_group_id: {issue_group_id}")
            lines.append(f"  - metric_key: {metric_key} (category: {category})")
            lines.append(f"  - issue_definition: {issue_def}")
            lines.append(f"  - affected_execution_count: {affected_count}")
            if description:
                lines.append(f"  - description: {description}")
            lines.append("")  # blank line between groups

        return "\n".join(lines).strip()

    def _build_human_benchmark_block_for_prompt(
        self,
        agg_metrics_for_human_comparison: Dict[str, float],
        *,
        modality: str,
    ) -> str:
        """
        Build a metrics summary block for comparing the agent to human benchmarks.
        """
        lines: List[str] = []
        matched_any = False

        for key, value in agg_metrics_for_human_comparison.items():
            # Skip metrics that are explicitly excluded from prompts
            if key in PROMPT_EXCLUDED_METRIC_KEYS:
                continue

            cfg = self._get_metric_cfg(key)
            hb = cfg.get("human_benchmark") or {}
            if not hb:
                continue

            cfg_modalities = cfg.get("modalities") or ["voice"]
            if modality not in set(str(m).lower() for m in cfg_modalities):
                continue

            matched_any = True

            label = hb.get("label", key)
            description = hb.get("description", "")
            human_range = hb.get("range", "")
            unit = hb.get("unit", "ratio")

            value_str = self._format_metric_value_for_prompt(
                float(value),
                unit,
                modality=modality,
            )

            lines.append(f"Metric: {label} ({key})")
            if description:
                lines.append(f"- Description: {description}")
            lines.append(f"- Agent average: {value_str}")
            if human_range:
                lines.append(f"- Typical human range: {human_range}")
            lines.append("")

        if not matched_any:
            return ""

        return "\n".join(lines).strip()

    def generate_system_level_analysis_voice(
        self,
        scenarios: List[Dict[str, Any]],
        aggregate_metrics: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Generate system-level recommendations from metrics and issue groups."""
        # Normalize inputs
        scenarios = scenarios or []
        aggregate_metrics = aggregate_metrics or {}

        # 1) Build per-call metric rows and aggregate metrics for comparison
        flattened_scenarios = self._flatten_per_scenario_metrics(scenarios)
        aggregate_agent_metrics = self._compute_human_comparable_averages(
            aggregated_metrics=aggregate_metrics,
            scenario_details=flattened_scenarios,
        )
        logger.debug(
            "system_analysis_aggregate_metrics_ready",
            modality="voice",
            aggregate_metric_count=len(aggregate_agent_metrics),
        )
        metrics_block = self._build_metrics_summary_block(
            aggregated_metrics=aggregate_agent_metrics,
            scenario_details=flattened_scenarios,
            modality="voice",
        )
        metric_issue_groups = self._compute_issue_groups_for_calls(
            flattened_scenarios, modality="voice"
        )
        issue_groups_block = self._build_issue_groups_block_for_prompt(
            metric_issue_groups
        )

        metrics_block = (metrics_block or "").strip()
        issue_groups_block = (issue_groups_block or "").strip()

        non_empty_issue_group_count = sum(
            1
            for g in metric_issue_groups.values()
            if isinstance(g.get("call_execution_ids"), list) and g["call_execution_ids"]
        )
        # 2) Ask LLM for system-level recommendations based on metrics + issue groups
        system_prompt = SYSTEM_LEVEL_ANALYSIS_PROMPT_VOICE.format(
            metrics_block=metrics_block,
            issue_groups_block=issue_groups_block,
        )
        logger.info(
            "system_analysis_llm_call_started",
            modality="voice",
            aggregate_metric_count=len(aggregate_agent_metrics),
            issue_group_count=len(metric_issue_groups),
            non_empty_issue_group_count=non_empty_issue_group_count,
            prompt_chars=len(system_prompt),
        )
        # Call LLM for system-level recommendations
        system_response = self._call_llm(
            system_prompt, response_format={"type": "json_object"}
        )
        system_result = self._parse_json(system_response)
        logger.debug(
            "system_analysis_llm_call_completed",
            modality="voice",
            response_chars=len(str(system_response or "")),
            parsed_keys=(
                list(system_result.keys()) if isinstance(system_result, dict) else []
            ),
        )
        if not isinstance(system_result, dict):
            system_result = {}

        # Normalize actionable_recommendations and attach call_execution_ids
        raw_recommendations = system_result.get("actionable_recommendations") or []
        system_recommendations: List[Dict[str, Any]] = []
        if isinstance(raw_recommendations, list):
            for rec in raw_recommendations:
                if isinstance(rec, dict):
                    system_recommendations.append(rec)

        # Attach call_execution_ids to each recommendation using deterministic issue groups
        if system_recommendations and isinstance(metric_issue_groups, dict):
            for rec in system_recommendations:
                raw_issue_group_ids = rec.get("issue_group_ids") or []
                # Normalize issue_group_ids to a list of strings
                if isinstance(raw_issue_group_ids, (str, int)):
                    raw_issue_group_ids = [raw_issue_group_ids]
                normalized_issue_group_ids: List[str] = []
                for issue_group_id in raw_issue_group_ids:
                    if issue_group_id is None:
                        continue
                    normalized_issue_group_ids.append(str(issue_group_id))

                # Union call_execution_ids across referenced issue groups
                affected_call_ids = set()
                for issue_group_id in normalized_issue_group_ids:
                    issue_group_data = metric_issue_groups.get(issue_group_id) or {}
                    for call_execution_id in (
                        issue_group_data.get("call_execution_ids") or []
                    ):
                        affected_call_ids.add(str(call_execution_id))
                if affected_call_ids:
                    rec["call_execution_ids"] = sorted(affected_call_ids)

                # We only use issue_group_ids internally for mapping; drop it from the API response.
                if "issue_group_ids" in rec:
                    rec.pop("issue_group_ids", None)

        # 3) Ask LLM for a short human comparison summary
        human_comparison_summary = ""
        human_benchmark_block = self._build_human_benchmark_block_for_prompt(
            aggregate_agent_metrics,
            modality="voice",
        )
        human_prompt = HUMAN_COMPARISON_PROMPT_TEMPLATE_VOICE.format(
            human_comparison_block=human_benchmark_block
            or "No human benchmark data is configured for these metrics."
        )
        logger.info(
            "human_comparison_llm_call_started",
            modality="voice",
            benchmark_block_present=bool((human_benchmark_block or "").strip()),
            prompt_chars=len(human_prompt),
        )
        human_analysis_response = self._call_llm(
            human_prompt, response_format={"type": "json_object"}
        )
        human_analysis_result = self._parse_json(human_analysis_response)
        logger.debug(
            "human_comparison_llm_call_completed",
            modality="voice",
            response_chars=len(str(human_analysis_response or "")),
            parsed_keys=(
                list(human_analysis_result.keys())
                if isinstance(human_analysis_result, dict)
                else []
            ),
        )
        if not isinstance(human_analysis_result, dict):
            human_analysis_result = {}

        human_comparison_summary = str(
            human_analysis_result.get("human_comparison_summary", "")
        ).strip()

        return {
            "actionable_recommendations": system_recommendations,
            "human_comparison_summary": human_comparison_summary,
        }

    def generate_system_level_analysis_chat(
        self,
        scenarios: List[Dict[str, Any]],
        aggregate_metrics: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Generate chat system-level recommendations from chat metrics and issue groups."""
        scenarios = scenarios or []
        aggregate_metrics = aggregate_metrics or {}

        flattened_scenarios = self._flatten_per_scenario_metrics(scenarios)

        chat_aggregate = aggregate_metrics.get("chat") or {}
        if not isinstance(chat_aggregate, dict):
            chat_aggregate = {}
        chat_aggregate_for_prompt = self._normalize_chat_aggregate_metrics_for_prompt(
            chat_aggregate
        )
        logger.debug(
            "system_analysis_aggregate_metrics_ready",
            modality="chat",
            aggregate_metric_count=len(chat_aggregate_for_prompt),
        )

        metrics_block = self._build_metrics_summary_block(
            aggregated_metrics=chat_aggregate_for_prompt,
            scenario_details=flattened_scenarios,
            modality="chat",
        )
        metric_issue_groups = self._compute_issue_groups_for_calls(
            flattened_scenarios, modality="chat"
        )
        issue_groups_block = self._build_issue_groups_block_for_prompt(
            metric_issue_groups
        )

        system_prompt = SYSTEM_LEVEL_ANALYSIS_PROMPT_CHAT.format(
            metrics_block=(metrics_block or "").strip(),
            issue_groups_block=(issue_groups_block or "").strip(),
        )
        logger.info(
            "system_analysis_llm_call_started",
            modality="chat",
            aggregate_metric_count=len(chat_aggregate_for_prompt),
            issue_group_count=len(metric_issue_groups),
            non_empty_issue_group_count=sum(
                1
                for g in metric_issue_groups.values()
                if isinstance(g.get("call_execution_ids"), list)
                and g["call_execution_ids"]
            ),
            prompt_chars=len(system_prompt),
        )
        system_response = self._call_llm(
            system_prompt, response_format={"type": "json_object"}
        )
        system_result = self._parse_json(system_response)
        logger.debug(
            "system_analysis_llm_call_completed",
            modality="chat",
            response_chars=len(str(system_response or "")),
            parsed_keys=(
                list(system_result.keys()) if isinstance(system_result, dict) else []
            ),
        )
        if not isinstance(system_result, dict):
            system_result = {}

        raw_recommendations = system_result.get("actionable_recommendations") or []
        system_recommendations: List[Dict[str, Any]] = []
        if isinstance(raw_recommendations, list):
            for rec in raw_recommendations:
                if isinstance(rec, dict):
                    system_recommendations.append(rec)

        if system_recommendations and isinstance(metric_issue_groups, dict):
            for rec in system_recommendations:
                raw_issue_group_ids = rec.get("issue_group_ids") or []
                if isinstance(raw_issue_group_ids, (str, int)):
                    raw_issue_group_ids = [raw_issue_group_ids]
                normalized_issue_group_ids: List[str] = []
                for issue_group_id in raw_issue_group_ids:
                    if issue_group_id is None:
                        continue
                    normalized_issue_group_ids.append(str(issue_group_id))

                affected_call_ids = set()
                for issue_group_id in normalized_issue_group_ids:
                    issue_group_data = metric_issue_groups.get(issue_group_id) or {}
                    for call_execution_id in (
                        issue_group_data.get("call_execution_ids") or []
                    ):
                        affected_call_ids.add(str(call_execution_id))
                if affected_call_ids:
                    rec["call_execution_ids"] = sorted(affected_call_ids)

                if "issue_group_ids" in rec:
                    rec.pop("issue_group_ids", None)

        # Human comparison summary (chat)
        human_comparison_summary = ""
        human_benchmark_block = self._build_human_benchmark_block_for_prompt(
            chat_aggregate_for_prompt,
            modality="chat",
        )
        human_prompt = HUMAN_COMPARISON_PROMPT_TEMPLATE_CHAT.format(
            human_comparison_block=human_benchmark_block
            or "No human benchmark data is configured for these metrics."
        )
        logger.info(
            "human_comparison_llm_call_started",
            modality="chat",
            benchmark_block_present=bool((human_benchmark_block or "").strip()),
            prompt_chars=len(human_prompt),
        )
        human_analysis_response = self._call_llm(
            human_prompt, response_format={"type": "json_object"}
        )
        human_analysis_result = self._parse_json(human_analysis_response)
        logger.debug(
            "human_comparison_llm_call_completed",
            modality="chat",
            response_chars=len(str(human_analysis_response or "")),
            parsed_keys=(
                list(human_analysis_result.keys())
                if isinstance(human_analysis_result, dict)
                else []
            ),
        )
        if not isinstance(human_analysis_result, dict):
            human_analysis_result = {}

        human_comparison_summary = str(
            human_analysis_result.get("human_comparison_summary", "")
        ).strip()

        return {
            "actionable_recommendations": system_recommendations,
            "human_comparison_summary": human_comparison_summary,
        }

    # =================================================================
    # GENERATE INSIGHTS
    # =================================================================
    def _build_overall_insights_block(
        self,
        agent_level: Dict[str, Any],
        domain_level: Dict[str, Any],
        system_level: Dict[str, Any],
    ) -> str:
        """Build a compact cross-analysis summary block for overall insights."""

        def _coerce_str_list(value: Any, max_items: int) -> List[str]:
            if isinstance(value, str):
                items = [value]
            elif isinstance(value, list):
                items = value
            else:
                return []

            out: List[str] = []
            for v in items:
                if not isinstance(v, (str, int, float)):
                    continue
                s = str(v).strip()
                if not s:
                    continue
                out.append(s)
                if len(out) >= max_items:
                    break
            return out

        def _coerce_dict_list(value: Any, max_items: int) -> List[Dict[str, Any]]:
            if not isinstance(value, list):
                return []
            out: List[Dict[str, Any]] = []
            for v in value:
                if isinstance(v, dict):
                    out.append(v)
                if len(out) >= max_items:
                    break
            return out

        def _has_content(text: Any) -> bool:
            return isinstance(text, str) and bool(text.strip())

        sections: List[str] = []

        # 1) Agent-level (first)
        agent_working = _coerce_str_list(
            agent_level.get("working_well") or agent_level.get("workingWell") or [],
            max_items=4,
        )
        agent_recs = _coerce_dict_list(
            agent_level.get("actionable_recommendations")
            or agent_level.get("actionableRecommendations")
            or [],
            max_items=3,
        )
        if agent_working or agent_recs:
            lines: List[str] = []
            lines.append("## Agent-Level Analysis")
            if agent_working:
                lines.append("Working well:")
                for s in agent_working:
                    lines.append(f"- {s}")
            if agent_recs:
                lines.append("Top recommendations:")
                for rec in agent_recs:
                    heading = str(rec.get("heading") or "").strip()
                    priority = str(rec.get("priority") or "").strip().lower()
                    recommendation = str(rec.get("recommendation") or "").strip()
                    breaking_points = _coerce_str_list(
                        rec.get("breaking_points") or rec.get("breakingPoints") or [],
                        max_items=3,
                    )
                    if heading:
                        lines.append(f"- {heading} (priority: {priority or 'medium'})")
                    if recommendation:
                        lines.append(f"  Recommendation: {recommendation}")
                    if breaking_points:
                        lines.append("  Evidence:")
                        for bp in breaking_points:
                            lines.append(f"  - {bp}")
            sections.append("\n".join(lines).strip())

        # 2) Domain-level (second)
        domain_recs = _coerce_dict_list(
            domain_level.get("actionable_recommendations")
            or domain_level.get("actionableRecommendations")
            or [],
            max_items=3,
        )
        if domain_recs:
            lines = []
            lines.append("## Domain-Level Analysis")
            for rec in domain_recs:
                branch_category = str(
                    rec.get("branch_category") or rec.get("branchCategory") or ""
                ).strip()
                conversation_branch = str(
                    rec.get("conversation_branch")
                    or rec.get("conversationBranch")
                    or ""
                ).strip()
                heading = str(rec.get("heading") or "").strip()
                priority = str(rec.get("priority") or "").strip().lower()
                recommendation = str(rec.get("recommendation") or "").strip()
                breaking_points = _coerce_str_list(
                    rec.get("breaking_points") or rec.get("breakingPoints") or [],
                    max_items=3,
                )
                eval_names = _coerce_str_list(
                    rec.get("eval_names") or rec.get("evalNames") or [],
                    max_items=5,
                )

                header = heading or "Recommendation"
                if branch_category:
                    header = f"{header} — {branch_category}"
                if priority:
                    header = f"{header} (priority: {priority})"
                lines.append(f"- {header}")
                if conversation_branch:
                    lines.append(f"  Flow: {conversation_branch}")
                if recommendation:
                    lines.append(f"  Recommendation: {recommendation}")
                if eval_names:
                    lines.append(f"  Evals: {', '.join(eval_names)}")
                if breaking_points:
                    lines.append("  Evidence:")
                    for bp in breaking_points:
                        lines.append(f"  - {bp}")
            sections.append("\n".join(lines).strip())

        # 3) System-level (third)
        human_summary = str(
            system_level.get("human_comparison_summary")
            or system_level.get("humanComparisonSummary")
            or ""
        ).strip()
        system_recs = _coerce_dict_list(
            system_level.get("actionable_recommendations")
            or system_level.get("actionableRecommendations")
            or [],
            max_items=3,
        )
        if _has_content(human_summary) or system_recs:
            lines = []
            lines.append("## System-Level Analysis")
            if _has_content(human_summary):
                lines.append(f"Human comparison: {human_summary}")
            if system_recs:
                lines.append("Top recommendations:")
                for rec in system_recs:
                    heading = str(rec.get("heading") or "").strip()
                    priority = str(rec.get("priority") or "").strip().lower()
                    recommendation = str(rec.get("recommendation") or "").strip()
                    breaking_points = _coerce_str_list(
                        rec.get("breaking_points") or rec.get("breakingPoints") or [],
                        max_items=3,
                    )
                    if heading:
                        lines.append(f"- {heading} (priority: {priority or 'medium'})")
                    if recommendation:
                        lines.append(f"  Recommendation: {recommendation}")
                    if breaking_points:
                        lines.append("  Evidence:")
                        for bp in breaking_points:
                            lines.append(f"  - {bp}")
            sections.append("\n".join(lines).strip())

        return "\n\n".join(sections).strip()

    def _generate_overall_insights(
        self,
        *,
        agent_level: Dict[str, Any],
        domain_level: Dict[str, Any],
        system_level: Dict[str, Any],
        modality: str,
        prompt_template: str,
    ) -> str:
        analysis_blocks = self._build_overall_insights_block(
            agent_level=agent_level or {},
            domain_level=domain_level or {},
            system_level=system_level or {},
        )
        if not analysis_blocks:
            logger.info(
                "overall_insights_returning_empty", reason="no_analysis_content"
            )
            return ""

        prompt = prompt_template.format(analysis_blocks=analysis_blocks)
        logger.info(
            "overall_insights_llm_call_started",
            modality=modality,
            prompt_chars=len(prompt),
        )
        response = self._call_llm(prompt, response_format={"type": "json_object"})
        parsed = self._parse_json(response)
        logger.debug(
            "overall_insights_llm_call_completed",
            modality=modality,
            response_chars=len(str(response or "")),
            parsed_keys=list(parsed.keys()) if isinstance(parsed, dict) else [],
        )
        return (
            str(parsed.get("insights") or "").strip()
            if isinstance(parsed, dict)
            else ""
        )

    def generate_overall_insights_voice(
        self,
        agent_level: Dict[str, Any],
        domain_level: Dict[str, Any],
        system_level: Dict[str, Any],
    ) -> str:
        """Generate a short overall insights summary from analysis outputs."""
        return self._generate_overall_insights(
            agent_level=agent_level,
            domain_level=domain_level,
            system_level=system_level,
            modality="voice",
            prompt_template=OVERALL_INSIGHTS_PROMPT_VOICE,
        )

    def generate_overall_insights_chat(
        self,
        agent_level: Dict[str, Any],
        domain_level: Dict[str, Any],
        system_level: Dict[str, Any],
    ) -> str:
        """Chat variant of overall insights (prompt swap only)."""
        return self._generate_overall_insights(
            agent_level=agent_level,
            domain_level=domain_level,
            system_level=system_level,
            modality="chat",
            prompt_template=OVERALL_INSIGHTS_PROMPT_CHAT,
        )
