import structlog

from tfc.ee_stub import _ee_stub

try:
    from ee.agenthub.fix_your_agent.simulation_analysis_agent import (
        SimulationAnalysisAgent,
    )
except ImportError:
    SimulationAnalysisAgent = _ee_stub("SimulationAnalysisAgent")

from model_hub.models.develop_dataset import Cell, Row
from simulate.models import (
    AgentDefinition,
    AgentOptimiser,
    AgentOptimiserRun,
    CallExecution,
    CallTranscript,
    SimulateEvalConfig,
    TestExecution,
)
from simulate.utils.eval_explaination_summary import (
    _get_eval_configs,
)
from simulate.utils.test_execution import calculate_aggregate_metrics

logger = structlog.get_logger(__name__)


def _resolve_simulation_type(
    test_execution: TestExecution, scenarios: list[dict] | None
) -> str:
    """
    Resolve simulation modality for downstream analysis branching.

    Preference order:
    1) Scenario payload (derived from call executions)
    2) Selected AgentVersion snapshot
    3) AgentDefinition agent_type
    """
    scenarios = scenarios or []
    scenario_types = {
        str(s.get("simulation_type") or "").strip().lower()
        for s in scenarios
        if isinstance(s, dict)
    }
    scenario_types.discard("")
    if scenario_types:
        logger.debug(
            "sim_analysis_type_candidates",
            test_execution_id=str(getattr(test_execution, "id", "")),
            scenario_types=sorted(scenario_types),
        )
        if scenario_types.issubset({"text", "chat"}):
            return "text"
        if "voice" in scenario_types:
            return "voice"

    run_test = getattr(test_execution, "run_test", None)
    agent_type = ""

    agent_version = getattr(run_test, "agent_version", None) if run_test else None
    version_snapshot = (
        getattr(agent_version, "configuration_snapshot", None)
        if agent_version
        else None
    )
    if isinstance(version_snapshot, dict):
        agent_type = str(
            version_snapshot.get("agent_type")
            or version_snapshot.get("agentType")
            or ""
        )

    if not agent_type and run_test and getattr(run_test, "agent_definition", None):
        agent_type = str(getattr(run_test.agent_definition, "agent_type", "") or "")

    normalized = agent_type.strip().lower()
    if normalized in {"text", "chat"}:
        return "text"
    return "voice"


def _build_chat_aggregate_metrics(
    call_executions: list,
) -> dict[str, float]:
    """
    Build chat aggregate metrics from per-call conversation metrics.

    Expects per-call metrics in ``CallExecution.conversation_metrics_data``.
    Produces averages using an ``agg_`` prefix (e.g., ``avg_latency_ms`` -> ``agg_avg_latency_ms``).
    """
    sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    considered_calls = 0

    for call in call_executions:
        if str(getattr(call, "simulation_call_type", "") or "").strip().lower() not in {
            "text",
            "chat",
        }:
            continue
        considered_calls += 1

        chat_metrics = getattr(call, "conversation_metrics_data", None)
        if not isinstance(chat_metrics, dict):
            continue

        for k, v in chat_metrics.items():
            if not isinstance(v, (int, float)):
                continue
            key = str(k)
            sums[key] = sums.get(key, 0.0) + float(v)
            counts[key] = counts.get(key, 0) + 1

    averages: dict[str, float] = {}
    for key, total in sums.items():
        count = counts.get(key) or 0
        if count <= 0:
            continue
        value = total / count

        # Keep aggregate values compact and stable for prompt blocks.
        if key in {"total_tokens", "input_tokens", "output_tokens", "avg_latency_ms"}:
            value = float(round(value))
        elif key in {"turn_count", "csat_score"}:
            value = float(round(value, 1))
        else:
            value = float(round(value, 2))

        averages[f"agg_{key}"] = value

    logger.info(
        "sim_analysis_chat_agg_built",
        considered_calls=considered_calls,
        aggregate_metric_count=len(averages),
    )
    return averages


def _build_fix_your_agent_eval_templates(
    eval_configs: list,
) -> tuple[list[dict], set[str], dict[str, str]]:
    """
    Build a deduplicated eval template list (unique by eval_template_id) for
    simulation analysis, plus the mappings needed to enrich scenario eval outputs.

    Returns:
      - eval_templates: list[dict] (unique by eval_template_id)
      - allowed_eval_config_ids: set[str]
      - eval_config_id_to_template_id: dict[str, str]
    """
    eval_templates_by_id: dict[str, dict] = {}
    allowed_eval_config_ids: set[str] = set()
    eval_config_id_to_template_id: dict[str, str] = {}

    for eval_config in eval_configs or []:
        if not getattr(eval_config, "eval_template", None):
            continue

        eval_config_id = str(eval_config.id)
        allowed_eval_config_ids.add(eval_config_id)
        eval_template = eval_config.eval_template
        eval_template_id = str(eval_template.id)
        eval_config_id_to_template_id[eval_config_id] = eval_template_id
        eval_template_config = eval_template.config or {}

        eval_template_output = (
            eval_template_config.get("output")
            if isinstance(eval_template_config, dict)
            else None
        )
        allowed_choices = None
        raw_choices = getattr(eval_template, "choices", None)
        if isinstance(raw_choices, list):
            allowed_choices = [
                str(c)
                for c in raw_choices
                if isinstance(c, (str, int, float)) and str(c).strip()
            ]

        multi_choice = None
        raw_multi_choice = getattr(eval_template, "multi_choice", None)
        if isinstance(raw_multi_choice, bool):
            multi_choice = raw_multi_choice

        # Optional score hints for "score" evals.
        failure_threshold = None
        score_range_hint = None

        if isinstance(eval_template_config, dict) and eval_template_output == "score":
            # Platform norm: score outputs are 0..1 inclusive.
            score_range_hint = {"min": 0.0, "max": 1.0}

            cfg_block = eval_template_config.get("config")
            if isinstance(cfg_block, dict):
                raw_failure_threshold = cfg_block.get("failure_threshold")
                if isinstance(raw_failure_threshold, (int, float)):
                    failure_threshold = float(raw_failure_threshold)

            if failure_threshold is None:
                failure_threshold = 0.5

        if eval_template_id not in eval_templates_by_id:
            eval_templates_by_id[eval_template_id] = {
                "eval_id": eval_template_id,
                "name": (
                    str(eval_template.name)
                    if getattr(eval_template, "name", None)
                    else eval_template_id
                ),
                "output_type": eval_template_output or "",
                "criteria": str(getattr(eval_template, "criteria", "") or ""),
                "failure_threshold": failure_threshold,
                "score_range_hint": score_range_hint,
                "choices": allowed_choices,
                "multi_choice": multi_choice,
            }

    return (
        list(eval_templates_by_id.values()),
        allowed_eval_config_ids,
        eval_config_id_to_template_id,
    )


def prepare_simulation_analysis_input(test_execution_id: str) -> dict | None:
    """
    Prepare input data for simulation analysis agent.

    This function gathers all required data for the agent optimiser run:
    - Call execution metrics
    - Aggregate metrics
    - Scenario data
    - Cluster dictionary for eval explanations

    Args:
        test_execution_id: UUID of the test execution

    Returns:
        dict: Input data ready to be stored in AgentOptimiserRun.input_data
        Format: {
            "test_execution_id": str,
            "scenarios": list[dict],
            "aggregate_metrics": dict,
            "cluster_dict_by_eval": dict,
            "metadata": dict
        }
        Returns None if no valid data can be prepared

    Raises:
        TestExecution.DoesNotExist: If test execution not found
    """
    try:
        logger.info("sim_analysis_input_start", test_execution_id=test_execution_id)
        test_execution = TestExecution.objects.get(id=test_execution_id)

        call_executions = CallExecution.objects.filter(
            test_execution=test_execution, status="completed"
        )

        call_count = call_executions.count()

        if call_count == 0:
            logger.warning(
                "sim_analysis_input_no_calls",
                test_execution_id=test_execution_id,
            )
            return None

        # Collect deduplicated eval templates once (avoid repeating per scenario).
        eval_configs = _get_eval_configs(test_execution.run_test)
        (
            eval_templates_list,
            allowed_eval_config_ids,
            eval_config_id_to_template_id,
        ) = _build_fix_your_agent_eval_templates(eval_configs)
        allowed_eval_template_ids = {
            str(t.get("eval_id"))
            for t in (eval_templates_list or [])
            if isinstance(t, dict) and t.get("eval_id")
        }

        if not eval_templates_list:
            logger.warning(
                "sim_analysis_no_eval_templates",
                test_execution_id=test_execution_id,
            )

        if not allowed_eval_config_ids:
            logger.warning(
                "sim_analysis_no_eval_configs",
                test_execution_id=test_execution_id,
            )

        logger.info(
            "sim_analysis_input_eval_templates",
            test_execution_id=test_execution_id,
            eval_template_count=len(eval_templates_list or []),
            eval_config_count=len(allowed_eval_config_ids),
        )

        # Construct scenarios
        scenarios = construct_scenarios_from_calls(
            call_executions,
            allowed_eval_config_ids=allowed_eval_config_ids,
            eval_config_id_to_template_id=eval_config_id_to_template_id,
            allowed_eval_template_ids=allowed_eval_template_ids,
        )
        # Calculate aggregate metrics
        aggregate_metrics = calculate_aggregate_metrics(call_executions)

        simulation_type = _resolve_simulation_type(test_execution, scenarios)
        logger.info(
            "sim_analysis_input_type_resolved",
            test_execution_id=test_execution_id,
            simulation_type=simulation_type,
            scenario_count=len(scenarios),
            call_count=call_count,
        )
        if simulation_type in {"text", "chat"}:
            chat_aggregate = _build_chat_aggregate_metrics(call_executions)
            if isinstance(aggregate_metrics, dict):
                # Attach chat aggregates under a dedicated key so voice metrics remain unchanged.
                aggregate_metrics["chat"] = chat_aggregate

        if not scenarios:
            logger.warning(
                f"No scenarios constructed for test execution {test_execution_id}"
            )
            return None

        if test_execution.eval_explanation_summary is None:
            # The analysis step relies on clustered eval explanations.
            from simulate.tasks.eval_summary_tasks import run_eval_summary_task

            logger.info(
                "sim_analysis_input_eval_summary_missing",
                test_execution_id=test_execution_id,
            )
            run_eval_summary_task(str(test_execution.id))
            test_execution.refresh_from_db(fields=["eval_explanation_summary"])

        input_data = {
            "test_execution_id": test_execution_id,
            "scenarios": scenarios,
            "aggregate_metrics": aggregate_metrics,
            "cluster_dict_by_eval": test_execution.eval_explanation_summary,
            # Global, deduplicated eval templates/metadata (avoid repeating per scenario).
            "eval_templates": eval_templates_list,
            "metadata": {
                "call_count": call_count,
                "scenario_count": len(scenarios),
                "run_test_id": str(test_execution.run_test.id),
                "run_test_name": test_execution.run_test.name,
                # Deterministic top-level modality selection used to branch voice vs chat behavior.
                "simulation_type": simulation_type,
            },
        }

        logger.info(
            "sim_analysis_input_done",
            test_execution_id=test_execution_id,
            simulation_type=simulation_type,
            scenario_count=len(scenarios),
            call_count=call_count,
            has_eval_explanation_summary=bool(test_execution.eval_explanation_summary),
        )
        return input_data

    except Exception as e:
        logger.exception(
            f"Failed to prepare simulation analysis input for {test_execution_id}: {str(e)}"
        )
        return None


def construct_scenarios_from_calls(
    call_executions,
    allowed_eval_config_ids: set[str] | None = None,
    eval_config_id_to_template_id: dict[str, str] | None = None,
    allowed_eval_template_ids: set[str] | None = None,
) -> list[dict]:
    """
    Construct scenario payloads from call execution data.

    Args:
        call_executions: QuerySet of CallExecution objects

    Returns:
        list: List of scenario dictionaries with metrics and metadata
    """
    scenarios = []

    try:
        logger.info(
            "sim_analysis_scenarios_start",
            call_count=call_executions.count(),
        )
        for call in call_executions:
            branch = call.call_metadata.get(
                "conversation_branch"
            ) or call.call_metadata.get("row_data", {}).get(
                "conversation_branch", "Unknown"
            )

            scenario_data = {
                # Include `call_execution_id` so downstream analysis can reference
                # specific calls when reporting issues.
                "call_execution_id": str(call.id),
                "simulation_type": str(
                    getattr(call, "simulation_call_type", "") or "voice"
                ).lower(),
                "metrics": {
                    "customer_latency_metrics": call.customer_latency_metrics or {},
                    "customer_cost_breakdown": call.customer_cost_breakdown or {},
                    "customer_cost_cents": call.customer_cost_cents or 0,
                    "avg_agent_latency_ms": call.avg_agent_latency_ms or 0,
                    "response_time_ms": call.response_time_ms or 0,
                    "agent_interruption_count": call.ai_interruption_count or 0,
                    "user_interruption_count": call.user_interruption_count or 0,
                    "talk_ratio": call.talk_ratio or 0.0,
                    "csat": call.overall_score or 0.0,
                },
                "conversation_branch": branch,
                "branch_category": call.call_metadata.get("row_data", {}).get(
                    "branch_category", ""
                ),
                # Raw eval outputs plus per-config metadata (if available).
                "eval_outputs": {},
                # Include tool evaluation outputs (when enabled) for future
                # tooling / pipeline diagnostics in the analysis.
                "tool_outputs": call.tool_outputs or {},
                "outcome": call.call_metadata.get("row_data", {}).get("outcome", ""),
                "situation": call.call_metadata.get("row_data", {}).get(
                    "situation", ""
                ),
            }

            # For chat simulations, include per-call chat metrics stored on the call.
            if str(getattr(call, "simulation_call_type", "") or "").lower() in {
                "text",
                "chat",
            }:
                chat_metrics = getattr(call, "conversation_metrics_data", None)
                if isinstance(chat_metrics, dict):
                    for k, v in chat_metrics.items():
                        if v is None:
                            continue
                        scenario_data["metrics"][str(k)] = v

            raw_eval_outputs = call.eval_outputs or {}
            if (
                isinstance(raw_eval_outputs, dict)
                and allowed_eval_config_ids is not None
            ):
                filtered: dict[str, dict] = {}
                for k, v in raw_eval_outputs.items():
                    eval_config_id = str(k)
                    if eval_config_id not in allowed_eval_config_ids:
                        continue
                    if not isinstance(v, dict):
                        continue

                    eval_template_id = (eval_config_id_to_template_id or {}).get(
                        eval_config_id
                    ) or ""
                    if (
                        allowed_eval_template_ids
                        and eval_template_id not in allowed_eval_template_ids
                    ):
                        continue

                    # Attach template id to each eval output so analysis can map
                    # results to the deduplicated eval_templates list without per-scenario
                    # metadata duplication.
                    enriched = dict(v)
                    if eval_template_id:
                        enriched["eval_template_id"] = eval_template_id
                    enriched["eval_config_id"] = eval_config_id
                    filtered[eval_config_id] = enriched

                scenario_data["eval_outputs"] = filtered
            else:
                scenario_data["eval_outputs"] = (
                    raw_eval_outputs if isinstance(raw_eval_outputs, dict) else {}
                )

            scenarios.append(scenario_data)

    except Exception as e:
        logger.exception(f"Failed to construct scenarios: {str(e)}")

    return scenarios


def execute_simulation_analysis(input_data: dict) -> dict:
    """
    Execute the simulation analysis agent with prepared input data.

    This function is called by the celery task to actually run the agent.

    Args:
        input_data: Input data from AgentOptimiserRun.input_data
                   Expected format: {
                       "scenarios": list[dict],
                       "aggregate_metrics": dict,
                       "cluster_dict_by_eval": dict,
                       ...
                   }

    Returns:
        dict: Analysis result from SimulationAnalysisAgent
        Format: {
            "agent_level": {...},
            "system_level": {...},
            ...
        }

    Raises:
        str: Error message if analysis fails
    """

    try:
        scenarios = input_data.get("scenarios", [])
        aggregate_metrics = input_data.get("aggregate_metrics", {})
        cluster_dict_by_eval = input_data.get("cluster_dict_by_eval", {})
        eval_templates_list = input_data.get("eval_templates", [])
        test_execution_id = input_data.get("test_execution_id")
        simulation_type = str(
            (input_data.get("metadata") or {}).get("simulation_type") or "voice"
        ).lower()

        logger.info(
            "sim_analysis_run_start",
            test_execution_id=test_execution_id,
            simulation_type=simulation_type,
            scenario_count=len(scenarios),
            eval_template_count=len(eval_templates_list or []),
            has_eval_explanation_summary=bool(cluster_dict_by_eval),
        )

        if not scenarios:
            logger.warning(
                f"No scenarios in input for test execution {test_execution_id}"
            )
            return {
                "error": "No scenarios provided",
                "agent_level": {},
                "system_level": {},
            }

        # Run the analysis agent
        agent = SimulationAnalysisAgent()
        analysis_result = agent.analyze(
            simulation_data={
                "scenarios": scenarios,
                "eval_templates": eval_templates_list,
            },
            aggregate_metrics=aggregate_metrics,
            cluster_dict_by_eval=cluster_dict_by_eval,
            simulation_type=simulation_type,
        )

        logger.info(
            "sim_analysis_run_done",
            test_execution_id=test_execution_id,
            simulation_type=simulation_type,
        )

        return analysis_result

    except Exception as e:
        test_exec_id = input_data.get("test_execution_id") if input_data else "unknown"
        logger.exception(
            f"Simulation analysis execution failed for test execution {test_exec_id}: {str(e)}"
        )
        raise


def get_or_create_optimiser_for_test_execution(
    test_execution: TestExecution,
) -> AgentOptimiser:
    """
    Get or create an AgentOptimiser for a test execution.

    Args:
        test_execution: TestExecution instance

    Returns:
        AgentOptimiser instance
    """
    if test_execution.agent_optimiser:
        return test_execution.agent_optimiser

    optimiser = AgentOptimiser.objects.create(
        name=f"Optimiser for {test_execution.run_test.name}",
        description="Simulation analysis optimiser for test execution runs",
        configuration={"type": "simulation_analysis"},
    )

    test_execution.agent_optimiser = optimiser
    test_execution.save(update_fields=["agent_optimiser"])

    logger.info(
        f"Created optimiser {optimiser.id} for test execution {test_execution.id}"
    )

    return optimiser


def get_latest_optimiser_result(
    optimiser: AgentOptimiser, test_execution: TestExecution
) -> dict:
    """
    Get the latest optimiser run result.

    Args:
        optimiser: AgentOptimiser instance
        test_execution: TestExecution instance
    Returns:
        dict with response data including status and result
    """
    latest_run = optimiser.latest_run

    if not latest_run:
        run = create_optimiser_run_for_test_execution(test_execution, optimiser)

        if not run:
            return {
                "response": None,
                "status": "completed",
                "message": "No optimiser runs found. Trigger a refresh to start analysis.",
            }

        latest_run = run

    return {
        "response": latest_run.result,
        "status": latest_run.status,
        "last_updated": latest_run.updated_at.isoformat(),
    }


def create_optimiser_run_for_test_execution(
    test_execution: TestExecution, optimiser: AgentOptimiser
) -> AgentOptimiserRun | None:
    """
    Create a new optimiser run for a test execution.

    Args:
        test_execution: TestExecution instance
        optimiser: AgentOptimiser instance

    Returns:
        AgentOptimiserRun instance or None if input data couldn't be prepared
    """
    input_data = prepare_simulation_analysis_input(str(test_execution.id))

    if not input_data:
        logger.warning(
            f"Unable to prepare input data for test execution {test_execution.id}"
        )
        return None

    run = AgentOptimiserRun.objects.create(
        agent_optimiser=optimiser,
        input_data=input_data,
    )

    logger.info(
        f"Created optimiser run {run.id} for test execution {test_execution.id}"
    )

    from simulate.tasks.agent_optimiser_tasks import execute_optimiser_run

    try:
        execute_optimiser_run.delay(str(run.id))
    except Exception as dispatch_error:
        run.mark_as_failed({"dispatch_error": str(dispatch_error)})
        logger.exception(
            "optimiser_run_dispatch_failed",
            optimiser_run_id=str(run.id),
            test_execution_id=str(test_execution.id),
            error=str(dispatch_error),
        )

    return run


def get_agent_definition_prompt(
    agent_definition_id: str,
    agent_version_id: str | None = None,
) -> dict | None:
    """
    Get the agent definition's prompt/description.

    Prioritizes getting data from the agent version's configuration snapshot
    if a version ID is provided. Falls back to agent definition data.

    Args:
        agent_definition_id: UUID of the agent definition
        agent_version_id: Optional UUID of the specific agent version used

    Returns:
        dict containing agent prompt details or None if not found
        Format: {
            "agent_id": str,
            "agent_name": str,
            "description": str,
            "provider": str,
            "agent_type": str,
            "version_id": str | None,
            "version_number": int | None
        }
    """
    try:
        agent_def = AgentDefinition.objects.get(id=agent_definition_id)

        if agent_version_id:
            from simulate.models import AgentVersion

            try:
                version = AgentVersion.objects.get(id=agent_version_id)
                snapshot = version.configuration_snapshot or {}

                return {
                    "agent_id": str(agent_def.id),
                    "inbound": snapshot.get("inbound", agent_def.inbound),
                    "agent_name": snapshot.get("agent_name", agent_def.agent_name),
                    "description": snapshot.get("description", agent_def.description),
                    "provider": snapshot.get("provider", agent_def.provider),
                    "agent_type": snapshot.get("agent_type", agent_def.agent_type),
                    "version_id": str(version.id),
                    "version_number": version.version_number,
                }
            except AgentVersion.DoesNotExist:
                logger.warning(
                    f"Agent version {agent_version_id} not found, falling back to agent definition"
                )

        result = {
            "agent_id": str(agent_def.id),
            "inbound": agent_def.inbound,
            "agent_name": agent_def.agent_name,
            "description": agent_def.description,
            "provider": agent_def.provider,
            "agent_type": agent_def.agent_type,
            "version_id": None,
            "version_number": None,
        }

        return result

    except AgentDefinition.DoesNotExist:
        logger.warning(f"Agent definition not found: {agent_definition_id}")
        return None
    except Exception as e:
        logger.exception(f"Error fetching agent definition prompt: {e}")
        return None


def get_call_executions_with_details(test_execution_id: str) -> list[dict] | None:
    """
    Get all call executions with transcripts, scenario data, and evaluations.

    Args:
        test_execution_id: UUID of the test execution

    Returns:
        list of call execution dicts or None if error
        Format: [{
            "call_execution_id": str,
            "status": str,
            "phone_number": str,
            "duration_seconds": int | None,
            "transcripts": list[dict],
            "scenario_data": dict,
            "evaluations": list[dict],
            "metrics": dict,
        }]
    """
    try:
        test_execution = TestExecution.objects.get(id=test_execution_id)

        status_filter = ["completed"]

        call_executions = CallExecution.objects.filter(
            test_execution=test_execution,
            status__in=status_filter,
        ).select_related("scenario")

        # Fetch eval configs once (not per-call) to avoid N+1.
        run_test = test_execution.run_test
        eval_configs = list(
            SimulateEvalConfig.objects.filter(run_test=run_test).select_related(
                "eval_template"
            )
        )

        results = []
        for call in call_executions:
            call_data = _build_call_execution_data(call, test_execution, eval_configs)
            results.append(call_data)

        return results

    except TestExecution.DoesNotExist:
        logger.warning(f"Test execution not found: {test_execution_id}")
        return None
    except Exception as e:
        logger.exception(f"Error fetching call executions: {e}")
        return None


def _build_call_execution_data(
    call: CallExecution,
    test_execution: TestExecution,
    eval_configs: list,
) -> dict:
    """Build a complete call execution data dict."""
    transcripts = _get_transcripts_for_call(call)
    scenario_data = _get_scenario_data(call)
    evaluations = _get_evaluations_for_call(call, eval_configs)

    return {
        "call_execution_id": str(call.id),
        "status": call.status,
        "phone_number": call.phone_number,
        "duration_seconds": call.duration_seconds,
        "started_at": call.started_at.isoformat() if call.started_at else None,
        "completed_at": call.completed_at.isoformat() if call.completed_at else None,
        "recording_url": call.recording_url,
        "transcripts": transcripts,
        "scenario_data": scenario_data,
        "evaluations": evaluations,
        "metrics": {
            "overall_score": call.overall_score,
            "response_time_ms": call.response_time_ms,
            "avg_agent_latency_ms": call.avg_agent_latency_ms,
            "user_interruption_count": call.user_interruption_count,
            "ai_interruption_count": call.ai_interruption_count,
            "talk_ratio": call.talk_ratio,
            "user_wpm": call.user_wpm,
            "bot_wpm": call.bot_wpm,
        },
        "eval_outputs": call.eval_outputs,
    }


def _get_transcripts_for_call(call: CallExecution) -> list[dict]:
    """Get transcripts for a call execution."""
    transcripts = CallTranscript.objects.filter(call_execution=call).order_by(
        "start_time_ms"
    )

    return [
        {
            "speaker_role": t.speaker_role,
            "content": t.content,
            "start_time_ms": t.start_time_ms,
            "end_time_ms": t.end_time_ms,
            "confidence_score": t.confidence_score,
        }
        for t in transcripts
    ]


def _get_scenario_data(call: CallExecution) -> dict:
    """
    Get scenario data (columns/row data) for a call execution.
    Fetches from the dataset if available.
    """
    scenario = call.scenario
    if not scenario:
        return {"columns": {}, "metadata": call.call_metadata.get("row_data", {})}

    scenario_data = {
        "scenario_id": str(scenario.id),
        "scenario_name": scenario.name,
        "scenario_type": scenario.scenario_type,
        "source": scenario.source,
        "columns": {},
        "row_data": call.call_metadata.get("row_data", {}),
    }

    if scenario.dataset and call.row_id:
        column_data = _fetch_dataset_row_columns(scenario.dataset.id, call.row_id)
        scenario_data["columns"] = column_data

    return scenario_data


def _fetch_dataset_row_columns(dataset_id: str, row_id: str) -> dict:
    """Fetch column data for a specific dataset row."""
    try:
        row = Row.objects.filter(id=row_id, dataset_id=dataset_id).first()
        if not row:
            return {}

        cells = Cell.objects.filter(row=row).select_related("column")

        column_data = {}
        for cell in cells:
            column_data[str(cell.column.id)] = {
                "value": cell.value,
                "data_type": cell.column.data_type,
                "column_name": str(cell.column.name),
            }

        return column_data

    except Exception as e:
        logger.exception(f"Error fetching dataset row columns: {e}")
        return {}


def _get_evaluations_for_call(
    call: CallExecution,
    eval_configs: list,
) -> list[dict]:
    evaluations = []
    for config in eval_configs:
        eval_data = _build_evaluation_data(config, call)
        evaluations.append(eval_data)

    return evaluations


def _build_evaluation_data(
    config: SimulateEvalConfig,
    call: CallExecution,
) -> dict:
    """
    Build evaluation data with inputs separated into audio and text,
    including actual data values used for evaluation.
    """
    template = config.eval_template
    required_keys = template.config.get("required_keys", [])
    mapping = config.mapping or {}
    row_data = call.call_metadata.get("row_data", {})

    # Build data dict with required_key: value
    data = {}

    for key in required_keys:
        mapped_field = mapping.get(key, key)
        actual_value = _get_actual_value_for_key(key, mapped_field, call, row_data)
        data[key] = actual_value

    eval_result = None
    if call.eval_outputs:
        eval_result = call.eval_outputs.get(str(config.id))

    return {
        "eval_config_id": str(config.id),
        "eval_template_id": str(template.id),
        "eval_name": config.name or template.name,
        "eval_template_name": template.name,
        "description": template.description,
        "criteria": template.criteria,
        "output_type": template.config.get("output", ""),
        "required_keys": required_keys,
        "data": data,
        "mapping": mapping,
        "config": config.config,
        "eval_tags": template.eval_tags,
        "model": config.model or template.model,
        "result": eval_result,
        "eval_type_id": template.config.get("eval_type_id"),
        "template_config": template.config,
    }


def _get_actual_value_for_key(
    key: str,
    mapped_field: str,
    call: CallExecution,
    row_data: dict,
) -> any:
    """
    Get the actual value for an evaluation input key.
    Checks multiple sources: special fields, row_data, call attributes.
    """
    # Handle special/built-in fields
    special_fields = {
        "transcript": _get_transcript_text(call),
        "recording_url": call.recording_url,
        "stereo_recording_url": call.stereo_recording_url,
        "stereo_audio": call.stereo_recording_url,
        "recording": call.recording_url,
        "audio": call.recording_url,
        "call_summary": call.call_summary,
        "duration": call.duration_seconds,
        "duration_seconds": call.duration_seconds,
    }

    # Check if key matches a special field
    key_lower = key.lower()
    for special_key, value in special_fields.items():
        if special_key in key_lower:
            return value

    # Check if mapped_field matches a special field
    mapped_lower = mapped_field.lower()
    for special_key, value in special_fields.items():
        if special_key == mapped_lower:
            return value

    # Check row_data for the mapped field
    if mapped_field in row_data:
        return row_data[mapped_field]

    # Check row_data for the key itself
    if key in row_data:
        return row_data[key]

    # Check call_metadata
    if mapped_field in call.call_metadata:
        return call.call_metadata[mapped_field]

    return None


def _get_transcript_text(call: CallExecution) -> str:
    """Get formatted transcript text from a call execution."""
    transcripts = CallTranscript.objects.filter(call_execution=call).order_by(
        "start_time_ms"
    )

    if not transcripts.exists():
        return ""

    transcript_lines = []
    for t in transcripts:
        role = t.speaker_role.upper() if t.speaker_role else "UNKNOWN"
        transcript_lines.append(f"{role}: {t.content}")

    return "\n".join(transcript_lines)


def _get_prompt_from_run_test(run_test) -> dict | None:
    from model_hub.models.run_prompt import PromptVersion

    if run_test.source_type != "prompt" or not run_test.prompt_version_id:
        return None
    try:
        version = PromptVersion.objects.get(id=run_test.prompt_version_id)
        snapshot = version.prompt_config_snapshot or {}
        messages = snapshot.get("messages", [])
        texts = []
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("text"):
                        texts.append(part["text"])
            elif isinstance(content, str) and content:
                texts.append(content)
        description = "\n".join(texts)
        return {
            "description": description,
            "inbound": True,
        }
    except PromptVersion.DoesNotExist:
        logger.warning(f"Prompt version {run_test.prompt_version_id} not found")
        return None


def get_full_test_execution_data(test_execution_id: str) -> dict | None:
    """
    Get complete test execution data including agent prompts,
    simulator prompt, and all call executions.

    Args:
        test_execution_id: UUID of the test execution

    Returns:
        Complete test execution data dict or None if error
        Format: {
            "test_execution_id": str,
            "status": str,
            "run_test_name": str,
            "agent_definition_prompt": dict,
            "simulator_agent_prompt": dict | None,
            "call_executions": list[dict],
            "metadata": dict,
        }
    """
    try:
        test_execution = TestExecution.objects.select_related(
            "run_test__prompt_version",
            "simulator_agent",
            "agent_definition",
            "agent_version",
        ).get(id=test_execution_id)

        agent_prompt = get_agent_definition_prompt(
            test_execution.agent_definition_id, test_execution.agent_version_id
        )
        if agent_prompt is None:
            agent_prompt = _get_prompt_from_run_test(
                test_execution.run_test
            )
        call_executions = get_call_executions_with_details(test_execution_id)

        return {
            "test_execution_id": str(test_execution.id),
            "status": test_execution.status,
            "run_test_name": test_execution.run_test.name,
            "agent_definition_prompt": agent_prompt or {},
            "call_executions": call_executions or [],
            "metadata": {
                "total_scenarios": test_execution.total_scenarios,
                "total_calls": test_execution.total_calls,
                "completed_calls": test_execution.completed_calls,
                "failed_calls": test_execution.failed_calls,
                "started_at": (
                    test_execution.started_at.isoformat()
                    if test_execution.started_at
                    else None
                ),
                "completed_at": (
                    test_execution.completed_at.isoformat()
                    if test_execution.completed_at
                    else None
                ),
            },
        }

    except TestExecution.DoesNotExist:
        logger.warning(f"Test execution not found: {test_execution_id}")
        return None
    except Exception as e:
        logger.exception(f"Error fetching full test execution data: {e}")
        return None
