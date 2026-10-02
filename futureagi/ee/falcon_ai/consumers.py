import asyncio
import uuid
from urllib.parse import parse_qs

import structlog
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.core.exceptions import ValidationError

from ai_tools.base import ToolContext
from ee.falcon_ai.agent import AgentLoop
from ee.falcon_ai.stream_buffer import StreamBuffer
from ee.usage.schemas.event_types import BillingEventType
from ee.usage.schemas.events import UsageEvent
from ee.usage.services.config import BillingConfig
from ee.usage.services.emitter import emit
from ee.usage.services.metering import check_usage
from tfc.ee_gating import EEFeature, FeatureUnavailable, check_ee_feature

logger = structlog.get_logger(__name__)


class FalconAIConsumer(AsyncJsonWebsocketConsumer):
    """
    WebSocket consumer for Falcon AI chat.

    Frontend connects with workspace_id query param. The consumer verifies the
    user's organization, resolves the workspace, and handles the agent loop.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._agent_task = None
        self.user = None
        self.organization = None
        self.workspace = None
        self.workspace_id = None
        self.organization_id = None
        self._message_timestamps = []  # For rate limiting
        self._rate_limit = 10  # max messages per window
        self._rate_window = 60  # seconds

    async def connect(self):
        user = self.scope.get("user")
        if not user or not user.is_authenticated:
            await self.close(code=4001)
            return

        self.user = user

        # Parse workspace_id from query string FIRST — it determines the org
        params = parse_qs(self.scope.get("query_string", b"").decode())
        self.workspace_id = params.get("workspace_id", [None])[0]

        # Resolve organization: prefer workspace_id (accurate), fall back to membership
        if self.workspace_id:
            org = await self._get_organization_from_workspace(self.workspace_id)
            if org is None:
                # workspace_id invalid or user has no access — fall back
                org = await self._get_organization()
            self.organization = org
        else:
            self.organization = await self._get_organization()

        if self.organization is None:
            await self.close(code=4002)
            return
        self.organization_id = str(self.organization.id)

        try:
            await database_sync_to_async(check_ee_feature)(
                EEFeature.FALCON_AI,
                org_id=self.organization_id,
            )
        except FeatureUnavailable:
            await self.close(code=4003)
            return

        # Resolve workspace
        self.workspace = await self._get_workspace()

        await self.accept()
        logger.info(
            "falcon_ai_ws_connected",
            user_id=str(self.user.id),
            organization_id=self.organization_id,
            workspace_id=str(self.workspace.id) if self.workspace else None,
        )

    async def receive_json(self, content):
        msg_type = content.get("type")

        # Rate limiting — only for actionable message types, not ping/reconnect
        if msg_type in ("chat", "feedback", "stop"):
            import time

            now = time.time()
            self._message_timestamps = [
                t for t in self._message_timestamps if now - t < self._rate_window
            ]
            if len(self._message_timestamps) >= self._rate_limit:
                await self.send_json(
                    {
                        "type": "error",
                        "data": {
                            "error": "Rate limit exceeded. Please wait before sending more messages.",
                            "conversation_id": content.get("conversation_id"),
                        },
                    }
                )
                return
            self._message_timestamps.append(now)

        if msg_type == "chat":
            await self._handle_chat(content)
        elif msg_type == "stop":
            await self._handle_stop()
        elif msg_type == "feedback":
            await self._handle_feedback(content)
        elif msg_type == "reconnect":
            await self._handle_reconnect(content)
        elif msg_type == "ping":
            await self.send_json({"type": "pong"})
        else:
            await self.send_json(
                {
                    "type": "error",
                    "data": {"error": f"Unknown message type: {msg_type}"},
                }
            )

    async def _handle_chat(self, content):
        conversation_id = content.get("conversation_id")
        message_content = content.get("message", "")
        # Context can be a string or an object {page: "...", path: "...", entity_type: "...", entity_id: "..."}
        raw_context = content.get("context", content.get("context_page", ""))
        if isinstance(raw_context, dict):
            context_page = raw_context.get("page", "")
            context_info = raw_context  # full context dict with entity_id etc.
        else:
            context_page = raw_context or ""
            context_info = {"page": context_page}
        skill_id = content.get("skill_id")
        file_ids = content.get("file_ids", [])

        # Parse inline slash command: "/skill-slug rest of message"
        if not skill_id and message_content.startswith("/"):
            parts = message_content.split(" ", 1)
            slug = parts[0][1:]  # strip leading "/"
            if slug:
                from ee.falcon_ai.models import Skill

                from django.db.models import Q

                slash_skill = await database_sync_to_async(
                    lambda: Skill.no_workspace_objects.filter(
                        Q(organization=self.organization)
                        | Q(organization__isnull=True, is_builtin=True),
                        slug=slug,
                        is_active=True,
                    ).first()
                )()
                if slash_skill:
                    skill_id = str(slash_skill.id)
                    # Strip the slash command from the message, keep the rest
                    rest = parts[1].strip() if len(parts) > 1 else ""
                    message_content = rest or f"Help me with: {slash_skill.name}"
                else:
                    # An unresolved slug silently downgrades the turn to a plain
                    # chat, so a skill that failed to seed looks like a feature
                    # that quietly does nothing. Debug hides that in prod.
                    logger.warning("unknown_slash_command", slug=slug)

        # Every error below carries conversation_id: clients route inbound frames
        # by it, so an untagged error matches no handler and is dropped. The run
        # then sits in its loading state forever instead of reporting what went
        # wrong — the failure is delivered to nobody.
        if not message_content and not file_ids:
            await self.send_json(
                {
                    "type": "error",
                    "data": {
                        "error": "Message is required",
                        "conversation_id": conversation_id,
                    },
                }
            )
            return

        if not conversation_id:
            await self.send_json(
                {"type": "error", "data": {"error": "conversation_id is required"}}
            )
            return

        # Load attached file contents — kept separate from visible message
        file_context = ""
        file_images = []
        files_metadata = []
        if file_ids:
            file_data = await self._load_file_contents(file_ids)
            file_context = (
                file_data.get("text", "") if isinstance(file_data, dict) else file_data
            )
            file_images = (
                file_data.get("images", []) if isinstance(file_data, dict) else []
            )
            files_metadata = await self._get_files_metadata(file_ids)

        # Fetch URLs from message
        url_context = ""
        if message_content:
            try:
                from ee.falcon_ai.url_fetcher import fetch_urls_from_message

                url_context = await database_sync_to_async(fetch_urls_from_message)(
                    message_content
                )
            except Exception as e:
                logger.warning("url_fetch_error", error=str(e))

        # Combine file context and URL context
        combined_context = "\n\n".join(filter(None, [file_context, url_context]))

        # Save user message to DB (clean message + file metadata, no file content dump)
        user_message = await self._save_user_message(
            conversation_id, message_content, files_metadata
        )
        if user_message is None:
            await self.send_json(
                {
                    "type": "error",
                    "data": {
                        "error": "Conversation not found",
                        "conversation_id": conversation_id,
                    },
                }
            )
            return

        # Launch agent loop — pass full context + file context + images
        self._agent_task = asyncio.create_task(
            self._run_agent(
                conversation_id,
                message_content,
                context_page,
                combined_context,
                context_info,
                file_images,
                skill_id=skill_id,
            )
        )

    async def _handle_stop(self):
        if self._agent_task and not self._agent_task.done():
            # A cluster-RCA run executes in a non-cancellable worker thread that
            # keeps calling the LLM after the coroutine unwinds. The bridge stashes
            # a threading.Event on the task; set it so the agent loop stops
            # cooperatively and returns its partial result (with accumulated cost,
            # which the bridge then bills from the thread). Set before cancel so the
            # worker observes it; harmless no-op for non-RCA runs.
            rca_stop_event = getattr(self._agent_task, "_rca_stop_event", None)
            if rca_stop_event is not None:
                rca_stop_event.set()
            self._agent_task.cancel()
            try:
                await self._agent_task
            except asyncio.CancelledError:
                pass
            self._agent_task = None
            await self.send_json({"type": "stopped"})

    async def _handle_feedback(self, content):
        message_id = content.get("message_id")
        feedback = content.get("feedback", "")

        if not message_id:
            await self.send_json(
                {"type": "error", "data": {"error": "message_id is required"}}
            )
            return

        updated = await self._update_message_feedback(message_id, feedback)
        if updated:
            await self.send_json({"type": "feedback_updated", "message_id": message_id})
        else:
            await self.send_json(
                {"type": "error", "data": {"error": "Message not found"}}
            )

    async def _handle_reconnect(self, content):
        """Replay buffered events for a conversation when a client reconnects."""
        conversation_id = content.get("conversation_id")
        if not conversation_id:
            await self.send_json(
                {"type": "reconnect_status", "data": {"status": "none"}}
            )
            return

        stream_buffer = StreamBuffer(conversation_id)
        status = await database_sync_to_async(stream_buffer.get_status)()

        if not status:
            # No active or recent stream — nothing to replay
            await self.send_json(
                {"type": "reconnect_status", "data": {"status": "none"}}
            )
            return

        # Get all buffered events for replay
        events = await database_sync_to_async(stream_buffer.get_all_events)()

        await self.send_json(
            {
                "type": "reconnect_status",
                "data": {"status": status, "event_count": len(events)},
            }
        )

        # Replay every event — the frontend handlers (text_delta, tool_call_start, etc.)
        # work identically for replayed events
        for event in events:
            await self.send_json(event)

        logger.info(
            "falcon_ai_reconnect_replay",
            conversation_id=conversation_id,
            status=status,
            event_count=len(events),
        )

    async def _run_agent(
        self,
        conversation_id,
        user_message_content,
        context_page,
        file_context="",
        context_info=None,
        file_images=None,
        skill_id=None,
    ):
        # Set up Redis stream buffer for reconnection support
        stream_buffer = StreamBuffer(conversation_id)
        await database_sync_to_async(stream_buffer.clear)()
        await database_sync_to_async(stream_buffer.set_status)("running")

        async def buffered_send(event):
            """Send to WebSocket AND buffer in Redis for reconnection."""
            # Tag every event with conversation_id so frontend can filter
            if (
                isinstance(event, dict)
                and "data" in event
                and isinstance(event["data"], dict)
            ):
                event["data"]["conversation_id"] = str(conversation_id)
            await self.send_json(event)
            await database_sync_to_async(stream_buffer.append_event)(event)

        agent = None
        try:
            # Build ToolContext
            tool_context = ToolContext(
                user=self.user,
                organization=self.organization,
                workspace=self.workspace,
            )

            # Resolve skill from DB if skill_id provided
            skill = None
            if skill_id:
                from ee.falcon_ai.models import Skill

                from django.db.models import Q

                skill = await database_sync_to_async(
                    lambda: Skill.no_workspace_objects.filter(
                        Q(organization=self.organization)
                        | Q(organization__isnull=True, is_builtin=True),
                        id=skill_id,
                    ).first()
                )()

            # If files attached, combine user message + file content for the agent
            # (file content goes to LLM but is NOT saved in the message DB)
            agent_message = user_message_content
            if file_context:
                agent_message = f"{user_message_content}\n\n{file_context}"

            # Get conversation and history
            conversation, history = await self._get_conversation_and_history(
                conversation_id
            )
            if conversation is None:
                await self.send_json(
                    {
                        "type": "error",
                        "data": {
                            "error": "Conversation not found",
                            "conversation_id": conversation_id,
                        },
                    }
                )
                await database_sync_to_async(stream_buffer.set_status)("error")
                return

            # Create AgentLoop instance
            agent = AgentLoop(tool_context=tool_context, conversation=conversation)

            # Pre-check usage before the LLM call
            usage_check = await database_sync_to_async(check_usage)(
                self.organization_id, BillingEventType.FALCON_AI_CHAT
            )
            if not usage_check.allowed:
                # Deterministic for any org at its limit: untagged, this denial
                # was dropped and the user watched a loader forever instead of
                # being told they were out of credit.
                await self.send_json(
                    {
                        "type": "error",
                        "data": {
                            "error": usage_check.reason or "Usage limit exceeded",
                            "conversation_id": conversation_id,
                        },
                    }
                )
                await database_sync_to_async(stream_buffer.set_status)("error")
                return

            # Run agent with buffered_send so events go to both WS and Redis
            result = await agent.run(
                user_message=agent_message,
                history_messages=history,
                send_callback=buffered_send,
                context_page=context_page,
                context_info=context_info or {},
                file_images=file_images or [],
                skill=skill,
            )

            # Save assistant message to DB (includes mode update in same transaction)
            mode = result.get("mode", "")
            await self._save_assistant_message(conversation_id, result, mode=mode)

            # Emit usage event for AI credit billing.
            # The cluster-RCA agent tracks cost directly from gateway headers
            # (no per-token pricing model); use the pre-computed cost_usd when
            # present, otherwise fall through to the token-based path.
            raw_cost_usd = result.get("cost_usd", 0)
            if raw_cost_usd and raw_cost_usd > 0:
                await database_sync_to_async(self._emit_chat_usage_from_cost)(
                    raw_cost_usd,
                    result.get("model_used", ""),
                    str(conversation_id),
                )
            else:
                await database_sync_to_async(self._emit_chat_usage)(
                    result.get("input_tokens", 0),
                    result.get("output_tokens", 0),
                    result.get("model_used", ""),
                    str(conversation_id),
                    getattr(agent.llm_client, "_gateway_cost", 0),
                )

            # Update conversation total_tokens for compaction tracking
            turn_tokens = result.get("input_tokens", 0) + result.get("output_tokens", 0)
            if turn_tokens > 0:
                await self._update_conversation_tokens(conversation_id, turn_tokens)

            # Send "done" message via both channels
            done_event = {
                "type": "done",
                "data": {
                    "message_id": result["id"],
                    "model_used": result.get("model_used", ""),
                    "mode": mode,
                    "input_tokens": result.get("input_tokens", 0),
                    "output_tokens": result.get("output_tokens", 0),
                },
            }
            await buffered_send(done_event)

            # Mark stream as done in Redis (sets TTL for cleanup)
            await database_sync_to_async(stream_buffer.cleanup_after_done)()
            self._agent_task = None

        except asyncio.CancelledError:
            # User clicked Stop — save partial response if any
            logger.info("falcon_ai_agent_cancelled", conversation_id=conversation_id)
            if agent and agent.partial_content:
                partial_result = {
                    "id": agent.assistant_message_id or str(uuid.uuid4()),
                    "content": agent.partial_content,
                    "tool_calls": agent.partial_tool_calls or [],
                    "completion_card": None,
                    "model_used": "",
                    "mode": agent.mode,
                    "input_tokens": agent.total_input_tokens,
                    "output_tokens": agent.total_output_tokens,
                }
                try:
                    await self._save_assistant_message(
                        conversation_id, partial_result, mode=agent.mode
                    )
                except Exception:
                    logger.warning("Failed to save partial response on cancel")
                # Emit usage for tokens consumed before cancellation
                if agent.total_input_tokens or agent.total_output_tokens:
                    await database_sync_to_async(self._emit_chat_usage)(
                        agent.total_input_tokens,
                        agent.total_output_tokens,
                        "",
                        str(conversation_id),
                        getattr(agent.llm_client, "_gateway_cost", 0),
                    )
            await database_sync_to_async(stream_buffer.set_status)("cancelled")
            self._agent_task = None
            return
        except Exception as e:
            logger.exception("falcon_ai_agent_error", error=str(e))
            await database_sync_to_async(stream_buffer.set_status)("error")
            try:
                await self.send_json(
                    {
                        "type": "error",
                        "data": {
                            "error": f"Agent error: {str(e)}",
                            "conversation_id": conversation_id,
                        },
                    }
                )
            except Exception:
                pass
            self._agent_task = None

    async def disconnect(self, close_code):
        logger.info(
            "falcon_ai_ws_disconnected",
            user_id=str(self.user.id) if self.user else None,
            close_code=close_code,
        )
        # DON'T cancel the agent task on disconnect — let it finish and save the result.
        # The send_json wrapper handles disconnected sockets gracefully (logs warning, no crash).
        # When the user reconnects and loads the conversation, they'll see the completed response.
        # Only cancel if the user explicitly clicked "Stop" (handled in _handle_stop).

    async def send_json(self, content, close=False):
        try:
            await super().send_json(content, close=close)
        except Exception as e:
            logger.warning(
                f"Failed to send WebSocket message (connection likely closed): {e}"
            )

    # ---- Database helpers (all use @database_sync_to_async) ----

    @database_sync_to_async
    def _get_organization(self):
        from accounts.models.organization_membership import OrganizationMembership

        try:
            membership = (
                OrganizationMembership.objects.filter(user=self.user, is_active=True)
                .select_related("organization")
                .first()
            )
            if membership:
                return membership.organization
            # Fallback to legacy FK
            if getattr(self.user, "organization", None):
                return self.user.organization
            return None
        except Exception:
            return None

    @database_sync_to_async
    def _get_organization_from_workspace(self, workspace_id):
        """Resolve organization from workspace_id, verifying user membership."""
        from accounts.models import Workspace
        from accounts.models.organization_membership import OrganizationMembership

        try:
            workspace = Workspace.objects.select_related("organization").get(
                id=workspace_id, is_active=True
            )
            has_access = OrganizationMembership.objects.filter(
                user=self.user,
                organization=workspace.organization,
                is_active=True,
            ).exists()
            if has_access:
                return workspace.organization
            return None
        except (Workspace.DoesNotExist, ValueError, ValidationError):
            return None

    @database_sync_to_async
    def _get_workspace(self):
        from accounts.models import Workspace

        if not self.organization_id:
            return None

        # Prefer workspace_id from query params
        if self.workspace_id:
            try:
                workspace = Workspace.objects.get(
                    id=self.workspace_id,
                    organization_id=self.organization_id,
                    is_active=True,
                )
                if self.user.can_access_workspace(workspace):
                    return workspace
            except (Workspace.DoesNotExist, ValueError):
                pass

        # Fallback to user.config
        user_config = getattr(self.user, "config", {}) or {}
        preferred_workspace_id = user_config.get(
            "currentWorkspaceId"
        ) or user_config.get("defaultWorkspaceId")

        if preferred_workspace_id:
            try:
                workspace = Workspace.objects.get(
                    id=preferred_workspace_id,
                    organization_id=self.organization_id,
                    is_active=True,
                )
                if self.user.can_access_workspace(workspace):
                    return workspace
            except (Workspace.DoesNotExist, ValueError):
                pass

        # Fallback to default workspace
        try:
            return Workspace.objects.get(
                organization_id=self.organization_id,
                is_default=True,
                is_active=True,
            )
        except Workspace.DoesNotExist:
            return None

    @database_sync_to_async
    def _save_user_message(self, conversation_id, content, files_metadata=None):
        from ee.falcon_ai.models import Conversation, Message

        try:
            conversation = Conversation.no_workspace_objects.get(
                id=conversation_id,
                user=self.user,
                organization=self.organization,
            )
            message = Message(
                conversation=conversation,
                role="user",
                content=content,
                files=files_metadata or [],
            )
            message.save()
            return message
        except Conversation.DoesNotExist:
            return None

    @database_sync_to_async
    def _save_assistant_message(self, conversation_id, result, mode=""):
        from django.db import transaction

        from ee.falcon_ai.models import Conversation, Message

        try:
            with transaction.atomic():
                conversation = Conversation.no_workspace_objects.get(
                    id=conversation_id,
                    user=self.user,
                    organization=self.organization,
                )
                message = Message(
                    id=result["id"],
                    conversation=conversation,
                    role="assistant",
                    content=result.get("content", ""),
                    thoughts=[],  # deprecated — tool calls are now first-class
                    tool_calls=result.get("tool_calls", []),
                    completion_card=result.get("completion_card"),
                    model_used=result.get("model_used", ""),
                    input_tokens=result.get("input_tokens", 0),
                    output_tokens=result.get("output_tokens", 0),
                )
                message.save()
                if mode:
                    conversation.mode = mode
                    conversation.save(update_fields=["mode", "updated_at"])
                return message
        except Conversation.DoesNotExist:
            return None

    @database_sync_to_async
    def _get_conversation_and_history(self, conversation_id):
        from ee.falcon_ai.models import Conversation

        try:
            conversation = Conversation.no_workspace_objects.get(
                id=conversation_id,
                user=self.user,
                organization=self.organization,
            )
            # Get recent messages for context (last 30), include tool_calls
            # so the agent remembers what tools it used in previous turns
            messages = list(
                conversation.messages.order_by("created_at").values(
                    "role", "content", "tool_calls"
                )[:30]
            )
            return conversation, messages
        except Conversation.DoesNotExist:
            return None, []

    @database_sync_to_async
    def _update_conversation_tokens(self, conversation_id, turn_tokens):
        """Accumulate token usage on the conversation for compaction tracking."""
        from django.db.models import F

        from ee.falcon_ai.models import Conversation

        Conversation.objects.filter(id=conversation_id).update(
            total_tokens=F("total_tokens") + turn_tokens
        )

    @database_sync_to_async
    def _get_files_metadata(self, file_ids):
        """Get file metadata for storing on messages (not content, just display info)."""
        from ee.falcon_ai.models import FalconFile

        files = FalconFile.objects.filter(id__in=file_ids, user=self.user)
        return [
            {
                "id": str(f.id),
                "name": f.name,
                "size": f.size,
                "content_type": f.content_type,
                "url": f.storage_url,
            }
            for f in files
        ]

    @database_sync_to_async
    def _load_file_contents(self, file_ids):
        """Load attached file contents for inclusion in LLM context.

        Returns a dict with 'text' (str) and 'images' (list of image content blocks).
        """
        import base64

        from ee.falcon_ai.models import FalconFile

        if not file_ids:
            return {"text": "", "images": []}

        files = FalconFile.objects.filter(
            id__in=file_ids,
            user=self.user,
        )

        text_parts = []
        images = []

        for f in files:
            if f.text_content:
                text_parts.append(
                    f"[Attached file: {f.name} ({f.content_type}, {f.size} bytes)]\n"
                    f"```\n{f.text_content[:20000]}\n```"
                )
            elif f.content_type and f.content_type.startswith("image/"):
                # Read image from MinIO and convert to base64 for vision
                try:
                    import httpx

                    resp = httpx.get(f.storage_url, timeout=10)
                    if resp.status_code == 200:
                        img_b64 = base64.b64encode(resp.content).decode("utf-8")
                        images.append(
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": f.content_type,
                                    "data": img_b64,
                                },
                            }
                        )
                        text_parts.append(f"[Attached image: {f.name}]")
                except Exception:
                    text_parts.append(
                        f"[Attached image: {f.name} — could not load for vision]"
                    )
            else:
                text_parts.append(
                    f"[Attached file: {f.name} ({f.content_type}, {f.size} bytes)] "
                    f"— Binary file, content not readable as text. URL: {f.storage_url}"
                )

        return {"text": "\n\n".join(text_parts), "images": images}

    @database_sync_to_async
    def _update_conversation_mode(self, conversation_id, mode):
        from ee.falcon_ai.models import Conversation

        try:
            conversation = Conversation.no_workspace_objects.get(
                id=conversation_id,
                user=self.user,
                organization=self.organization,
            )
            conversation.mode = mode
            conversation.save(update_fields=["mode", "updated_at"])
        except Conversation.DoesNotExist:
            pass

    def _emit_chat_usage(
        self,
        input_tokens: int,
        output_tokens: int,
        model_used: str,
        conversation_id: str,
        gateway_cost_usd: float = 0,
    ) -> None:
        """Calculate and emit an AI credit usage event for a Falcon AI turn.

        Looks up per-token pricing from AVAILABLE_MODELS via calculate_total_cost(),
        then applies the platform markup via calculate_ai_credits().
        """
        if not self.organization_id:
            return
        if not (input_tokens or output_tokens or gateway_cost_usd):
            return
        try:
            from agentic_eval.core_evals.fi_utils.token_count_helper import (
                calculate_total_cost,
            )

            cost_result = calculate_total_cost(
                model_used,
                {"prompt_tokens": input_tokens, "completion_tokens": output_tokens},
            )
            gateway_cost_usd = float(gateway_cost_usd or 0)
            actual_cost_usd = (
                gateway_cost_usd if gateway_cost_usd > 0 else cost_result["total_cost"]
            )
            pricing_source = (
                "gateway"
                if gateway_cost_usd > 0
                else cost_result.get("pricing_source", "")
            )

            config = BillingConfig.get()
            credits = config.calculate_ai_credits(actual_cost_usd)
            total_tokens = input_tokens + output_tokens

            emit(
                UsageEvent(
                    org_id=self.organization_id,
                    event_type=BillingEventType.FALCON_AI_CHAT,
                    amount=credits,
                    properties={
                        "source": "falcon_ai",
                        "source_id": conversation_id,
                        "model_used": model_used,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "prompt_tokens": input_tokens,
                        "completion_tokens": output_tokens,
                        "total_tokens": total_tokens,
                        "raw_cost_usd": str(actual_cost_usd),
                        "gateway_cost_usd": str(gateway_cost_usd),
                        "pricing_source": pricing_source,
                    },
                )
            )
        except Exception:
            logger.exception(
                "falcon_ai_usage_emit_failed",
                org_id=self.organization_id,
                conversation_id=conversation_id,
            )

    def _emit_chat_usage_from_cost(
        self,
        cost_usd: float,
        model_used: str,
        conversation_id: str,
    ) -> None:
        """Emit an AI credit usage event from a pre-computed USD cost.

        Used by the cluster-RCA agent which accumulates cost from gateway
        headers directly rather than tracking per-token counts.
        """
        if not self.organization_id or cost_usd <= 0:
            return
        try:
            config = BillingConfig.get()
            credits = config.calculate_ai_credits(cost_usd)

            emit(
                UsageEvent(
                    org_id=self.organization_id,
                    event_type=BillingEventType.FALCON_AI_CHAT,
                    amount=credits,
                    properties={
                        "source": "falcon_ai",
                        "source_id": conversation_id,
                        "model_used": model_used,
                        "raw_cost_usd": str(cost_usd),
                        "pricing_source": "gateway_header",
                    },
                )
            )
        except Exception:
            logger.exception(
                "falcon_ai_usage_from_cost_emit_failed",
                org_id=self.organization_id,
                conversation_id=conversation_id,
            )

    @database_sync_to_async
    def _update_message_feedback(self, message_id, feedback):
        from ee.falcon_ai.models import Message

        try:
            message = Message.no_workspace_objects.select_related("conversation").get(
                id=message_id,
                conversation__user=self.user,
                conversation__organization=self.organization,
            )
            message.feedback = feedback
            message.save(update_fields=["feedback", "updated_at"])
            return True
        except Message.DoesNotExist:
            return False
