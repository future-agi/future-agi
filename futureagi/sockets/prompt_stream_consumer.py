import asyncio
import uuid
from urllib.parse import parse_qs
from uuid import uuid4

import structlog
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.core.exceptions import ValidationError

from accounts.models import User, Workspace  # noqa: F401 — User re-exported for tests
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from model_hub.utils.async_generate_prompt_runner import generate_prompt_async
from model_hub.utils.async_improve_prompt_runner import improve_prompt_async
from model_hub.utils.async_prompt_runner import run_template_async
from model_hub.utils.websocket_direct_manager import WebSocketDirectManager
from model_hub.views.prompt_template import replace_ids_with_column_name_async

logger = structlog.get_logger(__name__)


# WebSocket close codes — cross-side contract with the FE.
# Mirrored in frontend/src/utils/constants.js (WS_CLOSE_CODES). The vitest
# snapshot in frontend/src/sections/workbench/createPrompt/__tests__/
# ws-close-codes.test.js pins these values so drift is caught in CI.
WS_CLOSE_CODE_UNAUTHENTICATED = 4001
WS_CLOSE_CODE_PERMISSION_DENIED = 4003
WS_CLOSE_CODE_NOT_FOUND = 4004


class PromptStreamConsumer(AsyncJsonWebsocketConsumer):
    """Workbench WS consumer.

    Access model: the workspace (from `?workspace_id=<uuid>` query param) is
    the single source of truth for what org the caller is acting on. On every
    execute path we resolve `(workspace, org)` from that query param and gate
    on ``user.can_access_workspace(workspace)``. Nothing about the connect-time
    membership set matters — it was the root of the multi-org bug (TH-5944).

    We never mutate a per-consumer ``organization_id`` from concurrent async
    tasks; each execute derives ``org_id`` locally and passes it as an explicit
    kwarg to downstream code.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.session_uuid = None
        self.workspace_id = None
        # Cache of the last successfully-resolved org, kept only so stop_*
        # handlers can point WebSocketDirectManager at the right namespace
        # without re-resolving. Never used for auth.
        self._last_org_id = None

    async def connect(self):
        self.user = self.scope.get("user")
        if not (self.user and self.user.is_authenticated):
            logger.warning("PromptStream connection rejected: user not authenticated.")
            await self.close(code=WS_CLOSE_CODE_UNAUTHENTICATED)
            return

        self.session_uuid = str(uuid4())
        params = parse_qs(self.scope.get("query_string", b"").decode())
        self.workspace_id = params.get("workspace_id", [None])[0]

        logger.info(
            f"PromptStream connection established: user={self.user.id}, "
            f"session={self.session_uuid}, workspace_id={self.workspace_id}"
        )
        await self.accept()

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
            await self._send_ws_error(f"Unknown message type: {message_type}")

    # ------------------------------------------------------------------
    # Access resolution — the single gate.
    # ------------------------------------------------------------------

    async def _resolve_workspace_and_org(self, *, correlation=None):
        """Resolve the workspace + derive its org.

        Emits a structured error frame + closes the socket with the appropriate
        WS_CLOSE_CODE_* on failure, and returns ``(None, None)``. Returns
        ``(workspace, org_id)`` on success.
        """
        if not self.workspace_id:
            await self._send_ws_error(
                "workspace_id query param is required.",
                correlation=correlation,
            )
            await self.close(code=WS_CLOSE_CODE_NOT_FOUND)
            return None, None

        try:
            workspace = await database_sync_to_async(
                lambda: Workspace.objects.get(id=self.workspace_id, is_active=True)
            )()
        except (Workspace.DoesNotExist, ValueError, ValidationError):
            await self._send_ws_error(
                "Workspace not found or inactive.",
                correlation=correlation,
            )
            await self.close(code=WS_CLOSE_CODE_NOT_FOUND)
            return None, None

        can_access = await database_sync_to_async(self.user.can_access_workspace)(
            workspace
        )
        if not can_access:
            await self._send_ws_error(
                "You do not have permission to use this workspace.",
                correlation=correlation,
            )
            await self.close(code=WS_CLOSE_CODE_PERMISSION_DENIED)
            return None, None

        self._last_org_id = workspace.organization_id
        return workspace, workspace.organization_id

    async def _send_ws_error(self, message, *, correlation=None):
        payload = {
            "type": "error",
            "message": message,
            "session_uuid": self.session_uuid,
        }
        if correlation:
            payload.update(correlation)
        await self.send_json(payload)

    # ------------------------------------------------------------------
    # run_template
    # ------------------------------------------------------------------

    async def handle_run_template(self, content):
        template_id = content.get("template_id")
        version = content.get("version")
        if not template_id:
            await self._send_ws_error("template_id is required")
            return
        if not version:
            await self._send_ws_error("version is required")
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
            workspace, org_id = await self._resolve_workspace_and_org(
                correlation={"template_id": template_id}
            )
            if workspace is None:
                return

            try:
                template = await database_sync_to_async(PromptTemplate.objects.get)(
                    id=template_id
                )
            except PromptTemplate.DoesNotExist:
                await self._send_ws_error(
                    "Template not found.",
                    correlation={"template_id": template_id},
                )
                await self.close(code=WS_CLOSE_CODE_NOT_FOUND)
                return

            if template.workspace_id:
                # Template is scoped to a specific workspace — the connected
                # workspace must match exactly. Same-org-different-workspace
                # is a leak too (TH-5944 only closed the cross-org hole).
                if template.workspace_id != workspace.id:
                    await self._send_ws_error(
                        "Template does not belong to the selected workspace.",
                        correlation={"template_id": template_id},
                    )
                    await self.close(code=WS_CLOSE_CODE_PERMISSION_DENIED)
                    return
            elif template.organization_id != org_id:
                # Legacy/org-wide template (no workspace_id) — fall back to
                # the looser org-level check.
                await self._send_ws_error(
                    "Template does not belong to the selected workspace's organization.",
                    correlation={"template_id": template_id},
                )
                await self.close(code=WS_CLOSE_CODE_PERMISSION_DENIED)
                return

            version_to_run = content.get("version")
            execution = await database_sync_to_async(PromptVersion.objects.get)(
                original_template=template, template_version=version_to_run
            )

            ws_manager = WebSocketDirectManager(
                organization_id=org_id,
                channel_name=self.channel_name,
                session_uuid=self.session_uuid,
                channel_layer=self.channel_layer,
                consumer_send_json_func=self.send_json,
            )
            await run_template_async(
                template=template,
                execution=execution,
                organization_id=org_id,
                version_to_run=version_to_run,
                is_run=content.get("is_run"),
                run_index=content.get("run_index"),
                workspace=workspace,
                ws_manager=ws_manager,
            )
        except Exception:
            logger.exception("execute_template_async failed")
            await self._send_ws_error(
                "Execution failed. Please retry.",
                correlation={"template_id": template_id},
            )

    async def handle_stop_streaming(self, content):
        template_id = content.get("template_id")
        version = content.get("version")
        ws_manager = WebSocketDirectManager(
            organization_id=self._last_org_id,
            channel_name=self.channel_name,
            session_uuid=self.session_uuid,
            channel_layer=self.channel_layer,
        )
        await ws_manager.set_stop_streaming(template_id, version)
        await self.send_json(
            {"type": "stop_acknowledged", "session_uuid": self.session_uuid}
        )

    # ------------------------------------------------------------------
    # improve_prompt
    # ------------------------------------------------------------------

    async def handle_improve_prompt(self, content):
        existing_prompt = content.get("existing_prompt")
        existing_prompt = await replace_ids_with_column_name_async(existing_prompt)
        improvement_requirements = content.get("improvement_requirements", "")

        if not existing_prompt:
            await self._send_ws_error("Existing Prompt is required")
            return
        if not improvement_requirements:
            await self._send_ws_error("Improvement Requirements are required")
            return

        payload = {
            "original_prompt": existing_prompt,
            "improvement_suggestions": improvement_requirements,
            "improve_id": f"improve_{uuid.uuid4()}",
            "user_id": str(self.user.id),
            "mixpanel_uid": None,
        }

        await self.send_json(
            {
                "type": "execution_started",
                "improve_id": payload["improve_id"],
                "session_uuid": self.session_uuid,
            }
        )
        asyncio.create_task(
            self.execute_improve_prompt_async(payload, payload["improve_id"])
        )

    async def execute_improve_prompt_async(self, content, improve_id):
        try:
            workspace, org_id = await self._resolve_workspace_and_org(
                correlation={"improve_id": improve_id}
            )
            if workspace is None:
                return

            ws_manager = WebSocketDirectManager(
                organization_id=org_id,
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
                organization_id=org_id,
                user_id=content.get("user_id"),
                uid=content.get("mixpanel_uid"),
                workspace=workspace,
                ws_manager=ws_manager,
            )
        except Exception:
            logger.exception("execute_improve_prompt_async failed")
            await self._send_ws_error(
                "Improve failed. Please retry.",
                correlation={"improve_id": improve_id},
            )

    async def handle_stop_improve_prompt(self, content):
        improve_id = content.get("improve_id")
        if not improve_id:
            await self._send_ws_error("improve_id is required")
            return

        ws_manager = WebSocketDirectManager(
            organization_id=self._last_org_id,
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

    # ------------------------------------------------------------------
    # generate_prompt
    # ------------------------------------------------------------------

    async def handle_generate_prompt(self, content):
        statement = content.get("statement")
        if not statement:
            await self._send_ws_error("Statement is required")
            return

        generation_id = f"generate_{uuid.uuid4()}"
        payload = {
            "description": statement,
            "generation_id": generation_id,
            "user_id": str(self.user.id),
            "mixpanel_uid": None,
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
            workspace, org_id = await self._resolve_workspace_and_org(
                correlation={"generation_id": generation_id}
            )
            if workspace is None:
                return

            ws_manager = WebSocketDirectManager(
                organization_id=org_id,
                channel_name=self.channel_name,
                session_uuid=self.session_uuid,
                channel_layer=self.channel_layer,
                consumer_send_json_func=self.send_json,
            )
            await generate_prompt_async(
                description=content.get("description", ""),
                generation_id=generation_id,
                organization_id=org_id,
                user_id=content.get("user_id"),
                uid=content.get("mixpanel_uid"),
                workspace=workspace,
                ws_manager=ws_manager,
            )
        except Exception:
            logger.exception("execute_generate_prompt_async failed")
            await self._send_ws_error(
                "Generate failed. Please retry.",
                correlation={"generation_id": generation_id},
            )

    async def handle_stop_generate_prompt(self, content):
        generation_id = content.get("generation_id")
        if not generation_id:
            await self._send_ws_error("generation_id is required")
            return

        ws_manager = WebSocketDirectManager(
            organization_id=self._last_org_id,
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
