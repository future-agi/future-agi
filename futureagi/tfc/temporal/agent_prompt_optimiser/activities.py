"""
Temporal activities for Agent Prompt Optimiser.

Design notes:
- Single activity model: setup -> run_optimization (long-running) -> finalize
- Per-trial persistence via callback (store_single_trial)
- Resume from latest PromptTrial.metadata["optimizer_state"]
- No checkpoint table - state lives in PromptTrial.metadata
- No locks - workflows are guaranteed not to conflict
"""

from __future__ import annotations

from typing import Any

import structlog
from asgiref.sync import sync_to_async
from django.db import close_old_connections
from temporalio import activity

# Activity-aware stub: invocations raise a Temporal non-retryable
# ApplicationError so the workflow fails once instead of retrying.
from tfc.ee_stub import _ee_activity_stub as _ee_stub

try:
    from ee.agenthub.fix_your_agent.fix_your_agent import FixYourAgent
except ImportError:
    FixYourAgent = _ee_stub("FixYourAgent")
from simulate.models import (
    AgentPromptOptimiserRun,
    PromptTrial,
)
from simulate.utils.agent_optimiser import get_full_test_execution_data
from simulate.utils.agent_prompt_optimiser import (
    get_agent_prompt_optimiser_run_steps,
    store_single_trial,
    update_agent_optimiser_run_step,
)
from tfc.temporal.common.heartbeat import Heartbeater

logger = structlog.get_logger(__name__)


def _safe_close_db():
    try:
        close_old_connections()
    except Exception:
        pass


def _compute_total_trials(optimiser_type: str, config: dict[str, Any]) -> int:
    """
    Calculate total trials based on optimizer type and configuration.

    Each optimizer has different loop structures:
    - random_search: num_variations trials (simple iteration)
    - metaprompt: num_rounds trials (iterative refinement)
    - bayesian: n_trials trials (Optuna-driven search)
    - protegi: Expansion-based with beam search (multiple evals per round)
    - promptwizard: Mutation + refinement phases (multiple evals per iteration)
    - gepa: max_metric_calls (evolutionary optimization)
    """
    optimiser_type = optimiser_type.lower()

    if optimiser_type == "random_search":
        # Simple: one trial per variation
        return int(config.get("num_variations", 3))

    elif optimiser_type == "metaprompt":
        # Simple: one trial per round
        return int(config.get("num_rounds", 5))

    elif optimiser_type == "bayesian":
        # Simple: one trial per Optuna iteration
        return int(config.get("n_trials", 10))

    elif optimiser_type == "protegi":
        # Complex: expansion phase creates many candidates per round
        # Per round: beam_size prompts each generate num_gradients * prompts_per_gradient new prompts
        # All candidates in the pool get scored
        num_rounds = int(config.get("num_rounds", 3))
        beam_size = int(config.get("beam_size", 3))
        num_gradients = int(config.get("num_gradients", 4))
        prompts_per_gradient = int(config.get("prompts_per_gradient", 1))

        # Round 1: start with 1 prompt
        # Subsequent rounds: start with beam_size prompts
        total = 0
        current_beam = 1  # Initial prompt
        for _round_num in range(num_rounds):
            # Expansion: each prompt in beam generates new candidates
            expanded = current_beam * num_gradients * prompts_per_gradient
            # Candidate pool = current beam + expanded prompts
            candidate_pool_size = current_beam + expanded
            total += candidate_pool_size
            # Next round starts with beam_size (top candidates selected)
            current_beam = min(beam_size, candidate_pool_size)

        return total

    elif optimiser_type == "promptwizard":
        # Complex: mutation + refinement phases per iteration
        # Per iteration:
        #   - Mutation: mutate_rounds * 2 (thinking styles) variations
        #   - Score: 1 (current) + mutated variations
        #   - Refinement: beam_size prompts get refined
        #   - Final score: current best + refined
        refine_iterations = int(config.get("refine_iterations", 2))
        mutate_rounds = int(config.get("mutate_rounds", 3))
        beam_size = int(config.get("beam_size", 1))
        thinking_styles = 2  # Based on the optimizer implementation

        total = 0
        for _ in range(refine_iterations):
            # Mutation phase: generates mutate_rounds * thinking_styles variants
            mutated_count = mutate_rounds * thinking_styles
            # Score candidates: current + mutated
            total += 1 + mutated_count
            # Refinement phase: top beam_size prompts get refined (1 refined each)
            refined_count = beam_size
            # Final scoring: current best + refined variants
            total += 1 + refined_count

        return total

    elif optimiser_type == "gepa":
        # Simple: max_metric_calls is the total budget
        return int(config.get("max_metric_calls", 150))

    else:
        # Unknown optimizer type - return 0
        return 0


def _select_scenario_manifest(execution_data: dict) -> list[str]:
    call_executions = execution_data.get("call_executions", [])
    total = len(call_executions)
    if total == 0:
        return []
    sample_size = max(5, min(10, int(total * 0.1))) if total > 10 else total
    # deterministic: sort by call_execution_id then slice
    sorted_calls = sorted(
        call_executions, key=lambda c: str(c.get("call_execution_id", ""))
    )
    return [str(c.get("call_execution_id")) for c in sorted_calls[:sample_size]]


def _calc_best_from_trials(run: AgentPromptOptimiserRun):
    """Calculate best trial from AgentPromptOptimiserRun object."""
    trials = (
        PromptTrial.objects.filter(agent_prompt_optimiser_run=run)
        .order_by("-average_score")
        .values("trial_number", "average_score", "prompt")
    )
    if not trials:
        return None, None, None
    best = trials[0]
    return (
        best["trial_number"],
        best["average_score"],
        best["prompt"],
    )


def _calc_best_from_trials_by_id(run_id: str):
    """Calculate best trial from run_id (optimized for read-only, no object needed)."""
    trials = (
        PromptTrial.objects.filter(agent_prompt_optimiser_run_id=run_id)
        .order_by("-average_score")
        .values("trial_number", "average_score", "prompt")
    )
    if not trials:
        return None, None, None
    best = trials[0]
    return (
        best["trial_number"],
        best["average_score"],
        best["prompt"],
    )


@activity.defn
async def setup_run_activity(input: dict[str, Any]) -> dict[str, Any]:
    """
    Mark run as RUNNING, pin scenario manifest, compute totals.
    Simplified without locks - workflows are guaranteed not to conflict.
    """
    _safe_close_db()
    hb = Heartbeater(("setup_run",))
    async with hb:
        run_id: str = input["run_id"]

        def _sync():
            close_old_connections()

            # Get the run
            try:
                run = AgentPromptOptimiserRun.objects.get(id=run_id)
            except AgentPromptOptimiserRun.DoesNotExist:
                raise ValueError(f"Run {run_id} does not exist") from None

            # Update status if not already running
            if run.status != AgentPromptOptimiserRun.Status.RUNNING:
                run.status = AgentPromptOptimiserRun.Status.RUNNING
                run.save(update_fields=["status", "updated_at"])
                logger.info("Run status updated to RUNNING", run_id=run_id)

            # Get basic info
            test_execution_id = str(run.test_execution_id)
            optimiser_type = run.optimiser_type
            configuration = run.configuration or {}

            # Get test execution data
            execution_data = get_full_test_execution_data(test_execution_id)

            if execution_data is None:
                raise ValueError(
                    f"Test execution {test_execution_id} not found or has no data"
                )

            # Pin scenario manifest deterministically
            scenario_manifest = _select_scenario_manifest(execution_data)

            total_trials = _compute_total_trials(optimiser_type, configuration)

            # Check for existing trials (resume case) - no lock needed for reads
            latest_trial = (
                PromptTrial.objects.filter(agent_prompt_optimiser_run_id=run_id)
                .order_by("-trial_number")
                .first()
            )

            current_trial_number = latest_trial.trial_number if latest_trial else -1

            # Get best trial - no lock needed for reads
            _, best_score, best_prompt = _calc_best_from_trials_by_id(run_id)

            # Extract optimizer state from latest trial metadata
            optimizer_state = None
            if latest_trial and latest_trial.metadata:
                optimizer_state = latest_trial.metadata.get("optimizer_state")

            logger.info(
                "Setup completed successfully",
                run_id=run_id,
                total_trials=total_trials,
                current_trial=current_trial_number,
                has_resume_state=optimizer_state is not None,
            )

            return {
                "run_id": run_id,
                "total_trials": total_trials,
                "current_trial_number": current_trial_number,
                "scenario_manifest": scenario_manifest,
                "optimizer_state": optimizer_state,
                "best_prompt": best_prompt,
                "best_score": best_score,
            }

        return await sync_to_async(_sync, thread_sensitive=False)()


@activity.defn
async def run_optimization_activity(input: dict[str, Any]) -> dict[str, Any]:
    """
    Run entire optimization in one activity. Resume from latest PromptTrial if exists.
    Uses callback to persist each trial immediately after completion.
    """
    _safe_close_db()
    hb = Heartbeater(("optimization",))
    async with hb:
        run_id = input["run_id"]
        scenario_manifest = input.get("scenario_manifest", [])

        def _sync():
            close_old_connections()

            try:
                run = AgentPromptOptimiserRun.objects.get(id=run_id)
            except AgentPromptOptimiserRun.DoesNotExist:
                raise ValueError(f"Run {run_id} does not exist") from None

            # TH-5642: the agent-learning-kit optimiser has no trial loop — it runs
            # through the kit bridge in one shot. Without this branch the type fell
            # into _compute_total_trials' unknown-type 0, silently completing the
            # run as a no-op with a NULL result.
            if (
                run.optimiser_type
                == AgentPromptOptimiserRun.OptimiserType.AGENT_LEARNING_KIT
            ):
                from simulate.utils.agent_prompt_optimiser import (
                    _run_agent_learning_kit_optimisation,
                )

                _run_agent_learning_kit_optimisation(run)
                run.refresh_from_db()
                if run.status == AgentPromptOptimiserRun.Status.FAILED:
                    raise ValueError(
                        run.error_message or "agent-learning-kit optimisation failed"
                    )
                kit_summary = (run.result or {}).get("summary") or {}
                return {
                    "trials_run": 0,
                    "best_score": kit_summary.get("best_score"),
                    "best_prompt": None,
                }

            # Check for existing trials (resume case)
            latest_trial = (
                PromptTrial.objects.filter(agent_prompt_optimiser_run=run)
                .order_by("-trial_number")
                .first()
            )

            # Determine resume state
            resume_state = None
            skip_baseline = False
            if latest_trial and latest_trial.metadata:
                optimizer_state = latest_trial.metadata.get("optimizer_state")
                if optimizer_state:
                    resume_state = {"optimizer_state": optimizer_state}
                skip_baseline = True  # Already have trials, skip baseline

            # Calculate remaining trials
            completed = latest_trial.trial_number if latest_trial else -1
            total_trials = _compute_total_trials(
                run.optimiser_type, run.configuration or {}
            )
            remaining = max(0, total_trials - max(0, completed))

            if remaining <= 0:
                _, best_score, best_prompt = _calc_best_from_trials(run)
                return {
                    "trials_run": 0,
                    "best_score": best_score,
                    "best_prompt": best_prompt,
                }

            execution_data = get_full_test_execution_data(str(run.test_execution.id))

            if execution_data is None:
                raise ValueError(
                    f"Test execution {run.test_execution.id} not found or has no data"
                )

            steps = get_agent_prompt_optimiser_run_steps(run_id)
            agent = FixYourAgent()

            # Create callback that saves trial AND updates progress
            def on_trial_complete(
                trial_data: dict,
                trial_number: int,
                stepper_state: dict,
                is_baseline: bool,
            ):
                store_single_trial(
                    prompt_optimiser_run=run,
                    trial_data=trial_data,
                    trial_number=trial_number,
                    stepper_state=stepper_state,
                    is_baseline=is_baseline,
                )

                # Update progress step
                description = f"Trial {trial_number} Completed."
                if is_baseline:
                    description = "Baseline evaluation completed."
                update_agent_optimiser_run_step(
                    steps,
                    3,  # Optimization step
                    description=description,
                )

            # Check if Temporal evaluation is enabled via configuration
            use_temporal_eval = (run.configuration or {}).get(
                "use_temporal_evaluation", True
            )

            result = agent.optimize_from_execution(
                execution_data=execution_data,
                optimizer_type=run.optimiser_type,
                optimization_model=run.model,
                optimizer_config=run.configuration or {},
                use_dual_llm_sim=True,
                agent_optimiser_run_steps=steps,
                organization=run.test_execution.run_test.organization,
                workspace=run.test_execution.run_test.workspace,
                resume_state=resume_state,
                max_new_trials=remaining,
                scenario_manifest=scenario_manifest,
                skip_baseline=skip_baseline,
                on_trial_callback=on_trial_complete,
                use_temporal_evaluation=use_temporal_eval,
            )

            # Get final best results
            _, best_score, best_prompt = _calc_best_from_trials(run)

            return {
                "trials_run": len(result.history),
                "best_score": best_score or result.final_score,
                "best_prompt": best_prompt or result.best_prompt,
            }

        return await sync_to_async(_sync, thread_sensitive=False)()


@activity.defn
async def finalize_run_activity(input: dict[str, Any]) -> dict[str, Any]:
    """
    Mark run as completed/failed/canceled.
    Simplified without locks - workflows are guaranteed not to conflict.
    """
    _safe_close_db()
    status = input.get("status", "completed")
    error = input.get("error")

    run_id = input["run_id"]

    def _sync():
        close_old_connections()

        # Calculate best results first
        _, best_score, best_prompt = _calc_best_from_trials_by_id(run_id)

        # Get the run
        try:
            run = AgentPromptOptimiserRun.objects.get(id=run_id)
        except AgentPromptOptimiserRun.DoesNotExist:
            logger.error("Run does not exist in finalize", run_id=run_id)
            raise ValueError(f"Run {run_id} does not exist") from None

        # Update status based on input
        if status == "completed":
            if run.status != AgentPromptOptimiserRun.Status.COMPLETED:
                run.mark_as_completed()
                logger.info("Run marked as completed", run_id=run_id)
        elif status == "canceled":
            if run.status != AgentPromptOptimiserRun.Status.FAILED:
                run.status = AgentPromptOptimiserRun.Status.FAILED
                run.error_message = "Canceled"
                run.save(update_fields=["status", "error_message", "updated_at"])
                logger.info("Run marked as canceled", run_id=run_id)
        else:  # Failed status
            if run.status != AgentPromptOptimiserRun.Status.FAILED:
                error_message = str(error) if error else "Unknown error"
                run.mark_as_failed(error_message=error_message)
                logger.info(
                    "Run marked as failed",
                    run_id=run_id,
                    error_message=error_message,
                )

        final_status = run.status

        logger.info(
            "Finalize completed",
            run_id=run_id,
            final_status=final_status,
            best_score=best_score,
        )

        return {
            "run_id": run_id,
            "status": final_status,
            "error": error,
            "best_score": best_score,
            "best_prompt": best_prompt,
        }

    return await sync_to_async(_sync, thread_sensitive=False)()


ALL_ACTIVITIES = [
    setup_run_activity,
    run_optimization_activity,
    finalize_run_activity,
]
