"""FutureAGIChatService — ChatServiceBlueprint implementation using the LLM class.

Session and assistant state is persisted in the DB (ChatSimulatorAssistant /
ChatSimulatorSession), which makes it recoverable across restarts and
inspectable for debugging.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

import structlog

from simulate.models.chat_simulator import ChatSimulatorAssistant, ChatSimulatorSession
from simulate.pydantic_schemas.chat import (
    ChatMessage,
    ChatRole,
    ToolCall,
    ToolCallFunction,
)
from simulate.services.chat_constants import (
    FUTUREAGI_CHAT_MAX_TOKENS,
    FUTUREAGI_CHAT_MODEL,
    FUTUREAGI_CHAT_TEMPERATURE,
    MAX_CONVERSATION_TURNS,
)
from simulate.services.chat_engine import ChatServiceBlueprint
from simulate.services.futureagi_chat.llm_client import generate_simulator_response
from simulate.services.types.chat import (
    CreateAssistantInput,
    CreateAssistantResult,
    CreateSessionInput,
    CreateSessionResult,
    DeleteAssistantInput,
    DeleteAssistantResult,
    GetSessionInput,
    GetSessionResult,
    LLMUsage,
    SendMessageInput,
    SendMessageResult,
)

logger = structlog.get_logger(__name__)


class FutureAGIChatService(ChatServiceBlueprint):
    """Future AGI chat simulation engine using the LLM class for completions.

    Assistant and session state is stored in the DB for persistence and
    auditability across Kubernetes pods.
    """

    def __init__(
        self,
        organization_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ):
        self.organization_id = organization_id
        self.workspace_id = workspace_id

    # ------------------------------------------------------------------
    # Assistant lifecycle
    # ------------------------------------------------------------------

    def create_assistant(self, input: CreateAssistantInput) -> CreateAssistantResult:
        """Persist a simulator assistant config to DB."""
        from accounts.models import Organization
        from accounts.models.workspace import Workspace

        try:
            org = Organization.objects.get(id=self.organization_id)
            workspace = (
                Workspace.objects.filter(id=self.workspace_id).first()
                if self.workspace_id
                else None
            )
            assistant = ChatSimulatorAssistant.objects.create(
                name=input.name,
                system_prompt=input.system_prompt,
                model=input.model or FUTUREAGI_CHAT_MODEL,
                temperature=(
                    input.temperature
                    if input.temperature is not None
                    else FUTUREAGI_CHAT_TEMPERATURE
                ),
                max_tokens=(
                    input.max_tokens
                    if input.max_tokens is not None
                    else FUTUREAGI_CHAT_MAX_TOKENS
                ),
                organization=org,
                workspace=workspace,
            )
            return CreateAssistantResult(
                success=True,
                assistant_id=str(assistant.id),
                provider_data={"model": assistant.model},
            )
        except Exception as e:
            logger.exception("create_assistant_failed", error=str(e))
            return CreateAssistantResult(success=False, error=str(e))

    def delete_assistant(self, input: DeleteAssistantInput) -> DeleteAssistantResult:
        """Delete an assistant from DB."""
        deleted, _ = ChatSimulatorAssistant.objects.filter(
            id=input.assistant_id
        ).delete()
        if deleted:
            return DeleteAssistantResult(success=True)
        return DeleteAssistantResult(
            success=False,
            error=f"Assistant {input.assistant_id} not found",
        )

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def create_session(self, input: CreateSessionInput) -> CreateSessionResult:
        """Persist a new chat session to DB."""
        from accounts.models import Organization
        from accounts.models.workspace import Workspace

        try:
            assistant = ChatSimulatorAssistant.objects.get(id=input.assistant_id)
        except ChatSimulatorAssistant.DoesNotExist:
            return CreateSessionResult(
                success=False,
                error=f"Assistant {input.assistant_id} not found",
            )

        initial_messages: List[Dict[str, Any]] = []
        if input.initial_message and input.initial_message.content:
            initial_messages.append(
                {
                    # Opening greeting seeded for the agent under test — the
                    # agent will see this as its incoming user turn. The
                    # simulator's own subsequent replies are stored as
                    # role="assistant" (see send_message).
                    "role": "user",
                    "content": input.initial_message.content,
                }
            )

        try:
            org = Organization.objects.get(
                id=input.organization_id or self.organization_id
            )
            workspace_id = input.workspace_id or self.workspace_id
            workspace = (
                Workspace.objects.filter(id=workspace_id).first()
                if workspace_id
                else None
            )
            session = ChatSimulatorSession.objects.create(
                assistant=assistant,
                messages=initial_messages,
                organization=org,
                workspace=workspace,
            )
        except Exception as e:
            logger.exception("create_session_failed", error=str(e))
            return CreateSessionResult(success=False, error=str(e))

        result_messages = [input.initial_message] if input.initial_message else []
        return CreateSessionResult(
            success=True,
            session_id=str(session.id),
            messages=result_messages,
        )

    def get_session(self, input: GetSessionInput) -> GetSessionResult:
        """Retrieve session from DB."""
        try:
            session = ChatSimulatorSession.objects.get(id=input.session_id)
        except ChatSimulatorSession.DoesNotExist:
            return GetSessionResult(success=False, error="Session not found")

        messages = self._convert_to_chat_messages(session.messages)
        return GetSessionResult(
            success=True,
            session_id=str(session.id),
            name=session.assistant.name,
            status=session.status,
            assistant_id=str(session.assistant_id),
            messages=messages,
        )

    # ------------------------------------------------------------------
    # Messaging
    # ------------------------------------------------------------------

    def send_message(self, input: SendMessageInput) -> SendMessageResult:
        """Send messages and return the simulator's response.

        Uses synchronous Django ORM for DB access and asyncio.to_thread for
        the LLM call (which is already sync internally).
        """
        try:
            session = ChatSimulatorSession.objects.select_related("assistant").get(
                id=input.session_id
            )
        except ChatSimulatorSession.DoesNotExist:
            return SendMessageResult(success=False, error="Session not found")

        if session.has_chat_ended:
            return SendMessageResult(
                success=False,
                error="Chat has already ended",
                has_chat_ended=True,
            )

        messages: List[Dict[str, Any]] = list(session.messages)

        for msg in input.messages:
            # Skip tool-role messages: the simulator role-plays a customer and
            # doesn't need the agent's internal tool exchanges. Including them
            # would orphan the `tool` message (its parent `tool_calls` is dropped
            # below), causing OpenAI to reject the next turn with
            # "messages with role 'tool' must be a response to a preceding
            # message with 'tool_calls'".
            if msg.role == ChatRole.TOOL:
                continue
            content = (msg.content or "").strip()
            if not content:
                logger.warning(
                    "empty_message_skipped",
                    session_id=input.session_id,
                    role=msg.role.value,
                )
                continue
            messages.append({"role": msg.role.value, "content": content})

        # Check turn limit
        turn_count = sum(1 for m in messages if m.get("role") == "user")
        if turn_count >= MAX_CONVERSATION_TURNS:
            logger.warning(
                "max_conversation_turns_reached",
                session_id=input.session_id,
                turn_count=turn_count,
            )
            final_content = f"Maximum conversation turns ({MAX_CONVERSATION_TURNS}) reached. Ending conversation."
            messages.append({"role": "assistant", "content": final_content})
            session.messages = messages
            session.has_chat_ended = True
            session.status = "ended"
            session.save(update_fields=["messages", "has_chat_ended", "status"])
            return SendMessageResult(
                success=True,
                session_id=input.session_id,
                input_messages=input.messages,
                output_messages=[
                    ChatMessage(role=ChatRole.USER, content=final_content)
                ],
                has_chat_ended=True,
                ended_reason="Max conversation turns reached",
                usage=LLMUsage(),
            )

        assistant = session.assistant
        org_id = input.organization_id or self.organization_id
        ws_id = input.workspace_id or self.workspace_id

        try:
            response = self._call_llm(
                messages=messages,
                system_prompt=assistant.system_prompt,
                model=assistant.model,
                temperature=assistant.temperature,
                max_tokens=assistant.max_tokens,
                organization_id=org_id,
                workspace_id=ws_id,
            )

            content = response.get("content", "")
            tool_calls = response.get("tool_calls", [])
            has_chat_ended = response.get("has_chat_ended", False)
            ended_reason = response.get("ended_reason")
            usage: LLMUsage = response.get("usage") or LLMUsage()

            # The simulator LLM's own output — must be stored as "assistant"
            # so the next OpenAI call sees its own prior turn correctly.
            # The SDK's cloud engine also expects backend simulator messages to
            # use role="assistant" (it converts them to "user" on its side).
            #
            # Only persist tool_calls when the chat actually ends (i.e. endCall).
            # A non-ending tool_call (hallucinated tool name, future simulator
            # tool, malformed endCall) would leave the next OpenAI turn with
            # dangling tool_calls and no `tool` response, re-triggering the
            # 400 this fix is for.
            include_tool_calls = bool(tool_calls) and has_chat_ended
            messages.append(
                {
                    "role": "assistant",
                    "content": content,
                    **({"tool_calls": tool_calls} if include_tool_calls else {}),
                }
            )

            session.messages = messages
            session.has_chat_ended = has_chat_ended
            session.status = "ended" if has_chat_ended else session.status
            session.total_tokens += usage.total_tokens
            session.save(
                update_fields=["messages", "has_chat_ended", "status", "total_tokens"]
            )

            output_tool_calls = None
            if tool_calls:
                output_tool_calls = [
                    ToolCall(
                        id=tc.get("id", str(uuid.uuid4())),
                        type=tc.get("type", "function"),
                        function=ToolCallFunction(
                            name=tc.get("function", {}).get("name", ""),
                            arguments=tc.get("function", {}).get("arguments", "{}"),
                        ),
                    )
                    for tc in tool_calls
                ]

            return SendMessageResult(
                success=True,
                input_messages=input.messages,
                output_messages=[
                    ChatMessage(
                        role=ChatRole.USER,
                        content=content,
                        tool_calls=output_tool_calls,
                    )
                ],
                message_id=str(uuid.uuid4()),
                has_chat_ended=has_chat_ended,
                ended_reason=ended_reason,
                usage=usage,
            )

        except Exception as e:
            logger.exception(
                "futureagi_send_message_failed",
                session_id=input.session_id,
                error=str(e),
            )
            session.status = "error"
            session.save(update_fields=["status"])
            return SendMessageResult(success=False, error=str(e))

    async def send_message_async(self, input: SendMessageInput) -> SendMessageResult:
        """Async wrapper — delegates to sync send_message via thread."""
        from asgiref.sync import sync_to_async

        return await sync_to_async(self.send_message)(input)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _call_llm(self, **kwargs) -> Dict[str, Any]:
        """Call the LLM synchronously."""
        return generate_simulator_response(**kwargs)

    def _convert_to_chat_messages(
        self, messages: List[Dict[str, Any]]
    ) -> List[ChatMessage]:
        result = []
        for msg in messages:
            role = ChatRole(msg.get("role", "user"))
            content = msg.get("content")
            tool_calls = None
            if msg.get("tool_calls"):
                tool_calls = [
                    ToolCall(
                        id=tc.get("id", str(uuid.uuid4())),
                        type=tc.get("type", "function"),
                        function=ToolCallFunction(
                            name=tc.get("function", {}).get("name", ""),
                            arguments=tc.get("function", {}).get("arguments", "{}"),
                        ),
                    )
                    for tc in msg["tool_calls"]
                ]
            result.append(
                ChatMessage(role=role, content=content, tool_calls=tool_calls)
            )
        return result

    def cleanup_session(self, session_id: str, cleanup_assistant: bool = True) -> bool:
        """Delete session (and optionally assistant) from DB."""
        try:
            session = ChatSimulatorSession.objects.get(id=session_id)
            assistant_id = session.assistant_id
            session.delete()
            if cleanup_assistant and assistant_id:
                ChatSimulatorAssistant.objects.filter(id=assistant_id).delete()
            return True
        except ChatSimulatorSession.DoesNotExist:
            logger.warning("cleanup_session_not_found", session_id=session_id)
            return False
        except Exception as e:
            logger.error(
                "session_cleanup_exception", session_id=session_id, error=str(e)
            )
            return False
