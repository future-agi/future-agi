import uuid

import structlog
from django.conf import settings
from django.utils import timezone

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from simulate.models import (  # SimulatorAgent
    CallExecution,
    TestExecution,
)
from simulate.models.chat_message import ChatMessageModel
from simulate.pydantic_schemas.chat import (
    ChatMessage,
    ChatRole,
    SimulationCallType,
)
from simulate.services.chat_initial_message import get_chat_initial_message
from simulate.services.chat_service_manager import ChatServiceManager
from simulate.services.test_executor import (
    TestExecutor,
)
from simulate.services.types.chat import ChatProviderChoices
from simulate.utils.test_execution_utils import generate_simulator_agent_prompt
from simulate.utils.websocket_notifications import notify_simulation_update

logger = structlog.get_logger(__name__)


def _get_chat_service(
    organization: Organization,
    workspace: Workspace | None = None,
    provider: ChatProviderChoices | None = None,
) -> ChatServiceManager:
    """Get configured chat service manager.

    Args:
        organization: Organization for API key resolution.
        workspace: Workspace for API key resolution (optional).
        provider: Override the default provider (optional).

    Returns:
        ChatServiceManager instance configured for the org/workspace.
    """
    return ChatServiceManager(
        provider=provider,
        organization_id=str(organization.id),
        workspace_id=str(workspace.id) if workspace else None,
    )


def _get_session_id_from_metadata(call_execution: CallExecution) -> str:
    """Safely retrieve session ID from call execution metadata.

    Args:
        call_execution: CallExecution instance

    Returns:
        Session ID string

    Raises:
        ValueError: If no session ID is found in metadata
    """
    metadata = call_execution.call_metadata or {}

    # Try new key first, fallback to legacy VAPI key for backward compatibility
    session_id = metadata.get("chat_session_id") or metadata.get("vapi_chat_session_id")

    # Validate we got something
    if not session_id:
        available_keys = list(metadata.keys())
        logger.error(
            "session_id_not_found",
            call_execution_id=str(call_execution.id),
            available_metadata_keys=available_keys,
        )
        raise ValueError(
            f"Chat session ID not found in call execution {call_execution.id}. "
            f"Available metadata keys: {available_keys}. "
            "The chat may not have been initialized properly. "
            "Please ensure initiate_chat was called before sending messages."
        )

    # Validate it's actually a non-empty string
    if not isinstance(session_id, str) or len(session_id) == 0:
        logger.error(
            "invalid_session_id",
            call_execution_id=str(call_execution.id),
            session_id=session_id,
            session_id_type=type(session_id).__name__,
        )
        raise ValueError(
            f"Invalid session ID in call execution {call_execution.id}: "
            f"{session_id} (type: {type(session_id).__name__}). "
            "Expected a non-empty string."
        )

    return session_id


def initiate_chat(
    call_execution: CallExecution, organization: Organization, workspace: Workspace
) -> list[ChatMessage] | None:
    """
    Initiate a chat
    """
    try:
        # Step 1 : Check if balance is there to initiate the chat
        test_executor = TestExecutor()
        (
            has_sufficient_balance,
            current_balance,
            estimated_cost,
            balance_error,
        ) = test_executor._check_call_balance(
            organization=organization, call_type=SimulationCallType.TEXT
        )

        if not has_sufficient_balance:
            call_execution.status = CallExecution.CallStatus.FAILED
            call_execution.ended_reason = balance_error
            call_execution.ended_at = timezone.now()
            call_execution.save(update_fields=["status", "ended_reason", "ended_at"])

            raise ValueError(
                f"Insufficient balance for call {call_execution.id}: {balance_error}"
            )

        # Step 2: Create simulation assistant using ChatServiceManager
        chat_service = _get_chat_service(organization, workspace)

        # Get system prompt from CallExecution metadata (already enhanced with row data if applicable)
        system_prompt = (
            call_execution.call_metadata.get("system_prompt")
            or call_execution.call_metadata.get("dynamic_prompt")
            or call_execution.call_metadata.get("base_prompt")
        )

        if not system_prompt:
            # Fallback: Resolve SimulatorAgent and get prompt
            scenario = call_execution.scenario
            run_test = call_execution.test_execution.run_test

            simulator_agent = (
                scenario.simulator_agent
                if scenario.simulator_agent
                else run_test.simulator_agent
            )

            if simulator_agent:
                system_prompt = simulator_agent.prompt
            else:
                # Last resort: Generate from agent definition or use generic prompt
                agent_definition = (
                    scenario.agent_definition or run_test.agent_definition
                )
                # generate_simulator_agent_prompt now handles None agent_definition
                # by returning a generic prompt for prompt-based simulations
                system_prompt = generate_simulator_agent_prompt(
                    agent_definition=agent_definition
                )

        # Resolve the simulated customer's first message (LLM → provided initial_message → default).
        initial_message, initial_message_source = get_chat_initial_message(
            call_execution
        )

        # Create assistant using ChatServiceManager
        assistant_result = chat_service.create_assistant(
            name=f"Sim Chat Customer - {(call_execution.scenario.name or '')[:10]} - {str(call_execution.id)}",
            system_prompt=system_prompt,  # Use enhanced prompt from CallExecution
            voice_settings={
                "initial_message": initial_message,
            },
        )

        if not assistant_result.success:
            raise Exception(
                f"Failed to create simulation assistant: {assistant_result.error}"
            )

        simulation_assistant_id = assistant_result.assistant_id

        # Step 3: Create a chat session

        if not initial_message or (
            isinstance(initial_message, str) and len(initial_message) == 0
        ):
            initial_message = "Hi!"

        # Create session using ChatServiceManager
        session_result = chat_service.create_session(
            assistant_id=simulation_assistant_id,
            name=f"Sim Chat Customer - {(call_execution.scenario.name or '')[:10]}",
            initial_message=ChatMessage(
                role=ChatRole.ASSISTANT, content=initial_message
            ),
        )

        if not session_result.success:
            raise Exception(f"Failed to create chat session: {session_result.error}")

        chat_session_id = session_result.session_id

        # Step 4: Update call execution
        call_execution.assistant_id = simulation_assistant_id
        call_execution.call_metadata["chat_session_id"] = chat_session_id
        call_execution.call_metadata["simulation_assistant_id"] = (
            simulation_assistant_id
        )
        call_execution.call_metadata["chat_provider"] = chat_service.provider.value
        call_execution.status = CallExecution.CallStatus.ONGOING
        call_execution.started_at = timezone.now()
        call_execution.simulation_call_type = "text"  # Set to TEXT for chat simulations
        call_execution.save(
            update_fields=[
                "assistant_id",
                "call_metadata",
                "status",
                "started_at",
                "simulation_call_type",
            ]
        )

        message = ChatMessage(role=ChatRole.USER, content=initial_message)

        # Calculate tokens for the initial message
        initial_msg_text = (
            initial_message
            if isinstance(initial_message, str)
            else str(initial_message)
        )
        initial_tokens = 0
        if initial_msg_text:
            try:
                from ee.agenthub.traceerroragent.token_utils import (
                    estimate_tokens_text,
                )
            except ImportError:
                if settings.DEBUG:
                    logger.warning(
                        "Could not import ee.agenthub.traceerroragent.token_utils",
                        exc_info=True,
                    )
                return None

            initial_tokens = estimate_tokens_text(initial_msg_text)

        ChatMessageModel.objects.create(
            id=uuid.uuid4(),
            role=ChatRole.USER,
            call_execution=call_execution,
            messages=(
                [initial_message]
                if isinstance(initial_message, str)
                else initial_message
            ),
            content=[message.model_dump(exclude_none=True)],
            session_id=chat_session_id,
            created_at=timezone.now(),
            organization=organization,
            workspace=workspace,
            tokens=initial_tokens,
        )

        ##check if test execution is pending
        if (
            call_execution.test_execution.status
            == TestExecution.ExecutionStatus.PENDING
        ):
            call_execution.test_execution.status = TestExecution.ExecutionStatus.RUNNING
            call_execution.test_execution.save(update_fields=["status"])

        # Notify frontend so the simulation runs grid shows the new execution
        notify_simulation_update(
            organization_id=str(organization.id),
            run_test_id=str(call_execution.test_execution.run_test_id),
            test_execution_id=str(call_execution.test_execution_id),
        )

        return [message]

    except ValueError as e:
        call_execution.status = CallExecution.CallStatus.FAILED
        call_execution.ended_reason = str(e)
        call_execution.ended_at = timezone.now()
        call_execution.save(update_fields=["status", "ended_reason", "ended_at"])
        raise ValueError(f"Error initiating chat: {str(e)}") from e

    except Exception as e:
        call_execution.status = CallExecution.CallStatus.FAILED
        call_execution.ended_reason = str(e)
        call_execution.ended_at = timezone.now()
        call_execution.save(update_fields=["status", "ended_reason", "ended_at"])
        logger.exception(f"Error initiating chat: {str(e)}")
        raise Exception(f"Error initiating chat: {str(e)}") from e


def run_prompt_based_conversation(
    call_execution: CallExecution,
    organization: Organization,
    workspace: Workspace,
    max_turns: int = 10,
) -> bool:
    """
    Run a full conversation for prompt-based simulations.

    This function handles the back-and-forth between:
    - The prompt template (agent under test) - uses PromptBasedAgentAdapter
    - The simulator agent (customer) - uses Vapi chat session

    Args:
        call_execution: The CallExecution instance
        organization: The organization
        workspace: The workspace
        max_turns: Maximum number of conversation turns (default 10)

    Returns:
        True if conversation completed successfully, False otherwise
    """
    from simulate.pydantic_schemas.chat import ChatMessage, ChatRole
    from simulate.services.prompt_based_agent_adapter import (
        create_adapter_from_run_test,
    )

    try:
        run_test = call_execution.test_execution.run_test

        # Create the prompt-based agent adapter
        # Get variable values from scenario row data if available
        variable_values = {}
        if call_execution.call_metadata:
            row_data = call_execution.call_metadata.get("row_data", {})
            variable_values.update(row_data)

        adapter = create_adapter_from_run_test(
            run_test,
            organization_id=organization.id,
            workspace_id=workspace.id if workspace else None,
            variable_values=variable_values,
        )

        # Build conversation history from existing messages
        conversation_history = []
        existing_messages = ChatMessageModel.objects.filter(
            call_execution=call_execution
        ).order_by("created_at")

        for msg in existing_messages:
            for content_item in msg.content or []:
                if isinstance(content_item, dict):
                    raw_content = content_item.get("content", "")
                    # Ensure content is a string (handle potential list/dict content)
                    if isinstance(raw_content, list):
                        # Extract text from list content
                        text_parts = []
                        for part in raw_content:
                            if isinstance(part, str):
                                text_parts.append(part)
                            elif isinstance(part, dict) and part.get("type") == "text":
                                text_parts.append(part.get("text", ""))
                        raw_content = "\n".join(text_parts)
                    elif not isinstance(raw_content, str):
                        raw_content = str(raw_content) if raw_content else ""

                    conversation_history.append(
                        {
                            "role": content_item.get("role", "user"),
                            "content": raw_content,
                        }
                    )

        logger.info(
            "prompt_based_conversation_starting",
            call_execution_id=str(call_execution.id),
            max_turns=max_turns,
            initial_history_length=len(conversation_history),
        )

        turn = 0
        while turn < max_turns:
            turn += 1

            # Step 1: Generate response from the prompt template (agent under test)
            logger.info(
                "prompt_based_agent_turn",
                call_execution_id=str(call_execution.id),
                turn=turn,
                history_length=len(conversation_history),
            )

            agent_response = adapter.generate_response(conversation_history)
            agent_content = agent_response.get("content", "")

            if not agent_content:
                logger.warning(
                    "prompt_based_agent_empty_response",
                    call_execution_id=str(call_execution.id),
                    turn=turn,
                )
                break

            # Add agent response to conversation history
            conversation_history.append(
                {
                    "role": "assistant",
                    "content": agent_content,
                }
            )

            # Step 2: Send agent response to simulator and get next message
            # Note: For the SDK, agent sends "user" messages (agent-under-test perspective)
            # But for storage, we swap roles (agent becomes assistant)
            messages_to_send = [ChatMessage(role=ChatRole.USER, content=agent_content)]

            chat_result = send_message_to_chat(
                call_execution=call_execution,
                organization=organization,
                workspace=workspace,
                messages=messages_to_send,
                metrics={
                    "prompt_tokens": agent_response.get("usage", {}).get(
                        "prompt_tokens"
                    ),
                    "completion_tokens": agent_response.get("usage", {}).get(
                        "completion_tokens"
                    ),
                    "latency": agent_response.get("latency_ms"),
                },
                store_sync=True,
            )

            if chat_result.get("chat_ended"):
                logger.info(
                    "prompt_based_conversation_ended_by_simulator",
                    call_execution_id=str(call_execution.id),
                    turn=turn,
                )
                return True

            # Get simulator's response for next turn.
            # output_message is List[ChatMessage] (Pydantic models) from
            # ChatSessionSendMessageResponse.output.
            output_messages: list[ChatMessage] = chat_result.get("output_message") or []
            sim_content = ""
            for msg in output_messages:
                if isinstance(msg, ChatMessage) and msg.content:
                    sim_content = msg.content
                    break

            if sim_content:
                conversation_history.append(
                    {
                        "role": "user",
                        "content": sim_content,
                    }
                )

            # Refresh call execution status
            call_execution.refresh_from_db()
            if call_execution.status != CallExecution.CallStatus.ONGOING:
                logger.info(
                    "prompt_based_conversation_stopped",
                    call_execution_id=str(call_execution.id),
                    status=call_execution.status,
                    turn=turn,
                )
                return call_execution.status == CallExecution.CallStatus.COMPLETED

        # Max turns reached or agent returned empty content
        logger.info(
            "prompt_based_conversation_loop_exited",
            call_execution_id=str(call_execution.id),
            turns_completed=turn,
        )
        _finalize_prompt_conversation(call_execution)

        return True

    except Exception as e:
        logger.exception(
            "prompt_based_conversation_error",
            call_execution_id=str(call_execution.id),
            error=str(e),
        )
        call_execution.status = CallExecution.CallStatus.FAILED
        call_execution.ended_reason = str(e)
        call_execution.ended_at = timezone.now()
        call_execution.save(update_fields=["status", "ended_reason", "ended_at"])
        return False


def send_message_to_chat(
    call_execution: CallExecution,
    organization: Organization,
    workspace: Workspace,
    messages: list[ChatMessage],
    metrics: dict[str, float | int | None] | None = None,
    store_sync: bool = False,
):
    try:
        if call_execution.status != CallExecution.CallStatus.ONGOING:
            raise Exception("Call execution is not ongoing")

        # Get chat service (provider is determined from metadata or env)
        chat_service = _get_chat_service(organization, workspace)

        # Get session ID (support both new and legacy keys)
        chat_session_id = _get_session_id_from_metadata(call_execution)

        # Get current session state
        session_result = chat_service.get_session(chat_session_id)
        if not session_result.success:
            raise Exception(f"Chat session not found: {session_result.error}")

        chat_session_messages = session_result.messages or []

        # Send message using ChatServiceManager
        send_result = chat_service.send_message(
            session_id=chat_session_id,
            messages=messages,
        )

        if not send_result.success:
            raise Exception(f"Failed to send message: {send_result.error}")

        # Extract usage metrics from send_result if available
        if metrics is None and send_result.usage:
            usage = send_result.usage
            metrics = {
                "input_tokens": usage.input_tokens or 0,
                "output_tokens": usage.output_tokens or 0,
                "total_tokens": usage.total_tokens or 0,
                "latency_ms": None,
            }

        # Lazy import to avoid circular dependency
        from simulate.tasks.chat_sim import store_chat_messages

        # Serialize Pydantic ChatMessage objects to plain dicts before dispatch.
        # `apply_async` → Temporal round-trips args through
        # `json.loads(json.dumps(..., default=str))` (see
        # `tfc/temporal/drop_in/runner.py::_make_serializable`). `default=str`
        # stringifies Pydantic models into `"role='user' content='...'"` — not
        # valid JSON objects — so `store_chat_messages` then sees a list of
        # strings and blows up at `msg.get("content")` with
        # `'str' object has no attribute 'get'`, swallowing the exception and
        # leaving the chat stuck in ANALYZING forever.
        def _to_dict(msg):
            if hasattr(msg, "model_dump"):
                return msg.model_dump()
            if hasattr(msg, "dict"):
                return msg.dict()
            return msg

        input_messages_serialized = [
            _to_dict(m) for m in (send_result.input_messages or [])
        ]
        output_messages_serialized = [
            _to_dict(m) for m in (send_result.output_messages or [])
        ]

        # Store chat messages — synchronously when called from the prompt-based
        # conversation loop so that all messages are in the DB before aggregation.
        store_args = (
            str(call_execution.id),
            str(organization.id),
            str(workspace.id),
            input_messages_serialized,
            output_messages_serialized,
            send_result.has_chat_ended,
            chat_session_id,
            timezone.now(),
            metrics,
        )
        # Set ANALYZING *before* persisting messages so that eval tasks
        # dispatched inside store_chat_messages see the correct status.
        if send_result.has_chat_ended:
            call_execution.status = CallExecution.CallStatus.ANALYZING
            call_execution.completed_at = timezone.now()
            if send_result.ended_reason:
                call_execution.ended_reason = send_result.ended_reason
            call_execution.save(
                update_fields=["status", "completed_at", "ended_reason"]
            )

        if store_sync:
            result = store_chat_messages(*store_args)
            if result is None:
                logger.error(
                    "store_chat_messages_failed_sync",
                    call_execution_id=str(call_execution.id),
                )
        else:
            try:
                store_chat_messages.apply_async(args=store_args)
            except Exception as dispatch_error:
                logger.exception(
                    "store_chat_messages_dispatch_failed_falling_back_sync",
                    call_execution_id=str(call_execution.id),
                    error=str(dispatch_error),
                )
                sync_store = getattr(
                    store_chat_messages, "_original_func", store_chat_messages
                )
                result = sync_store(*store_args)
                if result is None:
                    logger.error(
                        "store_chat_messages_failed_after_dispatch_fallback",
                        call_execution_id=str(call_execution.id),
                    )

        if send_result.has_chat_ended:
            notify_simulation_update(
                organization_id=str(organization.id),
                run_test_id=str(call_execution.test_execution.run_test_id),
                test_execution_id=str(call_execution.test_execution_id),
            )

        # Build the full post-turn message history for the SDK.
        # The SDK (cloud.py) rebuilds its local conversation_history from
        # message_history after every turn.  We must include the full updated
        # history (pre-send snapshot + agent input + new simulator output) so
        # the SDK sees the complete conversation and doesn't loop on stale data.
        full_message_history = list(chat_session_messages)
        if send_result.input_messages:
            full_message_history.extend(send_result.input_messages)
        if send_result.output_messages:
            full_message_history.extend(send_result.output_messages)

        return {
            "input_message": send_result.input_messages,
            "output_message": send_result.output_messages,
            "message_history": full_message_history,
            "chat_ended": send_result.has_chat_ended,
            "ended_reason": send_result.ended_reason,
        }

    except Exception as e:
        logger.exception(f"Error sending message to chat: {str(e)}")
        call_execution.status = CallExecution.CallStatus.FAILED
        call_execution.ended_reason = str(e)
        call_execution.ended_at = timezone.now()
        call_execution.save(update_fields=["status", "ended_reason", "ended_at"])
        raise Exception(f"Error sending message to chat: {str(e)}") from e


def finalize_chat_execution(call_execution: CallExecution):
    """
    Finalize a chat execution that ended without explicit endCall.

    This can be used for:
    - Prompt-based conversations that hit max turns
    - SDK/test scripts that exit without calling endCall
    - Timeout scenarios where chat is abandoned

    Sets status to ANALYZING, aggregates metrics, deducts cost, and dispatches evaluations.
    """
    from simulate.services.test_executor import _run_simulate_evaluations_task
    from simulate.utils.chat_simulation import _aggregate_chat_metrics

    call_execution.status = CallExecution.CallStatus.ANALYZING
    call_execution.completed_at = timezone.now()
    call_execution.ended_at = timezone.now()
    if call_execution.started_at:
        delta = call_execution.completed_at - call_execution.started_at
        call_execution.duration_seconds = int(delta.total_seconds())
    call_execution.save(
        update_fields=["status", "completed_at", "ended_at", "duration_seconds"]
    )

    notify_simulation_update(
        organization_id=str(call_execution.test_execution.run_test.organization_id),
        run_test_id=str(call_execution.test_execution.run_test_id),
        test_execution_id=str(call_execution.test_execution_id),
    )

    try:
        _aggregate_chat_metrics(call_execution)
        TestExecutor._deduct_call_cost(call_execution)
    except Exception:
        logger.exception(
            "finalize_chat_execution_metrics_error",
            call_execution_id=str(call_execution.id),
        )

    if not call_execution.call_metadata:
        call_execution.call_metadata = {}

    if not call_execution.call_metadata.get("eval_started"):
        call_execution.call_metadata["eval_started"] = True
        _run_simulate_evaluations_task.apply_async(args=(str(call_execution.id),))

    call_execution.save(
        update_fields=[
            "call_metadata",
            "conversation_metrics_data",
            "overall_score",
        ]
    )


def _finalize_prompt_conversation(call_execution: CallExecution):
    """
    Finalize a prompt-based conversation that exited via max turns or empty
    agent response. Wrapper around finalize_chat_execution for backward compatibility.
    """
    finalize_chat_execution(call_execution)
