import random
from typing import List, Dict, Any, Optional, Tuple, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

# Type for the per-trial callback: (trial_data, trial_number, stepper_state, is_baseline) -> None
OnTrialCallback = Callable[[dict, int, Dict[str, Any], bool], None]

from ee.agent_opt.base.evaluator import Evaluator
from ee.agent_opt.datamappers import BasicDataMapper
from ee.agent_opt.generators import LiteLLMGenerator
from ee.agent_opt.optimizers import (
    RandomSearchOptimizer,
    GEPAOptimizer,
    BayesianSearchOptimizer,
    MetaPromptOptimizer,
    PromptWizardOptimizer,
    ProTeGi,
)
from ee.agent_opt.types import (
    OptimizationResult,
    EvaluationResult,
    IterationHistory,
)

from .transcript_simulator import TranscriptSimulator
import time
from model_hub.utils.evals import evals_template
from simulate.utils.agent_prompt_optimiser import update_agent_optimiser_run_step

import structlog

logger = structlog.get_logger(__name__)
import numpy as np
from model_hub.views.utils.evals import run_eval_func


from agentic_eval.core.llm.llm import LLM
from model_hub.models.evals_metric import EvalTemplate
from model_hub.utils.scoring import score_eval_output
from simulate.models import SimulateEvalConfig


class SimulationEvaluator(Evaluator):
    """
    Custom evaluator that runs simulations and evaluates them.

    Evaluation modes (in priority order):
    1. Issue-based: Evaluate against known issues from SimulationAnalysisAgent
    2. Eval-config-based: Run configured evaluations in batch
    """

    def __init__(
        self,
        user_eval_configs: Optional[List[Any]] = None,
        issues: Optional[List[Dict[str, Any]]] = None,
        use_synthetic: bool = True,
        simulator_model: str = "gemini-2.5-flash",
        customer_model: str = "gemini-2.5-flash",
        max_parallel_evals: int = 5,
        use_issues: bool = True,
        use_evals: bool = True,
        use_dual_llm_sim: bool = False,
        is_inbound: bool = True,
        initial_agent_prompt: Optional[str] = None,
        organization: Optional[Any] = None,
        workspace: Optional[Any] = None,
        eval_source: str = "fix_your_agent",
        use_temporal_evaluation: bool = False,
    ):
        self.user_eval_configs = user_eval_configs or []
        self.issues = issues or []
        self.use_synthetic = use_synthetic
        self.simulator_model = simulator_model
        self.customer_model = customer_model
        self.max_parallel_evals = max_parallel_evals
        self.use_issues = use_issues
        self.use_evals = use_evals
        self.organization = organization
        self.workspace = workspace
        self.eval_source = eval_source
        self._eval_template_cache: Dict[str, Any] = {}
        # Track initial prompt to decide when to use existing transcripts
        # Existing transcripts are only valid for the initial prompt, not mutated ones
        self.initial_agent_prompt = initial_agent_prompt
        # Use Temporal activities for parallel scenario evaluation
        self.use_temporal_evaluation = use_temporal_evaluation
        self._is_inbound = is_inbound
        self._use_dual_llm_sim = use_dual_llm_sim

        # Build lookup for evals
        self.eval_lookup = {t["name"]: t for t in evals_template}

        # Initialize the transcript simulator
        if self.use_synthetic:
            self.simulator = TranscriptSimulator(
                agent_model=simulator_model,
                customer_model=customer_model,
                single_llm=not use_dual_llm_sim,  # single_llm=True means single LLM mode
                inbound=is_inbound,
            )

        logger.info(
            f"SimulationEvaluator initialized: "
            f"{len(self.issues)} issues, "
            f"{len(self.user_eval_configs)} eval configs, "
            f"issues={use_issues}, "
            f"use evals={use_evals},"
            f"synthetic={use_synthetic},"
            f"temporal={use_temporal_evaluation}"
        )

    def _resolve_eval_template_for_runner(self, eval_template: Any) -> Optional[Any]:
        if not eval_template:
            return None

        if hasattr(eval_template, "owner") or hasattr(eval_template, "organization"):
            return eval_template

        template_id = getattr(eval_template, "id", None)
        if not template_id:
            return None

        template_id_str = str(template_id)
        if template_id_str in self._eval_template_cache:
            return self._eval_template_cache[template_id_str]

        try:
            from model_hub.models.evals_metric import EvalTemplate

            resolved = EvalTemplate.objects.get(id=template_id_str)
            self._eval_template_cache[template_id_str] = resolved
            return resolved
        except Exception as e:
            logger.debug(
                f"[RUN_EVAL] Could not resolve EvalTemplate id={template_id_str} from DB: {e}"
            )
            self._eval_template_cache[template_id_str] = None
            return None

    def _should_use_eval_runner(self, eval_template: Any) -> bool:
        """
        use run_eval_func path
        """
        if not self.organization or not hasattr(self.organization, "id"):
            return False
        if not eval_template:
            return False
        if not (hasattr(eval_template, "config") and hasattr(eval_template, "id")):
            return False
        return self._resolve_eval_template_for_runner(eval_template) is not None

    def _extract_score_from_eval_result(
        self, result: Any, eval_template: Any
    ) -> Tuple[float, str]:
        """Return ``(score, reason)`` for a raw evaluator result."""
        reason = "No explanation provided"

        if hasattr(result, "eval_results") and result.eval_results:
            first_res = result.eval_results[0]
            if isinstance(first_res, list) and first_res:
                first_res = first_res[0]
            if isinstance(first_res, dict):
                data = first_res.get("data")
                if isinstance(data, dict):
                    reason = (
                        data.get("explanation")
                        or data.get("reason")
                        or first_res.get("reason")
                        or first_res.get("explanation")
                        or reason
                    )
                else:
                    reason = (
                        first_res.get("reason")
                        or first_res.get("explanation")
                        or reason
                    )

        return score_eval_output(result, eval_template, default_score=0.5), reason

    def evaluate(self, inputs: List[Dict[str, Any]]) -> List[EvaluationResult]:
        """
        Main evaluation method called by agent-opt.

        When use_temporal_evaluation=True, scenarios are evaluated in parallel
        using Temporal activities for better scalability and control.
        """
        logger.info(f"[EVALUATE] Starting evaluation of {len(inputs)} inputs")

        # Use Temporal activities for parallel evaluation if enabled
        if self.use_temporal_evaluation and len(inputs) > 1:
            return self._evaluate_via_temporal(inputs)

        results = []

        # Determine evaluation mode
        use_issues = bool(self.issues)
        use_evals = bool(self.user_eval_configs) and self.use_evals

        logger.info(f"[EVALUATE] Mode: issues={use_issues}, evals={use_evals}")

        for i, input_data in enumerate(inputs):
            try:
                logger.info(f"[EVALUATE] === Input {i + 1}/{len(inputs)} ===")

                agent_prompt = input_data.get("agent_prompt", "")
                persona = input_data.get("persona", "")
                situation = input_data.get("situation", "")
                expected_outcome = input_data.get("expected_outcome", "")

                # Check if we can use existing transcript
                # IMPORTANT: existing_transcript is only valid for the INITIAL prompt
                # For mutated/optimized prompts, we must re-simulate
                existing_transcript = input_data.get("existing_transcript")
                is_initial_prompt = (
                    self.initial_agent_prompt is not None
                    and agent_prompt == self.initial_agent_prompt
                )

                if existing_transcript and is_initial_prompt:
                    # Use existing transcript - only valid for initial prompt
                    logger.info(
                        f"[EVALUATE] Step 1: Using existing transcript for initial prompt (len={len(existing_transcript)})"
                    )
                    transcript = existing_transcript
                else:
                    # Step 1: Run simulation
                    scenario = input_data.copy()
                    # Ensure essential keys are present (though they should be in input_data)
                    scenario.update(
                        {
                            "persona": persona,
                            "situation": situation,
                            "outcome": expected_outcome,
                        }
                    )

                    logger.info(
                        f"[EVALUATE] Step 1: Running simulation (synthetic={self.use_synthetic})"
                    )
                    sim_start_time = time.time()

                    if self.use_synthetic:
                        sim_result = self._run_synthetic_simulation(
                            agent_prompt, scenario
                        )
                    else:
                        sim_result = self._run_real_simulation(agent_prompt, scenario)

                    sim_duration = time.time() - sim_start_time
                    logger.info(
                        f"[EVALUATE] Simulation completed in {sim_duration:.2f}s"
                    )

                    transcript = sim_result.get("transcript", "")

                # Step 2: Evaluate based on configured modes
                # Build scenario for evals (needed if using existing transcript)
                scenario = input_data.copy()
                scenario.update(
                    {
                        "persona": persona,
                        "situation": situation,
                        "outcome": expected_outcome,
                    }
                )

                all_scores = []
                reason_parts = []

                # Run issue-based evaluation if enabled AND issues exist
                if use_issues and self.issues:
                    logger.info(
                        f"[EVALUATE] Step 2a: Evaluating against {len(self.issues)} issues"
                    )
                    issue_start = time.time()

                    issue_score = self._evaluate_against_issues_batch(
                        agent_prompt=agent_prompt,
                        transcript=transcript,
                    )

                    issue_duration = time.time() - issue_start
                    all_scores.append(issue_score)
                    reason_parts.append(
                        f"Issues: {issue_score:.2f} ({len(self.issues)} issues, {issue_duration:.2f}s)"
                    )
                    logger.info(f"[EVALUATE] Issue score: {issue_score:.4f}")

                # Run eval-config-based evaluation if enabled AND configs exist
                if use_evals and self.user_eval_configs:
                    logger.info(
                        f"[EVALUATE] Step 2b: Running {len(self.user_eval_configs)} evaluations in parallel"
                    )
                    eval_batch_start = time.time()

                    eval_scores, eval_reasons = self._run_evals_batch(
                        transcript=transcript,
                        agent_prompt=agent_prompt,
                        scenario=scenario,
                    )

                    eval_batch_duration = time.time() - eval_batch_start
                    eval_avg_score = (
                        sum(eval_scores) / len(eval_scores) if eval_scores else 0.5
                    )
                    all_scores.append(eval_avg_score)

                    # Include individual eval details in reason
                    eval_details = []
                    for i, (cfg, score, rsn) in enumerate(
                        zip(self.user_eval_configs, eval_scores, eval_reasons)
                    ):
                        eval_details.append(f"{cfg.name}: {score:.2f}")

                    reason_parts.append(
                        f"Evals: {eval_avg_score:.2f} [{', '.join(eval_details)}] ({eval_batch_duration:.2f}s)"
                    )
                    logger.info(f"[EVALUATE] Eval score: {eval_avg_score:.4f}")

                # Compute final score
                component_evals = {}
                if all_scores:
                    final_score = sum(all_scores) / len(all_scores)
                    reason = " | ".join(reason_parts)

                    # Populate component_evals for metadata
                    if self.use_evals and self.user_eval_configs:
                        for cfg, score, reason_txt in zip(
                            self.user_eval_configs, eval_scores, eval_reasons
                        ):
                            component_evals[cfg.id] = {
                                "score": score,
                                "reason": reason_txt,
                            }

                else:
                    logger.info(
                        f"[EVALUATE] No issues or eval configs enabled, using default scoring"
                    )
                    final_score = 0
                    reason = "No issues or evals"

                results.append(
                    EvaluationResult(
                        score=final_score,
                        reason=reason,
                        metadata={
                            "component_evals": component_evals,
                            "input": agent_prompt,
                            "output": transcript,
                        },
                    )
                )

            except Exception as e:
                logger.error(f"Evaluation failed for input {i + 1}: {e}", exc_info=True)
                results.append(
                    EvaluationResult(score=0.0, reason=f"Evaluation failed: {str(e)}")
                )

        return results

    def _evaluate_via_temporal(
        self, inputs: List[Dict[str, Any]]
    ) -> List[EvaluationResult]:
        """
        Evaluate scenarios in parallel using Temporal activities.

        Each scenario is evaluated as a separate Temporal activity, providing:
        - Better visibility into individual scenario progress
        - Independent retries per scenario
        - Parallel execution across Temporal workers
        """
        from tfc.temporal.agent_prompt_optimiser.eval_activities import (
            serialize_evaluator_config,
            serialize_scenario,
            evaluate_scenarios_parallel,
        )

        logger.info(f"[EVALUATE_TEMPORAL] Running {len(inputs)} scenarios via Temporal")

        # Serialize evaluator config once
        evaluator_config = serialize_evaluator_config(
            eval_configs=self.user_eval_configs,
            issues=self.issues,
            use_synthetic=self.use_synthetic,
            simulator_model=self.simulator_model,
            customer_model=self.customer_model,
            max_parallel_evals=self.max_parallel_evals,
            use_issues=self.use_issues,
            use_evals=self.use_evals,
            use_dual_llm_sim=self._use_dual_llm_sim,
            is_inbound=self._is_inbound,
            initial_agent_prompt=self.initial_agent_prompt,
            organization=self.organization,
            workspace=self.workspace,
            eval_source=self.eval_source,
        )

        # Serialize scenarios
        scenarios = []
        for input_data in inputs:
            agent_prompt = input_data.get("agent_prompt", "")
            scenarios.append(serialize_scenario(input_data, agent_prompt))

        # Run parallel evaluation via Temporal
        temporal_results = evaluate_scenarios_parallel(
            scenarios=scenarios,
            evaluator_config=evaluator_config,
            timeout_per_scenario=600,  # 10 min per scenario
        )

        # Convert to EvaluationResult objects
        results = []
        for tr in temporal_results:
            results.append(
                EvaluationResult(
                    score=tr.get("score", 0.0),
                    reason=tr.get("reason", ""),
                    metadata={
                        "component_evals": tr.get("component_evals", {}),
                        "input": tr.get("agent_prompt", ""),
                        "output": tr.get("transcript", ""),
                    },
                )
            )

        logger.info(f"[EVALUATE_TEMPORAL] Completed {len(results)} scenarios")
        return results

    def _run_evals_batch(
        self,
        transcript: str,
        agent_prompt: str,
        scenario: Dict[str, Any],
    ) -> Tuple[List[float], List[str]]:
        """
        Run all evaluations in parallel using ThreadPoolExecutor.

        Returns:
            Tuple of (scores, reasons) lists
        """
        eval_scores = [0.5] * len(self.user_eval_configs)  # Default scores
        eval_reasons = ["Not evaluated"] * len(
            self.user_eval_configs
        )  # Default reasons

        def run_single_eval(idx_config):
            idx, eval_config = idx_config
            try:
                score, reason = self._run_eval_with_config(
                    eval_config=eval_config,
                    transcript=transcript,
                    agent_prompt=agent_prompt,
                    scenario=scenario,
                )
                logger.debug(
                    f"[BATCH] Eval {idx + 1} ({eval_config.name}): {score:.4f} - {reason[:100]}"
                )
                return idx, score, reason
            except Exception as e:
                logger.error(f"[BATCH] Eval {idx + 1} failed: {e}")
                return idx, 0.5, f"Eval failed: {str(e)}"

        with ThreadPoolExecutor(max_workers=self.max_parallel_evals) as executor:
            futures = {
                executor.submit(run_single_eval, (i, cfg)): i
                for i, cfg in enumerate(self.user_eval_configs)
            }

            for future in as_completed(futures):
                idx, score, reason = future.result()
                eval_scores[idx] = score
                eval_reasons[idx] = reason

        return eval_scores, eval_reasons

    def _run_eval_with_config(
        self, eval_config, transcript: str, agent_prompt: str, scenario: Dict[str, Any]
    ) -> Tuple[float, str]:
        """
        Run a single evaluation using direct evaluation class instantiation.

        Returns:
            Tuple of (score, reason)
        """
        try:
            # Get the evaluation template
            eval_template = eval_config.eval_template

            logger.debug(f"[RUN_EVAL] Starting eval: {eval_template.name}")
            logger.debug(f"[RUN_EVAL] Eval config model: {eval_config.model}")
            logger.debug(f"[RUN_EVAL] Eval template model: {eval_template.model}")

            # 1. Identify the evaluation class
            # CHECK SKIP CONDITIONS
            # Condition 1: Output type is "choices" — skip only for labeled
            # (non-numeric) choices when NO choice_scores mapping is configured.
            # Numeric choices (e.g. ["0.0", "0.2", ..., "1.0"]) produce valid scores.
            # Labeled choices WITH choice_scores flow through score_eval_output.
            # Empty choices default to scorer mode in the evaluator.
            output_type = eval_template.config.get("output", "")
            if isinstance(output_type, str) and output_type.lower() == "choices":
                inner_config = eval_template.config.get("config", {})
                choices = (
                    inner_config.get("choices", [])
                    if isinstance(inner_config, dict)
                    else []
                )
                all_numeric = not choices or all(
                    isinstance(c, (int, float))
                    or (
                        isinstance(c, str)
                        and c.replace(".", "", 1).lstrip("-").isdigit()
                    )
                    for c in choices
                )
                has_choice_scores = bool(getattr(eval_template, "choice_scores", None))
                if not all_numeric and not has_choice_scores:
                    logger.info(
                        f"[RUN_EVAL] Skipping eval {eval_template.name} because output type is 'choices' with non-numeric values and no choice_scores"
                    )
                    return (0.5, "skipped_choices")

            # Condition 2: Specific audio evals
            if eval_template.name in ["audio_quality", "ASR/STT_accuracy"]:
                logger.info(
                    f"[RUN_EVAL] Skipping eval {eval_template.name} because it requires audio recording"
                )
                return (0.5, "skipped_audio")

            eval_type_id = eval_template.config.get("eval_type_id")

            # If not found, try to look up in evals_template
            if not eval_type_id:
                name_key = eval_template.name
                template_data = self.eval_lookup.get(name_key)
                if template_data:
                    eval_type_id = template_data.get("config", {}).get("eval_type_id")
                    # Also update required_keys if missing
                    if not eval_template.config.get("required_keys"):
                        eval_template.config["required_keys"] = template_data.get(
                            "config", {}
                        ).get("required_keys", [])

            if not eval_type_id:
                # Fallback: try converting snake_case to PascalCase
                name_key = eval_template.name or ""
                if "_" in name_key:
                    eval_type_id = "".join(x.title() for x in name_key.split("_"))
                else:
                    eval_type_id = name_key.title()

            logger.debug(f"[RUN_EVAL] Resolved eval_type_id: {eval_type_id}")

            # 2. Prepare arguments (mapping)
            mapping = eval_config.mapping.copy() if eval_config.mapping else {}
            updated_mapping = {}

            for key, value in mapping.items():
                if value == "transcript":
                    updated_mapping[key] = transcript
                elif value == "agent_prompt":
                    updated_mapping[key] = agent_prompt
                elif value == "persona":
                    updated_mapping[key] = scenario.get("persona", "")
                elif value == "situation":
                    updated_mapping[key] = scenario.get("situation", "")
                elif value == "outcome":
                    updated_mapping[key] = scenario.get("outcome", "")
                elif value in ["voice_recording", "stereo_recording"]:
                    # Voice and stereo recordings are audio recordings of transcripts,
                    # so we can pass the transcript text instead
                    updated_mapping[key] = transcript
                elif value in ["assistant_recording", "customer_recording"]:
                    # Individual channel recordings cannot be derived from transcript
                    updated_mapping[key] = None
                elif value in scenario:
                    updated_mapping[key] = scenario[value]
                else:
                    updated_mapping[key] = value

            # CHECK REQUIRED KEYS
            # If any required key is None, we should skip this evaluation
            required_keys = eval_template.config.get("required_keys", [])
            for req_key in required_keys:
                # Check if this required key is mapped to something that is None
                # We need to find which argument in run_kwargs corresponds to this required key
                # Usually required_keys match the argument names in run()
                if req_key in updated_mapping and updated_mapping[req_key] is None:
                    logger.warning(
                        f"[RUN_EVAL] Skipping eval {eval_template.name} because required key '{req_key}' is None (missing input)"
                    )
                    return (0.5, "skip_eval")  # Neutral score for skipped eval

            if self._should_use_eval_runner(eval_template):
                try:
                    from django.db import close_old_connections

                    close_old_connections()

                    eval_template_for_runner = self._resolve_eval_template_for_runner(
                        eval_template
                    )
                    if not eval_template_for_runner:
                        raise ValueError("eval_template_unresolvable_for_runner")

                    config = (
                        eval_config.config.copy()
                        if getattr(eval_config, "config", None)
                        else {}
                    )
                    # `run_eval_func` expects `config` to be a dict and `config["config"]` to be a dict.
                    # Exported execution_data may contain `{"config": None, ...}`.
                    if config.get("config") is None or not isinstance(
                        config.get("config"), dict
                    ):
                        config["config"] = {}
                    model = getattr(eval_config, "model", None) or getattr(
                        eval_template_for_runner, "model", None
                    )
                    kb_id = getattr(eval_config, "kb_id", None)
                    error_localizer = bool(
                        getattr(eval_config, "error_localizer", False)
                    )

                    eval_result = run_eval_func(
                        config=config,
                        mappings=updated_mapping,
                        template=eval_template_for_runner,
                        org=self.organization,
                        model=model,
                        kb_id=kb_id,
                        error_localizer=error_localizer,
                        workspace=self.workspace,
                        source=self.eval_source,
                    )

                    if isinstance(eval_result, dict):
                        score = score_eval_output(
                            eval_result.get("output"),
                            eval_template,
                            default_score=0.5,
                        )
                        reason = eval_result.get("reason") or ""

                        if eval_template.config.get("reverse_output", False):
                            score = 1.0 - score

                        return score, reason

                    # run_eval_func sometimes returns an error string
                    if isinstance(eval_result, str):
                        return 0.0, eval_result

                    return 0.0, "Unknown eval result format"
                except Exception as e:
                    logger.warning(
                        f"[RUN_EVAL] EvalRunner path failed, falling back to direct eval: {e}"
                    )

            # try creating a manual instance of evaluation if invalid workspace org object
            import agentic_eval.core_evals.fi_evals as fi_evals

            eval_class = getattr(fi_evals, eval_type_id, None)

            if not eval_class:
                logger.warning(
                    f"[RUN_EVAL] Could not find evaluation class for {eval_type_id} (name: {eval_template.name})"
                )
                return (0.5, "no eval found")

            logger.debug(f"[RUN_EVAL] Using eval class: {eval_class.__name__}")

            # 3. Instantiate and run
            # Most eval classes take config in __init__ and arguments in run()
            # We must pass the FULL template config to the evaluator, especially for LlmEvaluator
            config = eval_template.config.copy() if eval_template.config else {}

            # Merge with user config if any (though usually empty for these)
            if eval_config.config:
                config.update(eval_config.config)

            # Pass the model name as-is; downstream eval classes resolve it
            # via ModelConfigs and route through the Turing API internally.
            model_name = eval_config.model or eval_template.model
            if model_name:
                config["model"] = model_name
            logger.debug(
                f"[RUN_EVAL] Using model: {model_name} eval_type={eval_type_id}"
            )

            logger.debug(f"[RUN_EVAL] Config keys: {list(config.keys())}")
            logger.debug(f"[RUN_EVAL] Run kwargs keys: {list(updated_mapping.keys())}")

            # Some evals expect specific config keys
            try:
                eval_instance = eval_class(**config)
            except TypeError as te:
                logger.debug(
                    f"[RUN_EVAL] TypeError creating instance with config: {te}"
                )
                # Fallback for classes that don't take **config
                eval_instance = eval_class()

            # Run the evaluation
            logger.debug(f"[RUN_EVAL] Running evaluation...")
            result = eval_instance.run(**updated_mapping)
            score, reason = self._extract_score_from_eval_result(result, eval_template)

            # Apply reverse_output if configured
            # For evals that detect "bad things" (e.g., answer_refusal, prompt_injection),
            # reverse_output=True means: 0.0 = didn't find bad thing = good = should be 1.0
            if eval_template.config.get("reverse_output", False):
                score = 1.0 - score

            return score, reason

        except Exception as e:
            logger.error(f"Evaluation with config failed: {e}", exc_info=True)
            return 0.0, f"Evaluation error: {str(e)}"

    def _evaluate_against_issues_batch(
        self, agent_prompt: str, transcript: str
    ) -> float:
        """
        Evaluate prompt and transcript against known issues in parallel.

        Issues format from SimulationAnalysisAgent:
        {
            "heading": "...",
            "priority": "high|medium|low",
            "recommendation": "...",
            "breaking_points": ["issue 1", "issue 2"],
            "cluster_ids": [0, 1]
        }
        """

        if not self.issues:
            return 0.5

        llm = LLM(
            model_name="gemini-2.5-flash",
            max_tokens=100,
            provider="vertex_ai",
            temperature=0.4,
        )

        def evaluate_single_issue(issue: Dict[str, Any]) -> float:
            """Evaluate a single issue."""
            # Extract issue details
            heading = issue.get("heading", "")
            recommendation = issue.get("recommendation", "")
            breaking_points = issue.get("breaking_points", [])
            priority = issue.get("priority", "medium")

            # Build issue description
            if breaking_points:
                bp_text = "\n".join(f"- {bp}" for bp in breaking_points)
                issue_desc = f"Issue: {heading}\nBreaking Points:\n{bp_text}\nRecommendation: {recommendation}"
            else:
                issue_desc = (
                    str(issue)
                    if isinstance(issue, str)
                    else f"Issue: {heading or recommendation}"
                )

            prompt_text = f"""Evaluate if this agent prompt and conversation avoids the following issue.

ISSUE TO AVOID:
{issue_desc}

AGENT PROMPT:
{agent_prompt}

CONVERSATION:
{transcript}

Score the conversation:
- 1.0 = Issue is fully addressed/avoided, behavior is correct
- 0.7 = Mostly addressed but minor concerns
- 0.5 = Partially addressed
- 0.3 = Issue is present but not severe
- 0.0 = Issue is clearly present and severe

Return ONLY a number between 0.0 and 1.0."""

            try:
                response = llm._get_completion_content(
                    messages=[{"role": "user", "content": prompt_text}],
                )
                # Extract score from response
                score_str = "".join(
                    c for c in response.strip() if c.isdigit() or c == "."
                )
                score = float(score_str) if score_str else 0.5
                return max(0.0, min(1.0, score))
            except Exception as e:
                logger.warning(f"Issue evaluation failed: {e}")
                return 0.5

        # Build weighted issues (high priority = more weight)
        priority_weights = {"high": 1.5, "medium": 1.0, "low": 0.7}

        scores = []
        weights: List[float] = []

        with ThreadPoolExecutor(max_workers=self.max_parallel_evals) as executor:
            future_to_issue = {
                executor.submit(evaluate_single_issue, issue): issue
                for issue in self.issues
            }

            for future in as_completed(future_to_issue):
                issue = future_to_issue[future]
                try:
                    score = future.result()
                    weight = priority_weights.get(issue.get("priority", "medium"), 1.0)
                    scores.append(score)
                    weights.append(weight)
                except Exception as e:
                    logger.error(f"Issue eval failed: {e}")
                    scores.append(0.5)
                    weights.append(1.0)

        # Weighted average
        if not scores:
            return 0.5

        total_weight = sum(weights)
        weighted_sum = sum(s * w for s, w in zip(scores, weights))
        return weighted_sum / total_weight if total_weight > 0 else 0.5

    def _evaluate_against_issues(self, agent_prompt: str) -> float:
        """Legacy: Fast evaluation of prompt only (deprecated, use _evaluate_against_issues_batch)."""
        return self._evaluate_against_issues_batch(agent_prompt, "")

    def _run_synthetic_simulation(
        self, agent_prompt: str, scenario: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Run 2-LLM text simulation using TranscriptSimulator.

        If issues are configured, they will be passed to the simulator to create
        challenging scenarios that test whether the agent handles known weaknesses.
        """
        customer_system_prompt = scenario.get("customer_system_prompt")
        transcript = self.simulator.run_simulation(
            agent_system_prompt=agent_prompt,
            scenario=scenario,
            customer_system_prompt=customer_system_prompt,
            max_turns=np.random.choice(np.arange(10, 20, 2)).item(),
            issues=self.issues,  # Pass issues to create challenging scenarios
        )

        return {
            "transcript": transcript,
            "voice_recording": None,
            "assistant_recording": None,
            "customer_recording": None,
            "stereo_recording": None,
        }

    def _run_real_simulation(
        self, agent_prompt: str, scenario: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Run actual voice simulation - integrate with voice infrastructure
        """
        # TODO: Integrate with actual voice simulation system (VAPI/Retell/etc)
        raise NotImplementedError("Real voice simulation integration needed")


class SimulationDataMapper(BasicDataMapper):
    """
    Maps scenario data to the format needed by SimulationEvaluator or DirectEvaluator.
    """

    def __init__(self):
        pass

    def map(
        self,
        generated_output: str,  # The agent_prompt from generator
        ground_truth_example: Dict[str, Any],  # The scenario dict
    ) -> Dict[str, Any]:
        """Transform data for the evaluator."""
        # Start with all data from the ground truth
        result = ground_truth_example.copy()

        # Add specific fields needed for evaluation
        result.update(
            {
                "agent_prompt": generated_output,
                "persona": ground_truth_example.get("persona", ""),
                "situation": ground_truth_example.get("situation", ""),
                "expected_outcome": ground_truth_example.get("outcome", ""),
                "customer_system_prompt": ground_truth_example.get(
                    "customer_system_prompt"
                ),
                "existing_transcript": ground_truth_example.get("existing_transcript"),
                # For direct evaluation (dataset optimization): include input data
                "input": ground_truth_example.get("input", {}),
            }
        )
        return result


class FixYourAgent:
    """
    Main agent for offline prompt optimization.

    Usage:
        agent = FixYourAgent()
        result = agent.optimize(
            initial_agent_prompt="You are a helpful agent...",
            scenarios=[...],
            eval_configs=[...],
            optimizer_type="random_search",
            num_variations=3
        )
    """

    def __init__(self):
        """Initialize the FixYourAgent."""
        pass

    def optimize(
        self,
        initial_agent_prompt: str,
        scenarios: List[Dict[str, Any]],
        eval_configs: Optional[List[Any]] = None,
        issues: Optional[List[Dict[str, Any]]] = None,
        optimizer_type: str = "random_search",
        optimizer_config: Optional[Dict[str, Any]] = None,
        use_synthetic: bool = True,
        optimization_model: str = "vertex_ai/gemini-2.5-flash",
        api_key: Optional[str] = None,
        use_issues: bool = True,
        use_evals: bool = True,
        is_inbound: bool = True,
        use_dual_llm_sim: bool = False,
        agent_optimiser_run_steps: List[Dict] = None,
        organization: Optional[Any] = None,
        workspace: Optional[Any] = None,
        eval_source: str = "fix_your_agent",
        resume_state: Optional[Dict[str, Any]] = None,
        max_new_trials: Optional[int] = None,
        skip_baseline: bool = False,
        on_trial_callback: Optional[OnTrialCallback] = None,
        use_temporal_evaluation: bool = False,
        use_direct_evaluation: bool = False,
        execution_model: str = "gpt-4o",
    ) -> OptimizationResult:
        """
        Optimize an agent definition prompt.

        Args:
            initial_agent_prompt: The starting agent prompt to optimize
            scenarios: List of dicts with keys: persona, situation, outcome
            eval_configs: List of SimulateEvalConfig instances (used if issues not provided or use_issues_only=False)
            issues: List of known issues from SimulationAnalysisAgent (primary evaluation method)
            optimizer_type: One of: random_search, gepa, protegi, bayesian, metaprompt, promptwizard
            optimizer_config: Dict
            use_synthetic: Whether to use synthetic (LLM) simulation
            optimization_model: Model to use for optimization (e.g., "vertex_ai/gemini-2.5-flash")
            use_issues_only: use agent_level issues
            organization: Organization instance for evaluation context
            workspace: Workspace instance for evaluation context
            eval_source: Usage logging source string (used when organization is set)

        Returns:
            OptimizationResult with the best prompt and optimization history
        """
        optimizer_config = optimizer_config or {}

        # logger.info("=" * 80)
        # logger.info("STARTING AGENT PROMPT OPTIMIZATION")
        logger.info(f"Initial prompt: {initial_agent_prompt[:100]}...")
        logger.info(f"Scenarios: {len(scenarios)}")
        logger.info(f"Issues: {len(issues) if issues else 0}")
        logger.info(f"Eval configs: {len(eval_configs) if eval_configs else 0}")
        logger.info(f"Optimizer: {optimizer_type}")
        logger.info(f"Use issues: {use_issues}, Use evals: {use_evals}")
        logger.info(f"Optimizer config: {optimizer_config}")
        # logger.info("=" * 80)

        # Step 1: Onboard/initializing - Mark as running
        update_agent_optimiser_run_step(agent_optimiser_run_steps, 1, status="running")

        # Sample scenarios: 10% of total, min 5, max 10
        total_scenarios = len(scenarios)
        sample_size = max(5, min(10, int(total_scenarios * 0.1)))
        sample_size = min(
            sample_size, total_scenarios
        )  # Don't exceed available scenarios

        if total_scenarios > sample_size:
            sampled_scenarios = random.sample(scenarios, sample_size)
            logger.info(
                f"Sampled {sample_size} scenarios from {total_scenarios} total "
                f"(10% with min=5, max=10)"
            )
        else:
            sampled_scenarios = scenarios
            logger.info(
                f"Using all {total_scenarios} scenarios (below sampling threshold)"
            )

        # 1. Prepare dataset from sampled scenarios
        dataset = []
        for i, scenario in enumerate(sampled_scenarios):
            # Use _original_row_data as base to include all extra columns
            item = scenario.get("_original_row_data", {}).copy()

            # Update with standard keys ensuring they take precedence
            item.update(
                {
                    "call_execution_id": scenario.get("call_execution_id", ""),
                    "persona": scenario.get("persona", ""),
                    "situation": scenario.get("situation", ""),
                    "outcome": scenario.get("outcome", ""),
                    # Pass these separately for evaluator use, but NOT for prompt optimization
                    "customer_system_prompt": scenario.get("customer_system_prompt"),
                    "existing_transcript": scenario.get("existing_transcript"),
                    # For direct evaluation (dataset optimization): include input data
                    "input": scenario.get("input", {}),
                }
            )

            dataset.append(item)

        # 2. Create the custom evaluator
        if use_direct_evaluation:
            # Use DirectEvaluator for single input/output evaluation (no conversation simulation)
            from .direct_evaluator import DirectEvaluator

            from ee.agent_opt.utils.template_variables import (
                extract_template_variables,
            )

            template_vars = extract_template_variables(initial_agent_prompt)

            evaluator = DirectEvaluator(
                user_eval_configs=eval_configs,
                execution_model=execution_model,
                initial_agent_prompt=initial_agent_prompt,
                organization=organization,
                workspace=workspace,
                eval_source=eval_source,
                template_variables=template_vars if template_vars else None,
                max_parallel_evals=sample_size,
            )
        else:
            # Use SimulationEvaluator for multi-turn conversation simulation
            evaluator = SimulationEvaluator(
                user_eval_configs=eval_configs,
                issues=issues,
                use_synthetic=use_synthetic,
                use_issues=use_issues,
                use_evals=use_evals,
                use_dual_llm_sim=use_dual_llm_sim,
                initial_agent_prompt=initial_agent_prompt,  # Track for existing_transcript logic
                is_inbound=is_inbound,
                organization=organization,
                workspace=workspace,
                eval_source=eval_source,
                use_temporal_evaluation=use_temporal_evaluation,
            )

        # 3. Create the data mapper
        data_mapper = SimulationDataMapper()

        # 4. Initialize Generators

        generator = LiteLLMGenerator(
            model=optimization_model,
            prompt_template=initial_agent_prompt,
        )
        teacher_generator = LiteLLMGenerator(
            model=optimization_model,
            prompt_template="{prompt}",
        )

        # 5. Initialize the optimizer based on type
        if optimizer_type == "random_search":
            optimizer = RandomSearchOptimizer(
                generator=generator,
                teacher_model=optimizer_config.get("teacher_model", optimization_model),
                num_variations=optimizer_config.get("num_variations", 3),
            )
        elif optimizer_type == "gepa":
            optimizer = GEPAOptimizer(
                reflection_model=optimizer_config.get(
                    "reflection_model", optimization_model
                ),
                generator_model=optimizer_config.get(
                    "generator_model", optimization_model
                ),
            )
        elif optimizer_type == "protegi":
            optimizer = ProTeGi(
                teacher_generator=teacher_generator,
                beam_size=optimizer_config.get("beam_size", 3),
                num_gradients=optimizer_config.get("num_gradients", 4),
                errors_per_gradient=optimizer_config.get("errors_per_gradient", 4),
                prompts_per_gradient=optimizer_config.get("prompts_per_gradient", 1),
            )
        elif optimizer_type == "bayesian":
            optimizer = BayesianSearchOptimizer(
                teacher_model_name=optimizer_config.get(
                    "teacher_model", optimization_model
                ),
                min_examples=optimizer_config.get("min_examples", 2),
                max_examples=optimizer_config.get("max_examples", 4),
                n_trials=optimizer_config.get("n_trials", 10),
            )
        elif optimizer_type == "metaprompt":
            optimizer = MetaPromptOptimizer(teacher_generator=teacher_generator)
        elif optimizer_type == "promptwizard":
            optimizer = PromptWizardOptimizer(
                teacher_generator=teacher_generator,
                mutate_rounds=optimizer_config.get("mutate_rounds", 3),
                refine_iterations=optimizer_config.get("refine_iterations", 2),
                beam_size=optimizer_config.get("beam_size", 1),
            )
        else:
            raise ValueError(
                f"Unknown optimizer type: {optimizer_type}. "
                f"Choose from: random_search, gepa, protegi, bayesian, metaprompt, promptwizard"
            )

        # 5.5 Run Baseline Evaluation (skip if resuming)
        # Step 1: Complete
        update_agent_optimiser_run_step(
            agent_optimiser_run_steps, 1, status="completed"
        )

        baseline_history = None
        if not skip_baseline:
            # Step 2: Baseline - Mark as running
            update_agent_optimiser_run_step(
                agent_optimiser_run_steps, 2, status="running"
            )

            logger.info("Running baseline evaluation on initial prompt...")
            baseline_inputs = [
                data_mapper.map(initial_agent_prompt, example) for example in dataset
            ]

            try:
                baseline_results_list = evaluator.evaluate(baseline_inputs)

                # Convert list of results to dict keyed by call_execution_id
                baseline_results_dict = {}
                for res, example in zip(baseline_results_list, dataset):
                    call_id = example.get("call_execution_id", "unknown")
                    baseline_results_dict[call_id] = res

                baseline_avg_score = (
                    sum(r.score for r in baseline_results_list)
                    / len(baseline_results_list)
                    if baseline_results_list
                    else 0.0
                )

                baseline_history = IterationHistory(
                    prompt=initial_agent_prompt,
                    average_score=baseline_avg_score,
                    individual_results=baseline_results_dict,
                )

                logger.info(f"Baseline score: {baseline_avg_score:.4f}")

                # Call callback to persist baseline immediately
                if on_trial_callback and baseline_history:
                    on_trial_callback(
                        trial_data=baseline_history.dict(),
                        trial_number=0,
                        stepper_state={},  # No optimizer state yet for baseline
                        is_baseline=True,
                    )

                # Step 2: Complete
                update_agent_optimiser_run_step(
                    agent_optimiser_run_steps, 2, status="completed"
                )

            except Exception as e:
                logger.error(f"Baseline evaluation failed: {e}", exc_info=True)
                # Step 2: Failed
                update_agent_optimiser_run_step(
                    agent_optimiser_run_steps, 2, status="failed", error=str(e)
                )
                # Create a dummy baseline if it fails
                baseline_history = IterationHistory(
                    prompt=initial_agent_prompt,
                    average_score=0.0,
                    individual_results={},
                )
        else:
            logger.info("Skipping baseline evaluation (resume mode)")
            # Mark step 2 as completed since we're skipping
            update_agent_optimiser_run_step(
                agent_optimiser_run_steps,
                2,
                status="completed",
                description="Baseline skipped (resuming)",
            )

        # 6. Run optimization
        logger.info(f"Starting optimization with {optimizer_type}...")

        update_agent_optimiser_run_step(
            agent_optimiser_run_steps,
            3,
            status="running",
            name=f"Running {optimizer_type} Optimizer...",
        )

        if api_key:
            optimizer_config["api_key"] = api_key

        try:
            extra_resume_kwargs = {}
            if optimizer_type == "random_search":
                extra_resume_kwargs = {
                    "resume_state": (resume_state or {}).get("optimizer_state"),
                    "max_new_trials": max_new_trials,
                }
            elif optimizer_type == "metaprompt":
                extra_resume_kwargs = {
                    "resume_state": (resume_state or {}).get("optimizer_state"),
                    "max_new_rounds": max_new_trials,
                }
            elif optimizer_type == "bayesian":
                extra_resume_kwargs = {
                    "resume_state": (resume_state or {}).get("optimizer_state"),
                    "max_new_trials": max_new_trials,
                }
            elif optimizer_type == "protegi":
                extra_resume_kwargs = {
                    "resume_state": (resume_state or {}).get("optimizer_state"),
                    "max_new_rounds": max_new_trials,
                }
            elif optimizer_type == "promptwizard":
                extra_resume_kwargs = {
                    "resume_state": (resume_state or {}).get("optimizer_state"),
                    "max_new_iterations": max_new_trials,
                }
            elif optimizer_type == "gepa":
                extra_resume_kwargs = {
                    "resume_state": (resume_state or {}).get("optimizer_state"),
                    "max_new_metric_calls": max_new_trials,
                }

            result = optimizer.optimize(
                evaluator=evaluator,
                data_mapper=data_mapper,
                dataset=dataset,
                initial_prompts=[initial_agent_prompt],
                agent_optimiser_run_steps=agent_optimiser_run_steps,
                on_trial_callback=on_trial_callback,
                **optimizer_config,
                **extra_resume_kwargs,
            )

            # Prepend baseline history only if we ran it
            if baseline_history is not None:
                result.history.insert(0, baseline_history)

            # Step 3: Complete
            update_agent_optimiser_run_step(
                agent_optimiser_run_steps, 3, status="completed"
            )

        except Exception as exc:
            logger.error(
                f"Optimization failed for {optimizer_type}: {exc}", exc_info=True
            )
            # Step 3: Failed
            update_agent_optimiser_run_step(
                agent_optimiser_run_steps, 3, status="failed", error=str(exc)
            )
            raise RuntimeError(
                f"[FixYourAgent] Optimization failed for {optimizer_type}: {exc}"
            )

        # logger.info(f"Best score: {result.final_score:.4f}")
        # logger.info(
        #     f"Best prompt: {result.best_generator.get_prompt_template()[:200]}..."
        # )

        # Calculate best_index
        # Step 4: Finalizing - Mark as running
        update_agent_optimiser_run_step(agent_optimiser_run_steps, 4, status="running")

        best_score = -1
        best_idx = 0
        for i, hist in enumerate(result.history):
            if hist.average_score > best_score:
                best_score = hist.average_score
                best_idx = i

        result.best_index = best_idx

        # Final safety net: repair best_prompt if template variables are missing
        # This catches any case where the optimizer stored an un-repaired candidate
        from ee.agent_opt.utils.template_variables import (
            extract_template_variables as _extract_tv,
            validate_template_variables,
            repair_template_variables,
        )

        template_vars_final = (
            _extract_tv(initial_agent_prompt) if initial_agent_prompt else set()
        )
        if template_vars_final and result.best_prompt:
            is_valid, _ = validate_template_variables(
                initial_agent_prompt, result.best_prompt, template_vars_final
            )
            if not is_valid:
                logger.warning(
                    "Final best_prompt missing template vars, repairing",
                    best_prompt_preview=result.best_prompt[:200],
                )
                result.best_prompt = repair_template_variables(
                    candidate_prompt=result.best_prompt,
                    original_prompt=initial_agent_prompt,
                    required_variables=template_vars_final,
                )

        # Step 4: Complete
        update_agent_optimiser_run_step(
            agent_optimiser_run_steps, 4, status="completed"
        )

        return result

    def optimize_from_execution(
        self,
        execution_data: Dict[str, Any],
        optimizer_type: str = "random_search",
        optimization_model: str = "vertex_ai/gemini-2.5-flash",
        use_synthetic: bool = True,
        use_issues: bool = True,
        use_evals: bool = True,
        api_key: Optional[str] = None,
        issues: Optional[List[Dict[str, Any]]] = None,
        optimizer_config: Optional[Dict[str, Any]] = None,
        use_dual_llm_sim: bool = True,
        agent_optimiser_run_steps: List[Dict] = None,
        organization: Optional[Any] = None,
        workspace: Optional[Any] = None,
        eval_source: str = "fix_your_agent",
        resume_state: Optional[Dict[str, Any]] = None,
        max_new_trials: Optional[int] = None,
        scenario_manifest: Optional[list[str]] = None,
        skip_baseline: bool = False,
        on_trial_callback: Optional[OnTrialCallback] = None,
        use_temporal_evaluation: bool = False,
        use_direct_evaluation: bool = False,
        execution_model: str = "gpt-4o",
    ) -> OptimizationResult:
        """
        Optimize an agent based on a previous test execution.

        Args:
            execution_data: The full test execution data dict
            optimizer_type: Which optimizer to use
            use_synthetic: Whether to use synthetic simulation
            organization: Organization instance
            workspace: Workspace instance
            **optimizer_kwargs: Additional optimizer args

        Returns:
            OptimizationResult
        """
        # 1. Extract initial agent prompt
        agent_def = execution_data.get("agent_definition_prompt", {})
        initial_agent_prompt = agent_def.get("description", "")
        is_inbound = agent_def.get("inbound", True)

        # 2. Extract scenarios and customer prompts
        scenarios = []
        call_executions = execution_data.get("call_executions", [])

        # Fallback: if initial_agent_prompt is empty, try to get from first call_execution
        if not initial_agent_prompt and call_executions:
            initial_agent_prompt = call_executions[0].get("initial_agent_prompt", "")
            if initial_agent_prompt:
                logger.info(
                    "Extracted initial_agent_prompt from first call_execution",
                    prompt_len=len(initial_agent_prompt),
                )

        if not initial_agent_prompt:
            logger.warning(
                "No agent description found in execution data, using empty string. "
                "Optimization will not work correctly without an initial prompt."
            )
        if scenario_manifest:
            manifest_set = {str(cid) for cid in scenario_manifest}
            call_map = {str(c.get("call_execution_id")): c for c in call_executions}
            call_executions = [
                call_map[cid] for cid in scenario_manifest if cid in call_map
            ]
            # Append any missing manifest ids? Keep only existing.

        # We also need to extract eval configs from the first call execution
        eval_configs = []
        if call_executions:
            first_call = call_executions[0]
            for eval_data in first_call.get("evaluations", []):
                try:
                    # Handle potential nested config structure
                    raw_config = eval_data.get("config") or {}
                    if (
                        isinstance(raw_config, dict)
                        and "config" in raw_config
                        and isinstance(raw_config["config"], dict)
                    ):
                        flat_config = raw_config["config"]
                    else:
                        flat_config = raw_config

                    # Create EvalTemplate instance (not saved to DB)
                    eval_template = EvalTemplate(
                        id=eval_data.get("eval_template_id"),
                        name=eval_data.get("eval_template_name"),
                        description=eval_data.get("description"),
                        criteria=eval_data.get("criteria"),
                        eval_tags=eval_data.get("eval_tags", []),
                        config=eval_data.get("template_config")
                        or {
                            "output": eval_data.get("output_type"),
                            "required_keys": eval_data.get("required_keys"),
                            "eval_type_id": eval_data.get("eval_type_id"),
                        },
                        model=eval_data.get("model"),
                        output_type_normalized=eval_data.get("output_type_normalized"),
                        choice_scores=eval_data.get("choice_scores"),
                        pass_threshold=eval_data.get("pass_threshold"),
                    )

                    # Create SimulateEvalConfig instance (not saved to DB)
                    config = SimulateEvalConfig(
                        id=eval_data.get("eval_config_id"),
                        name=eval_data.get("eval_name"),
                        mapping=eval_data.get("mapping"),
                        config=flat_config,
                        model=eval_data.get("model"),
                        eval_template=eval_template,
                    )
                    eval_configs.append(config)
                except Exception as e:
                    logger.warning(f"Failed to reconstruct eval config: {e}")

        for call in call_executions:
            # Extract customer system prompt and build full transcript
            customer_system_prompt = None
            transcript_parts = []

            for transcript in call.get("transcripts", []):
                speaker_role = transcript.get("speaker_role", "")
                content = transcript.get("content", "")

                if speaker_role == "system":
                    customer_system_prompt = content
                elif speaker_role in ["agent", "assistant"]:
                    transcript_parts.append(f"Agent: {content}")
                elif speaker_role in ["customer", "user"]:
                    transcript_parts.append(f"Customer: {content}")

            # Build full transcript from conversation
            existing_transcript = (
                "\n".join(transcript_parts) if transcript_parts else None
            )

            # Extract scenario data
            scenario_data = call.get("scenario_data", {})
            row_data = scenario_data.get("row_data", {})
            call_execution_id = call.get("call_execution_id")

            # For dataset optimization: capture the input dict directly
            # This contains the column values from the dataset row
            input_data = call.get("input", {})

            scenario = {
                "call_execution_id": call_execution_id,
                "persona": row_data.get("persona", ""),
                "situation": row_data.get("situation", ""),
                "outcome": row_data.get("outcome", ""),
                "customer_system_prompt": customer_system_prompt,
                "existing_transcript": existing_transcript,  # Pass existing transcript
                "_original_row_data": row_data,
                "input": input_data,  # For direct evaluation (dataset optimization)
            }
            scenarios.append(scenario)

        # 3. Run optimization
        return self.optimize(
            initial_agent_prompt=initial_agent_prompt,
            scenarios=scenarios,
            eval_configs=eval_configs,
            optimizer_type=optimizer_type,
            optimization_model=optimization_model,
            use_synthetic=use_synthetic,
            issues=issues,
            use_issues=use_issues,
            use_evals=use_evals,
            api_key=api_key,
            optimizer_config=optimizer_config,
            is_inbound=is_inbound,
            use_dual_llm_sim=use_dual_llm_sim,
            agent_optimiser_run_steps=agent_optimiser_run_steps,
            organization=organization,
            workspace=workspace,
            eval_source=eval_source,
            resume_state=resume_state,
            max_new_trials=max_new_trials,
            skip_baseline=skip_baseline,
            on_trial_callback=on_trial_callback,
            use_temporal_evaluation=use_temporal_evaluation,
            use_direct_evaluation=use_direct_evaluation,
            execution_model=execution_model,
        )
