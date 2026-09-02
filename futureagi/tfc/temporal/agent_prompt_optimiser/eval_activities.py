"""
Temporal activities for per-scenario evaluation.

These activities run simulation + evals for individual scenarios,
allowing parallel execution via the drop-in @temporal_activity decorator.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import structlog
from django.conf import settings
from django.db import close_old_connections

from tfc.temporal.common.client import get_workflow_result_sync
from tfc.temporal.drop_in.decorator import temporal_activity

logger = structlog.get_logger(__name__)


# ----- Serialization Helpers -----


def serialize_eval_config(eval_config: Any) -> Dict[str, Any]:
    """Serialize a SimulateEvalConfig Django model to a dict."""
    eval_template = eval_config.eval_template
    serialized_template = None

    if eval_template:
        # Get the type_id from config or name
        type_id = (
            eval_template.config.get("type_id", "") if eval_template.config else ""
        )
        if not type_id:
            type_id = eval_template.name

        serialized_template = {
            "id": str(eval_template.id),
            "name": eval_template.name,
            "type_id": type_id,
            "config": eval_template.config or {},
            "model": eval_template.model,
        }

    return {
        "id": str(eval_config.id),
        "name": eval_config.name or "",
        "config": eval_config.config or {},
        "mapping": eval_config.mapping or {},
        "model": eval_config.model,
        "error_localizer": bool(eval_config.error_localizer),
        "kb_id": str(eval_config.kb_id.id) if eval_config.kb_id else None,
        "eval_template": serialized_template,
    }


def serialize_evaluator_config(
    eval_configs: List[Any],
    issues: List[Dict[str, Any]],
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
) -> Dict[str, Any]:
    """Serialize evaluator configuration for Temporal activities."""
    serialized_configs = [serialize_eval_config(ec) for ec in (eval_configs or [])]

    return {
        "eval_configs": serialized_configs,
        "issues": issues or [],
        "use_synthetic": use_synthetic,
        "simulator_model": simulator_model,
        "customer_model": customer_model,
        "max_parallel_evals": max_parallel_evals,
        "use_issues": use_issues,
        "use_evals": use_evals,
        "use_dual_llm_sim": use_dual_llm_sim,
        "is_inbound": is_inbound,
        "initial_agent_prompt": initial_agent_prompt,
        "organization_id": str(organization.id) if organization else None,
        "workspace_id": str(workspace.id) if workspace else None,
        "eval_source": eval_source,
    }


def serialize_scenario(
    scenario_dict: Dict[str, Any], agent_prompt: str
) -> Dict[str, Any]:
    """Convert a scenario dict to serializable format.

    Preserves all fields from the original scenario dict to ensure
    evaluation works identically to the non-Temporal path.
    """
    # Start with a copy of all original fields
    result = {k: v for k, v in scenario_dict.items()}
    # Override/add the agent_prompt
    result["agent_prompt"] = agent_prompt
    # Ensure call_execution_id is a string
    if "call_execution_id" in result:
        result["call_execution_id"] = str(result["call_execution_id"])
    return result


# ----- Proxy Objects for Evaluation -----


class EvalConfigProxy:
    """Proxy object that mimics SimulateEvalConfig from serialized data."""

    def __init__(self, serialized: Dict[str, Any]):
        self.id = serialized["id"]
        self.name = serialized["name"]
        self.config = serialized["config"]
        self.mapping = serialized["mapping"]
        self.model = serialized["model"]
        self.error_localizer = serialized["error_localizer"]
        self.kb_id = serialized["kb_id"]

        if serialized.get("eval_template"):
            self.eval_template = EvalTemplateProxy(serialized["eval_template"])
        else:
            self.eval_template = None


class EvalTemplateProxy:
    """Proxy object that mimics EvalTemplate from serialized data."""

    def __init__(self, serialized: Dict[str, Any]):
        self.id = serialized["id"]
        self.name = serialized["name"]
        self.type_id = serialized.get("type_id", "")
        self.config = serialized["config"]
        self.model = serialized["model"]


def _reconstruct_evaluator(config: Dict[str, Any]):
    """Reconstruct a SimulationEvaluator from serialized config."""
    try:
        from ee.agenthub.fix_your_agent.fix_your_agent import SimulationEvaluator
    except ImportError:
        if settings.DEBUG:
            logger.warning("Could not import ee.agenthub.fix_your_agent.fix_your_agent", exc_info=True)
        return None

    # Convert serialized eval configs to proxy objects
    eval_config_proxies = [EvalConfigProxy(ec) for ec in config.get("eval_configs", [])]

    # Get organization/workspace if IDs provided
    organization = None
    workspace = None
    if config.get("organization_id"):
        try:
            from accounts.models import Organization

            organization = Organization.objects.get(id=config["organization_id"])
        except Exception:
            pass
    if config.get("workspace_id"):
        try:
            from accounts.models import Workspace

            workspace = Workspace.objects.get(id=config["workspace_id"])
        except Exception:
            pass

    return SimulationEvaluator(
        user_eval_configs=eval_config_proxies,
        issues=config.get("issues", []),
        use_synthetic=config.get("use_synthetic", True),
        simulator_model=config.get("simulator_model", "gemini-2.5-flash"),
        customer_model=config.get("customer_model", "gemini-2.5-flash"),
        max_parallel_evals=config.get("max_parallel_evals", 5),
        use_issues=config.get("use_issues", True),
        use_evals=config.get("use_evals", True),
        use_dual_llm_sim=config.get("use_dual_llm_sim", False),
        is_inbound=config.get("is_inbound", True),
        initial_agent_prompt=config.get("initial_agent_prompt"),
        organization=organization,
        workspace=workspace,
        eval_source=config.get("eval_source", "fix_your_agent"),
        use_temporal_evaluation=False,  # Prevent recursive Temporal calls
    )


# ----- Activities -----


@temporal_activity(time_limit=600, queue="tasks_xl")
def evaluate_scenario_task(
    scenario: Dict[str, Any], evaluator_config: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Evaluate a single scenario (simulation + evals).

    This is a drop-in Temporal activity that can be called via .delay()
    for parallel scenario evaluation.

    Args:
        scenario: Serialized scenario dict
        evaluator_config: Serialized evaluator config dict

    Returns:
        Dict with call_execution_id, score, reason, transcript, component_evals
    """
    close_old_connections()

    try:
        # Reconstruct evaluator
        evaluator = _reconstruct_evaluator(evaluator_config)

        # Convert scenario to eval input
        eval_input = scenario

        # Run evaluation for single scenario
        logger.info(
            f"[EVAL_TASK] Evaluating scenario {scenario.get('call_execution_id')}"
        )
        results = evaluator.evaluate([eval_input])

        agent_prompt = scenario.get("agent_prompt", "")

        if not results:
            return {
                "call_execution_id": scenario.get("call_execution_id", ""),
                "agent_prompt": agent_prompt,
                "score": 0.0,
                "reason": "No evaluation results",
                "transcript": "",
                "component_evals": {},
            }

        result = results[0]
        return {
            "call_execution_id": scenario.get("call_execution_id", ""),
            "agent_prompt": agent_prompt,
            "score": result.score,
            "reason": result.reason,
            "transcript": result.metadata.get("output", ""),
            "component_evals": result.metadata.get("component_evals", {}),
        }

    except Exception as e:
        logger.exception(f"[EVAL_TASK] Scenario evaluation failed: {e}")
        return {
            "call_execution_id": scenario.get("call_execution_id", ""),
            "agent_prompt": scenario.get("agent_prompt", ""),
            "score": 0.0,
            "reason": f"Evaluation failed: {str(e)}",
            "transcript": "",
            "component_evals": {},
        }


def _cancel_running_scenario_workflows(
    workflow_ids: List[str], results_collected: List[Dict[str, Any]]
):
    """
    Cancel any scenario evaluation workflows that are still running.

    Called during cleanup (finally block) to ensure child workflows don't
    continue running after the parent activity is canceled or fails.

    Args:
        workflow_ids: List of all spawned workflow IDs
        results_collected: List of results collected so far (may be incomplete)
    """
    # Determine which workflows haven't completed yet
    num_completed = len(results_collected)
    remaining_workflow_ids = workflow_ids[num_completed:]

    if not remaining_workflow_ids:
        return  # All workflows completed successfully

    logger.info(
        f"[EVAL_CLEANUP] Canceling {len(remaining_workflow_ids)} running scenario workflows"
    )

    try:
        from tfc.temporal.common.client import cancel_workflow_sync

        for workflow_id in remaining_workflow_ids:
            try:
                if cancel_workflow_sync(workflow_id):
                    logger.debug(f"[EVAL_CLEANUP] Canceled workflow {workflow_id}")
                else:
                    logger.warning(
                        f"[EVAL_CLEANUP] Workflow was not canceled: {workflow_id}"
                    )
            except Exception as e:
                # Best effort - log but don't fail
                logger.warning(
                    f"[EVAL_CLEANUP] Failed to cancel workflow {workflow_id}: {e}"
                )

        logger.info(
            f"[EVAL_CLEANUP] Sent cancel signal to {len(remaining_workflow_ids)} workflows"
        )

    except Exception as e:
        # Best effort cleanup - don't raise
        logger.error(f"[EVAL_CLEANUP] Cleanup failed: {e}")


def evaluate_scenarios_parallel(
    scenarios: List[Dict[str, Any]],
    evaluator_config: Dict[str, Any],
    timeout_per_scenario: float = 600,
) -> List[Dict[str, Any]]:
    """
    Evaluate multiple scenarios in parallel using Temporal activities.

    Args:
        scenarios: List of serialized scenario dicts
        evaluator_config: Serialized evaluator config dict
        timeout_per_scenario: Max time per scenario in seconds

    Returns:
        List of result dicts (same order as input scenarios)
    """
    if not scenarios:
        return []

    # Start all scenario evaluations in parallel
    workflow_ids = []
    for scenario in scenarios:
        result = evaluate_scenario_task.delay(scenario, evaluator_config)
        workflow_ids.append(result.id)

    logger.info(f"[EVAL_PARALLEL] Started {len(workflow_ids)} scenario evaluations")

    # Collect results (preserving order)
    results = []

    try:
        for i, workflow_id in enumerate(workflow_ids):
            try:
                # Wait for workflow to complete and get result
                workflow_result = get_workflow_result_sync(
                    workflow_id, timeout=timeout_per_scenario
                )

                # Unwrap double-nested result:
                # 1. TaskRunnerWorkflow returns TaskRunnerOutput(result=<activity_result>, ...)
                # 2. Activity wrapper returns {"result": <actual_result>, "status": "completed"}
                actual_result = workflow_result
                logger.debug(
                    f"[EVAL_PARALLEL] Raw workflow result type: {type(workflow_result)}"
                )

                if isinstance(actual_result, dict):
                    # Unwrap TaskRunnerOutput.result
                    actual_result = actual_result.get("result", actual_result)
                    logger.debug(
                        f"[EVAL_PARALLEL] After first unwrap: {type(actual_result)}"
                    )
                if isinstance(actual_result, dict):
                    # Unwrap activity wrapper's {"result": ..., "status": ...}
                    if "result" in actual_result and "status" in actual_result:
                        actual_result = actual_result.get("result", actual_result)
                        logger.debug(
                            f"[EVAL_PARALLEL] After second unwrap: {type(actual_result)}"
                        )

                logger.info(
                    f"[EVAL_PARALLEL] Scenario {i} score: {actual_result.get('score', 'N/A')}"
                )
                results.append(actual_result)
            except Exception as e:
                logger.error(
                    f"[EVAL_PARALLEL] Failed to get result for scenario {i}: {e}"
                )
                # Add a failed result to maintain order
                results.append(
                    {
                        "call_execution_id": scenarios[i].get("call_execution_id", ""),
                        "agent_prompt": scenarios[i].get("agent_prompt", ""),
                        "score": 0.0,
                        "reason": f"Failed to get result: {str(e)}",
                        "transcript": "",
                        "component_evals": {},
                    }
                )
    finally:
        # Cancel any running child workflows on cleanup
        _cancel_running_scenario_workflows(workflow_ids, results)

    logger.info(f"[EVAL_PARALLEL] Collected {len(results)} results")
    return results


__all__ = [
    "evaluate_scenario_task",
    "evaluate_scenarios_parallel",
    "serialize_evaluator_config",
    "serialize_scenario",
]
