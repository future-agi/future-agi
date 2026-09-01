from typing import Any, Dict, List

import structlog

from agentic_eval.core.llm.llm import LLM
from agentic_eval.core.utils.model_config import ModelConfigs

from .simulation_adapter import SimulationAdapter

logger = structlog.get_logger(__name__).bind(tag="FIX_YOUR_AGENT")


class SimulationAnalysisAgent:
    """Run analysis over a simulated call set."""

    # =================================================================
    # SETUP
    # =================================================================
    def __init__(
        self,
        model_name=ModelConfigs.VERTEX_GEMINI_2_5_PRO.model_name,
        temperature=ModelConfigs.VERTEX_GEMINI_2_5_PRO.temperature,
        max_tokens=ModelConfigs.VERTEX_GEMINI_2_5_PRO.max_tokens,
        provider=ModelConfigs.VERTEX_GEMINI_2_5_PRO.provider,
        llm=None,
    ):
        self.llm = (
            llm
            if llm
            else LLM(
                model_name=model_name,
                temperature=temperature,
                max_tokens=max_tokens,
                provider=provider,
                api_key=None,
            )
        )
        self.adapter = SimulationAdapter(self.llm)
        # Limits used when selecting/aggregating eval clusters and branches for analysis.
        self.MAX_FAIL_PER_EVAL = 2
        self.MAX_SUCCESS_PER_EVAL = 2
        self.MAX_TOTAL_CLUSTERS = 10
        self.EVAL_SEMANTICS_BATCH_SIZE = 10
        self.MIN_CALLS = 2
        self.MAX_BRANCHES = 5
        self.MAX_EVALS_PER_BRANCH = 5

    # =================================================================
    # ANALYZE
    # =================================================================
    def _analyze_voice(
        self,
        *,
        simulation_data: dict,
        aggregate_metrics: dict,
        cluster_dict_by_eval: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Voice analysis pipeline"""
        # Inputs (scenarios + eval templates for domain analysis)
        scenarios = simulation_data.get("scenarios", [])
        eval_templates = simulation_data.get("eval_templates", [])
        if not scenarios:
            logger.warning("No scenarios provided for analysis.")
            return {}

        # Step 1: group scenarios by conversation branch
        logger.info(
            "[1/6] simulation_analysis_started",
            scenario_count=len(scenarios),
            eval_template_count=len(eval_templates or []),
        )

        # Step 2: agent-level analysis
        logger.info("[2/6] agent_level_analysis_started", stage="agent_level")
        try:
            agent_analysis = self.adapter.generate_agent_level_analysis_voice(
                cluster_dict_by_eval or {},
                max_fail_per_eval=self.MAX_FAIL_PER_EVAL,
                max_success_per_eval=self.MAX_SUCCESS_PER_EVAL,
                max_total_clusters=self.MAX_TOTAL_CLUSTERS,
            )
        except Exception:
            logger.exception(
                "[2/6] agent_level_analysis_failed_continuing",
                stage="agent_level",
            )
            agent_analysis = {"working_well": [], "actionable_recommendations": []}

        # Step 3: system-level analysis
        logger.info("[3/6] system_level_analysis_started", stage="system_level")
        try:
            system_analysis = self.adapter.generate_system_level_analysis_voice(
                scenarios,
                aggregate_metrics or {},
            )
        except Exception:
            logger.exception(
                "[3/6] system_level_analysis_failed_continuing",
                stage="system_level",
            )
            system_analysis = {
                "actionable_recommendations": [],
                "human_comparison_summary": "",
            }

        # Step 4: domain-level analysis
        logger.info("[4/6] domain_level_analysis_started", stage="domain_level")
        try:
            domain_analysis = self.adapter.generate_domain_level_analysis_voice(
                scenarios,
                eval_templates=eval_templates,
                eval_semantics_batch_size=self.EVAL_SEMANTICS_BATCH_SIZE,
                min_calls=self.MIN_CALLS,
                max_branches=self.MAX_BRANCHES,
                max_evals_per_branch=self.MAX_EVALS_PER_BRANCH,
            )
        except Exception:
            logger.exception(
                "[4/6] domain_level_analysis_failed_continuing",
                stage="domain_level",
            )
            domain_analysis = {"actionable_recommendations": []}

        # Step 5: overall insights (agent -> domain -> system)
        logger.info("[5/6] overall_insights_started", stage="insights")
        try:
            insight_summary = self.adapter.generate_overall_insights_voice(
                agent_level=agent_analysis or {},
                domain_level=domain_analysis or {},
                system_level=system_analysis or {},
            )
        except Exception:
            logger.exception(
                "[5/6] overall_insights_failed_continuing",
                stage="insights",
            )
            insight_summary = ""

        # Final response
        final_response = {
            "agent_level": agent_analysis,
            "system_level": system_analysis,
            "domain_level": domain_analysis,
            "insights": insight_summary,
        }

        logger.info(
            "[6/6] simulation_analysis_completed",
            agent_recommendation_count=len(
                (agent_analysis or {}).get("actionable_recommendations") or []
            ),
            system_recommendation_count=len(
                (system_analysis or {}).get("actionable_recommendations") or []
            ),
            domain_recommendation_count=len(
                (domain_analysis or {}).get("actionable_recommendations") or []
            ),
            insights_present=bool((insight_summary or "").strip()),
        )
        return final_response

    def _analyze_chat(
        self,
        *,
        simulation_data: dict,
        aggregate_metrics: dict,
        cluster_dict_by_eval: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Chat analysis pipeline.

        Uses chat-specific prompt templates per stage while keeping the output schema
        consistent with voice analysis.
        """
        scenarios = simulation_data.get("scenarios", [])
        eval_templates = simulation_data.get("eval_templates", [])
        if not scenarios:
            logger.warning("No scenarios provided for analysis.")
            return {}

        logger.info(
            "[1/6] simulation_analysis_started",
            scenario_count=len(scenarios),
            eval_template_count=len(eval_templates or []),
        )

        logger.info("[2/6] agent_level_analysis_started", stage="agent_level")
        try:
            agent_analysis = self.adapter.generate_agent_level_analysis_chat(
                cluster_dict_by_eval or {},
                max_fail_per_eval=self.MAX_FAIL_PER_EVAL,
                max_success_per_eval=self.MAX_SUCCESS_PER_EVAL,
                max_total_clusters=self.MAX_TOTAL_CLUSTERS,
            )
        except Exception:
            logger.exception(
                "[2/6] agent_level_analysis_failed_continuing",
                stage="agent_level",
            )
            agent_analysis = {"working_well": [], "actionable_recommendations": []}

        logger.info("[3/6] system_level_analysis_started", stage="system_level")
        try:
            system_analysis = self.adapter.generate_system_level_analysis_chat(
                scenarios,
                aggregate_metrics or {},
            )
        except Exception:
            logger.exception(
                "[3/6] system_level_analysis_failed_continuing",
                stage="system_level",
            )
            system_analysis = {
                "actionable_recommendations": [],
                "human_comparison_summary": "",
            }

        logger.info("[4/6] domain_level_analysis_started", stage="domain_level")
        try:
            domain_analysis = self.adapter.generate_domain_level_analysis_chat(
                scenarios,
                eval_templates=eval_templates,
                eval_semantics_batch_size=self.EVAL_SEMANTICS_BATCH_SIZE,
                min_calls=self.MIN_CALLS,
                max_branches=self.MAX_BRANCHES,
                max_evals_per_branch=self.MAX_EVALS_PER_BRANCH,
            )
        except Exception:
            logger.exception(
                "[4/6] domain_level_analysis_failed_continuing",
                stage="domain_level",
            )
            domain_analysis = {"actionable_recommendations": []}

        logger.info("[5/6] overall_insights_started", stage="insights")
        try:
            insight_summary = self.adapter.generate_overall_insights_chat(
                agent_level=agent_analysis or {},
                domain_level=domain_analysis or {},
                system_level=system_analysis or {},
            )
        except Exception:
            logger.exception(
                "[5/6] overall_insights_failed_continuing",
                stage="insights",
            )
            insight_summary = ""

        final_response = {
            "agent_level": agent_analysis,
            "system_level": system_analysis,
            "domain_level": domain_analysis,
            "insights": insight_summary,
        }

        logger.info(
            "[6/6] simulation_analysis_completed",
            agent_recommendation_count=len(
                (agent_analysis or {}).get("actionable_recommendations") or []
            ),
            system_recommendation_count=len(
                (system_analysis or {}).get("actionable_recommendations") or []
            ),
            domain_recommendation_count=len(
                (domain_analysis or {}).get("actionable_recommendations") or []
            ),
            insights_present=bool((insight_summary or "").strip()),
        )
        return final_response

    def analyze(
        self,
        simulation_data: dict,
        aggregate_metrics: dict,
        cluster_dict_by_eval: Dict[str, Any],
        simulation_type: str | None = None,
    ) -> Dict[str, Any]:
        """Run agent-level, system-level, and domain-level analyses."""
        scenarios = simulation_data.get("scenarios", [])
        resolved_simulation_type = (simulation_type or "voice").strip().lower()
        logger.info(
            "simulation_analysis_branch_selected",
            simulation_type=resolved_simulation_type,
            scenario_count=len(scenarios),
        )
        logger.debug(
            "simulation_analysis_payload_summary",
            simulation_type=resolved_simulation_type,
            scenario_count=len(scenarios),
            aggregate_metrics_keys=list((aggregate_metrics or {}).keys()),
            cluster_eval_count=len(cluster_dict_by_eval or {}),
        )

        if resolved_simulation_type in {"text", "chat"}:
            return self._analyze_chat(
                simulation_data=simulation_data,
                aggregate_metrics=aggregate_metrics,
                cluster_dict_by_eval=cluster_dict_by_eval,
            )

        return self._analyze_voice(
            simulation_data=simulation_data,
            aggregate_metrics=aggregate_metrics,
            cluster_dict_by_eval=cluster_dict_by_eval,
        )
