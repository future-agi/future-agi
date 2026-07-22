import os
import uuid
from datetime import datetime, timedelta
from typing import Any

import structlog
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from simulate.models import (  # SimulatorAgent
    AgentDefinition,
    CallExecution,
    RunTest,
    TestExecution,
)
from simulate.models.chat_message import ChatMessageModel
from simulate.models.test_execution import EvalExplanationSummaryStatus
from simulate.pydantic_schemas.chat import (
    ChatMessage,
    ChatRole,
)
from simulate.services.test_executor import (
    TestExecutor,
    _run_simulate_evaluations_task,
)
from simulate.utils.chat_simulation import (
    _aggregate_chat_metrics,
    _calculate_tokens_from_messages,
)
from simulate.utils.websocket_notifications import notify_simulation_update
from tfc.temporal.drop_in import temporal_activity

logger = structlog.get_logger(__name__)


@temporal_activity(
    time_limit=1800,
    queue="tasks_l",
)
def store_chat_messages(
    call_execution_id: str,
    organization_id: str,
    workspace_id: str,
    input_messages: list[ChatMessage | dict],
    output_messages: list[ChatMessage | dict],
    chat_ended: bool,
    chat_session_id: str,
    create_timestamp: datetime,
    metrics: dict[str, float | int | None] | None = None,
):
    try:
        organization = Organization.objects.get(id=organization_id)
        workspace = Workspace.objects.get(id=workspace_id)
        call_execution = CallExecution.objects.get(id=call_execution_id)

        # Convert dicts to ChatMessage objects if needed (Temporal serialization)
        input_messages = [
            ChatMessage(**msg) if isinstance(msg, dict) else msg
            for msg in input_messages
        ]
        output_messages = [
            ChatMessage(**msg) if isinstance(msg, dict) else msg
            for msg in output_messages
        ]

        chats_messages = []
        # Extract content from messages regardless of role.
        # The role filtering was incorrect because:
        # - input_messages (from agent via SDK) come as role="user" (SDK converts assistant->user)
        # - output_messages (from simulator) are role="user" (simulator is the customer)
        # So we extract ALL content, not filtering by role.
        input_messages_content: list[str] = [
            (msg.content if hasattr(msg, "content") else msg.get("content", ""))
            for msg in input_messages
            if (msg.content if hasattr(msg, "content") else msg.get("content"))
        ]
        output_messages_content: list[str] = [
            (msg.content if hasattr(msg, "content") else msg.get("content", ""))
            for msg in output_messages
            if (msg.content if hasattr(msg, "content") else msg.get("content"))
        ]

        input_messages_dict = [
            message.model_dump(exclude_none=True) for message in input_messages
        ]
        output_messages_dict = [
            message.model_dump(exclude_none=True) for message in output_messages
        ]

        # Log metrics for debugging
        logger.debug(
            "store_chat_messages_metrics",
            call_execution_id=call_execution_id,
            has_metrics=metrics is not None,
            metrics=metrics,
        )

        # Calculate tokens for input messages (user)
        # Use metrics from LLM provider if available (more accurate), otherwise estimate
        if (
            metrics
            and isinstance(metrics, dict)
            and metrics.get("input_tokens") is not None
        ):
            input_tokens = int(metrics["input_tokens"])
            logger.debug(
                "using_llm_input_tokens",
                call_execution_id=call_execution_id,
                input_tokens=input_tokens,
            )
        else:
            input_tokens = _calculate_tokens_from_messages(
                input_messages_content, input_messages_dict
            )
            logger.debug(
                "estimated_input_tokens",
                call_execution_id=call_execution_id,
                input_tokens=input_tokens,
            )

        # Calculate tokens for output messages (assistant)
        # Use metrics from LLM provider if available (more accurate), otherwise estimate
        if (
            metrics
            and isinstance(metrics, dict)
            and metrics.get("output_tokens") is not None
        ):
            output_tokens = int(metrics["output_tokens"])
            logger.debug(
                "using_llm_output_tokens",
                call_execution_id=call_execution_id,
                output_tokens=output_tokens,
            )
        else:
            output_tokens = _calculate_tokens_from_messages(
                output_messages_content, output_messages_dict
            )
            logger.debug(
                "estimated_output_tokens",
                call_execution_id=call_execution_id,
                output_tokens=output_tokens,
            )

        # Extract latency from SDK metrics (if provided)
        latency_ms = None
        if metrics and isinstance(metrics, dict):
            if metrics.get("latency") is not None:
                latency_ms = int(metrics["latency"])
            elif metrics.get("latency_ms") is not None:
                latency_ms = int(metrics["latency_ms"])

        # IMPORTANT (dashboard convention):
        # - Simulator messages are stored as role="user"
        # - Agent-under-test messages are stored as role="assistant"
        input_chat_message_model = ChatMessageModel(
            id=uuid.uuid4(),
            role=ChatRole.ASSISTANT,
            call_execution=call_execution,
            messages=input_messages_content,
            content=input_messages_dict,
            session_id=chat_session_id,
            created_at=create_timestamp,
            organization=organization,
            workspace=workspace,
            tokens=input_tokens,
            latency_ms=latency_ms,  # SDK latency measures agent response latency
        )

        output_chat_message_model = ChatMessageModel(
            id=uuid.uuid4(),
            role=ChatRole.USER,
            call_execution=call_execution,
            messages=output_messages_content,
            content=output_messages_dict,
            session_id=chat_session_id,
            created_at=create_timestamp,
            organization=organization,
            workspace=workspace,
            tokens=output_tokens,
            latency_ms=None,
        )

        chats_messages.append(input_chat_message_model)
        chats_messages.append(output_chat_message_model)

        ChatMessageModel.objects.bulk_create(chats_messages)

        # Notify frontend so the simulation runs grid refreshes
        notify_simulation_update(
            organization_id=organization_id,
            run_test_id=str(call_execution.test_execution.run_test_id),
            test_execution_id=str(call_execution.test_execution_id),
        )

        if chat_ended:
            # Aggregate metrics from all chat messages for this call execution
            _aggregate_chat_metrics(call_execution)

            # cut cost for chat sim
            TestExecutor._deduct_call_cost(call_execution)
            logger.info(f"Successfully deducted cost for chat call {call_execution.id}")

            # Update CallExecution status to COMPLETED
            call_execution.status = CallExecution.CallStatus.COMPLETED
            call_execution.ended_at = timezone.now()
            if call_execution.started_at:
                delta = call_execution.ended_at - call_execution.started_at
                call_execution.duration_seconds = int(delta.total_seconds())
            else:
                logger.warning(
                    "chat_call_missing_started_at",
                    call_execution_id=str(call_execution.id),
                )

            # run evals for this call exec
            call_metadata = call_execution.call_metadata or {}
            should_start_evals = False

            if not call_metadata.get("eval_started"):
                call_metadata["eval_started"] = True
                call_execution.call_metadata = call_metadata
                should_start_evals = True

            call_execution.save(
                update_fields=[
                    "status",
                    "ended_at",
                    "duration_seconds",
                    "call_metadata",
                    "conversation_metrics_data",
                    "overall_score",
                ]
            )

            if should_start_evals:
                try:
                    _run_simulate_evaluations_task.apply_async(
                        args=(str(call_execution.id),)
                    )
                    logger.info(
                        f"Started evaluations for call after completion {call_execution.id}"
                    )
                except Exception as dispatch_error:
                    call_metadata = call_execution.call_metadata or {}
                    call_metadata["eval_started"] = False
                    call_metadata["eval_dispatch_failed"] = str(dispatch_error)
                    call_execution.call_metadata = call_metadata
                    call_execution.save(update_fields=["call_metadata"])
                    logger.exception(
                        "chat_eval_dispatch_failed",
                        call_execution_id=str(call_execution.id),
                        error=str(dispatch_error),
                    )

            logger.info(f"CallExecution {call_execution.id} marked as COMPLETED")

            # Trigger test execution monitoring to update TestExecution status
            try:
                monitor_test_execution_for_chat.apply_async(
                    args=(str(call_execution.test_execution_id),)
                )
            except Exception as dispatch_error:
                monitor_original = getattr(
                    monitor_test_execution_for_chat, "_original_func", None
                )
                if callable(monitor_original):
                    try:
                        monitor_original(str(call_execution.test_execution_id))
                    except Exception as monitor_error:
                        test_execution = call_execution.test_execution
                        test_execution.execution_metadata = (
                            test_execution.execution_metadata or {}
                        )
                        test_execution.execution_metadata[
                            "chat_monitor_dispatch_failed"
                        ] = str(monitor_error)
                        test_execution.picked_up_by_executor = False
                        test_execution.save(
                            update_fields=[
                                "execution_metadata",
                                "picked_up_by_executor",
                            ]
                        )
                        logger.exception(
                            "chat_monitor_sync_fallback_failed",
                            test_execution_id=str(call_execution.test_execution_id),
                            error=str(monitor_error),
                        )
                else:
                    test_execution = call_execution.test_execution
                    test_execution.execution_metadata = (
                        test_execution.execution_metadata or {}
                    )
                    test_execution.execution_metadata[
                        "chat_monitor_dispatch_failed"
                    ] = str(dispatch_error)
                    test_execution.picked_up_by_executor = False
                    test_execution.save(
                        update_fields=["execution_metadata", "picked_up_by_executor"]
                    )
                logger.exception(
                    "chat_monitor_dispatch_failed_falling_back_sync",
                    test_execution_id=str(call_execution.test_execution_id),
                    error=str(dispatch_error),
                )

        return True
    except Exception as e:
        logger.exception(f"Error storing chat messages: {str(e)}")
        return None


@temporal_activity(
    time_limit=3600,
    queue="tasks_s",
)
def monitor_chat_test_executions():
    try:
        # Get all active test executions
        # Filter by the agent type actually used for this execution (AgentVersion snapshot),
        # not the current AgentDefinition value (which may change over time).
        agent_type_text = AgentDefinition.AgentTypeChoices.TEXT
        active_test_executions = (
            TestExecution.objects.filter(
                status__in=[
                    TestExecution.ExecutionStatus.PENDING,
                    TestExecution.ExecutionStatus.RUNNING,
                    TestExecution.ExecutionStatus.EVALUATING,
                ],
                picked_up_by_executor=False,
            )
            .filter(
                Q(agent_version__configuration_snapshot__agent_type=agent_type_text)
                | Q(agent_version__configuration_snapshot__agentType=agent_type_text)
                # Backward-compat fallback for older executions without agent_version populated.
                | Q(
                    agent_version__isnull=True,
                    agent_definition__agent_type=agent_type_text,
                )
                # Include prompt-based simulations (source_type="prompt")
                | Q(run_test__source_type=RunTest.SourceTypes.PROMPT)
            )
            .order_by("created_at")
        )

        # take 10 at a time
        current_batch = active_test_executions[:10]

        for test_execution in current_batch:
            monitor_test_execution_for_chat.apply_async(args=(str(test_execution.id),))

        return
    except Exception as e:
        logger.exception(f"Error monitoring chat call executions: {str(e)}")
        return


@temporal_activity(
    time_limit=1800,
    queue="tasks_s",
)
def monitor_test_execution_for_chat(test_execution_id: str):
    try:
        test_execution = TestExecution.objects.get(id=test_execution_id)

        call_executions = CallExecution.objects.filter(
            test_execution=test_execution
        ).exclude(status=CallExecution.CallStatus.CANCELLED)

        if (
            not call_executions.exists()
            and test_execution.status != TestExecution.ExecutionStatus.CANCELLED
        ):
            test_execution.status = TestExecution.ExecutionStatus.COMPLETED
            test_execution.save()

            logger.info(f"Test execution {test_execution.id} marked as completed")

            test_execution.eval_explanation_summary_status = (
                EvalExplanationSummaryStatus.PENDING
            )
            test_execution.save(update_fields=["eval_explanation_summary_status"])
            # Lazy import to avoid circular dependency
            from simulate.tasks.eval_summary_tasks import run_eval_summary_task

            run_eval_summary_task.apply_async(args=(str(test_execution.id),))

            return

        is_evaluating = False
        all_terminal = True
        has_any_completed = False
        has_any_failed = False

        for call_execution in call_executions:
            if call_execution.status not in [
                CallExecution.CallStatus.COMPLETED,
                CallExecution.CallStatus.FAILED,
                CallExecution.CallStatus.CANCELLED,
            ]:
                all_terminal = False

            call_metadata = call_execution.call_metadata or {}

            if call_execution.status == CallExecution.CallStatus.FAILED:
                has_any_failed = True

            if call_execution.status == CallExecution.CallStatus.COMPLETED:
                has_any_completed = True

                eval_started = call_metadata.get("eval_started")
                eval_completed = call_metadata.get("eval_completed")

                if eval_started and not eval_completed:
                    is_evaluating = True
                    all_terminal = False

                if not eval_started:
                    # Evals haven't started yet for this completed call
                    all_terminal = False

        status_changed = False

        if is_evaluating and not all_terminal:
            test_execution.status = TestExecution.ExecutionStatus.EVALUATING
            test_execution.save(update_fields=["status"])
            status_changed = True

        # All calls reached a terminal state but none completed successfully
        elif all_terminal and not has_any_completed and has_any_failed:
            test_execution.status = TestExecution.ExecutionStatus.FAILED
            test_execution.save(update_fields=["status"])
            status_changed = True
            logger.info(
                f"Test execution {test_execution.id} marked as FAILED (all calls failed)"
            )

        elif all_terminal and has_any_completed:
            test_execution.status = TestExecution.ExecutionStatus.COMPLETED
            test_execution.eval_explanation_summary_status = (
                EvalExplanationSummaryStatus.PENDING
            )
            test_execution.save(
                update_fields=["status", "eval_explanation_summary_status"]
            )
            status_changed = True

            # Lazy import to avoid circular dependency
            from simulate.tasks.eval_summary_tasks import run_eval_summary_task

            run_eval_summary_task.apply_async(args=(str(test_execution.id),))

        if status_changed:
            notify_simulation_update(
                organization_id=test_execution.run_test.organization_id,
                run_test_id=str(test_execution.run_test_id),
                test_execution_id=str(test_execution.id),
            )
        else:
            logger.info(
                f"Test execution {test_execution.id} set to {test_execution.status} status"
            )

    except Exception as e:
        logger.exception(f"Error monitoring test execution for chat: {str(e)}")
        return None


@temporal_activity(
    time_limit=1800,
    queue="tasks_s",
)
def monitor_chat_timeout_call_executions():
    try:
        now = timezone.now()
        cutoff = now - timedelta(minutes=30)

        with transaction.atomic():
            # lock rows to avoid races if multiple workers run this
            ids = list[Any](
                CallExecution.objects.select_for_update(skip_locked=True)
                .filter(
                    simulation_call_type=CallExecution.SimulationCallType.TEXT,
                    completed_at__isnull=True,
                    created_at__lte=cutoff,
                )
                .exclude(
                    status__in=[
                        CallExecution.CallStatus.COMPLETED,
                        CallExecution.CallStatus.FAILED,
                        CallExecution.CallStatus.CANCELLED,
                    ]
                )
                .order_by("created_at", "id")
                .values_list("id", flat=True)[:50]
            )

            CallExecution.objects.filter(id__in=ids).update(
                status=CallExecution.CallStatus.FAILED,
                completed_at=now,
                updated_at=now,  # because .update() won't auto-update updated_at
            )

        return
    except Exception as e:
        logger.exception(f"Error monitoring chat call executions: {str(e)}")
        return


@temporal_activity(
    time_limit=3600,
    queue="tasks_s",
)
def process_prompt_based_chat_simulations():
    """
    Process REGISTERED CallExecutions for prompt-based simulations.

    This activity picks up CallExecutions that:
    - Have status REGISTERED
    - Are TEXT type simulations
    - Belong to prompt-based RunTests (source_type="prompt")

    For each CallExecution, it initiates the chat and runs the full conversation.
    """
    # Lazy import: simulate.services.chat_sim imports from this module (circular)

    try:
        # Per-organisation concurrency limit.  Each org uses its own LLM API
        # keys, so there is no need for an application-wide cap.
        MAX_PER_ORG = int(os.getenv("PROMPT_SIM_MAX_CONCURRENT_PER_ORG", "10"))

        # Atomically claim REGISTERED CallExecutions to prevent duplicates on retry
        with transaction.atomic():
            registered = list(
                CallExecution.objects.select_for_update(skip_locked=True)
                .filter(
                    status=CallExecution.CallStatus.REGISTERED,
                    simulation_call_type=CallExecution.SimulationCallType.TEXT,
                    test_execution__run_test__source_type=RunTest.SourceTypes.PROMPT,
                )
                .select_related("test_execution__run_test")
                .order_by("created_at")[:50]
            )

            if not registered:
                return 0

            # Per-org ongoing counts (single query)
            org_ids = {ce.test_execution.run_test.organization_id for ce in registered}
            org_ongoing = dict(
                CallExecution.objects.filter(
                    status=CallExecution.CallStatus.ONGOING,
                    simulation_call_type=CallExecution.SimulationCallType.TEXT,
                    test_execution__run_test__source_type=RunTest.SourceTypes.PROMPT,
                    test_execution__run_test__organization_id__in=org_ids,
                )
                .values_list("test_execution__run_test__organization_id")
                .annotate(cnt=Count("id"))
                .values_list("test_execution__run_test__organization_id", "cnt")
            )

            # Pick calls respecting per-org limit
            call_ids = []
            for ce in registered:
                org_id = ce.test_execution.run_test.organization_id
                current = org_ongoing.get(org_id, 0)
                if current < MAX_PER_ORG:
                    call_ids.append(ce.id)
                    org_ongoing[org_id] = current + 1

            if call_ids:
                CallExecution.objects.filter(id__in=call_ids).update(
                    status=CallExecution.CallStatus.ONGOING
                )

        logger.info(
            f"Found {len(call_ids)} REGISTERED prompt-based chat simulations to process"
        )

        for call_id in call_ids:
            run_single_prompt_chat.apply_async(args=(str(call_id),))

        return len(call_ids)
    except Exception as e:
        logger.exception(f"Error processing prompt-based chat simulations: {str(e)}")
        return 0


@temporal_activity(
    time_limit=1800,
    queue="tasks_l",
)
def run_single_prompt_chat(call_execution_id: str):
    """
    Run a single prompt-based chat simulation.

    This activity:
    1. Initiates the chat session
    2. Runs the full conversation loop
    """
    # Lazy import: simulate.services.chat_sim imports from this module (circular)
    from simulate.services.chat_sim import initiate_chat, run_prompt_based_conversation

    try:
        call_execution = CallExecution.objects.select_related(
            "test_execution",
            "test_execution__run_test",
            "test_execution__run_test__organization",
            "scenario",
        ).get(id=call_execution_id)

        # Only process calls claimed by the dispatcher (ONGOING)
        if call_execution.status != CallExecution.CallStatus.ONGOING:
            logger.info(
                f"CallExecution {call_execution_id} not in expected ONGOING status (status={call_execution.status})"
            )
            return False

        organization = call_execution.test_execution.run_test.organization
        workspace = call_execution.test_execution.run_test.workspace

        try:
            from ee.usage.schemas.event_types import BillingEventType
        except ImportError:
            BillingEventType = None
        try:
            from ee.usage.services.metering import check_usage
        except ImportError:
            check_usage = None

        if check_usage is not None and BillingEventType is not None:
            usage_check = check_usage(str(organization.id), BillingEventType.TEXT_CALL)
            if not usage_check.allowed:
                call_execution.status = CallExecution.CallStatus.FAILED
                call_execution.ended_reason = (
                    usage_check.reason or "Usage limit exceeded"
                )
                call_execution.save(update_fields=["status", "ended_reason"])
                return False

        logger.info(
            "prompt_chat_simulation_starting",
            call_execution_id=call_execution_id,
        )

        # Record start time for duration calculation
        call_execution.started_at = timezone.now()
        call_execution.save(update_fields=["started_at"])

        # Step 1: Initiate the chat
        initiate_chat(call_execution, organization, workspace)

        # Step 2: Run the full conversation
        success = run_prompt_based_conversation(
            call_execution=call_execution,
            organization=organization,
            workspace=workspace,
            max_turns=10,
        )

        try:
            call_execution.refresh_from_db()
            from django.db.models import Sum

            from simulate.models import ChatMessageModel

            try:
                from ee.usage.schemas.events import UsageEvent
            except ImportError:
                UsageEvent = None
            try:
                from ee.usage.services.emitter import emit
            except ImportError:
                emit = None

            total_tokens = (
                ChatMessageModel.objects.filter(
                    call_execution=call_execution
                ).aggregate(total=Sum("tokens"))["total"]
                or 0
            )

            emit(
                UsageEvent(
                    org_id=str(organization.id),
                    event_type=BillingEventType.TEXT_CALL,
                    amount=total_tokens,
                    properties={
                        "source": "simulate_prompt_chat",
                        "source_id": str(call_execution.id),
                        "total_tokens": total_tokens,
                    },
                )
            )
        except Exception:
            pass  # Metering failure must not break the action

        logger.info(
            "prompt_chat_simulation_completed",
            call_execution_id=call_execution_id,
            success=success,
        )

        return success

    except Exception as e:
        logger.exception(
            f"Error running prompt-based chat simulation {call_execution_id}: {str(e)}"
        )
        # Mark as failed if not already
        try:
            call_execution = CallExecution.objects.get(id=call_execution_id)
            if call_execution.status not in [
                CallExecution.CallStatus.COMPLETED,
                CallExecution.CallStatus.FAILED,
                CallExecution.CallStatus.CANCELLED,
            ]:
                call_execution.status = CallExecution.CallStatus.FAILED
                call_execution.ended_reason = str(e)
                call_execution.save(update_fields=["status", "ended_reason"])
        except Exception:
            pass
        return False
