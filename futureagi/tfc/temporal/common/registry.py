"""
Temporal workflow and activity registry.

Feature modules register their workflows and activities for specific queues.
Uses separate loading for workflows (no Django) and activities (has Django)
to avoid sandbox validation issues.
"""

from typing import Callable, Dict, List, Type

# =============================================================================
# Registry Storage
# =============================================================================

# Maps queue name -> list of workflow classes
_workflow_registry: Dict[str, List[Type]] = {}

# Maps queue name -> list of activity functions
_activity_registry: Dict[str, List[Callable]] = {}

# Track registration state
_workflows_registered: bool = False
_activities_registered: bool = False

TEMPORAL_ACTIVITY_MODULES = [
    # agent_prompt_optimiser eval activities
    "tfc.temporal.agent_prompt_optimiser.eval_activities",
    # Background tasks (post-registration, huggingface, etc.)
    "tfc.temporal.background_tasks.activities",
    # model_hub tasks
    "model_hub.tasks.run_prompt",
    "model_hub.tasks.experiment_runner",
    "model_hub.tasks.user_evaluation",
    "model_hub.tasks.insights",
    "model_hub.tasks.agent",
    "model_hub.tasks.model_log",
    "model_hub.tasks.dataset_embeddings",
    "model_hub.tasks.optimisation_runner",
    "model_hub.tasks.prompt_template_optimizer",
    "model_hub.tasks.develop_dataset",
    # model_hub views
    "model_hub.views.run_prompt",
    "model_hub.views.experiment_runner",
    "model_hub.views.dynamic_columns",
    "model_hub.views.optimize_dataset",
    "model_hub.views.prompt_template",
    "model_hub.views.develop_dataset",
    "model_hub.views.datasets.create.file_upload",
    "model_hub.views.utils.evals",
    # model_hub utils
    "model_hub.utils.auto_annotate",
    # tracer tasks
    "tracer.tasks",
    "tracer.tasks.trace_scanner",
    "tracer.tasks.recordings_rehost",
    "tracer.utils.span",
    "tracer.utils.eval",
    "tracer.utils.observability_provider",
    "tracer.utils.trace_ingestion",
    "tracer.utils.inline_evals",
    "tracer.utils.external_eval",
    "tracer.utils.eval_tasks",
    "tracer.utils.monitor",
    # simulate tasks
    "simulate.tasks.eval_summary_tasks",
    "simulate.tasks.scenario_tasks",
    "simulate.services.test_executor",
    "simulate.tasks.chat_sim",
    # voice tasks
    "ee.voice.tasks.call_log_tasks",
    # integration tasks
    "integrations.temporal.activities",
    "integrations.services.langfuse_service",
    "integrations.transformers.langfuse_transformer",
    # billing tasks (Phase 4.6 — budget catch-up)
    "tfc.temporal.schedules.billing",
]


# =============================================================================
# Registration Functions
# =============================================================================


def register_workflows(queue: str, workflows: List[Type]) -> None:
    """Register workflow classes for a specific queue."""
    if queue not in _workflow_registry:
        _workflow_registry[queue] = []

    for workflow_class in workflows:
        if workflow_class not in _workflow_registry[queue]:
            _workflow_registry[queue].append(workflow_class)


def register_activities(queue: str, activities: List[Callable]) -> None:
    """Register activity functions for a specific queue."""
    if queue not in _activity_registry:
        _activity_registry[queue] = []

    for activity_func in activities:
        if activity_func not in _activity_registry[queue]:
            _activity_registry[queue].append(activity_func)


def register_for_queues(
    queues: List[str],
    workflows: List[Type] = None,
    activities: List[Callable] = None,
) -> None:
    """Register workflows and activities for multiple queues at once."""
    for queue in queues:
        if workflows:
            register_workflows(queue, workflows)
        if activities:
            register_activities(queue, activities)


# =============================================================================
# Lazy Loading (separate for workflows and activities)
# =============================================================================


def _ensure_workflows_registered() -> None:
    """
    Load workflows only. Does NOT import Django.
    Safe to call before Worker creation.
    """
    global _workflows_registered

    if _workflows_registered:
        return

    try:
        # Import only workflows (no Django)
        from tfc.temporal.experiments import get_workflows

        workflows = get_workflows()
        register_for_queues(
            queues=["tasks_l", "tasks_xl"],
            workflows=workflows,
        )
    except ImportError as e:
        from tfc.logging.temporal import get_logger

        get_logger(__name__).warning(
            "could_not_load_experiment_workflows", error=str(e)
        )

    # Agent prompt optimiser workflows (tasks_xl)
    try:
        from tfc.temporal.agent_prompt_optimiser import (
            get_workflows as get_apo_workflows,
        )

        register_for_queues(
            queues=["tasks_xl"],
            workflows=get_apo_workflows(),
        )
    except ImportError as e:
        from tfc.logging.temporal import get_logger

        get_logger(__name__).warning(
            "could_not_load_agent_prompt_optimiser_workflows", error=str(e)
        )

    # Dataset optimization workflows (tasks_xl)
    try:
        from tfc.temporal.dataset_optimization import get_workflows as get_do_workflows

        register_for_queues(
            queues=["tasks_xl"],
            workflows=get_do_workflows(),
        )
    except ImportError as e:
        from tfc.logging.temporal import get_logger

        get_logger(__name__).warning(
            "could_not_load_dataset_optimization_workflows", error=str(e)
        )

    # Register evaluation workflows for tasks_s queue
    try:
        from tfc.temporal.evaluations.workflows import (
            RunEvaluationBatchWorkflow,
            RunEvaluationWorkflow,
        )

        register_for_queues(
            queues=["tasks_s", "default"],
            workflows=[RunEvaluationWorkflow, RunEvaluationBatchWorkflow],
        )
    except ImportError as e:
        from tfc.logging.temporal import get_logger

        get_logger(__name__).warning(
            "could_not_load_evaluation_workflows", error=str(e)
        )

    # Register ground truth embedding workflows for tasks_xl queue
    try:
        from tfc.temporal.ground_truth.workflows import (
            GenerateGroundTruthEmbeddingsWorkflow,
        )

        register_for_queues(
            queues=["tasks_xl"],
            workflows=[GenerateGroundTruthEmbeddingsWorkflow],
        )
    except ImportError as e:
        from tfc.logging.temporal import get_logger

        get_logger(__name__).warning(
            "could_not_load_ground_truth_workflows", error=str(e)
        )

    # Register Imagine analysis workflows for tasks_xl queue
    try:
        from tfc.temporal.imagine.activities import (
            fetch_trace_data,
            run_llm_analysis,
            save_analysis_result,
        )
        from tfc.temporal.imagine.workflows import ImagineAnalysisWorkflow

        register_for_queues(
            queues=["tasks_xl"],
            workflows=[ImagineAnalysisWorkflow],
            activities=[fetch_trace_data, run_llm_analysis, save_analysis_result],
        )
    except ImportError as e:
        from tfc.logging.temporal import get_logger

        get_logger(__name__).warning("could_not_load_imagine_workflows", error=str(e))

    # Register drop-in TaskRunnerWorkflow for all queues
    try:
        from tfc.temporal.drop_in import TaskRunnerWorkflow

        register_for_queues(
            queues=[
                "default",
                "tasks_s",
                "tasks_l",
                "tasks_xl",
                "trace_ingestion",
                "agent_compass",
            ],
            workflows=[TaskRunnerWorkflow],
        )
    except ImportError as e:
        from tfc.logging.temporal import get_logger

        get_logger(__name__).warning("could_not_load_dropin_workflow", error=str(e))

    # Register simulate workflows for tasks_xl queue
    # Note: Test execution workflows have been removed - using Celery tasks instead
    try:
        from tfc.temporal.simulate.workflows import (
            AddScenarioColumnsWorkflow,
            AddScenarioRowsWorkflow,
            CreateDatasetScenarioWorkflow,
            CreateGraphScenarioWorkflow,
            CreateScriptScenarioWorkflow,
            ScenarioGenerationWorkflow,
        )

        register_for_queues(
            queues=["tasks_xl"],
            workflows=[
                ScenarioGenerationWorkflow,
                AddScenarioRowsWorkflow,
                AddScenarioColumnsWorkflow,
                CreateDatasetScenarioWorkflow,
                CreateScriptScenarioWorkflow,
                CreateGraphScenarioWorkflow,
            ],
        )
    except ImportError as e:
        from tfc.logging.temporal import get_logger

        get_logger(__name__).warning("could_not_load_simulate_workflows", error=str(e))

    # Register agent_playground graph execution workflow for tasks_l queue
    try:
        from tfc.temporal.agent_playground import get_workflows as get_ap_workflows

        register_for_queues(
            queues=["tasks_l"],
            workflows=get_ap_workflows(),
        )
    except ImportError as e:
        from tfc.logging.temporal import get_logger

        get_logger(__name__).warning(
            "could_not_load_agent_playground_workflows", error=str(e)
        )

    # Register call execution workflows for tasks_l queue
    # TestExecutionWorkflow: Parent orchestrator for test executions
    # CallExecutionWorkflow: Individual call lifecycle (outbound/inbound)
    # CallDispatcherWorkflow: Singleton rate limiter for call slots
    # RerunCoordinatorWorkflow: Parent orchestrator for call execution reruns
    try:
        from ee.voice.temporal.workflows.call_dispatcher_workflow import (
            CallDispatcherWorkflow,
        )
        from ee.voice.temporal.workflows.call_execution_workflow import (
            CallExecutionWorkflow,
        )
        from ee.voice.temporal.workflows.phone_number_dispatcher_workflow import (
            PhoneNumberDispatcherWorkflow,
        )
        from simulate.temporal.workflows.rerun_coordinator_workflow import (
            RerunCoordinatorWorkflow,
        )
        from simulate.temporal.workflows.test_execution_workflow import (
            TestExecutionWorkflow,
        )

        register_for_queues(
            queues=["tasks_l"],
            workflows=[
                TestExecutionWorkflow,
                CallExecutionWorkflow,
                CallDispatcherWorkflow,
                PhoneNumberDispatcherWorkflow,
                RerunCoordinatorWorkflow,
            ],
        )
    except ImportError as e:
        from tfc.logging.temporal import get_logger

        get_logger(__name__).warning(
            "could_not_load_call_execution_workflows", error=str(e)
        )

    # Register billing/usage workflows for default queue
    # UsageConsumerWorkflow (long-running singleton) + MonthlyResetWorkflow
    try:
        try:
            from ee.usage.temporal import get_workflows as get_billing_workflows
        except ImportError:
            get_billing_workflows = None

        if get_billing_workflows is not None:
            register_for_queues(
                queues=["default"],
                workflows=get_billing_workflows(),
            )
    except ImportError as e:
        from tfc.logging.temporal import get_logger

        get_logger(__name__).warning("could_not_load_billing_workflows", error=str(e))

    try:
        from tfc.temporal.billing.workflows import MonthlyClosingWorkflow

        register_for_queues(
            queues=["tasks_s"],
            workflows=[MonthlyClosingWorkflow],
        )
    except ImportError as e:
        from tfc.logging.temporal import get_logger

        get_logger(__name__).warning(
            "could_not_load_monthly_closing_workflow", error=str(e)
        )

    _workflows_registered = True


def _ensure_activities_registered() -> None:
    """
    Load activities. DOES import Django.
    Only call AFTER Worker creation (after workflow validation).
    """
    global _activities_registered

    if _activities_registered:
        return

    from tfc.logging.temporal import get_logger

    log = get_logger(__name__)

    try:
        # Import activities (has Django dependencies)
        from tfc.temporal.experiments import get_activities

        activities = get_activities()
        register_for_queues(
            queues=["tasks_l", "tasks_xl"],
            activities=activities,
        )

        # Status check activity also on tasks_s
        from tfc.temporal.experiments.activities import check_experiment_status_activity

        register_for_queues(
            queues=["tasks_s"],
            activities=[check_experiment_status_activity],
        )
    except ImportError as e:
        log.warning("could_not_load_experiment_activities", error=str(e))

    # Register evaluation activities for tasks_s queue
    try:
        from tfc.temporal.evaluations.activities import run_single_evaluation_activity

        register_for_queues(
            queues=["tasks_s", "default"],
            activities=[run_single_evaluation_activity],
        )
        log.info("registered_evaluation_activities", queues=["tasks_s", "default"])
    except ImportError as e:
        log.warning("could_not_load_evaluation_activities", error=str(e))

    # Register ground truth embedding activities for tasks_xl queue
    try:
        from tfc.temporal.ground_truth.activities import (
            generate_ground_truth_embeddings_activity,
        )

        register_for_queues(
            queues=["tasks_xl"],
            activities=[generate_ground_truth_embeddings_activity],
        )
        log.info("registered_ground_truth_embedding_activities", queues=["tasks_xl"])
    except ImportError as e:
        log.warning("could_not_load_ground_truth_activities", error=str(e))

    # Agent prompt optimiser activities (tasks_xl)
    try:
        from tfc.temporal.agent_prompt_optimiser import (
            get_activities as get_apo_activities,
        )

        register_for_queues(
            queues=["tasks_xl"],
            activities=get_apo_activities(),
        )
        log.info("registered_agent_prompt_optimiser_activities")
    except ImportError as e:
        log.warning("could_not_load_agent_prompt_optimiser_activities", error=str(e))

    # Register drop-in activities from @temporal_activity decorators
    try:
        # Import all modules that have @temporal_activity decorators
        # This triggers the decorators to register activities in _ACTIVITY_REGISTRY
        log.info("importing_temporal_activity_modules")
        _import_temporal_activity_modules()

        # Check what's in the registry after importing
        from tfc.temporal.drop_in.decorator import _ACTIVITY_REGISTRY

        log.info(
            "activity_registry_loaded",
            count=len(_ACTIVITY_REGISTRY),
            sample=list(_ACTIVITY_REGISTRY.keys())[:10],
        )

        # Now get all the registered activities
        from tfc.temporal.drop_in.decorator import get_temporal_activities

        drop_in_activities = get_temporal_activities()
        log.info("registering_dropin_activities", count=len(drop_in_activities))

        # Register for all queues (activities specify their own queue in metadata)
        register_for_queues(
            queues=[
                "default",
                "tasks_s",
                "tasks_l",
                "tasks_xl",
                "agent_compass",
                "trace_ingestion",
            ],
            activities=drop_in_activities,
        )
    except Exception as e:
        log.exception("could_not_load_dropin_activities", error=str(e))

    # Register agent_playground graph execution activities for tasks_l queue
    try:
        from tfc.temporal.agent_playground import get_activities as get_ap_activities

        register_for_queues(
            queues=["tasks_l"],
            activities=get_ap_activities(),
        )
        log.info("registered_agent_playground_activities")
    except ImportError as e:
        log.warning("could_not_load_agent_playground_activities", error=str(e))

    # Register simulate activities for tasks_xl, tasks_l, and tasks_s queues
    # (tasks_s needed for scheduled cleanup workflows)
    try:
        from tfc.temporal.simulate.activities import (
            ALL_ACTIVITIES as SIMULATE_ACTIVITIES,
        )

        register_for_queues(
            queues=["tasks_xl", "tasks_l", "tasks_s"],
            activities=SIMULATE_ACTIVITIES,
        )
        log.info("registered_simulate_activities", count=len(SIMULATE_ACTIVITIES))
    except ImportError as e:
        log.warning("could_not_load_simulate_activities", error=str(e))

    # Register call execution activities
    # Small queue (tasks_s): Fast operations - phone acquisition, status updates, signals
    # Large queue (tasks_l): Provider interactions, monitoring, persistence
    # XL queue (tasks_xl): Long-running evaluations, client data fetch
    # Non-voice small activities (always available)
    try:
        from simulate.temporal.activities.small import (
            check_call_balance,
            persist_processing_skip_state,
            release_call_slot,
            report_workflow_error,
            request_call_slot,
            signal_call_analyzing,
            signal_call_completed,
            signal_slots_granted_batch,
            update_call_status,
        )

        register_for_queues(
            queues=["tasks_s"],
            activities=[
                update_call_status,
                persist_processing_skip_state,
                check_call_balance,
                signal_slots_granted_batch,
                request_call_slot,
                report_workflow_error,
            ],
        )
        register_for_queues(
            queues=["tasks_l"],
            activities=[
                release_call_slot,
                signal_call_analyzing,
                signal_call_completed,
            ],
        )
        log.info("registered_call_execution_small_activities", count=9)
    except ImportError as e:
        log.warning("could_not_load_call_execution_small_activities", error=str(e))

    # Voice small activities (Enterprise Edition)
    try:
        from ee.voice.temporal.activities.voice_small import (
            acquire_and_signal_phone_numbers_batch,
            acquire_phone_number,
            prepare_call,
            release_phone_number,
            release_phone_number_slot,
            request_phone_number_slot,
            sync_available_phone_numbers,
        )

        register_for_queues(
            queues=["tasks_s"],
            activities=[
                acquire_phone_number,
                prepare_call,
                request_phone_number_slot,
                acquire_and_signal_phone_numbers_batch,
                sync_available_phone_numbers,
            ],
        )
        register_for_queues(
            queues=["tasks_l"],
            activities=[
                release_phone_number,
                release_phone_number_slot,
            ],
        )
        log.info("registered_voice_small_activities", count=7)
    except ImportError as e:
        log.warning("could_not_load_voice_small_activities", error=str(e))

    try:
        from ee.voice.temporal.activities.voice_large import (
            calculate_conversation_metrics,
            deduct_call_cost,
            fetch_and_persist_call_result,
            initiate_call,
            monitor_call_until_complete,
        )

        register_for_queues(
            queues=["tasks_l"],
            activities=[
                initiate_call,
                monitor_call_until_complete,
                fetch_and_persist_call_result,
                deduct_call_cost,
                calculate_conversation_metrics,
            ],
        )
        log.info("registered_call_execution_large_activities", count=5)
    except ImportError as e:
        log.warning("could_not_load_call_execution_large_activities", error=str(e))

    # Non-voice XL activities (always available)
    try:
        from simulate.temporal.activities.xl import (
            run_simulate_evaluations,
            run_tool_call_evaluation,
        )

        register_for_queues(
            queues=["tasks_xl"],
            activities=[
                run_simulate_evaluations,
                run_tool_call_evaluation,
            ],
        )
        log.info("registered_call_execution_xl_activities", count=2)
    except ImportError as e:
        log.warning("could_not_load_call_execution_xl_activities", error=str(e))

    # Voice XL activities (Enterprise Edition)
    try:
        from ee.voice.temporal.activities.voice_xl import (
            calculate_voice_csat_score,
            fetch_client_call_data,
        )

        register_for_queues(
            queues=["tasks_xl"],
            activities=[
                fetch_client_call_data,
                calculate_voice_csat_score,
            ],
        )
        log.info("registered_voice_xl_activities", count=2)
    except ImportError as e:
        log.warning("could_not_load_voice_xl_activities", error=str(e))

    # Register bridge activities
    try:
        from ee.voice.temporal.activities.bridge import run_bridge

        register_for_queues(
            queues=["tasks_l"],
            activities=[run_bridge],
        )
        log.info("registered_bridge_activities")
    except ImportError as e:
        log.warning("could_not_load_bridge_activities", error=str(e))

    # Register test execution activities
    try:
        from simulate.temporal.activities.test_execution import (
            cancel_pending_calls,
            create_call_execution_records,
            finalize_test_execution,
            get_unlaunched_call_ids,
            setup_test_execution,
            update_test_execution_counts,
        )

        register_for_queues(
            queues=["tasks_l"],
            activities=[
                setup_test_execution,
                create_call_execution_records,
                get_unlaunched_call_ids,
                finalize_test_execution,
                cancel_pending_calls,
            ],
        )
        register_for_queues(
            queues=["tasks_s"],
            activities=[
                update_test_execution_counts,
            ],
        )
        log.info("registered_test_execution_activities", count=6)
    except ImportError as e:
        log.warning("could_not_load_test_execution_activities", error=str(e))

    # Register rerun activities for RerunCoordinatorWorkflow
    try:
        from simulate.temporal.activities.rerun import (
            cancel_rerun_calls,
            finalize_rerun_execution,
        )

        register_for_queues(
            queues=["tasks_l"],
            activities=[
                finalize_rerun_execution,
                cancel_rerun_calls,
            ],
        )
        log.info("registered_rerun_activities", count=2)
    except ImportError as e:
        log.warning("could_not_load_rerun_activities", error=str(e))

    # Register dataset optimization activities for tasks_xl queue
    try:
        from tfc.temporal.dataset_optimization.activities import (
            ALL_ACTIVITIES as DATASET_OPTIMIZATION_ACTIVITIES,
        )

        register_for_queues(
            queues=["tasks_xl"],  # Dataset optimization uses tasks_xl queue
            activities=DATASET_OPTIMIZATION_ACTIVITIES,
        )
        log.info(
            "registered_dataset_optimization_activities",
            count=len(DATASET_OPTIMIZATION_ACTIVITIES),
        )
    except ImportError as e:
        log.warning("could_not_load_dataset_optimization_activities", error=str(e))

    # Register billing activities (Stripe usage reporting, dunning)
    try:
        from tfc.temporal.billing import get_activities as get_billing_activities

        billing_activities = get_billing_activities()
        register_for_queues(
            queues=["default", "tasks_l", "tasks_s"],
            activities=billing_activities,
        )
        log.info("registered_billing_activities", count=len(billing_activities))
    except ImportError as e:
        log.warning("could_not_load_billing_activities", error=str(e))

    # Register usage metering activities (consumer, sync, monthly reset)
    try:
        try:
            from ee.usage.temporal import get_activities as get_usage_activities
        except ImportError:
            get_usage_activities = None

        if get_usage_activities is not None:
            usage_activities = get_usage_activities()
            register_for_queues(
                queues=["default"],
                activities=usage_activities,
            )
            log.info("registered_usage_metering_activities", count=len(usage_activities))
    except ImportError as e:
        log.warning("could_not_load_usage_metering_activities", error=str(e))

    _activities_registered = True


def _import_temporal_activity_modules() -> None:
    """
    Import all modules that contain @temporal_activity decorated functions.
    This ensures they're registered in the decorator's _ACTIVITY_REGISTRY.
    """
    from tfc.logging.temporal import get_logger

    log = get_logger(__name__)

    for module_name in TEMPORAL_ACTIVITY_MODULES:
        try:
            __import__(module_name)
            log.debug("module_imported", module=module_name)
        except ImportError as e:
            log.debug("module_import_failed", module=module_name, error=str(e))
        except Exception as e:
            log.warning("module_import_error", module=module_name, error=str(e))


# =============================================================================
# Retrieval Functions (separate for workflows and activities)
# =============================================================================


def get_workflows_for_queue(queue: str) -> List[Type]:
    """
    Get workflow classes for a queue.
    Does NOT import Django - safe to call before Worker creation.
    """
    _ensure_workflows_registered()
    return _workflow_registry.get(queue, [])


def get_activities_for_queue(queue: str) -> List[Callable]:
    """
    Get activity functions for a queue.
    DOES import Django - only call when setting up Worker activities.
    """
    _ensure_activities_registered()
    return _activity_registry.get(queue, [])


def get_all_queues() -> List[str]:
    """Get all queues that have registered workflows or activities."""
    _ensure_workflows_registered()
    _ensure_activities_registered()
    return list(set(list(_workflow_registry.keys()) + list(_activity_registry.keys())))


def get_all_workflows() -> List[Type]:
    """Get all unique workflow classes across all queues."""
    _ensure_workflows_registered()
    all_workflows = []
    seen = set()
    for workflows in _workflow_registry.values():
        for w in workflows:
            if w not in seen:
                all_workflows.append(w)
                seen.add(w)
    return all_workflows


def get_all_activities() -> List[Callable]:
    """Get all unique activity functions across all queues."""
    _ensure_activities_registered()
    all_activities = []
    seen = set()
    for activities in _activity_registry.values():
        for a in activities:
            if a not in seen:
                all_activities.append(a)
                seen.add(a)
    return all_activities


def get_registry_info() -> dict:
    """Get debug info about the registry."""
    _ensure_workflows_registered()
    _ensure_activities_registered()
    return {
        "workflows": {
            queue: [w.__name__ for w in workflows]
            for queue, workflows in _workflow_registry.items()
        },
        "activities": {
            queue: [a.__name__ for a in activities]
            for queue, activities in _activity_registry.items()
        },
    }


__all__ = [
    "register_workflows",
    "register_activities",
    "register_for_queues",
    "get_workflows_for_queue",
    "get_activities_for_queue",
    "get_all_queues",
    "get_all_workflows",
    "get_all_activities",
    "get_registry_info",
]
