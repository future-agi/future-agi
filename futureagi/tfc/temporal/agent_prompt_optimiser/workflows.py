"""
Temporal workflow orchestrating Agent Prompt Optimiser runs.

Design notes:
- Simple 3-activity workflow: setup -> run_optimization -> finalize
- Single long-running activity with heartbeats
- No signals (pause/resume/cancel) - simplify for reliability
- No queries - state is persisted in DB via callbacks
- Resume handled automatically via PromptTrial.metadata
"""

from __future__ import annotations

from datetime import timedelta
from typing import Optional

from temporalio import workflow
from temporalio.common import RetryPolicy

# Import types safely (no Django)
with workflow.unsafe.imports_passed_through():
    from tfc.temporal.agent_prompt_optimiser.types import (
        EvaluateTrialWorkflowInput,
        EvaluateTrialWorkflowOutput,
        RunAgentPromptOptimiserWorkflowInput,
        RunAgentPromptOptimiserWorkflowOutput,
        ScenarioResult,
    )
    from tfc.temporal.common.evaluation_denials import evaluation_denial_message


ACTIVITY_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    maximum_interval=timedelta(minutes=2),
    maximum_attempts=3,
    backoff_coefficient=2.0,
)


@workflow.defn
class AgentPromptOptimiserWorkflow:
    @workflow.run
    async def run(
        self, input: RunAgentPromptOptimiserWorkflowInput
    ) -> RunAgentPromptOptimiserWorkflowOutput:
        try:
            # Setup
            setup = await workflow.execute_activity(
                "setup_run_activity",
                {"run_id": input.run_id},
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=ACTIVITY_RETRY,
            )

            # Run ALL optimization in one activity (4 hour timeout)
            result = await workflow.execute_activity(
                "run_optimization_activity",
                {
                    "run_id": input.run_id,
                    "scenario_manifest": setup.get("scenario_manifest", []),
                },
                start_to_close_timeout=timedelta(hours=4),
                heartbeat_timeout=timedelta(
                    minutes=5
                ),  # Activity must heartbeat every 5 min
                retry_policy=ACTIVITY_RETRY,
            )

            # Finalize
            await workflow.execute_activity(
                "finalize_run_activity",
                {"run_id": input.run_id, "status": "completed"},
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=ACTIVITY_RETRY,
            )

            return RunAgentPromptOptimiserWorkflowOutput(
                run_id=input.run_id,
                status="completed",
                best_prompt=result.get("best_prompt"),
                best_score=result.get("best_score"),
                trials_completed=result.get("trials_run", 0),
                error=None,
            )

        except Exception as e:
            error_msg = evaluation_denial_message(e) or str(e)

            # Try to finalize with error status
            try:
                await workflow.execute_activity(
                    "finalize_run_activity",
                    {"run_id": input.run_id, "status": "failed", "error": error_msg},
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=ACTIVITY_RETRY,
                )
            except Exception:
                pass  # Best effort

            return RunAgentPromptOptimiserWorkflowOutput(
                run_id=input.run_id,
                status="failed",
                best_prompt=None,
                best_score=None,
                trials_completed=0,
                error=error_msg,
            )


# Retry policy for scenario evaluation activities
SCENARIO_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_interval=timedelta(minutes=1),
    maximum_attempts=2,
    backoff_coefficient=2.0,
)


@workflow.defn
class EvaluateTrialWorkflow:
    """
    Child workflow that evaluates a single trial's prompt against all scenarios.

    Runs scenario evaluations in parallel using activities, aggregates results.
    This provides better visibility and control over per-scenario evaluation.
    """

    @workflow.run
    async def run(
        self, input: EvaluateTrialWorkflowInput
    ) -> EvaluateTrialWorkflowOutput:
        """
        Execute parallel scenario evaluations for a trial.

        Args:
            input: Contains trial number, agent prompt, scenarios, and evaluator config

        Returns:
            EvaluateTrialWorkflowOutput with average score and per-scenario results
        """
        if not input.scenarios:
            return EvaluateTrialWorkflowOutput(
                trial_number=input.trial_number,
                average_score=0.0,
                results=[],
            )

        # Serialize evaluator config for activities
        evaluator_config_dict = None
        if input.evaluator_config:
            evaluator_config_dict = {
                "eval_configs": [
                    {
                        "id": ec.id,
                        "name": ec.name,
                        "config": ec.config,
                        "mapping": ec.mapping,
                        "model": ec.model,
                        "error_localizer": ec.error_localizer,
                        "kb_id": ec.kb_id,
                        "eval_template": (
                            {
                                "id": ec.eval_template.id,
                                "name": ec.eval_template.name,
                                "type_id": ec.eval_template.type_id,
                                "config": ec.eval_template.config,
                                "model": ec.eval_template.model,
                            }
                            if ec.eval_template
                            else None
                        ),
                    }
                    for ec in input.evaluator_config.eval_configs
                ],
                "issues": input.evaluator_config.issues,
                "use_synthetic": input.evaluator_config.use_synthetic,
                "simulator_model": input.evaluator_config.simulator_model,
                "customer_model": input.evaluator_config.customer_model,
                "max_parallel_evals": input.evaluator_config.max_parallel_evals,
                "use_issues": input.evaluator_config.use_issues,
                "use_evals": input.evaluator_config.use_evals,
                "use_dual_llm_sim": input.evaluator_config.use_dual_llm_sim,
                "is_inbound": input.evaluator_config.is_inbound,
                "initial_agent_prompt": input.evaluator_config.initial_agent_prompt,
                "organization_id": input.evaluator_config.organization_id,
                "workspace_id": input.evaluator_config.workspace_id,
                "eval_source": input.evaluator_config.eval_source,
            }

        # Schedule all scenario activities in parallel
        activity_handles = []
        for scenario in input.scenarios:
            scenario_dict = {
                "call_execution_id": scenario.call_execution_id,
                "agent_prompt": scenario.agent_prompt,
                "persona": scenario.persona,
                "situation": scenario.situation,
                "expected_outcome": scenario.expected_outcome,
                "existing_transcript": scenario.existing_transcript,
                "customer_system_prompt": scenario.customer_system_prompt,
                "extra": scenario.extra,
            }

            handle = workflow.start_activity(
                "evaluate_scenario_activity",
                {
                    "scenario": scenario_dict,
                    "evaluator_config": evaluator_config_dict,
                },
                start_to_close_timeout=timedelta(minutes=10),  # 10 min per scenario
                retry_policy=SCENARIO_RETRY,
            )
            activity_handles.append(handle)

        # Wait for all activities to complete
        results = []
        total_score = 0.0

        for handle in activity_handles:
            try:
                result_dict = await handle
                result = ScenarioResult(
                    call_execution_id=result_dict.get("call_execution_id", ""),
                    score=result_dict.get("score", 0.0),
                    reason=result_dict.get("reason", ""),
                    transcript=result_dict.get("transcript", ""),
                    component_evals=result_dict.get("component_evals", {}),
                )
                results.append(result)
                total_score += result.score
            except Exception as e:
                # Log error but continue with other scenarios
                workflow.logger.warning(f"Scenario evaluation failed: {e}")
                # Add a failed result with 0 score
                results.append(
                    ScenarioResult(
                        call_execution_id="unknown",
                        score=0.0,
                        reason=f"Evaluation failed: {str(e)}",
                    )
                )
                total_score += 0.0

        # Calculate average score
        average_score = total_score / len(results) if results else 0.0

        return EvaluateTrialWorkflowOutput(
            trial_number=input.trial_number,
            average_score=average_score,
            results=results,
        )
