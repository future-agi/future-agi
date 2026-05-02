import asyncio
import uuid
from urllib.parse import parse_qs
from uuid import uuid4

import structlog
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from accounts.models import User, Workspace

from tfc.ee_stub import _ee_stub

try:
    from ee.agenthub.prompt_generate_agent.prompt_generate import PromptGenerator
except ImportError:
    PromptGenerator = _ee_stub("PromptGenerator")

logger = structlog.get_logger(__name__)
from analytics.utils import (
    MixpanelEvents,
    get_mixpanel_properties,
    track_mixpanel_event,
)
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from model_hub.utils.async_generate_prompt_runner import generate_prompt_async
from model_hub.utils.async_improve_prompt_runner import improve_prompt_async
from model_hub.utils.async_prompt_runner import run_template_async
from model_hub.utils.websocket_direct_manager import WebSocketDirectManager
from model_hub.views.prompt_template import (
    replace_ids_with_column_name_async,
)
try:
    from ee.usage.models.usage import APICallStatusChoices, APICallTypeChoices
except ImportError:
    from futureagi._ee_stubs import APICallStatusChoices, APICallTypeChoices
try:
    from ee.usage.utils.usage_entries import count_text_tokens, log_and_deduct_cost_for_api_request
except ImportError:
    from futureagi._ee_stubs import count_text_tokens, log_and_deduct_cost_for_api_request


class PromptStreamConsumer(AsyncJsonWebsocketConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.session_uuid = None
        self.organization_id = None
        self.workspace_id = None

    async def connect(self):
        self.user = self.scope.get("user")
        if self.user and self.user.is_authenticated:
            self.session_uuid = str(uuid4())
            self.organization_id = await self.get_organization_id()

            # Extract workspace_id from query params (per-tab, avoids cross-tab leakage)
            params = parse_qs(self.scope.get("query_string", b"").decode())
            self.workspace_id = params.get("workspace_id", [None])[0]

            logger.info(
                f"PromptStream connection established: user={self.user.id}, session={self.session_uuid}"
            )
            await self.accept()
        else:
            logger.warning("PromptStream connection rejected: user not authenticated.")
            await self.close(code=4001)

    async def send_json(self, content, close=False):
        try:
            await super().send_json(content, close=close)
        except Exception as e:
            logger.warning(
                f"Failed to send WebSocket message (connection likely closed): {e}"
            )

    async def disconnect(self, close_code):
        logger.info(
            f"PromptStream connection closed: session={self.session_uuid}, code={close_code}"
        )

    async def receive_json(self, content):
        message_type = content.get("type")
        if message_type == "run_template":
            await self.handle_run_template(content)
        elif message_type == "improve_prompt":
            await self.handle_improve_prompt(content)
        elif message_type == "generate_prompt":
            await self.handle_generate_prompt(content)
        elif message_type == "stop_streaming":
            await self.handle_stop_streaming(content)
        elif message_type == "stop_improve_prompt":
            await self.handle_stop_improve_prompt(content)
        elif message_type == "stop_generate_prompt":
            await self.handle_stop_generate_prompt(content)
        else:
            await self.send_json(
                {"type": "error", "message": f"Unknown message type: {message_type}"}
            )

    async def handle_run_template(self, content):
        template_id = content.get("template_id")
        version = content.get("version")
        if not template_id:
            await self.send_json(
                {"type": "error", "message": "template_id is required"}
            )
            return
        if not version:
            await self.send_json({"type": "error", "message": "version is required"})
            return
        if not await self.validate_template_access(template_id):
            return

        await self.send_json(
            {
                "type": "execution_started",
                "template_id": template_id,
                "session_uuid": self.session_uuid,
            }
        )

        asyncio.create_task(self.execute_template_async(content, template_id))

    async def execute_template_async(self, content, template_id):
        try:
            workspace_id = await self.get_workspace_id()
            if not await self.validate_template_access(template_id):
                await self.send_json(
                    {
                        "type": "error",
                        "message": "Template access denied or not found",
                        "session_uuid": self.session_uuid,
                    }
                )
                return

            template = await database_sync_to_async(PromptTemplate.objects.get)(
                id=template_id
            )
            version_to_run = content.get("version")
            execution = await database_sync_to_async(PromptVersion.objects.get)(
                original_template=template, template_version=version_to_run
            )
            workspace = (
                await database_sync_to_async(Workspace.objects.get)(id=workspace_id)
                if workspace_id
                else None
            )

            ws_manager = WebSocketDirectManager(
                organization_id=self.organization_id,
                channel_name=self.channel_name,
                session_uuid=self.session_uuid,
                channel_layer=self.channel_layer,
                consumer_send_json_func=self.send_json,
            )

            await run_template_async(
                template=template,
                execution=execution,
                organization_id=self.organization_id,
                version_to_run=version_to_run,
                is_run=content.get("is_run"),
                run_index=content.get("run_index"),
                workspace=workspace,
                ws_manager=ws_manager,
            )
        except Exception as e:
            logger.exception(f"Error in execute_template_async: {e}")
            await self.send_json(
                {"type": "error", "message": str(e), "session_uuid": self.session_uuid}
            )

    async def handle_stop_streaming(self, content):
        template_id = content.get("template_id")
        version = content.get("version")
        # In this model, we stop the session directly.
        ws_manager = WebSocketDirectManager(
            organization_id=self.organization_id,
            channel_name=self.channel_name,
            session_uuid=self.session_uuid,
            channel_layer=self.channel_layer,
        )
        await ws_manager.set_stop_streaming(template_id, version)
        await self.send_json(
            {"type": "stop_acknowledged", "session_uuid": self.session_uuid}
        )

    async def handle_improve_prompt(self, content):

        existing_prompt = content.get("existing_prompt")
        existing_prompt = await replace_ids_with_column_name_async(existing_prompt)
        improvement_requirements = content.get("improvement_requirements", "")

        # Validate the input
        if not existing_prompt:
            await self.send_json(
                {"type": "error", "message": "Existing Prompt is required"}
            )
            return
        if not improvement_requirements:
            await self.send_json(
                {"type": "error", "message": "Improvement Requirements are required"}
            )
            return

        uid = None
        # Create the payload for improving the prompt
        payload = {
            "original_prompt": existing_prompt,
            "improvement_suggestions": improvement_requirements,
            "improve_id": f"improve_{uuid.uuid4()}",
            "organization_id": str(self.organization_id),
            "user_id": str(self.user.id),
            "mixpanel_uid": uid,
        }

        await self.send_json(
            {
                "type": "execution_started",
                "improve_id": payload.get("improve_id"),
                "session_uuid": self.session_uuid,
            }
        )

        asyncio.create_task(
            self.execute_improve_prompt_async(payload, payload.get("improve_id"))
        )

    async def execute_improve_prompt_async(self, content, improve_id):
        try:
            workspace_id = await self.get_workspace_id()
            workspace = (
                await database_sync_to_async(Workspace.objects.get)(id=workspace_id)
                if workspace_id
                else None
            )

            ws_manager = WebSocketDirectManager(
                organization_id=self.organization_id,
                channel_name=self.channel_name,
                session_uuid=self.session_uuid,
                channel_layer=self.channel_layer,
                consumer_send_json_func=self.send_json,
            )

            await improve_prompt_async(
                original_prompt=content.get("original_prompt", ""),
                improvement_suggestions=content.get("improvement_suggestions", ""),
                examples=content.get("examples", ""),
                improve_id=improve_id,
                organization_id=self.organization_id,
                user_id=content.get("user_id"),
                uid=content.get("mixpanel_uid"),
                workspace=workspace,
                ws_manager=ws_manager,
            )
        except Exception as e:
            logger.exception(f"Error in execute_improve_prompt_async: {e}")
            await self.send_json(
                {
                    "type": "error",
                    "message": str(e),
                    "improve_id": improve_id,
                    "session_uuid": self.session_uuid,
                }
            )

    async def handle_stop_improve_prompt(self, content):
        improve_id = content.get("improve_id")
        if not improve_id:
            await self.send_json({"type": "error", "message": "improve_id is required"})
            return

        ws_manager = WebSocketDirectManager(
            organization_id=self.organization_id,
            channel_name=self.channel_name,
            session_uuid=self.session_uuid,
            channel_layer=self.channel_layer,
        )
        await ws_manager.set_stop_improve_prompt(improve_id)
        await self.send_json(
            {
                "type": "stop_acknowledged",
                "improve_id": improve_id,
                "session_uuid": self.session_uuid,
            }
        )

    async def handle_generate_prompt(self, content):
        statement = content.get("statement")

        # Validate the input
        if not statement:
            await self.send_json({"type": "error", "message": "Statement is required"})
            return

        uid = None
        # Create the payload for generating the prompt
        generation_id = f"generate_{uuid.uuid4()}"
        payload = {
            "description": statement,
            "generation_id": generation_id,
            "organization_id": str(self.organization_id),
            "user_id": str(self.user.id),
            "mixpanel_uid": uid,
        }

        await self.send_json(
            {
                "type": "execution_started",
                "generation_id": generation_id,
                "session_uuid": self.session_uuid,
            }
        )

        asyncio.create_task(self.execute_generate_prompt_async(payload, generation_id))

    async def execute_generate_prompt_async(self, content, generation_id):
        try:
            workspace_id = await self.get_workspace_id()
            workspace = (
                await database_sync_to_async(Workspace.objects.get)(id=workspace_id)
                if workspace_id
                else None
            )

            ws_manager = WebSocketDirectManager(
                organization_id=self.organization_id,
                channel_name=self.channel_name,
                session_uuid=self.session_uuid,
                channel_layer=self.channel_layer,
                consumer_send_json_func=self.send_json,
            )

            await generate_prompt_async(
                description=content.get("description", ""),
                generation_id=generation_id,
                organization_id=self.organization_id,
                user_id=content.get("user_id"),
                uid=content.get("mixpanel_uid"),
                workspace=workspace,
                ws_manager=ws_manager,
            )
        except Exception as e:
            logger.exception(f"Error in execute_generate_prompt_async: {e}")
            await self.send_json(
                {
                    "type": "error",
                    "message": str(e),
                    "generation_id": generation_id,
                    "session_uuid": self.session_uuid,
                }
            )

    async def handle_stop_generate_prompt(self, content):
        generation_id = content.get("generation_id")
        if not generation_id:
            await self.send_json(
                {"type": "error", "message": "generation_id is required"}
            )
            return

        ws_manager = WebSocketDirectManager(
            organization_id=self.organization_id,
            channel_name=self.channel_name,
            session_uuid=self.session_uuid,
            channel_layer=self.channel_layer,
        )
        await ws_manager.set_stop_generate_prompt(generation_id)
        await self.send_json(
            {
                "type": "stop_acknowledged",
                "generation_id": generation_id,
                "session_uuid": self.session_uuid,
            }
        )

    @database_sync_to_async
    def get_organization_id(self):
        try:
            from accounts.models.organization_membership import OrganizationMembership

            membership = (
                OrganizationMembership.objects.filter(user=self.user, is_active=True)
                .select_related("organization")
                .first()
            )
            if membership:
                return membership.organization.id
            # Fallback to legacy FK
            if getattr(self.user, "organization", None):
                return self.user.organization.id
            return None
        except Exception:
            return None

    @database_sync_to_async
    def get_workspace_id(self):
        """Resolve workspace ID from query param (preferred) or user config (fallback).

        The query param is set per-tab by the frontend, so it avoids cross-tab
        leakage that can happen when reading from user.config (which is shared
        across all tabs).
        """
        if not self.organization_id:
            return None

        # Prefer workspace_id from query params (per-tab, no cross-tab leakage)
        if self.workspace_id:
            try:
                workspace = Workspace.objects.get(
                    id=self.workspace_id,
                    organization_id=self.organization_id,
                    is_active=True,
                )
                if self.user.can_access_workspace(workspace):
                    return str(workspace.id)
            except (Workspace.DoesNotExist, ValueError):
                pass

        # Fallback to user.config (legacy behavior)
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
                    return str(workspace.id)
            except (Workspace.DoesNotExist, ValueError):
                pass

        try:
            default_workspace = Workspace.objects.get(
                organization_id=self.organization_id,
                is_default=True,
                is_active=True,
            )
            return str(default_workspace.id)
        except Workspace.DoesNotExist:
            return None

    async def validate_template_access(self, template_id):
        """Validate that user has access to the template"""

        @database_sync_to_async
        def check_template():
            try:
                template = PromptTemplate.objects.get(id=template_id)
                if template.organization_id != self.organization_id:
                    return "no_permission"
                return "valid"
            except PromptTemplate.DoesNotExist:
                return "not_found"

        result = await check_template()

        if result == "no_permission":
            await self.send_json(
                {
                    "type": "error",
                    "message": "You do not have permission to access this template.",
                }
            )
            await self.close(code=4003)
            return False
        elif result == "not_found":
            await self.send_json({"type": "error", "message": "Template not found."})
            await self.close(code=4004)
            return False

        return True
