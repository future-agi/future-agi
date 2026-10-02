"""FutureAGIChatService — ChatServiceBlueprint implementation using the LLM class.

Session and assistant state is persisted in the DB (ChatSimulatorAssistant /
ChatSimulatorSession), which makes it recoverable across restarts and
inspectable for debugging.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import structlog
from asgiref.sync import sync_to_async

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


@dataclass
class SimulatorMessage:
    """OpenAI-format chat message assembled for the simulator persona LLM.

    Centralizes the role / content / tool_calls / tool_call_id shape that was
    previously built as raw dicts inline, so the tool-call <-> tool-result
    pairing (and the empty-content assistant turn) can't silently drift.
    """

    role: str
    content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        message: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            message["tool_calls"] = self.tool_calls
        if self.tool_call_id is not None:
            message["tool_call_id"] = self.tool_call_id
        return message


class FutureAGIChatService(ChatServiceBlueprint):
    """Future AGI chat simulation engine using the LLM class for completions.

    Assistant and session state is stored in the DB for persistence and
    auditability across Kubernetes pods.
    """

    def __init__(
        self,
        organization_id: str | None = None,
        workspace_id: str | None = None,
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

        initial_messages: list[dict[str, Any]] = []
        if input.initial_message and input.initial_message.content:
            initial_messages.append(
                {
                    "role": "user",  # Simulator (AI customer) speaks as "user"
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

        messages: list[dict[str, Any]] = list(session.messages)

        messages.extend(self._to_provider_messages(input.messages))

        # Check turn limit
        turn_count = sum(1 for m in messages if m.get("role") == "user")
        if turn_count >= MAX_CONVERSATION_TURNS:
            logger.warning(
                "max_conversation_turns_reached",
                session_id=input.session_id,
                turn_count=turn_count,
            )
            final_content = f"Maximum conversation turns ({MAX_CONVERSATION_TURNS}) reached. Ending conversation."
            messages.append(
                SimulatorMessage(role="user", content=final_content).to_dict()
            )
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
            has_chat_ended = response.get("has_chat_ended", False)
            ended_reason = response.get("ended_reason")
            usage: LLMUsage = response.get("usage") or LLMUsage()

            # An empty persona turn is never a valid customer message (an
            # endCall with no text is fine — has_chat_ended covers it).
            # Delivering "" sends the agent into "your message didn't come
            # through" spirals, so retry once and otherwise fail the send
            # loudly instead of corrupting the conversation.
            if not content.strip() and not has_chat_ended:
                logger.warning(
                    "simulator_empty_response_retrying",
                    session_id=input.session_id,
                    model=assistant.model,
                )
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
                has_chat_ended = response.get("has_chat_ended", False)
                ended_reason = response.get("ended_reason")
                retry_usage: LLMUsage = response.get("usage") or LLMUsage()
                usage = LLMUsage(
                    input_tokens=usage.input_tokens + retry_usage.input_tokens,
                    output_tokens=usage.output_tokens + retry_usage.output_tokens,
                    total_tokens=usage.total_tokens + retry_usage.total_tokens,
                )
                if not content.strip() and not has_chat_ended:
                    logger.error(
                        "simulator_empty_response",
                        session_id=input.session_id,
                        model=assistant.model,
                    )
                    session.status = "error"
                    session.save(update_fields=["status"])
                    return SendMessageResult(
                        success=False,
                        error="Simulator returned an empty message twice",
                    )

            # The persona's own tool call (e.g. endCall) is a control signal,
            # captured via has_chat_ended below. It is scrubbed from the stored
            # conversation so the agent-under-test never sees the simulator's
            # tooling (and so the persona's own context stays text-only).
            messages.append(
                SimulatorMessage(role="user", content=content).to_dict()
            )

            session.messages = messages
            session.has_chat_ended = has_chat_ended
            session.status = "ended" if has_chat_ended else session.status
            session.total_tokens += usage.total_tokens
            session.save(
                update_fields=["messages", "has_chat_ended", "status", "total_tokens"]
            )

            return SendMessageResult(
                success=True,
                input_messages=input.messages,
                # The persona's tool_calls (endCall) are intentionally omitted:
                # the agent-under-test only ever sees the customer's text, never
                # the simulator's tooling. The end signal is carried by
                # has_chat_ended / ended_reason instead.
                output_messages=[
                    ChatMessage(role=ChatRole.USER, content=content)
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
        return await sync_to_async(self.send_message)(input)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _call_llm(self, **kwargs) -> dict[str, Any]:
        """Call the LLM synchronously."""
        return generate_simulator_response(**kwargs)

    def _to_provider_messages(
        self,
        input_messages: list[ChatMessage],
    ) -> list[dict[str, Any]]:
        """Assemble the agent-under-test's turn for the persona LLM.

        The persona should experience a human-like conversation, so the agent's
        tool machinery is scrubbed here: tool results and pure tool-call turns
        are dropped, and only the agent's text replies are forwarded — as
        ``assistant`` turns (the agent is the "assistant" from the customer's
        side). This keeps the persona context to a clean user<->assistant text
        exchange, which also makes the malformed tool-call sequence that caused
        the send-message 500 structurally impossible.

        The agent's full tool data is still persisted to ``ChatMessageModel``
        by ``store_chat_messages`` (and read by the Tool-Call-Accuracy eval);
        this only controls what the simulator sees.
        """
        provider_messages: list[dict[str, Any]] = []
        for msg in input_messages:
            content = (msg.content or "").strip()
            role = msg.role.value if msg.role else "user"

            # Scrub the agent's tool results outright.
            if role == "tool":
                continue

            # Drop tool-call turns that carry no text (a pure tool call). A turn
            # with both text and tool_calls keeps only its text; the tool_calls
            # are never forwarded to the persona.
            if not content:
                continue

            provider_messages.append(
                SimulatorMessage(role="assistant", content=content).to_dict()
            )
        return provider_messages

    def _convert_to_chat_messages(
        self, messages: list[dict[str, Any]]
    ) -> list[ChatMessage]:
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
